$ErrorActionPreference = 'Stop'
$report = Join-Path $PSScriptRoot 'uninstall.log'
function Write-Log([string]$message) {
    foreach ($entry in @(@($PSScriptRoot, '<cleanup>'), @($data.root, '<install>'), @($data.home, '<app-data>'))) {
        if ($entry[0]) { $message = [regex]::Replace($message, [regex]::Escape($entry[0]), $entry[1], 'IgnoreCase') }
    }
    $message = [regex]::Replace($message, '(?i)[A-Z]:\\Users\\[^\\\s"'']+', '<user>')
    $message = [regex]::Replace($message, '(?i)((?:napcat|onebot11)_)[1-9][0-9]{4,11}(?=\.json\b)', '$1<account>')
    Add-Content -LiteralPath $report -Value ((Get-Date -Format 'yyyy-MM-dd HH:mm:ss ') + $message) -Encoding UTF8
}

function Remove-Tree([string]$target) {
    if (-not [IO.Directory]::Exists($target) -and -not [IO.File]::Exists($target)) { return }
    $attributes = [IO.File]::GetAttributes($target)
    $directory = ($attributes -band [IO.FileAttributes]::Directory) -ne 0
    if ($directory) {
        # 不穿过 junction / symlink 删除链接外的用户文件。
        if (($attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) {
            foreach ($child in [IO.Directory]::EnumerateFileSystemEntries($target)) { Remove-Tree $child }
        }
        [IO.Directory]::Delete($target)
    } else {
        if (($attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) {
            [IO.File]::SetAttributes($target, [IO.FileAttributes]::Normal)
        }
        [IO.File]::Delete($target)
    }
}

try {
    $data = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($target in $data.paths) {
        if (-not [IO.Path]::IsPathRooted($target) -or [IO.Path]::GetFullPath($target).TrimEnd('\') -eq [IO.Path]::GetPathRoot($target).TrimEnd('\')) {
            throw "拒绝清理不安全的路径：$target"
        }
    }
    Write-Log 'READY 等待程序退出'
    [IO.File]::WriteAllText((Join-Path $PSScriptRoot 'ready'), 'ready')
    $deadline = (Get-Date).AddSeconds(15)
    while (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'go'))) {
        if ((Get-Date) -gt $deadline) { throw '卸载准备未完成，未清理文件' }
        Start-Sleep -Milliseconds 100
    }
    foreach ($processId in (@($data.parent_pid) + @($data.window_pids) | Select-Object -Unique)) {
        $running = Get-Process -Id $processId -ErrorAction SilentlyContinue
        if ($running) { $running | Wait-Process -Timeout 45 -ErrorAction Stop }
    }
    # 只关闭一键包专用的 Edge 配置目录，保留用户的正常浏览器窗口。
    try {
        if (@($data.profiles).Count -gt 0) {
            foreach ($browser in (Get-CimInstance Win32_Process -Filter "Name='msedge.exe'")) {
                foreach ($profile in $data.profiles) {
                    $pattern = '(?:^|\s)"?--user-data-dir="?' + [regex]::Escape($profile) + '"?(?:\s|$)'
                    if ($browser.CommandLine -match $pattern) {
                        Stop-Process -Id $browser.ProcessId -Force -ErrorAction SilentlyContinue
                        break
                    }
                }
            }
        }
    } catch { Write-Log ("关闭程序窗口时遇到问题：" + $_.Exception.Message) }
    $attempts = if ($data.attempts) { [int]$data.attempts } else { 30 }
    $pending = @($data.paths)
    for ($attempt = 0; $attempt -lt $attempts -and $pending.Count -gt 0; $attempt++) {
        $next = @()
        foreach ($target in $pending) {
            try {
                Remove-Tree $target
                if (Test-Path -LiteralPath $target) { throw "文件仍然存在：$target" }
                Write-Log ("REMOVED " + $target)
            } catch {
                $next += $target
                Write-Log ("RETRY " + $target + ': ' + $_.Exception.Message)
            }
        }
        $pending = @($next)
        if ($pending.Count -gt 0) { Start-Sleep -Seconds 1 }
    }
    if ($pending.Count -gt 0) { throw ("仍有文件被占用或无权限：" + ($pending -join ', ')) }
    Write-Log 'SUCCESS 卸载完成'
    Remove-Item -LiteralPath (Join-Path $PSScriptRoot 'manifest.json'), (Join-Path $PSScriptRoot 'ready'), (Join-Path $PSScriptRoot 'go'), $PSCommandPath -Force
    exit 0
} catch {
    Write-Log ("FAILED " + $_.Exception.Message)
    exit 1
}
