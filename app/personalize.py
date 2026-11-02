"""第 3 页的个性化：写进配置包副本；再生成导入工具要的回答文件。"""
import json
from pathlib import Path

DEFAULT_NAME = "大肥鱼"
VOICE_PRESETS = [
    ("甜妹", "一个憨萌软糯的小女生，声音甜甜的、有点奶，十几岁的感觉，嘴馋爱撒娇，带一点小傲娇"),
    ("御姐", "二十多岁的成熟女生，声音低一点、慵懒有磁性，说话不紧不慢，偶尔带点调侃"),
    ("元气少年", "十几岁的阳光男生，声音清亮有活力，说话快、爱笑，有点小得意"),
]
PERSONA = Path("persona") / "大肥鱼.md"
PACK_JSON = Path("settings") / "pack.json"


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def default_profile(pack_dir: Path) -> dict:
    pack = _read_json(pack_dir / PACK_JSON)
    return {
        "name": DEFAULT_NAME,
        "persona": (pack_dir / PERSONA).read_text(encoding="utf-8-sig"),
        "voice": pack["voice"]["doubao"]["persona"],
        "memes": {m["id"]: m.get("use", True) for m in pack["meme_packs"]},
    }


def fix_plugin_imports(astrbot_data: Path) -> None:
    """补上游插件漏打包的文件。

    astrbot_plugin_group_chat_plus 1.2.3 的 private_chat 子包写着 from ._session_guard import …，
    但发行包里那个目录只有 private_chat_session_guard.py（内容和 utils/_session_guard.py 一样），
    于是整个插件每次启动都加载失败。补一个转手文件就行，不改人家的代码。
    """
    utils = Path(astrbot_data) / "plugins" / "astrbot_plugin_group_chat_plus" / "private_chat" / "private_chat_utils"
    src, shim = utils / "private_chat_session_guard.py", utils / "_session_guard.py"
    if src.exists() and not shim.exists():
        shim.write_text("from .private_chat_session_guard import *  # 上游少打包了这个文件，见 fix_plugin_imports\n",
                        encoding="utf-8")


def validate_profile(profile: dict) -> str | None:
    if not str(profile.get("name") or "").strip():
        return "给她起个名字吧"
    if len(str(profile.get("name")).strip()) > 20:
        return "名字太长了，20 个字以内"
    if not str(profile.get("persona") or "").strip():
        return "性格描述不能是空的"
    if not str(profile.get("voice") or "").strip():
        return "声音描述不能是空的"
    if not any((profile.get("memes") or {}).values()):
        return "表情包至少留一套"
    return None


def apply_profile(pack_dir: Path, profile: dict) -> None:
    if err := validate_profile(profile):
        raise ValueError(err)
    name = profile["name"].strip()
    voice = profile["voice"].strip()
    persona = profile["persona"].strip()
    memes = profile["memes"]

    persona = persona.replace(DEFAULT_NAME, name)
    if "{OWNER_QQ}" not in persona:
        # 导入工具靠这个占位写入主人 QQ；用户删了就补回来，不然她认不出主人
        persona += "\n\n主人的 QQ 号是 {OWNER_QQ}。消息里的 User ID 是这个号，才是主人本人。"
    (pack_dir / PERSONA).write_text(persona + "\n", encoding="utf-8")

    pack = _read_json(pack_dir / PACK_JSON)
    pack["voice"]["doubao"]["persona"] = voice
    first = True
    for m in pack["meme_packs"]:
        m["use"] = bool(memes.get(m["id"], False))
        # 主套必须是开着的那一套（导入工具把 default 那套设为默认规则的第一套）
        m["default"] = m["use"] and first
        first = first and not m["use"]
    (pack_dir / PACK_JSON).write_text(json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8")


def write_answers(path: Path, *, base_url: str, username: str, password: str, owner_qq: str,
                  secrets_dir: Path, sf_key: str, app_id: str, token: str) -> None:
    """密码和 key 写成单独的小文件（导入工具的 *_file 用法），回答文件本身不含明文。调用方用完删 secrets_dir。"""
    secrets_dir.mkdir(parents=True, exist_ok=True)
    files = {}
    for name, value in (("password", password), ("siliconflow_key", sf_key), ("volc_access_token", token)):
        f = secrets_dir / f"{name}.txt"
        f.write_text(value, encoding="utf-8")
        files[f"{name}_file"] = str(f)
    answers = {"base_url": base_url, "username": username, "owner_qq": owner_qq,
               "memory": "2", "voice": "3", "volc_app_id": app_id, **files}
    path.write_text(json.dumps(answers, ensure_ascii=False, indent=2), encoding="utf-8")
