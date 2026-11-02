"""只绑 127.0.0.1 的本机服务：给向导页面提供接口。每个 /api 请求都要带启动时生成的 token。"""
import json
import secrets
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import checks
import installer
import personalize
import services
from store import State, app_home, last_install, remember_install

WEB = Path(__file__).resolve().parent / "web"
MAX_BODY = 1024 * 1024
TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".js": "text/javascript; charset=utf-8", ".png": "image/png", ".ico": "image/x-icon",
         ".svg": "image/svg+xml"}
SECRET_FIELDS = {"deepseek": "deepseek", "siliconflow": "siliconflow", "app_id": "doubao_app_id",
                 "token": "doubao_token"}


class App:
    def __init__(self, mode: str, root: Path | None = None, boot: Path | None = None, home: Path | None = None,
                 checks_mod=checks, steps_factory=installer.default_steps):
        self.mode = mode  # install | status
        self.token = secrets.token_urlsafe(16)
        self.boot = Path(boot) if boot else Path(__file__).resolve().parent.parent
        self.home = home
        self.checks = checks_mod
        self.steps_factory = steps_factory
        self.last_ping = time.monotonic()
        self.quitting = False  # 用户点了「退出」或「卸载」：main 看到就收摊
        self._checked: set[tuple] = set()
        self._lock = threading.Lock()
        self.root = self.state = self.installer = None
        self._web = None
        self._starter = None
        if root:
            self._open(Path(root))

    # ---------- 共用 ----------
    def _open(self, root: Path) -> dict:
        self.root = root
        # 换新版一键包重装时，桌面快捷方式跑的 app\main.py 得换成新的，否则界面上看不到新功能
        installer.sync_app(root, self.boot)
        personalize.fix_plugin_imports(services.Layout(root).astrbot_data)
        self.state = State.load(root)
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
        h = app_home(self.home)
        h.mkdir(parents=True, exist_ok=True)
        with open(h / "wizard.log", "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
        return h / "wizard.log"

    @property
    def layout(self):
        return services.Layout(self.root)

    def pack_dir(self) -> Path:
        for d in ((self.root / "pack") if self.root else None, self.boot / "pack",
                  self.boot / "vendor" / "dafeiyu-config-pack"):  # 最后一个是源码目录里直接运行时
            if d and d.exists():
                return d
        raise FileNotFoundError("找不到配置包")

    def napcat(self):
        if self._web is None and self.state and self.state.data.get("ports"):
            self._web = services.NapCatWeb(f"http://127.0.0.1:{self.state.data['ports']['napcat_webui']}",
                                           self.state.get_secret("napcat_webui_token"))
        return self._web

    def login_status(self) -> str:
        web = self.napcat()
        return web.status() if web else "starting"

    # ---------- 接口 ----------
    def handle_info(self, _q) -> dict:
        profile = (self.state.data.get("profile") if self.state else None) or \
            personalize.default_profile(self.pack_dir())
        return {"mode": self.mode, "default_path": last_install(self.home) or self.checks.default_install_path(),
                "root": str(self.root) if self.root else None, "profile": profile,
                "owner_qq": self.state.data.get("owner_qq", "") if self.state else "",
                # 改设置时这几项留空就是不改，界面上要能提示「已保存」
                "saved_keys": sorted(f for f, name in SECRET_FIELDS.items()
                                     if self.state and self.state.data["secrets"].get(name)) if self.state else [],
                "voice_presets": [{"name": n, "voice": v} for n, v in personalize.VOICE_PRESETS]}

    def handle_path(self, body: dict) -> dict:
        path = str(body.get("path") or "").strip()
        if err := self.checks.check_install_path(path):
            return {"ok": False, "message": err}
        with self._lock:
            if self.root and Path(path) == self.root:
                resume = {"keys": bool(self.state.data.get("keys_ok")), "profile": bool(self.state.data.get("profile"))}
            else:
                resume = self._open(Path(path))
            remember_install(Path(path), self.home)
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
        ok, msg = self._check(str(body.get("field")), body)
        return {"ok": ok, "message": msg}

    def handle_keys(self, body: dict) -> dict:
        errors = {}
        qq = str(body.get("owner_qq") or "").strip()
        if not (qq.isdigit() and 5 <= len(qq) <= 12):
            errors["owner_qq"] = "QQ 号只能是 5 到 12 位数字"
        # 改设置时只改一项：留空的用已经存着的那个（已经验过，不再跑一次网络检查）
        vals = {f: str(body.get(f) or "").strip() or self.state.get_secret(name)
                for f, name in SECRET_FIELDS.items()}
        for field in ("deepseek", "siliconflow"):
            ok, msg = self._check(field, {"value": vals[field]})
            if not ok:
                errors[field] = msg
        ok, msg = self._check("doubao", {"app_id": vals["app_id"], "token": vals["token"]})
        if not ok:
            errors["doubao"] = msg
        if errors:
            return {"ok": False, "errors": errors}
        for field, name in SECRET_FIELDS.items():
            self.state.set_secret(name, vals[field])
        self.state.data.update(owner_qq=qq, keys_ok=True)
        self.state.save()
        self.installer.provide("keys", {"owner_qq": qq})
        return {"ok": True}

    def handle_preview_voice(self, body: dict) -> dict:
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
        self.installer.retry()
        return {"ok": True}

    def handle_login(self, _q) -> dict:
        status = self.login_status()
        self.remember_qq(status)
        web = self.napcat()
        if status == "error":
            try:
                web.refresh_qr()
            except Exception:
                pass
        return {"status": status, "error": web.last_error if web else ""}

    def handle_refresh_qr(self, _b) -> dict:
        """手动换一张码。NapCat 报「已登录」时只换码没用，得连它一起重启。"""
        web = self.napcat()
        if web and "已登录" in web.last_error:
            # -q 快速登录会卡住这个账号，清掉记下的 QQ 号，重启后回到扫码
            self.state.data.pop("qq", None)
            self.state.save()
            services.stop(self.state, "napcat")
            self._web = None
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
            self.state.save()

    def handle_status(self, _q) -> dict:
        pids = self.state.reload_pids()  # 别用启动时那份：另一个窗口可能刚起或停了服务
        starting = bool(self._starter and self._starter.progress()["running"])
        err = next((s["message"] for s in (self._starter.progress()["steps"] if self._starter else [])
                    if s["status"] == "error"), "")
        login = self.login_status()
        self.remember_qq(login)
        return {"astrbot": services.is_running(pids.get("astrbot")), "napcat": services.is_running(pids.get("napcat")),
                "login": login, "starting": starting, "error": err, "qq": self.state.data.get("qq") or ""}

    def handle_start(self, _b) -> dict:
        with self._lock:
            if not (self._starter and self._starter.progress()["running"]):
                self._starter = installer.Installer(self.root, self.state, resume=False, boot=self.boot, steps=[
                    installer.Step("astrbot", "启动 AstrBot", installer.ensure_astrbot),
                    installer.Step("napcat", "启动 NapCat", installer.ensure_napcat)])
                self._starter.start()
        return {"ok": True}

    def handle_stop(self, _b) -> dict:
        services.stop_all(self.state)
        return {"ok": True}

    def handle_switch_qq(self, _b) -> dict:
        """换小号 / 退出小号：忘掉当前登录，回到扫码。"""
        services.clear_qq_login(self.layout, self.state)
        self._web = None
        return {"ok": True}

    def handle_uninstall(self, _b) -> dict:
        """彻底卸载：停服务、删快捷方式，退出后由清理脚本删掉安装目录。"""
        import uninstall
        uninstall.uninstall(self.root, self.state, home=self.home)
        self.quitting = True  # 清理脚本在等我们退出，之后才能删掉安装目录
        return {"ok": True}

    def handle_quit(self, _b) -> dict:
        """一键停掉所有服务并退出（相当于关掉 app）。"""
        services.stop_all(self.state)
        self.quitting = True
        return {"ok": True}

    def handle_apply(self, _b) -> dict:
        """改完设置：停掉、重写配置、重新导入、重新登录。"""
        services.stop_all(self.state)
        self.installer.redo(["configs", "astrbot", "napcat", "import", "login"])
        return {"ok": True}

    def handle_ping(self, _b) -> dict:
        self.last_ping = time.monotonic()
        return {"ok": True}

    GET = {"/api/info": handle_info, "/api/progress": handle_progress, "/api/login": handle_login,
           "/api/done": handle_done, "/api/status": handle_status}
    POST = {"/api/path": handle_path, "/api/check": handle_check, "/api/keys": handle_keys,
            "/api/preview_voice": handle_preview_voice, "/api/profile": handle_profile,
            "/api/retry": handle_retry, "/api/start": handle_start, "/api/stop": handle_stop,
            "/api/apply": handle_apply, "/api/ping": handle_ping,
            "/api/refresh_qr": handle_refresh_qr, "/api/switch_qq": handle_switch_qq,
            "/api/quit": handle_quit, "/api/uninstall": handle_uninstall}


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
        if url.path == "/api/qr" and table is App.GET:
            png = services.qrcode_png(self.app.layout) if self.app.root else None
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
        if self.app.root is None and url.path not in ("/api/info", "/api/path", "/api/ping", "/api/progress"):
            return self._json({"ok": False, "message": "先选安装位置"}, 409)
        try:
            self._json(fn(self.app, arg))
        except Exception as e:
            log = self.app.log_error(f"接口 {url.path} 出错：\n{traceback.format_exc()}")
            # 测试阶段要能自己定位：把异常原文和日志路径直接显示出来
            self._json({"ok": False, "message": f"出错了：{type(e).__name__}: {e}\n日志：{log}"}, 500)

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
