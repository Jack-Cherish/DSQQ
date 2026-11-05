"""One-time local confirmation for QQ official private-message binding."""
import ctypes
import hmac
import json
import os
import re
import secrets
import shutil
import time
from pathlib import Path

TTL = 600
SIGNATURE = '    async def on_c2c_message_create(self, message: botpy.message.C2CMessage) -> None:\n'
HOOK = '''        from official_binding import capture
        if capture(message.content or "", message.author.user_openid or ""):
            return
'''


CLIENT_SIGNATURE = "class botClient(Client):\n"
READY_HOOK = "    async def on_ready(self):\n        from official_binding import set_connection\n        set_connection('ready')\n\n"
RESUMED_HOOK = "    async def on_resumed(self):\n        from official_binding import set_connection\n        set_connection('ready')\n\n"
CLOSE_SIGNATURE = "    async def on_closed(self, close_status_code, close_msg):\n"
CLOSE_HOOK = "        from official_binding import set_connection\n        set_connection('disconnected')\n"
ERROR_HOOK = "    async def on_error(self, exception):\n        from official_binding import set_connection\n        set_connection('disconnected')\n        await super().on_error(exception)\n\n"
CONNECT_SIGNATURE = "    async def bot_connect(self, session) -> None:\n"
CONNECT_HOOK = "        from official_binding import set_connection\n        set_connection('starting')\n"


def _process_birth(pid):
    if os.name != 'nt' or not isinstance(pid, int) or pid <= 0:
        return None
    try:
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        kernel.GetProcessTimes.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            creation, exit_time, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
            if not kernel.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_time),
                                          ctypes.byref(kernel_time), ctypes.byref(user_time)):
                return None
            return (creation.dwHighDateTime << 32) | creation.dwLowDateTime
        finally:
            kernel.CloseHandle(handle)
    except (OSError, AttributeError):
        return None


def set_connection(status, root=None, pid=None):
    if status not in ('starting', 'ready', 'disconnected'):
        return
    root = Path(root) if root is not None else Path(__file__).resolve().parent.parent
    pid = os.getpid() if pid is None else pid
    birth = _process_birth(pid)
    if birth is None:
        return
    directory = _directory(root)
    tmp = directory / ('connection-' + secrets.token_hex(8) + '.tmp')
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with tmp.open('x', encoding='utf-8') as f:
            json.dump({'status': status, 'pid': pid, 'birth': birth}, f)
        os.replace(tmp, directory / 'connection.json')
        print(time.strftime('%Y-%m-%d %H:%M:%S ') + '[INFO] [DaFeiYu connection] status=' + status, flush=True)
    except OSError:
        pass
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def connection_state(root, pid):
    birth = _process_birth(pid)
    if birth is None:
        return 'unknown'
    try:
        data = json.loads((_directory(root) / 'connection.json').read_text(encoding='utf-8'))
        if data.get('pid') == pid and data.get('birth') == birth and data.get('status') in ('starting', 'ready', 'disconnected'):
            return data['status']
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return 'unknown'


def _directory(root):
    return Path(root) / '.official-binding'


def _read(root, *, allow_expired=False):
    try:
        data = json.loads((_directory(root) / 'session.json').read_text(encoding='utf-8'))
        if (not re.fullmatch(r'[a-f0-9]{32}', data['id']) or
                not re.fullmatch(r'[a-f0-9]{16}', data['code']) or
                not isinstance(data['expires'], (int, float)) or (not allow_expired and time.time() >= data['expires'])):
            return None
        return data
    except (OSError, ValueError, KeyError, TypeError):
        return None


def create(root, *, renew=False):
    directory = _directory(root)
    directory.mkdir(parents=True, exist_ok=True)
    existing = _read(root)
    if existing and not renew:
        return existing['code']
    data = {'id': secrets.token_hex(16), 'code': secrets.token_hex(8), 'expires': time.time() + TTL}
    tmp = directory / (data['id'] + '.tmp')
    try:
        with tmp.open('x', encoding='utf-8') as f:
            json.dump(data, f)
        os.replace(tmp, directory / 'session.json')
    finally:
        tmp.unlink(missing_ok=True)
    return data['code']


def receipt(root):
    """Return a fixed result code for this session; never return message content."""
    data = _read(root, allow_expired=True)
    if not data:
        return 'not_received'
    if candidate(root):
        return 'received'
    try:
        record = json.loads((_directory(root) / 'receipt.json').read_text(encoding='utf-8'))
        if record.get('session') == data['id'] and record.get('result') in (
                'not_command', 'wrong_code', 'expired', 'invalid_sender', 'received', 'write_error'):
            return 'write_error' if record['result'] == 'received' else record['result']
    except FileNotFoundError:
        pass
    except (OSError, ValueError, TypeError, AttributeError):
        return 'write_error'
    return 'not_received'


def _record(root, data, result):
    directory = _directory(root)
    tmp = directory / ('receipt-' + secrets.token_hex(8) + '.tmp')
    try:
        with tmp.open('x', encoding='utf-8') as f:
            json.dump({'session': data['id'], 'result': result, 'at': time.time()}, f)
        os.replace(tmp, directory / 'receipt.json')
    except OSError:
        result = 'write_error'
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
    # Only fixed codes go to the runtime log, never credentials, text or identities.
    try:
        print(time.strftime('%Y-%m-%d %H:%M:%S ') + '[INFO] [DaFeiYu binding] result=' + result, flush=True)
    except (OSError, AttributeError):
        pass


def capture(content, openid, root=None):
    root = Path(root) if root is not None else Path(__file__).resolve().parent.parent
    data = _read(root, allow_expired=True)
    if not data:
        return False
    parts = content.strip().split() if isinstance(content, str) else []
    if not parts or parts[0] != '绑定':
        _record(root, data, 'not_command')
        return False
    if time.time() >= data['expires']:
        _record(root, data, 'expired')
        return True
    if (len(parts) != 2 or not re.fullmatch(r'[a-f0-9]{16}', parts[1])
            or not hmac.compare_digest(parts[1], data['code'])):
        _record(root, data, 'wrong_code')
        return True
    if not isinstance(openid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,128}', openid):
        _record(root, data, 'invalid_sender')
        return True
    directory = _directory(root)
    lock = directory / (data['id'] + '.lock')
    tmp = directory / (data['id'] + '.candidate.tmp')
    acquired = False
    result = 'write_error'
    try:
        with lock.open('x', encoding='utf-8'):
            pass
        acquired = True
        with tmp.open('x', encoding='utf-8') as f:
            f.write(openid)
        os.replace(tmp, directory / (data['id'] + '.candidate'))
        result = 'received'
    except FileExistsError:
        result = 'received' if candidate(root) else 'write_error'
    except OSError:
        pass
    finally:
        if acquired:
            try:
                tmp.unlink(missing_ok=True)
                if result != 'received':
                    lock.unlink(missing_ok=True)
            except OSError:
                pass
    _record(root, data, result)
    return True


def candidate(root):
    data = _read(root)
    if not data:
        return None
    try:
        value = (_directory(root) / (data['id'] + '.candidate')).read_text(encoding='utf-8')
        return value if re.fullmatch(r'[A-Za-z0-9_-]{16,128}', value) else None
    except OSError:
        return None


def session_id(root):
    data = _read(root)
    return data['id'] if data else None


def consume(root, expected_id):
    value = candidate(root)
    if not value:
        return None
    data = _read(root)
    if not data or not expected_id or not hmac.compare_digest(data['id'], expected_id):
        return None
    (_directory(root) / 'session.json').unlink(missing_ok=True)
    (_directory(root) / (data['id'] + '.candidate')).unlink(missing_ok=True)
    (_directory(root) / (data['id'] + '.lock')).unlink(missing_ok=True)
    return value


def install(root, *, before_update=None):
    astrbot = Path(root) / 'astrbot'
    adapter = astrbot / 'astrbot/core/platform/sources/qqofficial/qqofficial_platform_adapter.py'
    source = adapter.read_text(encoding='utf-8')
    patches = ((SIGNATURE, HOOK), (CLIENT_SIGNATURE, READY_HOOK),
               (CLOSE_SIGNATURE, CLOSE_HOOK), (CONNECT_SIGNATURE, CONNECT_HOOK))
    for signature, _ in patches:
        if source.count(signature) != 1:
            raise ValueError('QQ 官方适配器版本不兼容，无法安装安全绑定补丁')
    updated = source
    for signature, hook in patches:
        if signature + hook not in updated:
            updated = updated.replace(signature, signature + hook, 1)
    if RESUMED_HOOK not in updated:
        updated = updated.replace(READY_HOOK, READY_HOOK + RESUMED_HOOK, 1)
    if ERROR_HOOK not in updated:
        updated = updated.replace(CLOSE_SIGNATURE, ERROR_HOOK + CLOSE_SIGNATURE, 1)
    helper = astrbot / 'official_binding.py'
    if updated == source and helper.is_file() and helper.read_bytes() == Path(__file__).read_bytes():
        return
    # A running Python process caches this module; stop before updating either file.
    if before_update is not None:
        before_update()
    shutil.copyfile(Path(__file__), helper)
    if updated != source:
        tmp = adapter.with_suffix('.binding.tmp')
        try:
            tmp.write_text(updated, encoding='utf-8')
            os.replace(tmp, adapter)
        finally:
            tmp.unlink(missing_ok=True)
