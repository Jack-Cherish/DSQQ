"""带断点的安装流水线：每步做完记进 state.json，中断后再打开从没做完的那步接着跑。"""
import hashlib
import json
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
from store import State, redact

BUSY_HINT = "出了点问题，点「重试」；还不行，把安装目录里的 logs 文件夹发给 UP 主"


class InstallError(Exception):
    """预期内的失败，message 直接给用户看。"""


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
        # 解压产物没了（目录被删一半、杀毒隔离）就得从头来，否则后面每步都对着空目录失败
        if resume and state.is_done("extract") and not extracted_ok(self.root):
            state.data["done"] = []
            state.save()
        self._status = {s.key: ("done" if resume and state.is_done(s.key) else "todo", "") for s in self.steps}
        self._thread: threading.Thread | None = None
        self._waiting: str | None = None

    # ---------- 给 server 用 ----------
    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

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
        logs = self.root / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        with open(logs / "installer.log", "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + redact(msg, self.state.secret_values()) + "\n")

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
            while name not in self._provided:
                self._cond.wait()
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
            self.log(f"开始：{step.title}")
            try:
                step.run(ctx)
            except InstallError as e:
                self.log(f"失败：{step.title}：{e}")
                self._set(step.key, "error", redact(str(e), self.state.secret_values()))
                return
            except Exception as e:
                self.log(f"出错：{step.title}\n{traceback.format_exc()}")
                # 意料之外的异常：原文也给用户看，否则只能看到兜底文案，没法定位
                detail = redact(f"{type(e).__name__}: {e}", self.state.secret_values())
                self._set(step.key, "error", f"{BUSY_HINT}\n{detail}\n日志：{self.root / 'logs' / 'installer.log'}")
                return
            self.state.mark_done(step.key)
            self._set(step.key, "done")
            self.log(f"完成：{step.title}")


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
    src, dst = Path(boot) / "app", Path(root) / "app"
    if not src.is_dir() or not dst.is_dir() or src.resolve() == dst.resolve():
        return False
    # 状态页正开着时文件可能被占，同步失败不该让向导起不来
    try:
        if _tree_hash(src) == _tree_hash(dst):
            return False
        shutil.rmtree(dst)
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__"))
        return True
    except OSError:
        return False


def step_extract(ctx: Ctx) -> None:
    payload = ctx.boot / "payload.7z"
    seven = ctx.boot / "7zr.exe"
    if not payload.exists() or not seven.exists():
        raise InstallError("安装包不完整，重新下载一键包再试")
    ctx.root.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([str(seven), "x", str(payload), f"-o{ctx.root}", "-y", "-bso0", "-bsp0"],
                       capture_output=True, text=True, errors="replace", creationflags=services.CREATE_NO_WINDOW)
    if r.returncode != 0:
        ctx.log(f"7zr 输出：{r.stdout}\n{r.stderr}")
        raise InstallError("解压失败：多半是磁盘满了，或者杀毒软件拦了文件。腾出空间、给安装目录添加信任后点重试")


def step_ports(ctx: Ctx) -> None:
    ctx.state.data["ports"] = configs.pick_ports()
    for k, v in configs.new_secrets().items():
        ctx.state.set_secret(k, v)
    ctx.state.save()


def step_configs(ctx: Ctx) -> None:
    keys = ctx.wait_for("keys")
    st = ctx.state
    sec = {k: st.get_secret(k) for k in ("dashboard_password", "onebot_token", "napcat_webui_token", "jwt_secret")}
    configs.write_astrbot_config(ctx.layout.astrbot_data, ports=st.data["ports"], secrets=sec,
                                 deepseek_key=st.get_secret("deepseek"))
    configs.write_napcat_config(ctx.layout.napcat_work, ports=st.data["ports"], secrets=sec)
    st.data["owner_qq"] = keys.get("owner_qq") or st.data.get("owner_qq")
    st.save()


def ensure_astrbot(ctx: Ctx) -> None:
    if not services.is_running(ctx.state.data.get("pids", {}).get("astrbot")):
        services.start_astrbot(ctx.layout, ctx.state)
    if not services.wait_http(_urls(ctx.state)["dashboard"], 300):
        raise InstallError("AstrBot 五分钟都没启动起来。点重试；还不行，把 logs 文件夹发给 UP 主")


def ensure_napcat(ctx: Ctx) -> None:
    if not services.is_running(ctx.state.data.get("pids", {}).get("napcat")):
        services.start_napcat(ctx.layout, ctx.state, ctx.state.data.get("qq"))
    if not services.wait_http(_urls(ctx.state)["napcat"], 120):
        raise InstallError("NapCat 没启动起来，可能被杀毒软件拦了。给安装目录添加信任后点重试")


def step_import(ctx: Ctx) -> None:
    profile = ctx.wait_for("profile")
    st, layout = ctx.state, ctx.layout
    try:
        personalize.apply_profile(layout.pack, profile)
    except ValueError as e:
        raise InstallError(str(e))
    ensure_astrbot(ctx)
    work = ctx.root / ".import"
    secrets_dir = work / "s"
    answers, result = work / "answers.json", work / "result.json"
    work.mkdir(exist_ok=True)
    try:
        personalize.write_answers(answers, base_url=_urls(st)["dashboard"], username=configs.DASHBOARD_USER,
                                  password=st.get_secret("dashboard_password"), owner_qq=st.data["owner_qq"],
                                  secrets_dir=secrets_dir, sf_key=st.get_secret("siliconflow"),
                                  app_id=st.get_secret("doubao_app_id"), token=st.get_secret("doubao_token"))
        result.unlink(missing_ok=True)
        r = subprocess.run([str(layout.python), "-X", "utf8", str(layout.pack / "tools" / "import_dafeiyu.py"),
                            "--answers", str(answers), "--result-json", str(result)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3600,
                           creationflags=services.CREATE_NO_WINDOW)
        layout.logs.mkdir(exist_ok=True)
        (layout.logs / "import.log").write_text(redact(r.stdout + "\n" + r.stderr, st.secret_values()), encoding="utf-8")
        data = json.loads(result.read_text(encoding="utf-8")) if result.exists() else {"fatal": "没有结果"}
    finally:
        shutil.rmtree(secrets_dir, ignore_errors=True)
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
    web = services.NapCatWeb(_urls(st)["napcat"], st.get_secret("napcat_webui_token"))
    while True:
        ensure_napcat(ctx)  # 换码时会把 NapCat 停掉，这里负责再拉起来
        if web.status() == "online":
            break
        time.sleep(2)
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
        Step("login", "等你扫码登录小号", step_login),
    ]
