"""给大肥鱼做一个本地声音（Breeze TTS 2）。

两种做法：
  1 用一句话描述声音，出三个候选，挑一个存下来（不用录音）
  2 拿一段录音克隆（5–15 秒、只有一个人说话、没有背景音乐，还要录音里说的原话）
存下的声音叫 dafeiyu（在 settings/pack.json 的 voice.breeze.voice 里改），导入工具语音选 4 时用它。

用法：双击上两层的「做声音.bat」。要先用「装Breeze语音.bat」装好并启动 Breeze。
"""
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import uuid
import wave

PACK = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(PACK, "voice", "breeze")
DESIGN_TEXT = "哼，你终于想起来找我啦？我还以为你把我忘了呢。今天有没有给我带好吃的呀？"
TRY_TEXT = "在呢在呢，这么晚还不睡，是不是又在偷偷吃夜宵？"


def say(msg=""):
    print(msg, flush=True)


def request(method, path, body=None, content_type="application/json", timeout=600):
    req = urllib.request.Request(BASE + path, data=body, method=method)
    if body is not None:
        req.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except (urllib.error.URLError, OSError) as e:
        return 0, str(e).encode()


def speak(payload, out):
    """调 Breeze 念一段，存成 wav。返回是否成功。"""
    code, pcm = request("POST", "/v1/audio/speech", json.dumps({**payload, "response_format": "pcm"}).encode())
    if code != 200:
        say(f"  ！Breeze 报错（{code}）：{pcm[:200].decode('utf-8', 'replace')}")
        return False
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(pcm)
    return True


def play(path):
    if os.name == "nt":
        os.startfile(path)
    elif shutil.which("afplay"):
        subprocess.run(["afplay", path])
    say(f"  （文件在 {path}）")


def save_voice(audio_path, text, name, description=""):
    boundary = uuid.uuid4().hex
    parts = []
    for k, v in (("ref_text", text), ("name", name), ("description", description)):
        if v:
            parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode("utf-8"))
    fname = os.path.basename(audio_path)
    head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"ref_audio\"; filename=\"ref{os.path.splitext(fname)[1]}\"\r\n"
            "Content-Type: application/octet-stream\r\n\r\n").encode("utf-8")
    body = b"".join(parts) + head + open(audio_path, "rb").read() + f"\r\n--{boundary}--\r\n".encode()
    code, out = request("POST", "/v1/voices", body, f"multipart/form-data; boundary={boundary}")
    if code != 200:
        say(f"  ！存声音失败（{code}）：{out[:200].decode('utf-8', 'replace')}")
        return False
    return True


def to_wav(path):
    """Breeze 认 wav。别的格式有 ffmpeg 就先转成 24k 单声道 wav。"""
    if path.lower().endswith(".wav"):
        return path
    ff = shutil.which("ffmpeg")
    if not ff:
        say("  这段录音不是 wav，电脑上也没有 ffmpeg 来转。先转成 wav 再来，或者运行 winget install Gyan.FFmpeg 装上 ffmpeg。")
        return ""
    out = os.path.join(tempfile.gettempdir(), "breeze-ref.wav")
    r = subprocess.run([ff, "-y", "-loglevel", "error", "-i", path, "-ac", "1", "-ar", "24000", out])
    return out if r.returncode == 0 else ""


def design(name, persona):
    while True:
        say(f"\n声音描述：{persona}")
        say("出三个候选，每个要几秒到十几秒…")
        files = []
        for i in range(1, 4):
            out = os.path.join(OUT, f"候选{i}.wav")
            seed = random.randint(1, 99999)
            if speak({"input": DESIGN_TEXT, "instructions": persona, "cfg_scale": 4, "seed": seed}, out):
                files.append(out)
                say(f"  候选{i} 好了")
        if not files:
            return False
        for i, f in enumerate(files, 1):
            input(f"\n按回车听候选{i}…")
            play(f)
        say("\n喜欢第几个？输入 1/2/3 存下来；都不喜欢直接回车再出三个；想换个描述输入 d")
        pick = input("选：").strip().lower()
        if pick == "d":
            persona = input("新的描述（比如「二十出头的女生，说话直爽利落」）：").strip() or persona
            continue
        if pick in ("1", "2", "3") and int(pick) <= len(files):
            return save_voice(files[int(pick) - 1], DESIGN_TEXT, name, persona)


def clone(name):
    path = input("\n把录音文件拖进这个窗口（或者粘贴路径），回车：").strip().strip('"').strip("'")
    if not os.path.isfile(path):
        say("  ！找不到这个文件")
        return False
    text = input("这段录音里说的原话，一字不差打出来：").strip()
    if not text:
        say("  ！原话不能空着，Breeze 要靠它对上每个字的发音")
        return False
    wav = to_wav(path)
    return bool(wav) and save_voice(wav, text, name, "克隆：" + os.path.basename(path))


def main():
    global BASE
    pack = json.load(open(os.path.join(PACK, "settings", "pack.json"), encoding="utf-8-sig"))
    b = pack["voice"]["breeze"]
    BASE = (sys.argv[1] if len(sys.argv) > 1 else b["api_base"]).rstrip("/")
    if BASE.endswith("/v1"):
        BASE = BASE[:-3]
    name = b["voice"]
    os.makedirs(OUT, exist_ok=True)

    say("给大肥鱼做一个本地声音（Breeze TTS 2）")
    code, out = request("GET", "/v1/voices", timeout=10)
    if code != 200:
        say(f"\n连不上 Breeze（{BASE}）。先双击桌面的「启动Breeze语音」，等黑窗口里不再刷字了再来。")
        say("还没装的话，先双击配置包里的「装Breeze语音.bat」。")
        return
    have = [v.get("id") for v in json.loads(out or b"[]") if v.get("saved")]
    if name in have:
        say(f"已经有一个叫 {name} 的声音了，再做一个会把它换掉。")

    say("\n1 用一句话描述一个声音（不用录音，推荐先试这个）")
    say("2 用一段录音克隆（5–15 秒、只有一个人说话、没有背景音乐）")
    way = input("选（直接回车是 1）：").strip() or "1"
    ok = clone(name) if way == "2" else design(name, b["persona"])
    if not ok:
        say("\n没存成，再双击一次「做声音.bat」重来。")
        return

    out = os.path.join(OUT, "试听.wav")
    say(f"\n存好了，名字是 {name}。用它念一句试试…")
    if speak({"model": "tts-1", "voice": name, "input": TRY_TEXT}, out):
        play(out)
    say("\n第一次做的话，下一步双击「导入大肥鱼.bat」，语音那一步选 4。之前已经选过 4 的不用再导入，她下一条语音就是新声音。")


if __name__ == "__main__":
    main()
