"""带断点的安装流水线：每步做完记进 state.json，中断后再打开从没做完的那步接着跑。"""
import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import configs
import personalize
import services
import safe_logging
from store import State, file_lock, redact

BUSY_HINT = "出了点问题，点「检测与修复」或「重试」；分享日志前请检查其中是否包含个人信息"


class InstallError(Exception):
    """预期内的失败，message 直接给用户看。"""


class Cancelled(Exception):
    """用户主动停止，不作为安装失败。"""


@dataclass
class Step:
    key: str
    title: str
    run: Callable[["Ctx"], None]


class Ctx:
    """传给每一步的东西：安装目录、状态、等用户填的数据。"""

    def __init__(self, inst: "Installer"):
        self._inst = inst
        self.root = inst.root
        self.state = inst.state
        self.layout = services.Layout(inst.root)
        self.boot = inst.boot

    def wait_for(self, name: str) -> dict:
        return self._inst._wait_for(name)

    def log(self, msg: str) -> None:
        self._inst.log(msg)

    def check_cancelled(self) -> None:
        if self._inst._cancelled.is_set() or State.load(self.root).data.get("services_stopped"):
            raise Cancelled()


class Installer:
    def __init__(self, root: Path, state: State, steps: list[Step] | None = None, boot: Path | None = None,
                 resume: bool = True):
        """resume=False：不看 state 里的完成记录，每步都跑（状态页「让她上线」用）。"""
        self.root = Path(root)
        self.state = state
        self.steps = steps if steps is not None else default_steps()
        self.boot = Path(boot) if boot else Path(__file__).resolve().parent.parent
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._provided: dict[str, dict] = {}
        self._status = {s.key: ("done" if resume and state.is_done(s.key) else "todo", "") for s in self.steps}
        self._thread: threading.Thread | None = None
        self._waiting: str | None = None
        self._cancelled = threading.Event()

    # ---------- 给 server 用 ----------
    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._cancelled.clear()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def cancel(self, timeout: float = 0) -> bool:
        self._cancelled.set()
        with self._cond:
            self._cond.notify_all()
        if self._thread and timeout:
            self._thread.join(timeout)
        return not (self._thread and self._thread.is_alive())

    def retry(self) -> None:
        with self._lock:
            for k, (st, _) in self._status.items():
                if st == "error":
                    self._status[k] = ("todo", "")
        self.start()

    def provide(self, name: str, value: dict) -> None:
        with self._cond:
            self._provided[name] = value
            self._cond.notify_all()

    def provided(self, name: str) -> dict | None:
        with self._lock:
            return self._provided.get(name)

    def progress(self) -> dict:
        with self._lock:
            steps = [{"key": s.key, "title": s.title, "status": self._status[s.key][0],
                      "message": self._status[s.key][1]} for s in self.steps]
            running = bool(self._thread and self._thread.is_alive())
            return {"steps": steps, "running": running, "waiting": self._waiting,
                    "finished": all(s["status"] == "done" for s in steps)}

    def log(self, msg: str) -> None:
        safe_logging.append_log(self.root / "logs" / "installer.log", f"[controller {os.getpid()}] {msg}",
                                secrets=safe_logging.secret_values(self.state), root=self.root)

    def redo(self, keys: list[str]) -> None:
        """改设置后重跑指定的几步。"""
        with self._lock:
            for k in keys:
                self._status[k] = ("todo", "")
                if k in self.state.data["done"]:
                    self.state.data["done"].remove(k)
        self.state.save()
        self.start()

    # ---------- 内部 ----------
    def _wait_for(self, name: str) -> dict:
        with self._cond:
            self._waiting = name
            try:
                while name not in self._provided:
                    Ctx(self).check_cancelled()
                    self._cond.wait(1)
                Ctx(self).check_cancelled()
            finally:
                self._waiting = None
            return self._provided[name]

    def _set(self, key: str, status: str, message: str = "") -> None:
        with self._lock:
            self._status[key] = (status, message)

    def _run(self) -> None:
        ctx = Ctx(self)
        for step in self.steps:
            if self._status[step.key][0] == "done":
                continue
            self._set(step.key, "doing")
            self.log(f"开始：{step.title}（步骤 {step.key}）")
            started = time.monotonic()
            try:
                ctx.check_cancelled()
                step.run(ctx)
                ctx.check_cancelled()
                self.state.mark_done(step.key)
            except Cancelled:
                self._set(step.key, "todo")
                self.log(f"已取消：{step.title}")
                return
            except InstallError as e:
                self.log(f"失败：{step.title}，耗时 {time.monotonic() - started:.1f} 秒：{e}")
                self._set(step.key, "error", redact(str(e), safe_logging.secret_values(self.state)))
                return
            except Exception as e:
                self.log(f"出错：{step.title}，耗时 {time.monotonic() - started:.1f} 秒\n{traceback.format_exc()}")
                # 意料之外的异常：原文也给用户看，否则只能看到兜底文案，没法定位
                detail = redact(f"{type(e).__name__}: {e}", safe_logging.secret_values(self.state))
                self._set(step.key, "error", f"{BUSY_HINT}\n{detail}\n日志：{self.root / 'logs' / 'installer.log'}")
                return
            self._set(step.key, "done")
            self.log(f"完成：{step.title}，耗时 {time.monotonic() - started:.1f} 秒")


# ================= 真正的安装步骤 =================

def _urls(state: State) -> dict:
    p = state.data["ports"]
    return {"dashboard": f"http://127.0.0.1:{p['dashboard']}",
            "napcat": f"http://127.0.0.1:{p['napcat_webui']}"}


def extracted_ok(root: Path) -> bool:
    """解压产物还在不在。只信 state 里的 done 标记不够：目录被删一半或被杀毒隔离后
    会跳过解压，后面每步都对着空目录失败，重试也出不来。"""
    layout = services.Layout(root)
    return all(p.exists() for p in (layout.python, layout.astrbot / "main.py",
                                    layout.pack / personalize.PERSONA, layout.napcat / "node.exe"))


def _tree_hash(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(x for x in Path(root).rglob("*") if x.is_file() and "__pycache__" not in x.parts):
        h.update(p.relative_to(root).as_posix().encode("utf-8"))
        h.update(p.read_bytes())
    return h.hexdigest()


def sync_app(root: Path, boot: Path) -> bool:
    """把新包里的界面代码同步到安装目录。

    桌面快捷方式跑的是安装目录里的 app\\main.py，而解压那步标着「已完成」就不会再动它 ——
    不主动同步的话，换新版一键包重装之后，界面上还是旧的，新加的功能一个都看不到。
    只同步 app 这一份（约 1 MB），其他东西不动，key 和登录状态都留着。
    """
    root = Path(root).resolve()
    src, dst = Path(boot) / "app", root / "app"
    work = root / ".tmp"
    staged, previous = work / "app.next", work / "app.previous"

    def check_tree(path: Path) -> None:
        pending = [path]
        while pending:
            item = pending.pop()
            if item.is_symlink() or item.is_junction():
                raise InstallError(f"界面目录包含链接，未同步：{item}")
            if item.is_dir():
                pending.extend(item.iterdir())

    try:
        if any(p.is_symlink() or p.is_junction() for p in (src, dst, work)):
            raise InstallError("界面目录或临时目录是链接，未同步；请检查安装目录")
        if not src.is_dir() or src.resolve() == dst.resolve():
            return False
        if not dst.is_dir() and not previous.is_dir():
            return False
        work.mkdir(parents=True, exist_ok=True)
        lock = work / "app-sync.lock"
        if lock.is_symlink() or lock.is_junction():
            raise InstallError("界面同步锁文件是链接，未同步")
        with file_lock(lock):
            check_tree(previous)
            if not dst.exists() and previous.exists():
                previous.replace(dst)  # 上次在两次重命名之间退出时，先恢复可运行的旧版。
            check_tree(src)
            check_tree(dst)
            digest = _tree_hash(src)
            if digest == _tree_hash(dst):
                return False
            check_tree(staged)
            if staged.exists():
                shutil.rmtree(staged)
            try:
                shutil.copytree(src, staged, ignore=shutil.ignore_patterns("__pycache__"))
                check_tree(staged)
                if _tree_hash(staged) != digest:
                    raise InstallError("新界面代码复制不完整，已保留旧版，请重试")
                if previous.exists():
                    shutil.rmtree(previous)
                dst.replace(previous)
                try:
                    staged.replace(dst)
                except OSError:
                    try:
                        previous.replace(dst)
                    except OSError as rollback:
                        raise InstallError(f"界面更新失败，旧版保留在 {previous}；请关闭占用后重新打开安装包恢复") from rollback
                    raise
                return True
            finally:
                if staged.exists():
                    check_tree(staged)
                    shutil.rmtree(staged)
    except OSError as exc:
        raise InstallError(f"界面同步失败，已保留旧版，请关闭占用后重试：{exc}") from exc


def step_extract(ctx: Ctx) -> None:
    payload = ctx.boot / "payload.7z"
    seven = ctx.boot / "7zr.exe"
    if not payload.exists() or not seven.exists():
        raise InstallError("安装包不完整，重新下载一键包再试")
    if ctx.state.is_done("configs") or (ctx.layout.astrbot_data / "cmd_config.json").exists():
        raise InstallError("检测到已有配置，不能覆盖解压。请使用「检测与修复」保留现有数据。")
    ctx.root.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([str(seven), "x", str(payload), f"-o{ctx.root}", "-y", "-bso0", "-bsp0"],
                       capture_output=True, text=True, errors="replace", creationflags=services.CREATE_NO_WINDOW)
    if r.returncode != 0:
        ctx.log(f"7zr 输出：{r.stdout}\n{r.stderr}")
        raise InstallError(f"解压失败（退出码 {r.returncode}）。请检查磁盘空间、目录权限和安装包完整性；详情见 installer.log。")


def step_ports(ctx: Ctx) -> None:
    ctx.state.data["ports"] = configs.pick_ports()
    for k, v in configs.new_secrets().items():
        ctx.state.set_secret(k, v)
    ctx.state.save()


def step_configs(ctx: Ctx) -> None:
    keys = ctx.wait_for("keys")
    st = ctx.state
    sec = {k: st.get_secret(k) for k in ("dashboard_password", "onebot_token", "napcat_webui_token", "jwt_secret")}
    platform = st.data.get("platform", "napcat")
    configs.write_astrbot_config(ctx.layout.astrbot_data, ports=st.data["ports"], secrets=sec,
                                 deepseek_key=st.get_secret("deepseek"), platform=platform,
                                 official_appid=st.get_secret("official_appid") if platform == "official" else "",
                                 official_secret=st.get_secret("official_secret") if platform == "official" else "",
                                 owner_openid=keys.get("owner_qq") or st.data.get("owner_qq", ""))
    if platform == "napcat":
        configs.write_napcat_config(ctx.layout.napcat_work, ports=st.data["ports"], secrets=sec)
    st.data["owner_qq"] = keys.get("owner_qq") or st.data.get("owner_qq")
    st.save()


def ensure_astrbot(ctx: Ctx) -> None:
    ctx.check_cancelled()
    if ctx.state.data.get("platform") == "official":
        import official_binding
        official_binding.install(ctx.root, before_update=lambda: services.stop(ctx.state, 'astrbot', layout=ctx.layout))
    services.start_astrbot(ctx.layout, ctx.state)
    if not services.wait_http(_urls(ctx.state)["dashboard"], 300,
                              alive=lambda: ctx.check_cancelled() or services.is_running(ctx.state.data.get("pids", {}).get("astrbot"))):
        import diagnostics
        report = diagnostics.inspect(ctx.root)
        codes = ", ".join(item["code"] for item in report["items"]) or "未识别"
        ctx.log(f"AstrBot 启动失败：进程退出或服务未就绪；诊断码：{codes}；日志：{ctx.layout.logs / 'astrbot.log'}")
        raise InstallError(f"AstrBot 没有启动成功（诊断码：{codes}）。请点「检测与修复」。")


def wait_official(ctx: Ctx, timeout=90) -> None:
    import official_binding
    import diagnostics
    deadline = time.monotonic() + timeout
    while True:
        ctx.check_cancelled()
        if issue := diagnostics.official_error(ctx.root):
            ctx.log(f"QQ 连接检查失败：{issue['code']}")
            raise InstallError(issue["message"])
        pid = ctx.state.reload_pids().get("astrbot")
        if official_binding.connection_state(ctx.root, pid) == "ready":
            ctx.log("QQ WebSocket 已连接；主人资料已保存")
            return
        if time.monotonic() >= deadline:
            raise InstallError("QQ 尚未连接。请检查网络、AppID / AppSecret，以及 QQ 平台的 WebSocket 设置，再重试。已保存的资料会保留。")
        ctx._inst._cancelled.wait(1)


def ensure_napcat(ctx: Ctx) -> None:
    ctx.check_cancelled()
    if ctx.state.data.get("platform") == "official":
        return
    services.start_napcat(ctx.layout, ctx.state, ctx.state.data.get("qq"))
    if not services.wait_http(_urls(ctx.state)["napcat"], 120,
                              alive=lambda: ctx.check_cancelled() or services.is_running(ctx.state.data.get("pids", {}).get("napcat"))):
        ctx.log(f"NapCat 启动失败：进程退出或服务未就绪；日志：{ctx.layout.logs / 'napcat.log'}")
        raise InstallError("NapCat 没有启动成功，请点「检测与修复」并查看 NapCat 日志。")


def step_import(ctx: Ctx, *, bind_only=False) -> None:
    profile = ctx.wait_for("profile")
    st, layout = ctx.state, ctx.layout
    try:
        if not bind_only:
            personalize.apply_profile(layout.pack, profile)
    except ValueError as e:
        raise InstallError(str(e))
    ensure_astrbot(ctx)
    work = ctx.root / ".tmp" / "import"
    secrets_dir = work / "s"
    answers, result = work / "answers.json", work / "result.json"
    work.mkdir(parents=True, exist_ok=True)
    try:
        memory = st.data.get("memory", "2" if st.data["secrets"].get("siliconflow") else "0")
        voice = st.data.get("voice", "3" if st.data["secrets"].get("doubao_token") else "0")
        personalize.write_answers(answers, base_url=_urls(st)["dashboard"], username=configs.DASHBOARD_USER,
                                  password=st.get_secret("dashboard_password"), owner_qq=st.data.get("owner_qq") or "DAFEIYU_UNBOUND_OWNER",
                                  secrets_dir=secrets_dir, sf_key=st.get_secret("siliconflow") if memory != "0" else "",
                                  app_id=st.get_secret("doubao_app_id") if voice != "0" else "",
                                  token=st.get_secret("doubao_token") if voice != "0" else "",
                                  memory=memory, voice=voice,
                                  platform=st.data.get("platform", "napcat"))
        result.unlink(missing_ok=True)
        script = layout.pack / "tools" / "import_dafeiyu.py"
        if bind_only:
            # 续装旧目录时使用新版工具；人设和插件配置从正在运行的后台读取。
            candidates = (ctx.boot / "pack", ctx.boot / "vendor" / "dafeiyu-config-pack")
            script = next((p / "tools" / "import_dafeiyu.py" for p in candidates
                           if (p / "tools" / "import_dafeiyu.py").is_file()), script)
            if "def bind_owner(" not in script.read_text(encoding="utf8"):
                raise InstallError("绑定工具版本过旧，请重新打开最新一键包继续。")
        command = [str(layout.python), "-X", "utf8", str(script), "--answers", str(answers), "--result-json", str(result)]
        if bind_only:
            command.append("--bind-only")
        proc = services.logged_process("import", command,
                                       layout.astrbot, {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}, layout, st)
        deadline = time.monotonic() + 3600
        try:
            while True:
                ctx.check_cancelled()
                try:
                    returncode = proc.wait(timeout=1)
                    break
                except subprocess.TimeoutExpired:
                    if time.monotonic() >= deadline:
                        raise InstallError("导入超时，请查看 logs/import.log 后重试。")
        except (Cancelled, InstallError):
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True,
                           timeout=15, creationflags=services.CREATE_NO_WINDOW)
            proc.wait(timeout=15)
            raise
        ctx.log(f"导入工具退出：返回码={returncode}")
        if returncode:
            raise InstallError(f"导入工具异常退出（返回码 {returncode}），请查看 logs/import.log。")
        data = json.loads(result.read_text(encoding="utf-8")) if result.exists() else {"fatal": "没有结果"}
    finally:
        shutil.rmtree(secrets_dir, ignore_errors=True)
        answers.unlink(missing_ok=True)
    if data.get("fatal"):
        ctx.log(f"导入工具失败：{data['fatal']}")
        raise InstallError(f"配置大肥鱼时出错了，点重试。原因：{data['fatal']}\n"
                           f"详细日志：{layout.logs / 'import.log'}")
    bad = [x["name"] for x in data.get("results", []) if not x["ok"]]
    if bad:
        raise InstallError("这几项没装好：" + "、".join(bad) + "。多半是网络抖了一下，点重试（装好的会跳过）")


def step_shortcut(ctx: Ctx) -> None:
    import shortcut
    shortcut.create_shortcut(ctx.layout.pythonw, f'"{ctx.layout.app / "main.py"}" --status --root "{ctx.root}"',
                             "启动大肥鱼", ctx.layout.app / "web" / "img" / "icon.ico")


def step_login(ctx: Ctx) -> None:
    st = ctx.state
    if st.data.get("platform") == "official":
        if st.data.get("owner_qq") in (None, "", "DAFEIYU_UNBOUND_OWNER"):
            ensure_astrbot(ctx)
            ctx.wait_for("official_bound")
        owner = st.data.get("owner_qq")
        if not owner or owner == "DAFEIYU_UNBOUND_OWNER":
            raise InstallError("主人绑定尚未完成，请重试。")
        ctx._inst._set("login", "doing", "正在记住主人，保留现有 QQ 连接…")
        step_import(ctx, bind_only=True)
        ctx._inst._set("login", "doing", "主人已记住，正在确认 QQ 连接…")
        wait_official(ctx)
        return
    web = services.NapCatWeb(_urls(st)["napcat"], st.get_secret("napcat_webui_token"))
    while True:
        ensure_napcat(ctx)  # 换码时会把 NapCat 停掉，这里负责再拉起来
        if web.status() == "online":
            break
        ctx._inst._cancelled.wait(2)
    st.data["qq"] = web.login_uin() or st.data.get("qq")
    st.save()


def default_steps() -> list[Step]:
    return [
        Step("extract", "解压运行环境和程序", step_extract),
        Step("ports", "准备端口和密码", step_ports),
        Step("configs", "写入你的 key 和连接设置", step_configs),
        Step("astrbot", "启动 AstrBot（她的大脑）", ensure_astrbot),
        Step("napcat", "启动 NapCat（登 QQ 用）", ensure_napcat),
        Step("import", "装上人设、插件、表情包、语音、记忆", step_import),
        Step("shortcut", "在桌面放「启动大肥鱼」", step_shortcut),
        Step("login", "连接 QQ 并确认主人", step_login),
    ]
