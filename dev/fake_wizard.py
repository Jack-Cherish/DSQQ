"""开发用：不装 AstrBot / NapCat，用假步骤把 6 页走一遍看界面。不打进包里。

用法：.venv\\Scripts\\python dev\\fake_wizard.py [--status]   然后打开它打印的地址。
key 里带 bad 算错；登录在进入扫码页 8 秒后变成功。
"""
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

import server  # noqa: E402
import services  # noqa: E402
from installer import Step  # noqa: E402

QR = (ROOT / "app" / "web" / "img" / "favicon.png").read_bytes()


class FakeChecks:
    @staticmethod
    def check_install_path(p):
        return None if p.isascii() and " " not in p and p[1:3] == ":\\" else "路径里不能有中文和空格，换一个，比如 D:\\DaFeiYu"

    default_install_path = staticmethod(lambda: "D:\\DaFeiYu")

    @staticmethod
    def check_deepseek(k):
        time.sleep(0.6)
        return ("bad" not in k, "可以用，余额 9.50 元" if "bad" not in k else "key 不对，回 DeepSeek 开放平台重新复制一下")

    @staticmethod
    def check_siliconflow(k):
        time.sleep(0.6)
        return ("bad" not in k, "可以用" if "bad" not in k else "key 不对")

    @staticmethod
    def check_doubao(a, t, voice=None, text=None):
        time.sleep(1.5)
        if "bad" in a + t:
            return False, "还没领试用：到豆包语音旧版控制台「API 服务中心」点「试用」", ""
        return True, "可以用", ""


def slow(seconds, fail_once=False):
    state = {"failed": False}

    def run(ctx):
        time.sleep(seconds)
        if fail_once and not state["failed"]:
            state["failed"] = True
            from installer import InstallError
            raise InstallError("这几项没装好：表情包 deepseek-fanart-meme-pack。多半是网络抖了一下，点重试")
    return run


def steps():
    def wait(name):
        return lambda ctx: (ctx.wait_for(name), time.sleep(1))

    def login(ctx):
        LOGIN["since"] = time.monotonic()
        while time.monotonic() - LOGIN["since"] < 8:
            time.sleep(0.5)

    return [Step("extract", "解压运行环境和程序", slow(2)), Step("ports", "准备端口和密码", slow(0.5)),
            Step("configs", "写入你的 key 和连接设置", wait("keys")), Step("astrbot", "启动 AstrBot（她的大脑）", slow(2)),
            Step("napcat", "启动 NapCat（登 QQ 用）", slow(1.5)),
            Step("import", "装上人设、插件、表情包、语音、记忆", lambda c: (c.wait_for("profile"), slow(3, True)(c))),
            Step("shortcut", "在桌面放「启动大肥鱼」", slow(0.5)), Step("login", "等你扫码登录小号", login)]


LOGIN = {"since": None}


def fake_login(self):
    if LOGIN["since"] is None:
        return "starting"
    dt = time.monotonic() - LOGIN["since"]
    return "need_scan" if dt < 5 else "scanned" if dt < 8 else "online"


def main():
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="fakewiz"))
    server.App.login_status = fake_login
    services.qrcode_png = lambda layout: QR
    mode = "status" if "--status" in sys.argv else "install"
    root = None
    if mode == "status":
        root = tmp / "DaFeiYu"
        root.mkdir()
        (root / "state.json").write_text('{"done":[],"secrets":{},"ports":{"dashboard":6185,"onebot":6199,"napcat_webui":6099}}')
        LOGIN["since"] = time.monotonic()
    app = server.App(mode, root=root, boot=ROOT, home=tmp / "home", checks_mod=FakeChecks, steps_factory=steps)
    httpd = server.make_server(app, 8765)
    print(f"http://127.0.0.1:8765/?token={app.token}", flush=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == "__main__":
    main()
