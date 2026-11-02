# 大肥鱼一键包

把 DeepSeek 养进 QQ，双击一个 exe 就能装好。

装完后，她就是一个住在 QQ 小号里的「大肥鱼」：有自己的性格，会发表情包、发语音、唱歌，记得你说过的话，隔一阵会主动找你，心情变了还会自己改 QQ 签名。

整个过程你只需要：**双击、填 key、扫码、等它装完**。不用装 Python，不用敲命令，也不用翻墙。

> ⚠️ **请用 QQ 小号**。机器人登录有被风控、封号的风险，别拿自己的大号去登。

## 下载

到 [Releases](../../releases/latest) 下载 `DaFeiYu-OneClick-v1.0.0.exe`（约 790 MB）。GitHub 不支持中文文件名，所以用了英文名，下载后改不改名都能用。

下载后可以核对 sha256，确认文件完整、没被改过：

```
e60514b76d0397d3a3f07d04631dd422b0ba72e46eb72a849e713f64d5896fdd
```

在 PowerShell 里运行 `Get-FileHash .\DaFeiYu-OneClick-v1.0.0.exe` 就能看到。

## 系统要求

- Windows 10 / 11，64 位
- 安装盘剩余空间 10 GB 以上
- 安装路径只能有英文和数字，不能有中文和空格（默认是 `D:\DaFeiYu`，没有 D 盘就用 `C:\DaFeiYu`）

## 开始之前要准备的

| 要准备什么 | 用来干什么 |
|---|---|
| 一个 QQ 小号，登在手机上 | 机器人登这个号，安装时用手机 QQ 扫码 |
| 你自己的 QQ 大号 | 她只认这个号是主人 |
| DeepSeek API key | 她用来想事情、说话 |
| 豆包语音的 APP ID 和 Access Token（火山引擎） | 发语音、唱歌 |
| 硅基流动 API key | 长期记忆 |

每一项在安装界面里都有「去哪申请」，点开有图文说明。

## 安装步骤

1. **双击下载的 exe**。弹出「Windows 已保护你的电脑」时，点「更多信息」→「仍要运行」。exe 没有数字签名，所以会有这个提示。
2. **选安装位置**，点「开始安装」。从这一步起后台就开始装了，你填后面几页的时候它一直在跑。
3. **填信息**：大号 QQ、DeepSeek key、豆包 APP ID 和 Access Token、硅基流动 key。每填一项就马上检查，打勾才算对。填错了会告诉你是 key 不对、余额不够，还是豆包没领试用。
4. **个性化**（可以跳过）：改名字、改性格、写一句话描述她的声音并试听、选表情包，也可以加你自己的图。
5. **等安装完成**。弹出 Windows 安全提示时点「允许」。
6. **扫码**：用登着**小号**的手机 QQ 扫窗口里的二维码。
7. **完成**。用大号私聊小号说一句「在吗」，她会回你。

桌面上会多一个「启动大肥鱼」。以后电脑重启了，双击它就能让她重新上线。登录没过期的话不用再扫码。

装到一半关了窗口、断电或者出错，再双击 exe 就会从断点接着装，填过的东西不用重填。

## 装好以后

双击「启动大肥鱼」会打开状态页，可以在里面：

- 看她现在的状态：在线、正在启动、需要扫码，或者出错了（会写原因）
- 让她下线
- 改设置：名字、性格、声音、表情包、各种 key，改完自动生效
- 打开 AstrBot 后台

后台的地址、账号、密码在安装目录的 `我的信息.txt` 里。

改了性格之后，在 QQ 上给她发 `/reset`，她就会用新的人格重新开始聊。

## 常见问题

**被杀毒软件拦了**
把安装目录加进杀毒软件的信任列表，然后再双击 exe 接着装。

**她不回话了 / 掉线了**
新注册的小号容易被风控踢下线。双击「启动大肥鱼」，状态页提示需要扫码的话，重新扫一次就好。

**端口被占用**
不用管。安装器会自己换一个没人用的端口。

**会不会动我电脑上原来的 QQ、AstrBot、NapCat？**
不会。一键包只用它自己目录里的那一套，原来装的东西一样都不碰。

**想卸载**
双击「启动大肥鱼」，在状态页里点「彻底卸载」，会删掉全部文件。

## 安全

- AstrBot 后台、NapCat 后台、OneBot 连接都只在本机（`127.0.0.1`）开放，局域网里的其他设备连不上。OneBot 连接带随机 Token。
- 你填的 key 用 Windows DPAPI 加密保存，日志里会把 key、Token、密码过滤掉。
- 她只认你填的那个大号是主人。发空间、改签名这类操作只听主人的。
- AstrBot 的「使用电脑能力」是关着的。

---

## 开发者说明

下面是给维护者看的。普通用户直接下载 exe 就行。

### 它是怎么做的

- **外壳**：7-Zip 自解压（SFX）。双击后先解出一个很小的引导部分（便携 Python + 向导程序），等用户选好安装位置，再把真正的大包解到那里。
- **向导**：用 Python 标准库写的本地 HTTP 服务，只绑 `127.0.0.1`、随机端口，URL 里带一次性 token。页面是纯 HTML/CSS/JS，用 Edge 的 `--app` 模式打开，所以没有地址栏，看着像个独立窗口。
- **机器人**：[AstrBot](https://github.com/AstrBotDevs/AstrBot) 4.29.3 跑在便携 Python 3.12.10 上，QQ 协议端用 [NapCat](https://github.com/NapNeko/NapCatQQ) v4.18.41 的自包含 Node 版。
- **大肥鱼的配置**：直接调用 [dafeiyu-config-pack](https://github.com/zexuanw958-svg/dafeiyu-config-pack) 自带的导入工具，没有另外重写。

### 目录结构

```
app/                向导和状态页程序
  main.py           入口：判断这次该开安装向导还是状态页，然后打开窗口
  server.py         本地 HTTP 服务和接口
  installer.py      后台安装流程，支持断点续装
  checks.py         检查 DeepSeek、豆包、硅基流动的 key
  configs.py        生成 AstrBot / NapCat 配置，挑空闲端口
  services.py       启动、停止 AstrBot 和 NapCat
  personalize.py    把名字、性格、声音、表情包写进配置包
  store.py          state.json 读写，key 用 DPAPI 加密
  shortcut.py       创建桌面快捷方式
  uninstall.py      卸载
  web/              前端页面和图片
build/
  build.py          打包脚本，产出 dist/大肥鱼一键包.exe
  sfx_config.txt    自解压模块的配置
vendor/dafeiyu-config-pack/   大肥鱼配置包（上游提交 8c01b03）
dev/fake_wizard.py  本地调界面用的假后端，不装任何东西
tests/              单元测试
docs/               设计文档、实现计划、测试清单
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

   便携 Python、get-pip、7-Zip 这些脚本会自己下载。依赖装的时候走阿里云 PyPI 镜像。

脚本的流程：组装目录 → 装依赖 → 起一个临时的 AstrBot，把插件和表情包提前下好 → 清掉构建过程中留下的密码、数据库、日志，然后扫一遍确认没有残留 → 压成 SFX。最后会输出 exe 的大小和 sha256。

中间文件都在 `.tmp/build/` 里。各组件的版本号写死在 `build/build.py` 开头，升级要手动改。

### 测试

```
python -m unittest discover -s tests -t .
```

要 Python 3.10 以上。真机测试按 [`docs/测试清单.md`](docs/测试清单.md) 来，在干净的 Win10 和 Win11 上各跑一遍。

调界面的时候不用真装，可以用假后端：

```
python dev/fake_wizard.py          # 加 --status 看状态页
```

## 致谢与第三方组件

- [AstrBot](https://github.com/AstrBotDevs/AstrBot)：机器人框架
- [NapCat](https://github.com/NapNeko/NapCatQQ)：QQ 协议端
- [dafeiyu-config-pack](https://github.com/zexuanw958-svg/dafeiyu-config-pack)：大肥鱼的人格、设置和插件（MIT）
- 一起装的 AstrBot 插件：group_chat_plus、LivingMemory、meme_manager、qzone、qqprofile、proactive_chat，版权归各自作者
- 表情包：蓝色大肥鱼表情包、DeepSeek 二创表情包，图片版权归各权利人

上面这些组件分别遵循各自的许可协议。本项目只供个人学习、交流使用。
