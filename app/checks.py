"""安装路径、端口和三种 key 的检查。所有返回给用户的文字都是中文人话。"""
import json
import os
import shutil
import socket
import urllib.error
import urllib.request
import uuid
from pathlib import Path

DEEPSEEK_BALANCE = "https://api.deepseek.com/user/balance"
SILICONFLOW_EMBED = "https://api.siliconflow.cn/v1/embeddings"
DOUBAO_TTS = "https://openspeech.bytedance.com/api/v3/tts/create"
DOUBAO_RESOURCE = "seed-audio-1.0"
DEFAULT_VOICE = "一个憨萌软糯的小女生，声音甜甜的、有点奶，十几岁的感觉，嘴馋爱撒娇，带一点小傲娇"


def default_install_path() -> str:
    return "D:\\DaFeiYu" if os.path.exists("D:\\") else "C:\\DaFeiYu"


def check_install_path(path: str, min_free_gb: float = 10) -> str | None:
    path = (path or "").strip()
    if not path:
        return "先选一个安装位置"
    if not path.isascii() or " " in path:
        return "路径里不能有中文和空格，换一个，比如 D:\\DaFeiYu"
    p = Path(path)
    if not p.is_absolute() or not p.drive:
        return "要写完整路径，比如 D:\\DaFeiYu"
    if p.exists():
        if not p.is_dir():
            return "这是个文件，不是文件夹，换一个"
        if any(p.iterdir()) and not (p / "state.json").exists():
            return "这个文件夹里已经有别的东西了，换一个空文件夹"
    probe = p
    while not probe.exists():
        if probe.parent == probe:
            return "这个盘不存在，换一个"
        probe = probe.parent
    free_gb = shutil.disk_usage(probe).free / 1024**3
    if free_gb < min_free_gb:
        return f"这个盘剩余空间只有 {free_gb:.1f}G，至少要 {min_free_gb:g}G，换个盘"
    return None


def _bindable(host: str, port: int) -> bool:
    # 只 bind 不 listen，不会触发防火墙弹窗
    with socket.socket() as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def free_port(preferred: int) -> int:
    """Windows 上别人占着 0.0.0.0:p 时 127.0.0.1:p 仍能 bind，反之亦然，所以两个都试。"""
    port = preferred
    while port < 65535:
        if _bindable("127.0.0.1", port) and _bindable("0.0.0.0", port):
            return port
        port += 1
    raise RuntimeError("找不到空闲端口")


def _call(opener, url, *, headers=None, body=None, timeout=20):
    """发请求，返回 (status, dict)；网络不通时抛 URLError。"""
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with opener(req, timeout=timeout) as r:
            status, raw = r.status, r.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    try:
        return status, json.loads(raw or b"{}")
    except ValueError:
        return status, {"raw": raw[:200].decode("utf-8", "replace")}


def check_deepseek(key: str, opener=urllib.request.urlopen) -> tuple[bool, str]:
    try:
        status, data = _call(opener, DEEPSEEK_BALANCE, headers={"Authorization": f"Bearer {key.strip()}"})
    except (urllib.error.URLError, OSError):
        return False, "连不上 DeepSeek，检查一下网络"
    if status in (401, 403):
        return False, "key 不对，回 DeepSeek 开放平台重新复制一下"
    if status != 200:
        return False, f"DeepSeek 返回了错误（{status}），过一会儿再试"
    if not data.get("is_available"):
        return False, "余额不够了，先去 DeepSeek 开放平台充值"
    infos = data.get("balance_infos") or [{}]
    return True, f"可以用，余额 {infos[0].get('total_balance', '?')} 元"


def check_siliconflow(key: str, opener=urllib.request.urlopen) -> tuple[bool, str]:
    try:
        status, data = _call(opener, SILICONFLOW_EMBED, headers={"Authorization": f"Bearer {key.strip()}"},
                             body={"model": "BAAI/bge-m3", "input": "你好"})
    except (urllib.error.URLError, OSError):
        return False, "连不上硅基流动，检查一下网络"
    if status in (401, 403):
        return False, "key 不对，回硅基流动「API 密钥」页面重新复制一下"
    if status != 200 or not data.get("data"):
        return False, f"硅基流动返回了错误（{status}），过一会儿再试"
    return True, "可以用"


def check_doubao(app_id: str, token: str, voice: str = DEFAULT_VOICE, text: str = "你好呀",
                 opener=urllib.request.urlopen) -> tuple[bool, str, str]:
    """用豆包念一句。成功时第三项是 mp3 的 base64，试听用。会消耗一点试用额度。"""
    headers = {
        "X-Api-App-Id": app_id.strip(),
        "X-Api-Access-Key": token.strip(),
        "X-Api-Resource-Id": DOUBAO_RESOURCE,
        "X-Api-Request-Id": str(uuid.uuid4()),
    }
    prompt = f"{voice}，在QQ上给好朋友发语音，语气轻快自然，只说引号里的这句话，一字不差：“{text}”"
    body = {"model": DOUBAO_RESOURCE, "text_prompt": prompt,
            "audio_config": {"format": "mp3", "sample_rate": 24000, "enable_subtitle": True}}
    try:
        status, data = _call(opener, DOUBAO_TTS, headers=headers, body=body, timeout=60)
    except (urllib.error.URLError, OSError):
        return False, "连不上豆包语音，检查一下网络", ""
    blob = json.dumps(data, ensure_ascii=False)
    if "45000030" in blob or "not granted" in blob:
        return False, "还没领试用：到豆包语音旧版控制台「API 服务中心」选豆包音频生成模型 1.0，点「试用」", ""
    if status in (401, 403):
        return False, "APP ID 或 Access Token 不对（要旧版控制台的，不是火山方舟的 API Key）", ""
    if status != 200 or not data.get("audio"):
        return False, f"豆包语音返回了错误（{status}），过一会儿再试", ""
    return True, "可以用", data["audio"]
