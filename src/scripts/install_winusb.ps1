<#
.SYNOPSIS
    强制把 Nomad Jukebox 3 (VID_041E&PID_4101) 从 Creative 私有驱动
    切换到 Windows 内置 WinUSB (winusb.sys)，让 njb3_tool.exe 直连 PDE。

.DESCRIPTION
    旧版用 pnputil /add-driver + 禁用/启用设备，Windows 会再次选中已安装的
    Creative 驱动，导致切换失败。本版改用 Windows 内置 API
    UpdateDriverForPlugAndPlayDevices(INSTALLFLAG_FORCE) 强制安装本机 INF，
    与 Zadig 内部机制同类，能覆盖已存在的 Creative 驱动。
    只用微软自带组件，无需第三方驱动、无需测试签名模式。

.PARAMETER Revert   恢复 Creative 原厂驱动。
.PARAMETER OnlyCheck 只读：显示当前绑定状态，不做任何改动。
#>
param(
    [switch]$Revert,
    [switch]$OnlyCheck
)

$ErrorActionPreference = 'Stop'
$INF   = Join-Path $PSScriptRoot 'njb3_winusb.inf'
$HWID  = 'USB\VID_041E&PID_4101'
$CREATIVE_INF = 'G:\Drivers\jukebox3\win10\Creative_USB_x86_x64\Jukebox 3 Drivers\ctpdusb.inf'

function Section($t) { Write-Host "`n=== $t ===" -ForegroundColor Cyan }
function Ok($t)   { Write-Host "[OK] $t" -ForegroundColor Green }
function Warn($t) { Write-Host "[!]  $t" -ForegroundColor Yellow }
function Err($t)  { Write-Host "[×]  $t" -ForegroundColor Red }
function Info($t) { Write-Host "     $t" -ForegroundColor Gray }

function Find-NJB3 {
    Get-PnpDevice -ErrorAction SilentlyContinue |
        Where-Object { $_.InstanceId -like 'USB\VID_041E&PID_4101*' } |
        Select-Object -First 1
}

function Show-State {
    $d = Find-NJB3
    if (-not $d) { Err "NJB3 (VID_041E PID_4101) 未连接。请插好 USB 线。"; return $null }
    Ok "找到设备: $($d.FriendlyName)"
    Info "实例路径: $($d.InstanceId)"
    Info "状态    : $($d.Status)  类: $($d.Class)"
    $inf = (Get-PnpDeviceProperty -InstanceId $d.InstanceId `
              -KeyName 'DEVPKEY_Device_DriverInfPath' -ErrorAction SilentlyContinue).Data
    $svc = (Get-PnpDeviceProperty -InstanceId $d.InstanceId `
              -KeyName 'DEVPKEY_Device_Service' -ErrorAction SilentlyContinue).Data
    $ver = (Get-PnpDeviceProperty -InstanceId $d.InstanceId `
              -KeyName 'DEVPKEY_Device_DriverVersion' -ErrorAction SilentlyContinue).Data
    Info "驱动INF : $inf"
    Info "服务    : $svc"
    Info "版本    : $ver"
    if ($svc -match 'winusb') { Ok "当前已是 WinUSB —— 可以直接运行 njb3_tool.exe。" }
    else { Warn "当前不是 WinUSB —— njb3_tool.exe 将无法控制设备。" }
    return $d
}

# --- P/Invoke for the Windows driver-install API ---
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class DevInstall {
    [DllImport("newdev.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    public static extern bool UpdateDriverForPlugAndPlayDevices(
        IntPtr hwndParent,
        string HardwareId,
        string FullInfPath,
        uint InstallFlags,
        out bool rebootRequired);
}
'@

Section "NJB3 WinUSB 强制绑定工具"
Info "只使用 Windows 自带组件（winusb.sys + newdev.dll）。"

if ($OnlyCheck) { Show-State | Out-Null; exit 0 }

# --- admin check ---
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Err "需要管理员权限。请右键本文件 -> 以管理员身份运行。"
    exit 2
}

$d = Find-NJB3
if (-not $d) { Err "NJB3 未连接。插好设备后重试。"; exit 3 }
Show-State | Out-Null

$targetInf = if ($Revert) { $CREATIVE_INF } else { $INF }
if (-not (Test-Path $targetInf)) { Err "找不到 INF: $targetInf"; exit 4 }

Section "步骤 1/3 - 把 INF 加入驱动库"
$pnp = pnputil /add-driver "`"$targetInf`"" /install 2>&1
$pnp | ForEach-Object { Info $_ }
Info "pnputil 返回码: $LASTEXITCODE (1=已存在/正常, 0=新增)"

Section "步骤 2/3 - 强制安装到设备节点"
# INSTALLFLAG_FORCE(0x1) | NONINTERACTIVE(0x4) = 0x5
$flags = [uint32]0x00000005
$reboot = $false
Info "硬件ID : $HWID"
Info "INF    : $targetInf"
Info "Flags  : 0x$($flags.ToString('X8')) (FORCE|NONINTERACTIVE)"
$ok = [DevInstall]::UpdateDriverForPlugAndPlayDevices(
    [IntPtr]::Zero, $HWID, $targetInf, $flags, [ref]$reboot)
$win32err = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
if ($ok) {
    Ok "驱动安装 API 调用成功。"
} else {
    Err "驱动安装 API 失败。Win32 错误码: $win32err"
    Info "常见原因: 0xE=无效句柄, 0x2=找不到文件, 0x32=其他错误。"
    Warn "将尝试自动回退方案（重建设备节点）..."
    # Fallback: remove + rescan so the freshly staged INF is selected.
    $inst = $d.InstanceId
    Info "移除设备节点: $inst"
    pnputil /remove-device "`"$inst`"" 2>&1 | ForEach-Object { Info $_ }
    Start-Sleep -Seconds 2
    Info "重新扫描硬件..."
    pnputil /scan-devices 2>&1 | ForEach-Object { Info $_ }
    Start-Sleep -Seconds 3
}

Section "步骤 3/3 - 验证结果"
$d2 = Find-NJB3
if (-not $d2) {
    Warn "设备暂时未出现在系统里。请拔插一次 USB 线后重新运行本脚本（-OnlyCheck 查看）。"
    exit 6
}
$svc2 = (Get-PnpDeviceProperty -InstanceId $d2.InstanceId -KeyName 'DEVPKEY_Device_Service' -ErrorAction SilentlyContinue).Data
$inf2 = (Get-PnpDeviceProperty -InstanceId $d2.InstanceId -KeyName 'DEVPKEY_Device_DriverInfPath' -ErrorAction SilentlyContinue).Data
Info "驱动: $inf2   服务: $svc2   状态: $($d2.Status)"

if ($Revert) {
    Ok "已尝试恢复 Creative 驱动。若服务名仍非 Jukebox3，请在设备管理器中手动选择。"
    exit 0
}

if ($svc2 -match 'winusb') {
    Ok "成功！设备已绑定到 WinUSB。"
    Ok "现在运行 02_工具\njb3_tool.exe（或启动工具.bat）即可。"
    if ($reboot) { Warn "系统提示需要重启才能完全生效，建议重启一次。" }
    exit 0
} else {
    Err "自动切换未生效。请用内置的 Zadig 手动切换（03_备用工具\zadig.exe）："
    Info "1) 管理员运行 zadig.exe"
    Info "2) Options -> List All Devices"
    Info "3) 选 'NOMAD Jukebox 3 (041E:4101)'（不要选带 Parent 的项）"
    Info "4) 目标驱动选 WinUSB，点 Replace Driver"
    Info "5) 完成后重新运行本脚本的 -OnlyCheck 验证"
    exit 7
}
