"""本机安装诊断和受限修复；不读取聊天记录或运行模型生成的命令。"""
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import services
from store import read_state


def _pth(root: Path) -> Path:
    return root / "runtime" / "python" / "python312._pth"


def _has_astrbot_path(path: Path) -> bool:
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError):
        return False
    return any(line.strip().replace("/", "\\").lower() == r"..\..\astrbot" for line in lines)


def official_error(root: Path) -> dict | None:
    """只返回最近一次运行的固定错误分类，不把原始日志交给界面或 AI。"""
    try:
        with (Path(root) / "logs" / "astrbot.log").open("rb") as stream:
            stream.seek(0, 2)
            stream.seek(max(0, stream.tell() - 65536))
            tail = stream.read().decode("utf8", "replace")
    except OSError:
        return None
    tail = tail.rsplit("=== DA FEI YU PROCESS START", 1)[-1]
    tail = tail.rsplit("[DaFeiYu connection] status=ready", 1)[-1]
    if "invalid appid or secret" in tail or re.search(r"[\"']?code[\"']?\s*:\s*100016\b", tail):
        return {"code": "qq_credentials_invalid", "message": "QQ 平台拒绝了凭证。请修改机器人凭证：重新填写同一个机器人的 AppID 和最新 AppSecret，再保存重启。重装不能解决此问题。"}
    if "获取token失败" in tail:
        return {"code": "qq_connection_failed", "message": "QQ 平台暂未返回可用凭证，可能是网络、平台服务或权限问题。请先点击「重新连接」；仍失败再验证机器人凭证。"}
    if re.search(r"Task platform_qq_official_.*failed:", tail):
        return {"code": "qq_connection_failed", "message": "QQ 连接任务已停止。先检查网络，在 QQ 开放平台确认 WebSocket 和内部体验号码，再点击「重新连接」。仍失败时查看 astrbot.log。"}
    return None


def inspect(root: Path) -> dict:
    root = Path(root)
    items = []
    def add(code: str, message: str) -> None:
        items.append({"code": code, "message": message})

    if not (root / "runtime" / "python" / "python.exe").is_file():
        add("python_missing", "内置 Python 不存在，目前无法自动恢复；请先备份数据并联系维护者。")
    if not (root / "astrbot" / "main.py").is_file():
        add("astrbot_entry_missing", "AstrBot 启动文件不存在。")
    core = root / "astrbot" / "astrbot"
    if not (core / "__init__.py").is_file() or not (core / "utils" / "http_ssl_common.py").is_file():
        add("astrbot_core_missing", "AstrBot 核心文件不完整，可能导致无法导入 astrbot。")
    if not _has_astrbot_path(_pth(root)):
        add("astrbot_path_missing", "内置 Python 的 AstrBot 搜索路径缺失。")
    try:
        state = read_state(root / "state.json")
        official = state.get("platform") == "official"
    except (OSError, ValueError):
        official = False
        add("state_invalid", "安装状态缺失或损坏；请先在恢复页检查状态备份。")
    if not official and not (root / "napcat" / "node.exe").is_file():
        add("napcat_missing", "NapCat 运行环境不完整。")
    config = root / "astrbot" / "data" / "cmd_config.json"
    try:
        if not isinstance(json.loads(config.read_text(encoding="utf-8-sig")), dict):
            raise ValueError()
    except (OSError, ValueError):
        add("config_invalid", "AstrBot 配置缺失或无法解析。")
    log = root / "logs" / "astrbot.log"
    try:
        with log.open("rb") as stream:
            stream.seek(max(0, log.stat().st_size - 65536))
            tail = stream.read().decode("utf-8", "replace")
        tail = tail.rsplit("=== DA FEI YU PROCESS START ", 1)[-1]
        missing = re.findall(r"ModuleNotFoundError: No module named ['\"]([A-Za-z_][A-Za-z0-9_.]{0,127})['\"]", tail)
        if missing:
            module = missing[-1]
            if module == "astrbot":
                add("astrbot_import_error", "最近启动日志显示 Python 无法导入 astrbot。")
            else:
                items.append({"code": "python_dependency_missing",
                              "message": f"最近启动日志显示缺少 Python 模块 {module}。", "module": module})
        if "PermissionError" in tail:
            add("permission_error", "最近启动日志显示文件访问权限错误。")
        if "DLL load failed" in tail or "WinError 193" in tail:
            add("dll_load_error", "最近启动日志显示 DLL 加载或程序架构错误。")
    except OSError:
        pass
    service_log = root / "logs" / "services.log"
    try:
        with service_log.open("rb") as stream:
            stream.seek(max(0, service_log.stat().st_size - 65536))
            tail = stream.read().decode("utf-8", "replace")
        for name in ("napcat", "astrbot"):
            failed = tail.rfind(f"停止失败：{name}")
            stopped = tail.rfind(f"已确认 {name} 停止")
            start = tail.rfind(f"停止 {name}：", 0, failed)
            detail = tail[max(0, start):failed + 160].lower()
            if failed > stopped and ("拒绝访问" in detail or "访问被拒绝" in detail or "access is denied" in detail or "access denied" in detail):
                add("stop_permission_denied", "最近停止服务时被 Windows 拒绝访问；请重试下线并按提示进行管理员授权。")
                break
    except OSError:
        pass
    if issue := official_error(root):
        items.append(issue)
    return {"ok": not items, "items": items}


def restore_core(root: Path, source: Path) -> None:
    """仅补齐同版本核心缺失文件，并补回内置 Python 搜索路径。"""
    root, source = Path(root).absolute(), Path(source).absolute()
    for base in (root, source):
        for part in (base, base / "astrbot", base / "astrbot" / "astrbot", _pth(base)):
            if part.resolve() != part.absolute():
                raise ValueError("修复目录包含链接，无法安全自动修复。")
    for name in ("main.py", "pyproject.toml"):
        current, packaged = root / "astrbot" / name, source / "astrbot" / name
        if not current.is_file() or not packaged.is_file() or current.read_bytes() != packaged.read_bytes():
            raise ValueError("安装包与当前 AstrBot 版本无法确认一致，请使用原版一键包。")
    packaged_core = source / "astrbot" / "astrbot"
    if not (packaged_core / "__init__.py").is_file() or not (packaged_core / "utils" / "http_ssl_common.py").is_file():
        raise ValueError("安装包中的 AstrBot 核心不完整。")
    if not _has_astrbot_path(_pth(source)):
        raise ValueError("安装包中的 Python 搜索路径不完整。")

    core = root / "astrbot" / "astrbot"
    for tree in (core, packaged_core):
        for entry in tree.rglob("*"):
            if entry.is_symlink() or entry.is_junction():
                raise ValueError("核心目录包含链接，无法安全自动修复。")
    work = root / ".tmp"
    if work.resolve() != work.absolute():
        raise ValueError("修复临时目录包含链接，无法安全自动修复。")
    work.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="repair-", dir=work) as temp:
        temp = Path(temp)
        staged = temp / "astrbot"
        shutil.copytree(packaged_core, staged)
        core.mkdir(parents=True, exist_ok=True)
        core_resolved = core.resolve()
        for src in staged.rglob("*"):
            dest = core / src.relative_to(staged)
            if core_resolved not in dest.resolve().parents:
                raise ValueError("核心目录包含不安全的链接，无法自动修复。")
            if src.is_dir():
                dest.mkdir(parents=True, exist_ok=True)
            elif src.is_file() and not dest.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                created = False
                try:
                    with src.open("rb") as inp, dest.open("xb") as out:
                        created = True
                        shutil.copyfileobj(inp, out)
                except FileExistsError:
                    pass
                except Exception:
                    if created:
                        dest.unlink(missing_ok=True)
                    raise
        pth = _pth(root)
        if not _has_astrbot_path(pth):
            if pth.exists():
                content = pth.read_bytes() + b"\n..\\..\\astrbot\n"
            else:
                content = _pth(source).read_bytes()
            new_pth = temp / "python312._pth"
            new_pth.write_bytes(content)
            pth.parent.mkdir(parents=True, exist_ok=True)
            os.replace(new_pth, pth)


def repair(root: Path, boot: Path) -> dict:
    """从当前安装包解压到隔离目录，再修复已确认的核心缺失。"""
    root, boot = Path(root), Path(boot)
    payload, seven = boot / "payload.7z", boot / "7zr.exe"
    if not payload.is_file() or not seven.is_file():
        raise ValueError("修复需要原版一键包：请重新双击一键包，再从状态页点检测并修复。")
    work = root / ".tmp"
    if work.resolve() != work.absolute():
        raise ValueError("修复临时目录包含链接，无法安全自动修复。")
    work.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="repair-payload-", dir=work) as tmp:
        result = subprocess.run([str(seven), "x", str(payload), f"-o{tmp}", "-y", "-bso0", "-bsp0"],
                                capture_output=True, text=True, errors="replace", timeout=900,
                                creationflags=services.CREATE_NO_WINDOW)
        if result.returncode:
            raise OSError(f"安装包解压失败（退出码 {result.returncode}）。")
        restore_core(root, Path(tmp))
    return inspect(root)
import urllib.request

_AI_CODES = {
    "qq_credentials_invalid": "QQ rejected the AppID or AppSecret (100016). Verify the matching credentials; reinstalling cannot fix this.",
    "qq_connection_failed": "The QQ official platform task stopped with an error.",
    "stop_permission_denied": "Windows denied permission while stopping a service.",
    "state_invalid": "The installation state is missing or invalid.",
    "python_missing": "Bundled Python executable is missing.",
    "astrbot_entry_missing": "AstrBot entry point is missing.",
    "astrbot_core_missing": "AstrBot core files are missing.",
    "astrbot_path_missing": "The bundled Python import path for AstrBot is missing.",
    "astrbot_import_error": "The latest AstrBot log reports that astrbot cannot be imported.",
    "napcat_missing": "NapCat runtime is missing.",
    "config_invalid": "The AstrBot configuration is missing or invalid.",
    "python_dependency_missing": "A Python module is missing.",
    "permission_error": "The latest startup reported a file permission error.",
    "dll_load_error": "The latest startup reported a DLL load or architecture error.",
}


def analyze_with_deepseek(key: str, report: dict, opener=urllib.request.urlopen) -> str:
    codes = [item.get("code") for item in report.get("items", [])
             if item.get("code") in _AI_CODES]
    if not codes:
        return "目前没有足够的错误证据，不调用 DeepSeek 猜测。请先重现问题，再运行检查；QQ 无回复时确认平台的 WebSocket 设置和内部体验号码。"
    facts = [{"code": code, "meaning": _AI_CODES[code]} for code in codes]
    for fact, item in zip(facts, [x for x in report.get("items", []) if x.get("code") in _AI_CODES]):
        module = item.get("module", "")
        if fact["code"] == "python_dependency_missing" and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{0,127}", module):
            fact["module"] = module
    body = {
        "model": "deepseek-flash",
        "messages": [
            {"role": "system", "content": "你是 Windows 安装问题诊断助手。仅根据给出的诊断码，用中文说明可能原因、程序能否自动修复和用户可执行的安全检查。不要索要密钥，不要建议删除用户数据，不要声称已执行操作。"},
            {"role": "user", "content": json.dumps(facts, ensure_ascii=False)},
        ],
        "max_tokens": 600,
        "stream": False,
    }
    req = urllib.request.Request("https://api.deepseek.com/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST")
    with opener(req, timeout=40) as response:
        result = json.loads(response.read())
    answer = result["choices"][0]["message"]["content"]
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError("DeepSeek 未返回诊断建议")
    return answer[:6000]
