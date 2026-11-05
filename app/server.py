"""只绑 127.0.0.1 的本机服务：给向导页面提供接口。每个 /api 请求都要带启动时生成的 token。"""
import hashlib
import json
import msvcrt
import re
import secrets
import subprocess
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import checks
import diagnostics
import installer
import official_binding
import personalize
import services
import safe_logging
from store import State, StateCorruptError, app_home, last_install, read_state, remember_install, restore_state_backup

WEB = Path(__file__).resolve().parent / "web"
MAX_BODY = 1024 * 1024
TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".js": "text/javascript; charset=utf-8", ".png": "image/png", ".ico": "image/x-icon",
         ".svg": "image/svg+xml"}
SECRET_FIELDS = {"deepseek": "deepseek", "siliconflow": "siliconflow", "app_id": "doubao_app_id",
                 "token": "doubao_token", "official_appid": "official_appid", "official_secret": "official_secret"}


class App:
    def __init__(self, mode: str, root: Path | None = None, boot: Path | None = None, home: Path | None = None,
                 checks_mod=checks, steps_factory=installer.default_steps, exclusive=False, find_instance=None):
        self._exclusive = exclusive
        self._lease = None
        self._lease_root = None
        self._find_instance = find_instance
        self.mode = mode  # install | status
        self.token = secrets.token_urlsafe(16)
        self.boot = Path(boot) if boot else Path(__file__).resolve().parent.parent
        self.home = home
        self.checks = checks_mod
        self.steps_factory = steps_factory
        self.last_ping = time.monotonic()
        self.window_closed = False
        self.quitting = False  # 用户点了「退出」或「卸载」：main 看到就收摊
        self._checked: set[tuple] = set()
        self._lock = threading.Lock()
        self.root = self.state = self.installer = None
        self._web = None
        self._starter = None
        self.recovery = None
        if root:
            self._open(Path(root))

    def close(self):
        if self._lease:
            self._lease.close()  # Closing releases the OS lock even after a failed open.
            self._lease = self._lease_root = None

    def _claim(self, root):
        if not self._exclusive or self._lease_root == root.resolve():
            return
        self.close()
        url = self._find_instance(root) if self._find_instance else None
        if not url:
            root.mkdir(parents=True, exist_ok=True)
            lease = (root / ".controller.lock").open("a+b")
            try:
                lease.seek(0)
                msvcrt.locking(lease.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                lease.close()
            else:
                self._lease, self._lease_root = lease, root.resolve()
                return
        self.recovery = {"busy": True, "backup_available": False, "continue_url": url,
                         "message": "这个安装目录已在另一个窗口中使用。请回到已有窗口继续；关闭它后，也可以在这里重新检查。"}

    # ---------- 共用 ----------
    def _open(self, root: Path) -> dict:
        self.root = root
        self.recovery = None
        self.state = self.installer = None
        try:
            self._claim(root)
            if self.recovery:
                return {"keys": False, "profile": False}
            if not (root / "state.json").exists() and ((root / "state.json.bak").exists() or (root / "astrbot" / "data" / "cmd_config.json").exists()):
                raise StateCorruptError("安装状态文件丢失，已有数据已保留。请从有效备份恢复后继续。")
            self.state = State.load(root)
            installer.sync_app(root, self.boot)
            personalize.fix_plugin_imports(services.Layout(root).astrbot_data)
        except (StateCorruptError, installer.InstallError, OSError) as e:
            valid_backup = False
            try:
                read_state(root / "state.json.bak")
                valid_backup = isinstance(e, StateCorruptError)
            except (OSError, ValueError):
                pass
            self.recovery = {"message": safe_logging.sanitize(str(e)), "backup_available": valid_backup}
            self.state = self.installer = None
            self.log_error(f"打开安装目录失败：{type(e).__name__}: {e}")
            return {"keys": False, "profile": False}
        self.installer = installer.Installer(root, self.state, steps=self.steps_factory(), boot=self.boot)
        # 中断后再打开：之前填过的不用再填
        resume = {"keys": bool(self.state.data.get("keys_ok")), "profile": bool(self.state.data.get("profile"))}
        if resume["keys"]:
            self.installer.provide("keys", {"owner_qq": self.state.data.get("owner_qq")})
        if resume["profile"]:
            self.installer.provide("profile", self.state.data["profile"])
        return resume

    def log_error(self, msg: str) -> Path:
        """写日志，返回日志文件路径（界面上要显示出来好让人找到）。"""
        if self.installer:
            self.installer.log(msg)  # 安装目录 logs\installer.log，已脱敏
            return self.root / "logs" / "installer.log"
        path = self.root / "logs" / "installer.log" if self.root else app_home(self.home) / "wizard.log"
        safe_logging.append_log(path, msg, secrets=safe_logging.secret_values(self.state), root=self.root)
        return path

    @property
    def layout(self):
        return services.Layout(self.root)

    def pack_dir(self) -> Path:
        for d in ((self.root / "pack") if self.root else None, self.boot / "pack",
                  self.boot / "vendor" / "dafeiyu-config-pack"):  # 最后一个是源码目录里直接运行时
            if d and (d / "settings" / "pack.json").is_file():
                return d
        raise FileNotFoundError("找不到配置包")

    def napcat(self):
        if self._web is None and self.state and self.state.data.get("ports"):
            self._web = services.NapCatWeb(f"http://127.0.0.1:{self.state.data['ports']['napcat_webui']}",
                                           self.state.get_secret("napcat_webui_token"))
        return self._web

    def login_status(self) -> str:
        if self.state and self.state.data.get("platform") == "official":
            return "official"
        web = self.napcat()
        return web.status() if web else "starting"

    # ---------- 接口 ----------
    def handle_info(self, _q) -> dict:
        if self.recovery:
            return {"mode": "recovery", "root": str(self.root), "profile": {"name": "大肥鱼"},
                    "recovery": self.recovery}
        profile = (self.state.data.get("profile") if self.state else None) or \
            personalize.default_profile(self.pack_dir())
        return {"mode": self.mode, "default_path": last_install(self.home) or self.checks.default_install_path(),
                "root": str(self.root) if self.root else None, "profile": profile,
                "can_repair": bool(self.root and any((self.root / name).is_dir() for name in ("astrbot", "runtime"))),
                "platform": self.state.data.get("platform", "napcat") if self.state else "napcat",
                "owner_qq": self.state.data.get("owner_qq", "") if self.state else "",
                "memory": self.state.data.get("memory", "2" if self.state.data["secrets"].get("siliconflow") else "0") if self.state else "0",
                "voice": self.state.data.get("voice", "3" if self.state.data["secrets"].get("doubao_token") else "0") if self.state else "0",
                # 改设置时这几项留空就是不改，界面上要能提示「已保存」
                "saved_keys": sorted(f for f, name in SECRET_FIELDS.items()
                                     if self.state and self.state.data["secrets"].get(name)) if self.state else [],
                "voice_presets": [{"name": n, "voice": v} for n, v in personalize.VOICE_PRESETS]}

    def handle_path(self, body: dict) -> dict:
        path = str(body.get("path") or "").strip()
        if err := self.checks.check_install_path(path):
            return {"ok": False, "message": err}
        platform = str(body.get("platform") or "napcat")
        if platform not in ("napcat", "official"):
            return {"ok": False, "message": "不支持的 QQ 接入方式"}
        with self._lock:
            if self.root and Path(path) == self.root and self.state:
                resume = {"keys": bool(self.state.data.get("keys_ok")), "profile": bool(self.state.data.get("profile"))}
            else:
                if any(worker and worker.progress()["running"] for worker in (self.installer, self._starter)):
                    return {"ok": False, "message": "当前安装仍在进行，请先在当前目录完成安装。"}
                resume = self._open(Path(path))
            if self.recovery:
                return {"ok": False, "recovery": True, "message": self.recovery["message"]}
            existing = self.state.data.get("platform", "napcat")
            if self.state.is_done("configs") and existing != platform:
                return {"ok": False, "message": "已有安装不能直接切换接入方式，请选择新的安装目录。"}
            self.state.data["platform"] = platform
            self.state.save()
            remember_install(Path(path), self.home)
            services.allow_start(self.state)
            self.installer.start()
        return {"ok": True, "resume": resume}

    def _check(self, field: str, body: dict) -> tuple[bool, str]:
        if field == "doubao":
            key = ("doubao", body.get("app_id", "").strip(), body.get("token", "").strip())
            if not key[1] or not key[2]:
                return False, "APP ID 和 Access Token 都要填"
        else:
            key = (field, str(body.get("value", "")).strip())
            if not key[1]:
                return False, "还没填"
        if key in self._checked:
            return True, "可以用"
        if field == "deepseek":
            ok, msg = self.checks.check_deepseek(key[1])
        elif field == "siliconflow":
            ok, msg = self.checks.check_siliconflow(key[1])
        elif field == "doubao":
            ok, msg, _ = self.checks.check_doubao(key[1], key[2])
        else:
            return False, "未知项目"
        if ok:
            self._checked.add(key)
        return ok, msg

    def handle_check(self, body: dict) -> dict:
        if body.get("field") == "doubao" and self.state:
            body = {**body, "app_id": str(body.get("app_id") or "").strip() or self.state.get_secret("doubao_app_id"),
                    "token": str(body.get("token") or "").strip() or self.state.get_secret("doubao_token")}
        ok, msg = self._check(str(body.get("field")), body)
        return {"ok": ok, "message": msg}

    def handle_keys(self, body: dict) -> dict:
        errors = {}
        qq = str(body.get("owner_qq") or "").strip()
        official = self.state.data.get("platform") == "official"
        if official:
            qq = str(self.state.data.get("owner_qq") or "").strip()
            if qq == "DAFEIYU_UNBOUND_OWNER":
                qq = ""
        elif not (qq.isdigit() and 5 <= len(qq) <= 12):
            errors["owner_qq"] = "QQ 号只能是 5 到 12 位数字"
        # 改设置时只改一项：留空的用已经存着的那个（已经验过，不再跑一次网络检查）
        saved = self.state.data["secrets"]
        memory = str(body.get("memory", self.state.data.get("memory", "2" if body.get("siliconflow") or saved.get("siliconflow") else "0")))
        voice = str(body.get("voice", self.state.data.get("voice", "3" if body.get("app_id") or body.get("token") or saved.get("doubao_token") else "0")))
        enabled = {"deepseek"} | ({"siliconflow"} if memory == "2" else set()) | ({"app_id", "token"} if voice == "3" else set())
        if official:
            enabled |= {"official_appid", "official_secret"}
        vals = {}
        for field, name in SECRET_FIELDS.items():
            vals[field] = str(body.get(field) or "").strip()
            if not vals[field] and field in enabled:
                try:
                    vals[field] = self.state.get_secret(name)
                except ValueError:
                    errors["doubao" if field in ("app_id", "token") else field] = "原凭据无法解密，请重新填写，或使用原 Windows 用户打开。"
        if official:
            if not vals["official_appid"].isdigit():
                errors["official_appid"] = "请填写官方机器人 AppID"
            if not vals["official_secret"]:
                errors["official_secret"] = "请填写官方机器人 AppSecret"
            if qq and vals["official_appid"] != self.state.get_secret("official_appid"):
                errors["official_appid"] = "已经绑定主人，请保持同一个机器人的 AppID。更换机器人请使用新的安装目录。"
        if memory not in ("0", "2") or voice not in ("0", "3"):
            return {"ok": False, "errors": {"mode": "不支持的记忆或语音选项"}}
        if errors:
            return {"ok": False, "errors": errors}
        if official:
            ok, msg = self.checks.check_official(vals["official_appid"], vals["official_secret"])
            if not ok:
                return {"ok": False, "errors": {"official_secret": msg}}
        for field in ("deepseek", "siliconflow"):
            if field == "siliconflow" and memory == "0":
                continue
            ok, msg = self._check(field, {"value": vals[field]})
            if not ok:
                errors[field] = msg
        if voice == "3":
            ok, msg = self._check("doubao", {"app_id": vals["app_id"], "token": vals["token"]})
            if not ok:
                errors["doubao"] = msg
        if errors:
            return {"ok": False, "errors": errors}
        with self._lock:
            if official:
                # A binding confirmation may have completed during the network checks.
                qq = self.state.data.get("owner_qq") or ""
                if qq == "DAFEIYU_UNBOUND_OWNER":
                    qq = ""
                changed = any(vals[f] != self.state.get_secret(f) for f in ("official_appid", "official_secret"))
                if changed and not qq:
                    official_binding.create(self.root, renew=True)
            for field, name in SECRET_FIELDS.items():
                if vals[field]:
                    self.state.set_secret(name, vals[field])
            self.state.data.update(owner_qq=qq, keys_ok=True, memory=memory, voice=voice)
            self.state.save()
            self.installer.provide("keys", {"owner_qq": qq})
        return {"ok": True}

    def handle_preview_voice(self, body: dict) -> dict:
        if self.state.data.get("voice", "3" if self.state.data["secrets"].get("doubao_token") else "0") != "3":
            return {"ok": False, "message": "请先启用并配置豆包语音"}
        name = str(body.get("name") or personalize.DEFAULT_NAME).strip()[:20]
        ok, msg, audio = self.checks.check_doubao(self.state.get_secret("doubao_app_id"),
                                                  self.state.get_secret("doubao_token"),
                                                  voice=str(body.get("voice") or ""), text=f"你好呀，我是{name}")
        return {"ok": ok, "message": msg, "audio_b64": audio}

    def handle_profile(self, body: dict) -> dict:
        profile = {k: body.get(k) for k in ("name", "persona", "voice", "memes")}
        if err := personalize.validate_profile(profile):
            return {"ok": False, "message": err}
        self.state.data["profile"] = profile
        self.state.save()
        self.installer.provide("profile", profile)
        return {"ok": True}

    def handle_progress(self, _q) -> dict:
        p = self.installer.progress() if self.installer else {"steps": [], "running": False, "waiting": None,
                                                                "finished": False}
        return p

    def handle_retry(self, _b) -> dict:
        services.allow_start(self.state)
        self.installer.retry()
        return {"ok": True}

    def handle_login(self, _q) -> dict:
        status = self.login_status()
        self.remember_qq(status)
        web = self.napcat()
        return {"status": status, "error": web.last_error if web else "", "qr_revision": self.qr_revision()}

    def qr_revision(self) -> str:
        png = services.qrcode_png(self.layout)
        return hashlib.sha256(png).hexdigest() if png else ""

    def handle_refresh_qr(self, _b) -> dict:
        """手动换一张码。NapCat 报「已登录」时只换码没用，得连它一起重启。"""
        web = self.napcat()
        if web and "已登录" in web.last_error:
            # -q 快速登录会卡住这个账号，清掉记下的 QQ 号，重启后回到扫码
            self.state.data.pop("qq", None)
            self.state.save()
            services.stop(self.state, "napcat")
            self._web = None
            if self.mode == "status":
                self.handle_start({})
            return {"ok": True, "message": "正在重启 NapCat，二维码大概 30 秒后出来"}
        try:
            web.refresh_qr()
        except Exception as e:
            return {"ok": False, "message": f"换码失败：{e}"}
        return {"ok": True, "message": "换了一张新的码"}

    def handle_done(self, _q) -> dict:
        info = {"dashboard_url": f"http://127.0.0.1:{self.state.data['ports']['dashboard']}",
                "username": "dafeiyu", "password": self.state.get_secret("dashboard_password"),
                "qq": self.state.data.get("qq", "")}
        (self.root / "我的信息.txt").write_text(
            "大肥鱼后台（改设置用，别发给别人）\n"
            f"地址：{info['dashboard_url']}\n账号：{info['username']}\n密码：{info['password']}\n", encoding="utf-8")
        return info

    # ---------- 状态页 ----------
    def remember_qq(self, login: str) -> None:
        """登录成功了就把小号记下来。有它下次启动才能快速登录，不然每次都要重新扫码。"""
        if login != "online" or self.state.data.get("qq"):
            return
        web = self.napcat()
        uin = web.login_uin() if web else None
        if uin:
            self.state.data["qq"] = uin
            self.state.reload_pids()
            self.state.save()

    def handle_status(self, _q) -> dict:
        starting = bool(self._starter and self._starter.progress()["running"])
        err = next((s["message"] for s in (self._starter.progress()["steps"] if self._starter else [])
                    if s["status"] == "error"), "")
        login = self.login_status()
        self.remember_qq(login)
        pids = self.state.reload_pids()
        try:
            found = services.find_processes(self.layout)
            for name, pid in pids.items():
                if name not in found or pid in found[name]:
                    continue
                if services.is_running(pid):
                    expected = self.layout.python if name == "astrbot" else self.layout.napcat / "node.exe"
                    if services.process_path(pid) == expected.resolve():
                        found[name].append(pid)
        except PermissionError:
            return {"astrbot": None, "napcat": None, "permission_denied": True,
                    "message": "Windows 拒绝检查后台进程，可点「让她下线」并按提示授权。"}
        actual = {name: (pids.get(name) if pids.get(name) in ids else ids[0]) for name, ids in found.items() if ids}
        if pids != actual:
            self.state.data["pids"] = actual
            self.state.save()
        stopped = State.load(self.root).data.get("services_stopped", False)
        web = self.napcat() if login != "official" else None
        connection = official_binding.connection_state(self.root, actual.get("astrbot")) if login == "official" else ""
        issue = diagnostics.official_error(self.root) if login == "official" and found["astrbot"] else None
        return {"qq_connection": connection, "connection_error": issue["message"] if issue else "",
                "astrbot": bool(found["astrbot"]), "napcat": bool(found["napcat"]), "stopped": stopped,
                "login": login, "starting": starting, "error": err, "qq": self.state.data.get("qq") or "",
                "login_error": web.last_error if web else "", "qr_revision": self.qr_revision() if login in ("need_scan", "scanned", "error") else ""}

    def handle_start(self, _b) -> dict:
        with self._lock:
            if self.quitting:
                return {"ok": False, "message": "正在退出，无法启动。"}
            services.allow_start(self.state)
            if not (self._starter and self._starter.progress()["running"]):
                steps = [installer.Step("astrbot", "启动 AstrBot", installer.ensure_astrbot)]
                if self.state.data.get("platform") == "official":
                    steps.append(installer.Step("qq", "确认 QQ 连接", installer.wait_official))
                if self.state.data.get("platform") != "official":
                    steps.append(installer.Step("napcat", "启动 NapCat", installer.ensure_napcat))
                self._starter = installer.Installer(self.root, self.state, resume=False, boot=self.boot, steps=steps)
                self._starter.start()
        return {"ok": True}

    def handle_stop(self, _b) -> dict:
        with self._lock:
            self._stop_runtime()
        return {"ok": True}

    def _stop_runtime(self) -> None:
        workers = [worker for worker in (self.installer, self._starter) if worker]
        for worker in workers:
            worker.cancel()
        try:
            services.stop_all(self.state)
        except services.StopPermissionError:
            self.log_error("停止服务权限不足，正在请求 Windows 管理员授权")
            services.elevated_stop(self.root)
            services.stop_all(self.state)  # 再次确认服务确实退出，并同步当前窗口的状态。
        for worker in workers:
            if not worker.cancel(timeout=10):
                raise RuntimeError("后台任务尚未退出，请稍等片刻后重试。")
        self._web = None

    def handle_switch_qq(self, _b) -> dict:
        """换小号 / 退出小号：忘掉当前登录，回到扫码。"""
        if self.state.data.get("platform") == "official":
            return {"ok": False, "message": "官方机器人请在后台修改连接设置。"}
        with self._lock:
            self._stop_runtime()
            services.clear_qq_login(self.layout, self.state)
        return {"ok": True}

    def handle_uninstall(self, _b) -> dict:
        """彻底卸载：停服务、删快捷方式，退出后由清理脚本删掉安装目录。"""
        import uninstall
        with self._lock:
            self._stop_runtime()
            script = uninstall.uninstall(self.root, self.state, home=self.home)
            self.quitting = True
        return {"ok": True, "log": str(script.parent / "uninstall.log")}

    def handle_quit(self, _b) -> dict:
        """一键停掉所有服务并退出（相当于关掉 app）。"""
        with self._lock:
            self._stop_runtime()
            self.quitting = True
        return {"ok": True}

    def _restart_steps(self, steps) -> dict:
        if not self._lock.acquire(blocking=False):
            return {"ok": False, "message": "上一次操作仍在处理中，请稍候查看进度，不用重复点击。"}
        try:
            self._stop_runtime()
            services.allow_start(self.state)
            self.installer.redo(steps)
        finally:
            self._lock.release()
        return {"ok": True}

    def handle_apply(self, _b) -> dict:
        return self._restart_steps(["configs", "astrbot", "napcat", "import", "login"])

    def handle_reconnect(self, _b) -> dict:
        # Resume unfinished steps; completed extraction and imports stay intact.
        return self._restart_steps(["astrbot", "login"])

    def handle_diagnose(self, _q) -> dict:
        report = diagnostics.inspect(self.root)
        self.log_error("本机诊断：" + (", ".join(item["code"] for item in report["items"]) or "未发现已知问题"))
        return report

    def handle_recover(self, body) -> dict:
        with self._lock:
            if not self.recovery:
                return {"ok": True}
            if body.get("action") == "restore_backup":
                if not self.recovery["backup_available"]:
                    return {"ok": False, "message": "没有可用的状态备份，原文件保持不变。"}
                restore_state_backup(self.root)
            elif body.get("action") != "recheck":
                return {"ok": False, "message": "不支持的恢复操作"}
            self._open(self.root)
            if not self.recovery:
                self.mode = "status" if self.state.is_done("login") else "install"
            return {"ok": self.recovery is None, "message": self.recovery["message"] if self.recovery else "状态已恢复"}

    def handle_repair(self, _b) -> dict:
        with self._lock:
            if self.installer.progress()["running"] or (self._starter and self._starter.progress()["running"]):
                return {"ok": False, "message": "安装或启动仍在进行，请等待完成后重试。"}
            if any(services.find_processes(self.layout).values()):
                return {"ok": False, "message": "请先在状态页让她下线，再执行修复。"}
            self.installer.log("开始修复：检查安装包版本并补齐缺失文件")
            try:
                report = diagnostics.repair(self.root, self.boot)
            except (OSError, ValueError, TimeoutError, subprocess.TimeoutExpired) as e:
                self.installer.log(f"修复失败：{type(e).__name__}: {e}")
                return {"ok": False, "message": str(e)}
            self.installer.log("修复结束：" + (", ".join(item["code"] for item in report["items"]) or "已知问题已排除"))
            return {"ok": True, "report": report}

    def handle_ai_diagnose(self, body: dict) -> dict:
        if body.get("consent") is not True:
            return {"ok": False, "message": "需要同意发送诊断码给 DeepSeek 才能分析。"}
        key = self.state.get_secret("deepseek")
        if not key:
            return {"ok": False, "message": "尚未保存 DeepSeek Key。"}
        report = diagnostics.inspect(self.root)
        try:
            advice = diagnostics.analyze_with_deepseek(key, report)
        except Exception as e:
            self.installer.log(f"DeepSeek 诊断请求失败：{type(e).__name__}")
            return {"ok": False, "message": "DeepSeek 分析失败，请检查网络、Key 和账户状态。"}
        self.installer.log("DeepSeek 诊断请求完成（未发送原始日志或用户数据）")
        return {"ok": True, "advice": advice}

    def handle_official_bind(self, _q) -> dict:
        if not self.root or not self.state or self.state.data.get("platform") != "official" or not self.state.data.get("keys_ok"):
            return {"ok": False, "message": "请先完成官方机器人凭证设置。"}
        with self._lock:
            owner = self.state.data.get("owner_qq")
            if owner and owner != "DAFEIYU_UNBOUND_OWNER":
                return {"ok": True, "bound": True}
            code = official_binding.create(self.root)
            session = official_binding.session_id(self.root)
            candidate = official_binding.candidate(self.root)
            receipt = official_binding.receipt(self.root)
        masked = candidate[:4] + "…" + candidate[-4:] if candidate else ""
        connection = "unknown"
        try:
            pid = self.state.reload_pids().get("astrbot")
            if pid and services.is_running(pid) and services.process_path(pid) == self.layout.python.resolve():
                connection = official_binding.connection_state(self.root, pid)
        except (OSError, ValueError):
            pass
        if _q.get("check") == ["1"]:
            self.log_error(f"绑定检查：connection={connection}, receipt={receipt}")
        problem = diagnostics.official_error(self.root) or {}
        return {"ok": True, "bound": False, "code": "绑定 " + code,
                "candidate": masked, "candidate_id": session if candidate else "", "connection": connection,
                "receipt": receipt, "problem": problem.get("message", ""), "issue_code": problem.get("code", ""),
                "message": "请使用主人 QQ 私聊机器人发送绑定指令。"}

    def handle_official_bind_confirm(self, body: dict) -> dict:
        if not self.root or not self.state or self.state.data.get("platform") != "official" or not self.state.data.get("keys_ok"):
            return {"ok": False, "message": "当前不能绑定主人。"}
        with self._lock:
            owner = self.state.data.get("owner_qq")
            if owner and owner != "DAFEIYU_UNBOUND_OWNER":
                self.installer.provide("official_bound", {"owner_qq": owner})
                return {"ok": True}
            expected = str(body.get("candidate_id") or "")
            if not expected or expected != official_binding.session_id(self.root):
                return {"ok": False, "message": "绑定请求已失效，请重新获取绑定码。"}
            candidate = official_binding.candidate(self.root)
            if not candidate or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", candidate) or candidate == "DAFEIYU_UNBOUND_OWNER":
                return {"ok": False, "message": "无法确认绑定身份，请重试。"}
            self.state.data["owner_qq"] = candidate
            try:
                self.state.save()
            except Exception:
                self.state.data["owner_qq"] = owner
                raise
            try:
                official_binding.consume(self.root, expected)
            finally:
                self.installer.provide("official_bound", {"owner_qq": candidate})
        return {"ok": True}

    def handle_ping(self, _b) -> dict:
        self.last_ping = time.monotonic()
        self.window_closed = False
        return {"ok": True}

    def handle_window_closed(self, _b) -> dict:
        self.last_ping = time.monotonic()
        self.window_closed = True
        return {"ok": True}

    GET = {"/api/info": handle_info, "/api/progress": handle_progress, "/api/login": handle_login,
           "/api/done": handle_done, "/api/status": handle_status, "/api/diagnose": handle_diagnose,
           "/api/official_bind": handle_official_bind}
    POST = {"/api/path": handle_path, "/api/check": handle_check, "/api/keys": handle_keys,
            "/api/recover": handle_recover,
            "/api/preview_voice": handle_preview_voice, "/api/profile": handle_profile,
            "/api/retry": handle_retry, "/api/start": handle_start, "/api/stop": handle_stop,
            "/api/apply": handle_apply, "/api/reconnect": handle_reconnect, "/api/ping": handle_ping, "/api/window_closed": handle_window_closed,
            "/api/refresh_qr": handle_refresh_qr, "/api/switch_qq": handle_switch_qq,
            "/api/quit": handle_quit, "/api/uninstall": handle_uninstall,
            "/api/repair": handle_repair, "/api/ai_diagnose": handle_ai_diagnose,
            "/api/official_bind/confirm": handle_official_bind_confirm}


class Handler(BaseHTTPRequestHandler):
    app: App

    def log_message(self, *a):  # 不往控制台打访问日志
        pass

    def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, code=200):
        self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def _authorized(self, query) -> bool:
        tok = self.headers.get("X-Token") or (query.get("token") or [""])[0]
        return secrets.compare_digest(tok, self.app.token)

    def _dispatch(self, table):
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        if not url.path.startswith("/api/"):
            return self._static(url.path) if table is App.GET else self._json({}, 404)
        if not self._authorized(query):
            return self._json({"message": "forbidden"}, 403)
        if url.path != "/api/window_closed":
            self.app.handle_ping({})
        if url.path == "/api/qr" and table is App.GET:
            png = services.qrcode_png(self.app.layout) if self.app.root else None
            if png and query.get("rev") and query["rev"][0] != hashlib.sha256(png).hexdigest():
                return self._json({}, 409)
            return self._send(200, png, "image/png") if png else self._json({}, 404)
        fn = table.get(url.path)
        if not fn:
            return self._json({}, 404)
        if table is App.POST:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return self._json({"message": "too large"}, 413)
            try:
                arg = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self._json({"message": "bad json"}, 400)
            if not isinstance(arg, dict):
                return self._json({"message": "bad json"}, 400)
        else:
            arg = query
        if self.app.root is None and url.path not in ("/api/info", "/api/path", "/api/ping", "/api/window_closed", "/api/progress"):
            return self._json({"ok": False, "message": "先选安装位置"}, 409)
        if self.app.recovery and url.path not in ("/api/info", "/api/ping", "/api/window_closed", "/api/progress", "/api/diagnose", "/api/recover"):
            return self._json({"ok": False, "recovery": True, "message": self.app.recovery["message"]}, 409)
        started = time.monotonic()
        try:
            self._json(fn(self.app, arg))
        except Exception as e:
            sensitive = safe_logging.secret_values(self.app.state)
            if table is App.POST:
                sensitive += [str(arg.get(key) or "") for key in SECRET_FIELDS]
                if arg.get("field") in ("deepseek", "siliconflow"):
                    sensitive.append(str(arg.get("value") or ""))
            trace = safe_logging.sanitize(traceback.format_exc(), sensitive, self.app.root)
            try:
                log = self.app.log_error(f"接口 {url.path} 出错：\n{trace}")
            except Exception:
                log = "无法写入日志，请检查安装目录权限和磁盘空间"
            # 把脱敏后的异常和日志位置直接显示出来。
            detail = safe_logging.sanitize(f"{type(e).__name__}: {e}", sensitive, self.app.root)
            self._json({"ok": False, "message": f"出错了：{detail}\n日志：{log}"}, 500)
        finally:
            elapsed = time.monotonic() - started
            if elapsed >= 3:
                try:
                    self.app.log_error(f"慢接口：{url.path}，耗时 {elapsed:.1f} 秒")
                except OSError:
                    pass

    def _static(self, path: str):
        name = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (WEB / name).resolve()
        if WEB.resolve() not in target.parents or not target.is_file():
            return self._json({}, 404)
        body = target.read_bytes()
        if name == "index.html":
            body = body.replace(b"{{TOKEN}}", self.app.token.encode())
        self._send(200, body, TYPES.get(target.suffix, "application/octet-stream"))

    def do_GET(self):
        self._dispatch(App.GET)

    def do_POST(self):
        self._dispatch(App.POST)


def make_server(app: App, port: int) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"app": app})
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    httpd.daemon_threads = True
    return httpd
