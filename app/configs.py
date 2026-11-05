"""服务第一次启动前就写好的配置：AstrBot 的 cmd_config.json，NapCat 的 webui.json / onebot11*.json。

字段名对照 AstrBot 4.29.3 的 astrbot/core/config/default.py 与 NapCat 4.18.41 的 napcat.mjs 配置 schema。
"""
import hashlib
import json
import secrets
import string
from pathlib import Path

from checks import free_port
from fileio import atomic_write_text

DASHBOARD_USER = "dafeiyu"
PLATFORM_ID = "dafeiyu"
SOURCE_ID = "deepseek_source"
CHAT_MODEL = "deepseek-flash"
CHAT_PROVIDER_ID = f"deepseek/{CHAT_MODEL}"


def _token(n: int) -> str:
    return "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(n))


def new_secrets() -> dict:
    # AstrBot 要求密码含大写、小写、数字
    pw = secrets.choice(string.ascii_uppercase) + secrets.choice(string.ascii_lowercase) + \
        secrets.choice(string.digits) + _token(17)
    return {"dashboard_password": pw, "onebot_token": _token(24),
            "napcat_webui_token": _token(16), "jwt_secret": secrets.token_hex(32)}


def hash_dashboard_password(pw: str) -> tuple[str, str]:
    """与 AstrBot astrbot/core/utils/auth_password.py 相同的存储格式。"""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(salt), 600_000).hex()
    return f"pbkdf2_sha256$600000${salt}${digest}", hashlib.md5(pw.encode("utf-8")).hexdigest()


def pick_ports() -> dict:
    ports = {}
    for name, preferred in (("dashboard", 6185), ("onebot", 6199), ("napcat_webui", 6099)):
        port = free_port(preferred)
        while port in ports.values():
            port = free_port(port + 1)
        ports[name] = port
    return ports


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}


def _write(path: Path, data: dict) -> None:
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2), backup=True)


def _upsert(items: list, item: dict) -> list:
    """按 id 原地替换，没有就追加。"""
    items = list(items or [])
    for i, old in enumerate(items):
        if isinstance(old, dict) and old.get("id") == item["id"]:
            items[i] = item
            return items
    return items + [item]


def write_astrbot_config(data_dir: Path, *, ports: dict, secrets: dict, deepseek_key: str,
                         platform: str = "napcat", official_appid: str = "", official_secret: str = "",
                         owner_openid: str = "") -> None:
    path = Path(data_dir) / "cmd_config.json"
    conf = _read(path)
    pbkdf2, md5 = hash_dashboard_password(secrets["dashboard_password"])
    conf.setdefault("dashboard", {}).update({
        "enable": True, "username": DASHBOARD_USER, "pbkdf2_password": pbkdf2, "password": md5,
        "password_storage_upgraded": True, "password_change_required": False,
        "jwt_secret": secrets["jwt_secret"], "host": "127.0.0.1", "port": ports["dashboard"],
    })
    if platform == "official":
        adapter = {"id": PLATFORM_ID, "type": "qq_official", "enable": True, "appid": official_appid,
                   "secret": official_secret, "enable_group_c2c": True, "enable_guild_direct_message": False}
        owner_openid = owner_openid or "DAFEIYU_UNBOUND_OWNER"
        ps = conf.setdefault("platform_settings", {})
        ps["enable_id_white_list"] = True
        whitelist = ps.setdefault("id_whitelist", [])
        owner_session = f"{PLATFORM_ID}:FriendMessage:{owner_openid}"
        if owner_session not in whitelist:
            whitelist.append(owner_session)
        admins = conf.setdefault("admins_id", [])
        if owner_openid != "DAFEIYU_UNBOUND_OWNER" and owner_openid not in admins:
            admins.append(owner_openid)
    elif platform == "napcat":
        adapter = {"id": PLATFORM_ID, "type": "aiocqhttp", "enable": True, "ws_reverse_host": "127.0.0.1",
                   "ws_reverse_port": ports["onebot"], "ws_reverse_token": secrets["onebot_token"]}
    else:
        raise ValueError("不支持的 QQ 接入方式")
    conf["platform"] = _upsert(conf.get("platform", []), adapter)
    conf["provider_sources"] = _upsert(conf.get("provider_sources", []), {
        "id": SOURCE_ID, "provider": "deepseek", "type": "openai_chat_completion",
        "provider_type": "chat_completion", "enable": True, "key": [deepseek_key],
        "api_base": "https://api.deepseek.com/v1", "timeout": 120, "proxy": "", "custom_headers": {},
    })
    conf["provider"] = _upsert(conf.get("provider", []), {
        "id": CHAT_PROVIDER_ID, "provider_source_id": SOURCE_ID, "model": CHAT_MODEL,
        "modalities": ["text", "image", "tool_use"], "custom_extra_body": {}, "enable": True,
    })
    runner = conf.setdefault("agent_runner", {"runner_type": "local", "config": {}})
    runner.setdefault("config", {}).setdefault("model", {})["provider_id"] = CHAT_PROVIDER_ID
    conf.setdefault("provider_settings", {})["default_image_caption_provider_id"] = CHAT_PROVIDER_ID
    conf.setdefault("config_version", 5)
    _write(path, conf)


def _ws_client(ports: dict, secrets: dict) -> dict:
    return {"name": "AstrBot", "enable": True, "url": f"ws://127.0.0.1:{ports['onebot']}/ws",
            "messagePostFormat": "array", "reportSelfMessage": False, "reconnectInterval": 1000,
            "token": secrets["onebot_token"], "debug": False, "heartInterval": 1000}


def write_napcat_config(workdir: Path, *, ports: dict, secrets: dict) -> None:
    cfg = Path(workdir) / "config"
    webui = _read(cfg / "webui.json")
    webui.update({"host": "127.0.0.1", "port": ports["napcat_webui"], "token": secrets["napcat_webui_token"]})
    _write(cfg / "webui.json", webui)
    # 登录前不知道 QQ 号：NapCat 第一次登录时找不到 onebot11_<QQ>.json 就读 onebot11.json 另存一份
    default = {"network": {"httpServers": [], "httpSseServers": [], "httpClients": [], "websocketServers": [],
                           "websocketClients": [], "plugins": []},
               "musicSignUrl": "", "enableLocalFile2Url": False, "parseMultMsg": False}
    targets = [cfg / "onebot11.json"] + sorted(cfg.glob("onebot11_*.json"))
    for path in targets:
        conf = _read(path) or json.loads(json.dumps(default))
        conf.setdefault("network", {})["websocketClients"] = [_ws_client(ports, secrets)]
        _write(path, conf)
