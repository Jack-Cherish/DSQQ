"""面向排障的 UTF-8 日志：先脱敏、再限量写入，日志失败不影响业务。"""
import re
import os
import tempfile
import time
from collections import deque
from pathlib import Path

MAX_BYTES = 5 * 1024 * 1024
MAX_LINE = 16 * 1024
_HEADER_BYTES = b"# DaFeiYu sanitized log v1\n"
_ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
_SECRET = re.compile(r'''(?i)(\b(?:api[_ -]?key|access[_ -]?(?:token|key)|app[_ -]?secret|[\w-]*(?:secret|password|passwd|token)|pwd|key|credential|authorization)\b["']?\s*[:=]\s*)(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s,;&}\]]+)''')
_ACCOUNT = re.compile(r'''(?i)(\b(?:qq|uin|openid|user[_ -]?id|group[_ -]?id|owner[_ -]?qq|nick(?:name)?)\b["']?\s*[:=]\s*)(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s,;&}\]]+)''')
_PREFIX = re.compile(r"^(\[\d{2}:\d{2}:[^\]\r\n]+\].*?\[[\w.]+:\d+\]:\s*)")
_HEADER = re.compile(r"^(?:\[\d{2}:\d{2}:[^\]\r\n]+\]\s+\[|\d{2,4}[-/]\d{2}(?:[-/]\d{2})?\s+\d{2}:\d{2}:\d{2}\s+\[(?:info|debug|warn|error)\]|Traceback \(most recent call last\):$|[A-Za-z_.]+(?:Error|Exception):|===)", re.I)
_CHAT = re.compile(r"接收\s*<-|发送\s*->|\b(?:received|sent)\s+(?:message|chat)\b|Prepare to send\s*-|使用工具：.*参数：|Tool\s+`[^`]+`\s+Result:|Speech-to-text.*result:|\bRawMessage\b|\[event_bus:\d+\]:\s*\[[^\]]+\]\s*\[|(?:^\s*|[{,]\s*)[\"']?(?:messages|prompt|user_input|chat_history|message_chain)[\"']?\s*[:=]", re.I)


class Sanitizer:
    def __init__(self, secrets=(), root=None):
        self.secrets = sorted({str(s) for s in secrets if s and len(str(s)) >= 4}, key=len, reverse=True)
        self.root = str(Path(root).resolve()) if root else ""
        self.private_block = False

    def line(self, text: str) -> str:
        text = _ANSI.sub("", str(text)).replace("\r", "").replace("\ufeff", "")
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
        if _CHAT.search(text):
            self.private_block = True
            # 昵称常在日志前缀与消息标记之间，只保留时间/级别。
            prefix = _PREFIX.match(text) or re.match(r"^.*?\[(?:info|debug|warn|error)\]", text, re.I)
            return (prefix.group(0) + " " if prefix else "") + "[message payload omitted]"
        if self.private_block:
            if not _HEADER.match(text):
                return ""
            self.private_block = False
        if re.search(r"[█▀▄]{4,}|二维码解码|qrcodeurl", text, re.I):
            return "[QR content omitted]"
        for secret in self.secrets:
            text = text.replace(secret, "***")
        text = re.sub(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9+/=_~.\-]+", r"\1 ***", text)
        text = re.sub(r'''(?i)(\b(?:cookie|set-cookie)["']?\s*[:=]\s*).*''', r"\1***", text)
        text = _SECRET.sub(r"\1***", text)
        text = _ACCOUNT.sub(r"\1***", text)
        text = re.sub(r"\bsk-[A-Za-z0-9_-]{4,}", "***", text)
        text = re.sub(r"(https?://[^\s?#]+)[?#][^\s]*", r"\1?[redacted]", text)
        text = re.sub(r"(https?://)[^/@\s]+@", r"\1***@", text)
        text = re.sub(r"(?i)((?:napcat|onebot11)_)\d{5,}", r"\1<account>", text)
        if self.root:
            text = re.sub(re.escape(self.root), "<install>", text, flags=re.I)
            text = re.sub(re.escape(self.root.replace('\\', '/')), "<install>", text, flags=re.I)
        text = re.sub(r"(?i)([A-Z]:[\\/](?:Users|Documents and Settings)[\\/])[^\\/:\r\n]+", r"\1<user>", text)
        def number(match):
            context = text[max(0, match.start() - 40):match.start()]
            if re.search(r"(?i)(?:PID|PPID|port|code|status|WinError|errno|HTTP|返回码|错误码|端口|timestamp)[= :：\"']*$", context):
                return match.group(0)
            return "<id>"
        return re.sub(r"(?<![\w])\d{5,}(?![\w])", number, text)


def sanitize(text: str, secrets=(), root=None) -> str:
    parser = Sanitizer(secrets, root)
    return "\n".join(line for raw in str(text).splitlines() if (line := parser.line(raw)))


def secret_values(state) -> list[str]:
    try:
        return state.secret_values() if state is not None else []
    except (OSError, ValueError, TypeError):
        return []


def iter_lines(stream, limit=MAX_LINE):
    """拒绝超长记录，既避免内存膨胀，也避免把截断的密钥后半截落盘。"""
    while raw := stream.readline(limit + 1):
        if len(raw) > limit:
            while raw and not raw.endswith(b"\n"):
                raw = stream.readline(limit + 1)
            yield "[oversized log record omitted]"
            continue
        try:
            yield raw.decode("utf-8-sig").rstrip("\r\n")
        except UnicodeDecodeError:
            yield "[non-UTF8 source] " + raw.decode("oem", errors="backslashreplace").rstrip("\r\n")


def _prepare_file(path, secrets, root, max_bytes):
    if not path.exists():
        return
    with path.open("rb") as stream:
        if stream.readline(len(_HEADER_BYTES) + 1) == _HEADER_BYTES and path.stat().st_size <= max_bytes:
            return
        offset = max(0, path.stat().st_size - max_bytes)
        stream.seek(offset)
        if offset:
            # 不把尾部截到一半的凭据或聊天续行当作独立记录。
            while raw := stream.readline(MAX_LINE + 1):
                if raw.endswith(b"\n"):
                    break
        parser, tail, size = Sanitizer(secrets, root), deque(), len(_HEADER_BYTES)
        parser.private_block = offset > 0
        for raw in iter_lines(stream):
            text = parser.line(raw)
            if not text:
                continue
            line = (text + "\n").encode("utf-8", errors="backslashreplace")
            if len(line) + len(_HEADER_BYTES) > max_bytes:
                line = b"[oversized log record omitted]\n"
            tail.append(line)
            size += len(line)
            while tail and size > max_bytes:
                size -= len(tail.popleft())
    temp_dir = Path(root or path.parent) / ".tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="log-", dir=temp_dir) as temp:
        staged = Path(temp) / "sanitized.log"
        with staged.open("wb") as out:
            out.write(_HEADER_BYTES)
            out.writelines(tail)
            out.flush()
            os.fsync(out.fileno())
        staged.replace(path)


def append_log(path: Path, message: str, *, secrets=(), root=None, max_bytes=MAX_BYTES, backups=2) -> bool:
    from store import file_lock
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        text = sanitize(message, secrets, root)
        if not text:
            return True
        record = (time.strftime("%Y-%m-%d %H:%M:%S ") + text + "\n").encode("utf-8", errors="backslashreplace")
        if len(record) + len(_HEADER_BYTES) > max_bytes:
            record = (time.strftime("%Y-%m-%d %H:%M:%S ") + "[oversized log record omitted]\n").encode("utf-8")
        with file_lock(path.with_name(path.name + ".lock")):
            # 旧版日志首次使用时也脱敏，只保留有界尾部；临时文件从不写原始内容。
            for candidate in (path, *(path.with_name(f"{path.name}.{n}") for n in range(1, backups + 1))):
                _prepare_file(candidate, secrets, root, max_bytes)
            if path.exists() and path.stat().st_size + len(record) > max_bytes:
                for n in range(backups, 0, -1):
                    old = path if n == 1 else path.with_name(f"{path.name}.{n - 1}")
                    new = path.with_name(f"{path.name}.{n}")
                    if old.exists():
                        old.replace(new)
            with path.open("ab") as out:
                if out.tell() == 0:
                    out.write(_HEADER_BYTES)
                out.write(record)
        return True
    except (OSError, ValueError):
        return False
