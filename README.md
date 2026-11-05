# 大肥鱼一键包

把 DeepSeek 养进 QQ，双击一个 exe 就能装好。

装完后，她就是住在你 QQ 里的「大肥鱼」：有自己的性格，会发表情包、发语音、唱歌，记得你说过的话，隔一阵会主动找你，心情变了还会自己改 QQ 签名。

整个过程你只需要：**双击、填 key、扫码（或绑定）、等它装完**。不用装 Python，不用敲命令，也不用翻墙。

> ⚠️ **用小号模式时请用 QQ 小号**。第三方客户端登录有被风控、封号的风险，别拿自己的大号去登。

## 下载

到 [Releases](../../releases/latest) 下载 `DaFeiYu-OneClick-v1.2.0.exe`（约 790 MB）。GitHub 不支持中文文件名，所以用了英文名，下载后改不改名都能用。

下载后可以核对 sha256，确认文件完整、没被改过：

```
fc3349717693b72d7e0f0a75a71316ec6f8ba81201e9404cc2646959e85f3d39
```

在 PowerShell 里运行 `Get-FileHash .\DaFeiYu-OneClick-v1.2.0.exe` 就能看到。

## 系统要求

- Windows 10 / 11，64 位
- 安装盘剩余空间 10 GB 以上
- 安装路径只能有英文、数字和符号，不能有中文和空格（默认是 `D:\DaFeiYu`，没有 D 盘就用 `C:\DaFeiYu`）

## 两种接入方式

安装第一页先选她怎么上 QQ：

| | QQ 小号（默认） | QQ 官方机器人 |
|---|---|---|
| 怎么登录 | 手机 QQ 扫码登录一个小号 | 用 QQ 开放平台的机器人 AppID 和 AppSecret |
| 功能 | 完整：聊天、表情包、语音、主动找你、改签名、发空间 | 基础聊天，QQ 空间这类小号功能用不了 |
| 风险 | 第三方登录，可能被风控或限制功能 | 走腾讯官方接口，不用登录小号 |
| 认主人 | 安装时填你的大号 QQ | 不用填，装完后用你的 QQ 私聊机器人完成绑定 |

拿不准就选小号模式。

## 开始之前要准备的

| 要准备什么 | 用来干什么 | 必须吗 |
|---|---|---|
| DeepSeek API key | 她用来想事情、说话 | 必须 |
| 你自己的 QQ 大号 | 她只认这个号是主人 | 必须（官方机器人模式装完再绑定） |
| 一个 QQ 小号，登在手机上 | 机器人登这个号，安装时用手机 QQ 扫码 | 小号模式需要 |
| QQ 机器人的 AppID 和 AppSecret（[QQ 开放平台](https://q.qq.com/)） | 机器人登录 | 官方机器人模式需要 |
| 豆包语音的 APP ID 和 Access Token（火山引擎） | 发语音、唱歌 | 可选，打开「语音与唱歌」才需要 |
| 硅基流动 API key | 长期记忆 | 可选，打开「长期记忆」才需要 |

每一项在安装界面里都有「去哪申请」，点开有一步步的说明。

## 安装步骤

1. **双击下载的 exe**。弹出「Windows 已保护你的电脑」时，点「更多信息」→「仍要运行」。exe 没有数字签名，所以会有这个提示。
2. **选接入方式和安装位置**，点「开始安装」。从这一步起后台就开始装了，你填后面几页的时候它一直在跑。
3. **填信息**：DeepSeek key，再加上大号 QQ（小号模式）或机器人的 AppID 和 AppSecret（官方机器人模式）。语音和长期记忆按需要打开，再填对应的 key。每填一项就马上检查，打勾才算对。填错了会告诉你是 key 不对、余额不够，还是豆包没领试用。
4. **个性化**（可以跳过）：改名字、改性格、写一句话描述她的声音并试听、选表情包，也可以加你自己的图。
5. **等安装完成**。弹出 Windows 安全提示时点「允许」。
6. **登录**：
   - 小号模式：用登着**小号**的手机 QQ 扫窗口里的二维码。
   - 官方机器人模式：点「复制指令」，用你的 QQ 私聊机器人把这行指令发过去，再回窗口点「我已发送，检查结果」。指令 10 分钟内有效。
7. **完成**。用大号私聊她说一句「在吗」，她会回你。

桌面上会多一个「启动大肥鱼」。以后电脑重启了，双击它就能让她重新上线。登录没过期的话不用再扫码。

装到一半关了窗口、断电或者出错，再双击 exe 就会从断点接着装，填过的东西不用重填。

### 官方机器人模式要先做的设置

在 [QQ 开放平台](https://q.qq.com/) 创建机器人后：

1. 在开发设置里复制 AppID 和 AppSecret。
2. 「开发基础设置」→「事件订阅与回调地址」选 **WebSocket**，不能选 Webhook。
3. 「开发基础设置」→「内部体验号码」里加上你自己的 QQ 号。

详细说明见 [AstrBot 接入指南](https://docs.astrbot.app/platform/qqofficial/websockets.html)。

## 装好以后

双击「启动大肥鱼」会打开状态页，可以在里面：

- 看她现在的状态：在线、正在启动、需要扫码，或者出错了（会写原因）
- 让她上线、暂停聊天，或者全部停止并退出
- 改名字、性格、声音、表情包，改各种 key，改完自动生效
- 退出小号或换一个号
- 打开 AstrBot 后台
- 遇到问题时点「检测与修复」

后台的地址、账号、密码在安装目录的 `我的信息.txt` 里。

改了性格之后，在 QQ 上给她发 `/reset`，她就会用新的人格重新开始聊。

## 常见问题

**被杀毒软件拦了**
把安装目录加进杀毒软件的信任列表，然后再双击 exe 接着装。

**她不回话了 / 掉线了**
新注册的小号容易被风控踢下线。双击「启动大肥鱼」，状态页提示需要扫码的话，重新扫一次就好。

**装不上、起不来，不知道哪里出了问题**
点「检测与修复」。它会在本机检查安装文件和最近一次的启动错误，能修的直接给你修复按钮。看不懂结果的话，可以点「补充解释（DeepSeek）」让 DeepSeek 帮你分析。只会发送固定的诊断码，不会发原始日志、聊天记录、路径和配置，发之前也会先问你。

**端口被占用**
不用管。安装器会自己换一个没人用的端口。

**会不会动我电脑上原来的 QQ、AstrBot、NapCat？**
不会。一键包只用它自己目录里的那一套，原来装的东西一样都不碰。

**想卸载**
双击「启动大肥鱼」，在状态页里点「彻底卸载」，会删掉全部文件。

## 安全

- AstrBot 后台、NapCat 后台、OneBot 连接都只在本机（`127.0.0.1`）开放，局域网里的其他设备连不上。OneBot 连接带随机 Token。
- 你填的 key 用 Windows DPAPI 加密保存，日志里会把 key、Token、密码过滤掉。
- 她只认你那个号是主人。发空间、改签名这类操作只听主人的。
- AstrBot 的「使用电脑能力」是关着的。
- 诊断不读聊天记录，也不会执行模型给出的命令。

## 许可和素材

本包是非商业的整合安装工具。AstrBot（AGPL-3.0）、NapCat、Python（PSF）和大肥鱼配置包（MIT）的版权与许可归各自的作者所有，完整许可证在安装目录的 `pack\LICENSE` 和 `astrbot\LICENSE`。

人设文字、立绘和表情包素材只供个人学习和自用。**请勿把本包或其中素材用于商业用途、二次售卖或再分发。** 素材的权利人如果希望移除，请提 Issue 联系。

---

## 开发者说明

下面是给维护者看的。普通用户直接下载 exe 就行。

### 它是怎么做的

- **外壳**：7-Zip 自解压（SFX）。双击后先解出一个很小的引导部分（便携 Python + 向导程序），等用户选好安装位置，再把真正的大包解到那里。
- **向导**：用 Python 标准库写的本地 HTTP 服务，只绑 `127.0.0.1`、随机端口，URL 里带一次性 token。页面是纯 HTML/CSS/JS，用 Edge 的 `--app` 模式打开，所以没有地址栏，看着像个独立窗口。
- **机器人**：[AstrBot](https://github.com/AstrBotDevs/AstrBot) 4.29.3 跑在便携 Python 3.12.10 上。小号模式的 QQ 协议端是 [NapCat](https://github.com/NapNeko/NapCatQQ) v4.18.41 的自包含 Node 版；官方机器人模式直接用 AstrBot 自带的 QQ 官方 WebSocket 适配器。
- **大肥鱼的配置**：直接调用 [dafeiyu-config-pack](https://github.com/zexuanw958-svg/dafeiyu-config-pack) 自带的导入工具，没有另外重写。

### 目录结构

```
app/                  向导和状态页程序
  main.py             入口：判断这次该开安装向导还是状态页，然后打开窗口
  server.py           本地 HTTP 服务和接口
  installer.py        后台安装流程，支持断点续装
  checks.py           检查安装路径，以及 DeepSeek、豆包、硅基流动的 key
  configs.py          生成 AstrBot / NapCat 配置，挑空闲端口
  services.py         启动、停止 AstrBot 和 NapCat
  personalize.py      把名字、性格、声音、表情包写进配置包
  store.py            state.json 读写，key 用 DPAPI 加密
  shortcut.py         创建桌面快捷方式
  official_binding.py QQ 官方机器人模式：私聊绑定主人时的本机确认
  diagnostics.py      本机诊断和受限修复
  safe_logging.py     排障日志：先脱敏再限量写入
  log_capture.py      独立的日志读取进程，向导退出后继续收集服务输出
  fileio.py           先完整写临时文件再替换，避免配置写坏
  uninstall.py        卸载（实际删除由 uninstall.ps1 完成）
  web/                前端页面和图片
build/
  build.py            打包脚本，产出 dist/大肥鱼一键包.exe
  sfx_config.txt      自解压模块的配置
vendor/dafeiyu-config-pack/   大肥鱼配置包（上游提交 8c01b03）
dev/fake_wizard.py    本地调界面用的假后端，不装任何东西
tests/                单元测试
```

### 本地调界面

```
python dev/fake_wizard.py            # 走一遍安装向导
python dev/fake_wizard.py --status   # 看状态页
```

然后打开它打印出来的地址。假后端不会装任何东西：key 里带 `bad` 就算填错，进入扫码页 8 秒后自动登录成功。

### 跑测试

测试要在 **Windows** 上跑，有些用例依赖 `msvcrt` 和 PowerShell。

```
python -m unittest discover -s tests -t .
node --test tests/test_web.cjs
```

### 打包

要在 **Windows** 上跑，要联网，要 Python 3.12+。

1. 先把下面三个文件放进 `downloads/`：
   - `astrbot-src.zip`：AstrBot 4.29.3 源码
   - `astrbot.zip`：AstrBot 后台页面（dist）
   - `napcat-node.zip`：NapCat.Shell.Windows.Node v4.18.41
2. 运行：

   ```
   python build/build.py
   ```

   便携 Python、get-pip、7-Zip 这些脚本会自己下载，已经下载过的话可以加 `--skip-download`。依赖走阿里云 PyPI 镜像。输出目录默认是 `dist/`，可以用 `--out` 改。中间文件都在 `.tmp/build/`。

脚本的流程：组装目录 → 装依赖 → 起一个临时的 AstrBot，把插件和表情包提前下好 → 清掉构建过程中留下的密码、数据库、日志，然后扫一遍确认没有残留 → 压成 SFX。最后会输出 exe 的大小和 sha256。

### 发布

把 `dist/大肥鱼一键包.exe` 改名为 `DaFeiYu-OneClick-v<版本>.exe`，传到 GitHub Release，再把本文「下载」一节的文件名和 sha256 更新一下。exe 不进 git 仓库。
