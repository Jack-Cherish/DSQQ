# 大肥鱼一键包 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 一个双击就能装好、扫码就能用的「完整大肥鱼」Windows 一键包（向导 + 状态页 + 打包脚本）。

**Architecture:** 便携 Python 跑一个只绑 127.0.0.1 的标准库 HTTP 服务，Edge `--app` 窗口显示 HTML 向导。安装器把 payload 解到用户目录，**启动前直接写好** AstrBot 的 `cmd_config.json`（后台账号、OneBot 平台、DeepSeek 提供商）和 NapCat 的 `webui.json` / `onebot11.json`，然后拉起两个服务，调用配置包现成的 `import_dafeiyu.py --answers` 完成人设/插件/语音/记忆。维护者用 `build/build.py` 在有网的机器上预装所有依赖、插件、表情包，压成 7z 自解压 exe。

**Tech Stack:** Python 3.12 标准库（http.server、urllib、ctypes DPAPI、unittest），原生 HTML/CSS/JS，AstrBot 4.29.3，NapCat 4.18.41 `NapCat.Shell.Windows.Node.zip`，7-Zip SFX。

**Spec:** `docs/superpowers/specs/2026-10-28-dafeiyu-oneclick-design.md`

## Global Constraints

- 只支持 Windows 10/11 x64；`app/` 只用 Python 标准库（运行在打包的 Python 3.12 上）。
- 所有服务只绑 `127.0.0.1`；OneBot Token ≥ 20 位随机；NapCat WebUI token 随机；AstrBot 后台密码随机（≥ 16 位，含大小写和数字，满足 AstrBot `validate_dashboard_password`）。
- 记忆固定硅基流动（`BAAI/bge-m3`，`https://api.siliconflow.cn/v1`，维度 1024）；语音固定豆包（`https://openspeech.bytedance.com/api/v3/tts/create`，`seed-audio-1.0`）；对话模型 `deepseek-flash`（`https://api.deepseek.com/v1`）。
- 必填 5 项：主人 QQ（纯数字）、DeepSeek key、豆包 APP ID、豆包 Access Token、硅基流动 key。
- 安装路径不能有非 ASCII 字符和空格；默认 `D:\DaFeiYu`，无 D 盘用 `C:\DaFeiYu`。
- key 只以 DPAPI 加密形式落盘；日志和报错文本过 `redact()`。
- 不用、不改系统里已有的 Python / Node / QQ / AstrBot / NapCat。
- 用户可见文字全部中文，口语化，不出现堆栈。
- 测试用 `unittest`，在项目 `.venv` 里跑：`.venv\Scripts\python -m unittest discover -s tests -v`。

## Review Focus

- 安装中途关窗口再双击：必须从断点续装，不要求重填 key（Task 6 `test_resume_skips_done_steps`）。
- 端口 6185/6199/6099 被占用：自动换端口，且 AstrBot 平台端口与 NapCat ws URL 一致（Task 4 `test_ports_consistent`）。
- key 出现在日志：所有落盘日志经过 redact（Task 1 `test_redact_masks_all_secrets`）。
- 用户把性格文本里的 `{OWNER_QQ}` 删了：仍要让她认得主人（Task 3 `test_owner_placeholder_restored`）。
- 二维码过期 / 快速登录失效：状态页回到扫码而不是卡死（Task 5 `test_login_status_maps_phases`）。

---

## File Structure

```
onekey-dev/
  app/                      ← 打进包里的向导（标准库）
    main.py                 入口：python main.py [--status]；起服务、开 Edge、心跳退出
    server.py               HTTP 路由 + 静态文件 + 一次性 token
    store.py                state.json 读写、DPAPI 加解密、redact
    checks.py               路径/磁盘/端口检查，三种 key 在线检查
    personalize.py          把名字/性格/声音/表情包写进 pack 副本，生成 answers.json
    configs.py              生成 AstrBot cmd_config.json 种子、NapCat webui.json/onebot11.json
    services.py             启停 AstrBot/NapCat 进程，NapCat WebUI 登录状态/二维码
    installer.py            带断点的安装步骤流水线
    shortcut.py             桌面快捷方式
    web/                    index.html app.css app.js img/
  vendor/dafeiyu-config-pack/   配置包副本（导入工具加 --result-json）
  build/build.py            维护者打包脚本
  build/sfx_config.txt      7z SFX 配置
  tests/                    unittest
  docs/                     设计、计划、给用户的测试清单
```

### Task 1: 项目骨架 + store（状态、DPAPI、脱敏）

**Files:** Create `app/store.py`, `tests/test_store.py`, `.gitignore`（`downloads/ .tmp/ dist/ .venv/ __pycache__/`）；`git init`。

**Interfaces — Produces:**
- `protect(text: str) -> str` / `unprotect(blob: str) -> str`：DPAPI（`CryptProtectData`，ctypes），结果 base64。
- `class State`：`State.load(root: Path) -> State`，属性 `data: dict`，`save()` 原子写 `root/state.json`；`set_secret(name, value)` / `get_secret(name) -> str`（存 `data["secrets"][name]` 为 protect 后的串）；`mark_done(step: str)`、`is_done(step) -> bool`。
- `redact(text: str, secrets: Iterable[str]) -> str`：把每个长度 ≥ 4 的 secret 替换为 `***`。

- [ ] Step 1: 写测试 `test_dpapi_roundtrip`（`unprotect(protect("sk-abc")) == "sk-abc"` 且 protect 结果不含 `sk-abc`）、`test_state_persists_and_hides_secrets`（set_secret 后 save，`state.json` 原文不含明文，load 回来 get_secret 相等）、`test_mark_done`、`test_redact_masks_all_secrets`（两个 secret 都被替换，短于 4 的不替换）。
- [ ] Step 2: 运行，确认失败。
- [ ] Step 3: 实现。
- [ ] Step 4: 运行通过。
- [ ] Step 5: commit `feat: state store with DPAPI secrets`。

### Task 2: checks（路径、端口、key 检查）

**Files:** Create `app/checks.py`, `tests/test_checks.py`

**Interfaces — Produces:**
- `check_install_path(path: str) -> str | None`：返回中文错误或 None。规则：非空、绝对路径、只含 ASCII 且无空格、所在盘剩余 ≥ 10 GB（`shutil.disk_usage` 取最近存在的父目录）、目标若存在且非空且不是一键包目录（无 `state.json`）则报错「这个文件夹里已经有别的东西了」。
- `default_install_path() -> str`
- `free_port(preferred: int) -> int`：preferred 能 bind 127.0.0.1 就用，否则从 preferred+1 往上找。
- `check_deepseek(key) / check_siliconflow(key) / check_doubao(app_id, token) -> CheckResult`，`CheckResult = (ok: bool, message: str)`；全部接收可选参数 `opener=urllib.request.urlopen` 以便测试注入。
  - DeepSeek：`GET https://api.deepseek.com/user/balance`，Bearer；401 → 「key 不对」；`is_available` 为 False → 「余额不够了，先去充值」；成功 message 带余额。
  - 硅基流动：`POST https://api.siliconflow.cn/v1/embeddings` `{"model":"BAAI/bge-m3","input":"你好"}`；401/403 → 「key 不对」。
  - 豆包：照配置包 `astrbot_plugin_seed_audio_tts/main.py::_request` 的 headers/body 念「你好呀」；返回体含 `45000030` 或 `not granted` → 「还没领试用：到豆包语音旧版控制台 API 服务中心点『试用』」；401/403 → 「APP ID 或 Access Token 不对」；成功时额外返回音频 base64（`check_doubao` 返回 `(ok, message, audio_b64)` 三元组，试听复用）。
  - 网络异常统一 → 「连不上 xxx，检查一下网络」。

- [ ] Step 1: 写测试：`test_path_rules`（`D:\大肥鱼`、`D:\Da Fei` 报错，`relative` 报错，临时目录通过）、`test_free_port_skips_busy`（先占一个端口，返回值不同且可 bind）、`test_deepseek_invalid_key`/`test_deepseek_no_balance`/`test_doubao_not_granted`/`test_network_error`（fake opener 返回预设 status/body 或抛 `URLError`）。
- [ ] Step 2: 运行失败。 Step 3: 实现。 Step 4: 通过。 Step 5: commit。

### Task 3: personalize（个性化 + answers）

**Files:** Create `app/personalize.py`, `tests/test_personalize.py`

**Interfaces — Produces:**
- `DEFAULT_NAME = "大肥鱼"`；`VOICE_PRESETS: list[tuple[str, str]]`（3 个：甜妹/御姐/元气少年，描述文案自定）。
- `default_profile(pack_dir: Path) -> dict`：`{"name", "persona", "voice", "memes": {"blue-fat-fish-meme-pack": True, "deepseek-fanart-meme-pack": True}}`，persona 读 `persona/大肥鱼.md`，voice 读 `settings/pack.json` 的 `voice.doubao.persona`。
- `apply_profile(pack_dir: Path, profile: dict) -> None`：写回 `persona/大肥鱼.md`（名字替换：把文本中的 `大肥鱼` 换成新名字；若无 `{OWNER_QQ}` 则末尾追加「主人的 QQ 号是 {OWNER_QQ}。」）、`pack.json` 的 `voice.doubao.persona` 和 `meme_packs[*].use`；至少一套表情包为 True，否则 `ValueError`。
- `write_answers(path: Path, *, base_url, username, password, owner_qq, secrets_dir: Path, sf_key, app_id, token) -> None`：写 import 工具的回答文件，`memory="2"`、`voice="3"`；password / siliconflow_key / volc_access_token 用 `*_file` 指向 `secrets_dir` 下的临时文件。

- [ ] Step 1: 测试 `test_default_profile_reads_pack`、`test_rename_and_voice_written`、`test_owner_placeholder_restored`、`test_at_least_one_meme_pack`、`test_answers_have_no_plain_secrets`（answers.json 原文不含 key）。用 `vendor/dafeiyu-config-pack` 复制到临时目录做夹具。
- [ ] Step 2–5: 失败 → 实现 → 通过 → commit。

### Task 4: configs（启动前写好的配置）

**Files:** Create `app/configs.py`, `tests/test_configs.py`

**Interfaces — Produces:**
- `new_secrets() -> dict`：`{"dashboard_password", "onebot_token", "napcat_webui_token", "jwt_secret"}`。
- `hash_dashboard_password(pw) -> (pbkdf2: str, md5: str)`：与 AstrBot `astrbot/core/utils/auth_password.py` 相同算法（`pbkdf2_sha256$600000$<16字节salt hex>$<hex>`）。
- `write_astrbot_config(data_dir: Path, *, ports: dict, secrets: dict, deepseek_key: str) -> None`：写 `data_dir/cmd_config.json`；若已存在则只合并下列键，不覆盖其他内容：
  - `dashboard`: `username="dafeiyu"`, `pbkdf2_password`, `password`(md5), `password_storage_upgraded=True`, `password_change_required=False`, `jwt_secret`, `host="127.0.0.1"`, `port=ports["dashboard"]`
  - `platform`: 一项 `{"id":"dafeiyu","type":"aiocqhttp","enable":True,"ws_reverse_host":"127.0.0.1","ws_reverse_port":ports["onebot"],"ws_reverse_token":secrets["onebot_token"]}`（按 id 替换）
  - `provider_sources`: `{"id":"deepseek_source","provider":"deepseek","type":"openai_chat_completion","provider_type":"chat_completion","enable":True,"key":[deepseek_key],"api_base":"https://api.deepseek.com/v1","timeout":120,"proxy":"","custom_headers":{}}`
  - `provider`: `{"id":"deepseek/deepseek-flash","provider_source_id":"deepseek_source","model":"deepseek-flash","modalities":["text","image","tool_use"],"custom_extra_body":{},"enable":True}`
  - `agent_runner.config.model.provider_id` 与 `provider_settings.default_image_caption_provider_id` = `"deepseek/deepseek-flash"`
  - `config_version: 5`
- `write_napcat_config(workdir: Path, *, ports, secrets) -> None`：`workdir/config/webui.json` `{"host":"127.0.0.1","port":ports["napcat_webui"],"token":...}`；`workdir/config/onebot11.json` 含一个 `websocketClients` 项：`{"name":"AstrBot","enable":True,"url":"ws://127.0.0.1:<onebot>/ws","messagePostFormat":"array","reportSelfMessage":False,"reconnectInterval":1000,"token":...,"debug":False,"heartInterval":1000}`，其他 network 列表为空、`musicSignUrl:""`、`enableLocalFile2Url:False`、`parseMultMsg:False`。（NapCat 登录后若无 `onebot11_<QQ>.json` 会读这个默认文件并另存，见 napcat.mjs 的 `read()`。）同时把 `onebot11_*.json` 里已有的 websocketClients 也改成同样内容（换端口重装时用）。
- `pick_ports() -> dict`：`{"dashboard": free_port(6185), "onebot": free_port(6199), "napcat_webui": free_port(6099)}`，三者互不相同。

- [ ] Step 1: 测试 `test_password_hash_matches_astrbot`（用同算法验证：从 pbkdf2 串取 salt 重算 digest 相等；md5 相等）、`test_astrbot_config_fields`、`test_merge_keeps_other_keys`（已有 `{"foo":1}` 保留）、`test_ports_consistent`（ws URL 端口 == 平台端口、token 一致）、`test_pick_ports_distinct`。
- [ ] Step 2–5: 失败 → 实现 → 通过 → commit。

### Task 5: services（进程 + NapCat 登录）

**Files:** Create `app/services.py`, `tests/test_services.py`

**Interfaces — Consumes:** `store.State`, `store.redact`。**Produces:**
- `Layout(root: Path)`：属性 `python = root/"runtime/python/python.exe"`、`pythonw`、`astrbot = root/"astrbot"`、`napcat = root/"napcat"`、`napcat_work = root/"napcat-data"`、`pack = root/"pack"`、`logs = root/"logs"`。
- `start_astrbot(layout, state) -> int` / `start_napcat(layout, state, quick_qq: str | None) -> int`：`subprocess.Popen`，`creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP`，stdout/stderr 追加到 `logs/astrbot.log` / `logs/napcat.log`；AstrBot：`cwd=layout.astrbot`，`env` 加 `ASTRBOT_ROOT=<astrbot>`、`PYTHONUTF8=1`，命令 `python main.py`；NapCat：`node.exe index.js [-q QQ]`，`cwd=layout.napcat`，env `NAPCAT_WORKDIR=<napcat_work>`。pid 记到 `state.data["pids"]`。
- `stop_all(state) -> None`：对记录的 pid `taskkill /PID <pid> /T /F`，清空 pids。
- `is_running(pid) -> bool`（`tasklist /FI "PID eq n"`）。
- `wait_http(url, timeout_s) -> bool`。
- `class NapCatWeb(base_url, token, opener=urlopen)`：`login()`（POST `/api/auth/login` `{"hash": sha256(token + ".napcat")}`，取 `data.Credential`，之后 `Authorization: Bearer <Credential>`）；`status() -> LoginStatus`；`qrcode_png(workdir) -> bytes | None`（读 `napcat_work/cache/qrcode.png`）；`refresh_qr()`（POST `/api/QQLogin/RefreshQRcode`）；`login_uin() -> str | None`（POST `/api/QQLogin/GetQQLoginInfo` 的 `uin`）。
- `LoginStatus = Literal["starting","need_scan","scanned","online","offline","error"]`：由 `/api/QQLogin/CheckLoginStatus` 映射：`isLogin`→online；`isOffline`→offline；`loginPhase=="qrcode_scanned"`→scanned；`qrcodeurl` 非空→need_scan；`loginError` 非空→error；连不上→starting。

- [ ] Step 1: 测试 `test_login_hash`（Authorization 头格式）、`test_login_status_maps_phases`（fake opener 依次给出各种 CheckLoginStatus 返回）、`test_start_command_line`（monkeypatch `subprocess.Popen` 捕获 argv/env/cwd/flags，验证 `-q` 只在 quick_qq 时出现、env 带 NAPCAT_WORKDIR）。
- [ ] Step 2–5: 失败 → 实现 → 通过 → commit。

### Task 6: installer（断点续装流水线）+ 导入工具的结果输出

**Files:** Create `app/installer.py`, `tests/test_installer.py`；Copy 配置包到 `vendor/dafeiyu-config-pack/`（来源 `D:\Coding\test\dafeiyu-astrbot\.tmp\dafeiyu-config-pack`，提交 `8c01b03`，不带 `.git`）；Modify `vendor/dafeiyu-config-pack/tools/import_dafeiyu.py`：加 `--result-json <path>`，在 `main()` 末尾把 `results` 写成 `[{"name":..., "ok":...}]`；`sys.exit(msg)` 的失败路径也写 `{"fatal": msg}`（包一层 try/except SystemExit）。

**Interfaces — Consumes:** Task 1–5 全部。**Produces:**
- `STEPS: list[Step]`，`Step = (key: str, title: str, fn: Callable[[Ctx], None])`，顺序与标题（第 4 页清单显示用）：
  1. `extract` 「解压运行环境和程序」：`<root>/7z.exe x payload.7z -o<install> -y`（7z 在向导所在目录），解完写 `<install>/payload.ok`；已存在则跳过。
  2. `ports` 「分配端口」：`pick_ports()`、`new_secrets()` 存 state（secrets 用 set_secret）。
  3. `configs` 「写入配置」：`write_astrbot_config`、`write_napcat_config`。**要等第 2 页填完**：`ctx.wait_for("keys")`。
  4. `astrbot` 「启动 AstrBot」：`start_astrbot`，`wait_http(dashboard, 300)`。
  5. `napcat` 「启动 NapCat」：`start_napcat(quick_qq=None)`，`wait_http(webui, 120)`。
  6. `import` 「装上大肥鱼（人设、插件、语音、记忆）」：`ctx.wait_for("profile")` 后 `apply_profile` + `write_answers`，运行 `<python> -X utf8 pack/tools/import_dafeiyu.py --answers a.json --result-json r.json`，输出进 `logs/import.log`（redact）；结果任何项 ok=False → 步骤失败，message 列出失败项名称；secrets 临时文件 `finally` 删除。
  7. `shortcut` 「放桌面快捷方式」：Task 9 的 `create_shortcut`。
  8. `login` 「等你扫码」：等 `NapCatWeb.status()=="online"`，记 `state.data["qq"] = login_uin()`。
- `class Installer(root: Path, state: State)`：`start()` 起后台线程；`progress() -> dict`：`{"steps":[{"key","title","status":"todo|doing|done|error","message"}], "running": bool}`；`provide(name: str, value: dict)` 解除 `wait_for`；`retry()` 从失败步骤重新跑。每步成功后 `state.mark_done(key)` 并 save；已 done 的步骤跳过。异常 → 该步 `error`，message 为 `redact(str(e))` 的人话（`InstallError(user_message)` 用于预期错误，其他异常统一「出了点问题，点重试；还不行把 logs 文件夹发给 UP 主」），完整 traceback 只写 `logs/installer.log`。

- [ ] Step 1: 测试 `test_resume_skips_done_steps`（用替身 STEPS，两步 done 时只跑第三步）、`test_error_then_retry`（第一次抛 InstallError，progress 显示 error+message；retry 后 done）、`test_wait_for_blocks_until_provide`、`test_import_result_json`（在 vendor 导入工具上跑 `--answers` 指向不存在的后台地址，`--result-json` 写出 `fatal`）。
- [ ] Step 2–5: 失败 → 实现 → 通过 → commit。

### Task 7: server + main（本机服务和窗口）

**Files:** Create `app/server.py`, `app/main.py`, `tests/test_server.py`

**Interfaces — Produces（全部 JSON，除静态文件外都要求 header `X-Token` 或 query `token` 等于启动时随机 token，否则 403）：**
- `GET /` → `web/index.html`（`?token=` 写入页面）；`GET /static/*`。
- `GET /api/info` → `{"mode":"install|status","default_path","installed": bool, "profile": default_profile(...), "voice_presets": [...]}`
- `POST /api/path` `{"path"}` → `{"ok","message"}`（check_install_path；ok 时建 State 并 `Installer.start()`）
- `POST /api/check` `{"field":"deepseek|siliconflow|doubao","value"...}` → `{"ok","message"}`
- `POST /api/keys` `{"owner_qq","deepseek","siliconflow","app_id","token"}` → 全部再查一次，全 ok 才 `set_secret` 并 `installer.provide("keys")`
- `POST /api/preview_voice` `{"voice"}` → `{"ok","message","audio_b64"}`（用已存的豆包凭证，念「你好呀，我是<名字>」）
- `POST /api/profile` `{name, persona, voice, memes, extra_images: [base64...]}` → provide("profile")；自定义图片存 `<install>/my-memes/`，由 import 步骤之后调用 meme_manager 上传（接口名由实现方查 meme_manager 源码，查不到就先只存文件并在完成页提示「自己的图在后台表情包管理里导入」——标 `shortcut:`）。
- `GET /api/progress` → `Installer.progress()`
- `POST /api/retry`
- `GET /api/qr` → png 字节（无则 404）；`GET /api/login` → `{"status": LoginStatus}`
- `GET /api/done` → `{"dashboard_url","username","password"}`（同时写 `我的信息.txt`）
- 状态页：`POST /api/start`、`POST /api/stop`、`GET /api/status` → `{"astrbot": bool, "napcat": bool, "login": LoginStatus}`
- `POST /api/ping`：心跳。`main.py` 中，安装未在跑且 20 秒无心跳 → 退出进程。
- `main.py`：`python main.py [--status] [--root <install>]`；随机端口起 `ThreadingHTTPServer(("127.0.0.1", port))`；找 Edge（`%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe`、`%ProgramFiles%\...`），`msedge --app=<url> --window-size=980,720 --user-data-dir=<root>/.edge`；找不到用 `webbrowser.open`。单实例：`<root>/.wizard.lock` 记端口，已在跑就只打开窗口。

- [ ] Step 1: 测试 `test_token_required`（无 token 403）、`test_path_endpoint_rejects_chinese`、`test_keys_requires_all_ok`（替换 checks 为 fake）、`test_progress_shape`。用真 `ThreadingHTTPServer` 起在随机端口测。
- [ ] Step 2–5: 失败 → 实现 → 通过 → commit。

### Task 8: 向导页面（6 页 + 状态页）

**Files:** Create `app/web/index.html`, `app/web/app.css`, `app/web/app.js`, `app/web/img/`（从 `D:\Coding\test\dafeiyu-astrbot\assets\avatars\v3-20261025\` 复制 `01_招手微笑.png`、`03_抱鲸鱼.png`、`10_安静微笑.png`、`14_夜间陪伴.png`，重命名为 `wave.png hug.png smile.png night.png`，可缩到宽 480px 以内）

- 单页应用，`app.js` 按 `/api/info.mode` 进入安装向导或状态页；所有请求带 token；每 3 秒 `POST /api/ping`。
- 视觉：主色 DeepSeek 蓝 `#4D6BFE`，浅蓝渐变背景，白色圆角卡片（16px），左侧立绘、右侧内容；顶部步骤条 1–6；系统字体 `"Microsoft YaHei UI", "PingFang SC", sans-serif`；按钮圆角、悬停加深；✅/❌/⏳ 用 CSS 圆点或 emoji。
- 页面内容按设计文档 5.1；「去哪申请」是可展开的说明块，文案取自教程第 4 章、9.2 节（DeepSeek：platform.deepseek.com → 充值 → API keys；豆包：旧版控制台 `console.volcengine.com/speech/app`、先点「试用」别点「开通」、不是火山方舟的 key、Secret Key 不要；硅基流动：siliconflow.cn → API 密钥 → 新建）。外链用 `target=_blank`。
- 第 2 页：输入框失焦即 `POST /api/check`；豆包需两个都填后再查；底部细进度条显示 `/api/progress` 当前步骤。
- 第 4 页：步骤清单；出错显示 message + 「重试」。
- 第 5 页：`<img src="/api/qr?token=..&t=时间戳">` 每 3 秒刷新；`/api/login` 为 scanned 显示「扫到了，在手机上点确认」；online 进第 6 页。
- 第 6 页、状态页：照设计文档。状态页显示在线状态 + 「让她上线 / 让她下线」+「改设置」（复用第 2、3 页组件，提交后 `installer` 重跑 `configs`、`import` 两步并提示在 QQ 发 `/reset`）。

- [ ] Step 1: 起 `app/main.py`（用 fake Installer 环境变量 `DAFEIYU_FAKE=1`：步骤每 2 秒完成一个，二维码返回 `img/smile.png`，登录 10 秒后 online），在浏览器走完 6 页，截图核对布局。
- [ ] Step 2: commit `feat: wizard UI`。

### Task 9: 快捷方式与状态模式

**Files:** Create `app/shortcut.py`；Modify `app/installer.py`（shortcut 步骤）、`app/server.py`（start/stop/status）。

**Interfaces — Produces:** `create_shortcut(target: Path, args: str, name: str, icon: Path | None) -> Path`：用 `powershell -NoProfile -Command` + `WScript.Shell.CreateShortcut` 在 `[Environment]::GetFolderPath('Desktop')` 写 `启动大肥鱼.lnk`，目标 `runtime\python\pythonw.exe`，参数 `app\main.py --status --root <install>`，参数用单引号转义（路径已保证 ASCII 无空格）。
- 状态模式「让她上线」：`start_astrbot` → `start_napcat(quick_qq=state.data.get("qq"))`；快速登录失败（status 为 need_scan）就在状态页显示二维码。
- NapCat 巡检：状态页打开期间每 60 秒查一次 AstrBot 日志里最近是否有 `适配器已连接` 之后的断开；简化为：`login=="online"` 但 AstrBot `/api/v1/...` 平台状态不在线时重启 NapCat（`shortcut:` 若接口难找就先不做，记在 README 风险里）。

- [ ] Step 1: 测试 `test_shortcut_command_escapes`（只测生成的 PowerShell 命令文本）。
- [ ] Step 2–5: 失败 → 实现 → 通过 → commit。

### Task 10: 打包脚本

**Files:** Create `build/build.py`, `build/sfx_config.txt`, `build/README.md`

**Interfaces:** `python build/build.py [--skip-download] [--out dist]`，在维护者机器（有网、Windows）运行，全部中间文件放 `.tmp/build/`。版本常量写在文件顶部：
`PYTHON = "3.12.10"`（python.org `python-3.12.10-embed-amd64.zip`）、`ASTRBOT = "4.29.3"`（已有 `downloads/astrbot-src.zip`、`downloads/astrbot.zip` 为 dist）、`NAPCAT = "v4.18.41"`（`downloads/napcat-node.zip`）、`PYPI = "https://mirrors.aliyun.com/pypi/simple/"`、7-Zip：`7zr.exe` 与含 `7zSD.sfx` 的 `lzma2501.7z`（7-zip.org）。

步骤：
1. 组装 `stage/`：`runtime/python`（embed zip 解压，改 `python312._pth` 加 `Lib\site-packages` 和 `import site`，用 `get-pip.py` 装 pip）、`astrbot/`（源码 + `data/dist` 来自 dist zip）、`napcat/`（Node zip 解压）、`pack/`（vendor 副本）、`app/`。
2. `runtime/python/python.exe -m pip install -i PYPI -r astrbot/requirements.txt`。
3. 构建期跑一次：`write_astrbot_config`（假 key `sk-build`）+ `start_astrbot`，等后台起来，运行导入工具（answers：`memory="0"`、`voice="3"`、假豆包凭证、owner `10000`），让它从插件市场装插件、装插件依赖、下表情包；停掉。
4. 清理：删 `astrbot/data/cmd_config.json`、`astrbot/data/*.db`、`astrbot/data/temp`、`astrbot/data/logs`、`__pycache__`；插件配置里的假值保留（用户安装时导入工具会覆盖）。断言 stage 里搜不到 `sk-build`、`10000` 之外的敏感串（扫 json）。
5. `7zr a payload.7z stage/* -mx=7`；引导目录 `boot/`：`runtime/python`（不含 site-packages 里 AstrBot 依赖，只要标准库）、`app/`、`7zr.exe`、`payload.7z`；`7zr a boot.7z boot/*`；`copy /b 7zSD.sfx + sfx_config.txt + boot.7z dist/大肥鱼一键包.exe`；打印大小与 sha256。
   - `sfx_config.txt`：`;!@Install@!UTF-8!` / `Title="大肥鱼一键包"` / `RunProgram="runtime\python\pythonw.exe app\main.py"` / `;!@InstallEnd@!`。
   - Task 6 的 extract 步骤用 `7zr.exe`（与此一致）。

- [ ] Step 1: 测试 `test_sfx_config_text`、`test_scan_for_secrets_flags_key`（构建脚本里抽出 `scan_for_secrets(dir, needles) -> list[Path]` 并单测）。
- [ ] Step 2: 实现并 commit。整包构建由用户实测（需要联网和较长时间）。

### Task 11: 给用户的测试清单

**Files:** Create `docs/测试清单.md`：干净 Win10/Win11 各一台；正常流程；中途关窗再双击；占用 6185/6199/6099 后安装；填错每一种 key；被踢下线后状态页扫码；改设置后 `/reset`；杀毒拦截；路径含中文时的提示。每项写「怎么做 / 应该看到什么」。

- [ ] Step 1: 写文档并 commit。
