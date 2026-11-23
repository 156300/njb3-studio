<#
.SYNOPSIS
    Creative MediaSource 5 + NJB2/NJB3 插件安装辅助（Windows 10/11）。

.DESCRIPTION
    这两个安装包是 2006 年左右的 InstallShield "Web Installer"，启动时会运行
    Creative 产品检测（CTDetctu.exe）。如果系统里没有识别到 Creative 设备
    （NJB3 = USB VID_041e&PID_4101），就会弹出"检测不到支持的产品"并终止。

    因此本脚本在安装前先做设备预检，并给出三种运行方式：
      - 默认：静默演练（不真装，只打印命令）
      - -Execute：静默安装（加 /s）
      - -Execute -Interactive：交互式安装，可在弹窗出现时插入设备并点"重试"
      - -Execute -Force：跳过设备预检强制安装（仍可能被产品检测弹窗拦截）

    另外仍保留：校验安装器、复制到无中文/空格的中间目录、装后验证。

.PARAMETER SourceDir
    含 CMS5_*.exe 的目录，默认原始素材 win10 目录（只读访问）。

.PARAMETER StageDir
    中间目录，默认 $env:TEMP\NJB3-CMS5。

.PARAMETER Execute
    真正运行安装器。不加则仅演练。

.PARAMETER Interactive
    与 -Execute 配合：用交互界面安装而非静默，便于在检测弹窗出现时插入设备点"重试"。

.PARAMETER Force
    跳过设备预检，强制继续（不推荐；产品检测弹窗仍可能出现）。

.EXAMPLE
    .\install_cms5.ps1                          # 演练
    .\install_cms5.ps1 -Execute                 # 静默安装
    .\install_cms5.ps1 -Execute -Interactive    # 交互安装（可点"重试"）
    .\install_cms5.ps1 -Execute -Force          # 跳过设备预检强制装
#>
[CmdletBinding()]
param(
    [string]$SourceDir = 'G:\Drivers\jukebox3\win10',
    [string]$StageDir  = (Join-Path $env:TEMP 'NJB3-CMS5'),
    [switch]$Execute,
    [switch]$Interactive,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

function Write-Step($msg) { Write-Host "`n[*] $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "[+] $msg" -ForegroundColor Green }
function Write-Warn2($msg){ Write-Host "[!] $msg" -ForegroundColor Yellow }
function Write-Err($msg)  { Write-Host "[-] $msg" -ForegroundColor Red }

# 防御：若被错误地把 '-Execute' 当位置参数传进来，恢复默认源目录并视为需要真装
if ($SourceDir -like '-*') {
    Write-Warn2 "检测到 SourceDir 被误设为开关值 '$SourceDir'，已恢复默认路径并按 -Execute 处理。"
    $SourceDir = 'G:\Drivers\jukebox3\win10'
    $Execute = $true
}

$mainExe   = Join-Path $SourceDir 'CMS5_PCAPP_LB_5_10_38.exe'
$pluginExe = Join-Path $SourceDir 'CMS5_NJB2_PCAPP_LB_5_00_22.exe'

Write-Step "校验安装器"
foreach ($f in @($mainExe, $pluginExe)) {
    if (-not (Test-Path $f)) { Write-Err "找不到：$f"; exit 1 }
    $item = Get-Item $f
    if ($item.Length -lt 1000000) { Write-Warn2 "$($item.Name) 体积异常：$($item.Length) 字节" }
    # PE machine: 0x014C = i386
    $b = [IO.File]::ReadAllBytes($f)
    $pe = [BitConverter]::ToInt32($b, 0x3c)
    $machine = if ($pe -gt 0 -and $pe + 6 -lt $b.Length) { [BitConverter]::ToUInt16($b, $pe + 4) } else { 0 }
    Write-Ok ("{0}  {1:N0} 字节  PE=machine 0x{2:X4} ({3})" -f $item.Name, $item.Length, $machine, $(if ($machine -eq 0x014C) { 'x86，Win11 通过 WOW64 运行' } else { '未知' }))
}

# ---------- 设备预检：这是"检测不到支持的产品"的根因 ----------
Write-Step "设备预检（CMS5 Web Installer 会强制扫描 Creative 设备）"
$creativDevs = @(Get-PnpDevice -ErrorAction SilentlyContinue | Where-Object {
    $_.InstanceId -match 'VID_041E' -or $_.FriendlyName -match 'Jukebox|Nomad|Creative'
})
if ($creativDevs.Count -gt 0) {
    Write-Ok "检测到 $($creativDevs.Count) 个 Creative/NJB 相关设备："
    $creativDevs | Select-Object -First 8 | ForEach-Object {
        Write-Host ("    [{0}] {1}  ({2})" -f $_.Status, $_.FriendlyName, $_.InstanceId)
    }
} else {
    Write-Warn2 "未检测到任何 Creative/NJB 设备（USB VID_041e & PID_4101）。"
    Write-Host "  → 这就是弹窗『检测不到支持的产品』的直接原因。"
    Write-Host "  → 正确顺序：先插入 NJB3 → 设备管理器手动装好 ctpdusb.inf 驱动 → 再装 CMS5。"
    Write-Host "  → 若设备已插却不显示：检查数据线/接口，或在设备管理器『扫描检测硬件改动』。"
    if (-not $Force) {
        Write-Host ""
        Write-Host "  选择："
        Write-Host "    a) 现在插入 NJB3 并确认设备识别，然后重新运行本脚本（推荐）"
        Write-Host "    b) 用 -Execute -Interactive 运行，在检测弹窗出现时插入设备并点『重试』"
        Write-Host "    c) 坚持继续（可能仍被拦截）：加参数 -Force"
        if ($Execute) {
            Write-Err "已中止：未检测到设备且未指定 -Force。请先把 NJB3 接上并装好驱动。"
            exit 2
        }
    } else {
        Write-Warn2 "已指定 -Force，跳过设备预检继续（产品检测弹窗仍可能出现）。"
    }
}

Write-Step "准备无中文/空格的中间目录：$StageDir"
New-Item -ItemType Directory -Force -Path $StageDir | Out-Null
foreach ($f in @($mainExe, $pluginExe)) { Copy-Item -Path $f -Destination $StageDir -Force }
Write-Ok "已复制 2 个安装器。"

$mainStage   = Join-Path $StageDir 'CMS5_PCAPP_LB_5_10_38.exe'
$pluginStage = Join-Path $StageDir 'CMS5_NJB2_PCAPP_LB_5_00_22.exe'

Write-Step "安装顺序与命令"
Write-Host @"
  1) 主程序：  $mainStage
  2) 插件：    $pluginStage
  3) 两者都装完后，重新插拔 NJB3，CMS5 的『从设备管理器』应能看到曲目。
"@
if ($Interactive) {
    Write-Host "  模式：交互式（不加 /s）。若弹『检测不到支持的产品』，先插入 NJB3 再点『重试』。"
    $isy = @()
} else {
    Write-Host "  模式：静默（/s）。静默方式下产品检测弹窗可能被自动跳过，也可能直接失败。"
    $isy = @('/s')
}

if (-not $Execute) {
    Write-Warn2 "演练模式：未运行任何安装器。确认无误后加 -Execute（可配合 -Interactive）重试。"
    Write-Host "  手动安装时若提示解压失败：先用 7-Zip 把 EXE 解包，再运行其中的 setup.exe。"
    exit 0
}

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { Write-Err "真正安装需要管理员权限。"; exit 1 }

foreach ($f in @($mainStage, $pluginStage)) {
    Write-Step ("运行 " + (Split-Path $f -Leaf))
    try {
        if ($isy.Count -gt 0) {
            $p = Start-Process -FilePath $f -ArgumentList $isy -Wait -PassThru
        } else {
            $p = Start-Process -FilePath $f -Wait -PassThru
        }
        Write-Ok ("退出码 " + $p.ExitCode)
        if ($p.ExitCode -ne 0) {
            Write-Warn2 "安装可能未成功。若弹『检测不到支持的产品』：插入 NJB3 后用 -Interactive 重试，或手动双击安装。"
        }
    } catch {
        Write-Warn2 "启动失败：$($_.Exception.Message)"
    }
}

Write-Step "验证"
$dirs = @("$env:ProgramFiles\Creative\MediaSource", "${env:ProgramFiles(x86)}\Creative\MediaSource",
          "$env:ProgramFiles\Creative\MediaSource5", "${env:ProgramFiles(x86)}\Creative\MediaSource5")
$found = $false
foreach ($d in $dirs) { if (Test-Path $d) { Write-Ok "已安装到：$d"; $found = $true } }
if (-not $found) { Write-Warn2 "未找到 MediaSource 安装目录，安装可能失败。" }
Write-Host "  若 CMS5 仍看不到设备：确认驱动已装（Get-PnpDevice | Where-Object InstanceId -match 'VID_041e'），"
Write-Host "  然后重启电脑再插拔一次。"
