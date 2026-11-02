# 装 Breeze TTS 2 本地语音（Windows）。由上两层的「装Breeze语音.bat」调用，可以反复运行，下好的会跳过。
# 程序来自 github.com/HoppouAI/Breeze-TTS-2.cpp（Apache-2.0），模型来自 Hugging Face（只许研究和非商业使用）。
param([string]$Dir = "", [string]$Model = "")
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8

$Release = 'v0.1.3'
$ZipName = "breeze-tts-2-$Release-windows-x64-vulkan.zip"
$ZipUrl = "https://github.com/HoppouAI/Breeze-TTS-2.cpp/releases/download/$Release/$ZipName"
$ZipSha = 'd31feee5380e0609dc44b537d22cbe4f3cf8e93495756f3a580d8b448db79f7f'
$Models = @{
    'q8_0' = @{ File = 'breeze-tts-2-q8_0.gguf'; Bytes = 3568844480; Sha = 'a02bcc4b69b0601032727f8040c4942149b1b73aa0f69022fe5aaa6a8f0ef879' }
    'q4_k' = @{ File = 'breeze-tts-2-q4_k.gguf'; Bytes = 2538909376; Sha = '483418fbbb438f5f1c08dbe2b017e42db0f1d126765cd3f772d8622a30a4915c' }
}
# GitHub 加速站按顺序试，最后的空串是直连；挂了往这里补新的
$Accel = @('https://gh.dpik.top/', 'https://hk.gh-proxy.com/', 'https://ghfast.top/', 'https://gh-proxy.com/', '')
$HfHosts = @('https://hf-mirror.com', 'https://huggingface.co')
$Port = 8137

function Say($m) { Write-Host $m }
function Fail($m) { Write-Host "  ！$m" -ForegroundColor Yellow; exit 1 }

function Test-Server {
    try { $null = Invoke-WebRequest "http://127.0.0.1:$Port/v1/voices" -UseBasicParsing -TimeoutSec 3; return $true } catch { return $false }
}

function Get-VramGB {
    # 先问 nvidia-smi（N 卡最准），再读显卡驱动在注册表里记的显存（A 卡、I 卡）
    try {
        $mb = & nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>$null | Select-Object -First 1
        if ($mb) { return [math]::Round([double]$mb / 1024, 1) }
    } catch {}
    $best = 0
    $key = 'HKLM:\SYSTEM\ControlSet001\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}'
    Get-ChildItem $key -ErrorAction SilentlyContinue | ForEach-Object {
        $v = (Get-ItemProperty $_.PSPath -Name 'HardwareInformation.qwMemorySize' -ErrorAction SilentlyContinue).'HardwareInformation.qwMemorySize'
        if ($v -and [double]$v -gt $best) { $best = [double]$v }
    }
    return [math]::Round($best / 1GB, 1)
}

function Get-File($urls, $out, $bytes, $sha) {
    if ((Test-Path $out) -and (Get-FileHash $out -Algorithm SHA256).Hash -eq $sha.ToUpper()) { Say "  已下好，跳过"; return }
    foreach ($u in $urls) {
        Say "  从 $(([uri]$u).Host) 下载…"
        for ($i = 1; $i -le 4; $i++) {
            & curl.exe -L -f -s -S -C - --retry 3 --connect-timeout 20 -o $out $u
            if ((Test-Path $out) -and ((-not $bytes) -or (Get-Item $out).Length -ge $bytes)) { break }
        }
        if ((Test-Path $out) -and (Get-FileHash $out -Algorithm SHA256).Hash -eq $sha.ToUpper()) { Say "  下好了，校验通过"; return }
        Say "  这个地址没下完整，换一个"
        Remove-Item $out -ErrorAction SilentlyContinue
    }
    Fail "下载失败。过一会儿再双击一次，下了一半的会接着下。"
}

Say "装 Breeze TTS 2 本地语音"
Say ""

# ---------- 装到哪 ----------
if (-not $Dir) {
    $def = "$env:SystemDrive\breeze-tts"
    $Dir = Read-Host "装到哪（直接回车用 $def；路径别带中文和空格）"
    if (-not $Dir) { $Dir = $def }
}
$Dir = $Dir.Trim().Trim('"').TrimEnd('\')
if ($Dir -match '[^\x21-\x7E]') { Fail "路径里有中文或空格，换一个全英文的，比如 D:\breeze-tts" }
New-Item -ItemType Directory -Force "$Dir\bin", "$Dir\models", "$Dir\voices" | Out-Null

# ---------- 看显卡，挑模型 ----------
$gpus = (Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name }) -join '、'
$vram = Get-VramGB
Say "显卡：$gpus（显存约 $vram G）"
if (-not $Model) {
    if ($vram -ge 6) { $Model = 'q8_0' }
    else { $Model = 'q4_k' }
}
$m = $Models[$Model]
if (-not $m) { Fail "不认识的模型 $Model，只能是 q8_0 或 q4_k" }
if ($vram -lt 3) {
    Say "  ！显存不到 3G，会退到 CPU 上算，一句话可能要十几秒以上。能用，但不推荐。"
}
Say "用的模型：$Model（$([math]::Round($m.Bytes / 1GB, 1)) GB）"

# ---------- 下程序 ----------
Say ""
Say "▶ 下载 Breeze 程序（约 75 MB）"
$zip = "$Dir\$ZipName"
Get-File ($Accel | ForEach-Object { $_ + $ZipUrl }) $zip 0 $ZipSha
Expand-Archive -Force $zip "$Dir\bin"
$exe = Get-ChildItem -Recurse "$Dir\bin" -Filter breeze-server.exe | Select-Object -First 1 -ExpandProperty FullName
if (-not $exe) { Fail "解压后没找到 breeze-server.exe" }

# ---------- 下模型 ----------
Say ""
Say "▶ 下载模型（$([math]::Round($m.Bytes / 1GB, 1)) GB，网速慢要十几分钟，下断了再双击会接着下）"
$gguf = "$Dir\models\$($m.File)"
Get-File ($HfHosts | ForEach-Object { "$_/HoppouAI/Breeze-TTS-2.cpp/resolve/main/$($m.File)" }) $gguf $m.Bytes $m.Sha

# ---------- 启动脚本 ----------
$bat = "$Dir\start-breeze.cmd"
$lines = @(
    '@echo off',
    'title Breeze TTS - keep this window open',
    "cd /d `"$Dir`"",
    "`"$exe`" `"$gguf`" --host 127.0.0.1 --port $Port --voices-dir `"$Dir\voices`" --ws-port -1 --webui"
)
[IO.File]::WriteAllText($bat, ($lines -join "`r`n") + "`r`n", (New-Object Text.UTF8Encoding $false))

$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) '启动Breeze语音.lnk'))
$lnk.TargetPath = $bat; $lnk.WorkingDirectory = $Dir; $lnk.WindowStyle = 7; $lnk.Save()
Say ""
Say "桌面上放了「启动Breeze语音」，以后双击它就能开语音服务。"
$auto = Read-Host "开机自动启动 Breeze 吗？（直接回车是要，输入 n 不要）"
if ($auto -ne 'n') {
    $lnk = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Startup')) '启动Breeze语音.lnk'))
    $lnk.TargetPath = $bat; $lnk.WorkingDirectory = $Dir; $lnk.WindowStyle = 7; $lnk.Save()
    Say "  好，开机会自己起来（最小化的黑窗口，别关）"
}

# ---------- 启动并等它好 ----------
Say ""
if (Test-Server) {
    Say "Breeze 已经在运行了。换了模型的话，关掉那个黑窗口再双击桌面的「启动Breeze语音」。"
} else {
    Say "▶ 启动 Breeze（第一次要预热一会儿）"
    Start-Process -FilePath $bat -WorkingDirectory $Dir -WindowStyle Minimized
    $ok = $false
    for ($i = 0; $i -lt 90; $i++) { Start-Sleep 2; if (Test-Server) { $ok = $true; break } }
    if (-not $ok) { Fail "等了 3 分钟还没起来。看看任务栏里那个黑窗口有没有报错，截图发给帮你装的人。" }
}
Say ""
Say "装好了。浏览器打开 http://127.0.0.1:$Port 能看到 Breeze 自带的网页（英文的）。"
Say "下一步：双击配置包里的「做声音.bat」，给她做一个声音。"
