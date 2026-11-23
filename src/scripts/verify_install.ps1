<#
.SYNOPSIS
    NJB3 Windows 11 安装后只读体检。

.DESCRIPTION
    汇总报告：系统版本、HVCI、Driver Store 中的 Creative 驱动、
    1394 主机控制器、NJB3 设备、CMS5 安装痕迹、传输工具可用性。
    本脚本【只读】，不安装、不修改、不重启任何东西。
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Continue'
function Sec($t) { Write-Host "`n=== $t ===" -ForegroundColor Cyan }
function Row($k, $v) { Write-Host ("  {0,-22} {1}" -f $k, $v) }

$os = Get-CimInstance Win32_OperatingSystem
Sec "系统"
Row "OS" $os.Caption
Row "Build" $os.BuildNumber
Row "架构" $env:PROCESSOR_ARCHITECTURE
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
Row "管理员" $(if ($isAdmin) { '是' } else { '否（部分查询可能受限）' })

Sec "HVCI / 内存完整性"
try {
    $sc = Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\DeviceGuard\Scenarios\HypervisorEnforcedCodeIntegrity' -ErrorAction SilentlyContinue
    Row "Enabled" $(if ($null -eq $sc) { '未配置（通常关闭）' } else { $sc.Enabled })
} catch { Row "Enabled" "读取失败" }

Sec "Driver Store 中的 Creative 驱动"
try {
    $drv = & pnputil /enum-drivers 2>&1 | Out-String
    $lines = $drv -split "`r?`n"
    $hits = @()
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match 'ctpdusb|ctpd1394|ctpdfwx') {
            $hits += ($lines[$i].Trim() + "  |  " + $(if ($i + 1 -lt $lines.Count) { $lines[$i+1].Trim() } else { '' }))
        }
    }
    if ($hits.Count -eq 0) { Write-Host "  （未找到 Creative PDE 驱动，请先运行 install_win11_driver.ps1）" -ForegroundColor Yellow }
    else { $hits | ForEach-Object { Write-Host "  $_" } }
} catch { Write-Host "  查询失败：$($_.Exception.Message)" }

Sec "1394 主机控制器"
try {
    $fw = @(Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | Where-Object { $_.Class -eq '1394' -or $_.FriendlyName -like '*1394*' -or $_.FriendlyName -like '*FireWire*' })
    if ($fw.Count -eq 0) { Write-Host "  （未检测到；若已插卡请检查 BIOS/插槽）" -ForegroundColor Yellow }
    else { $fw | Select-Object Status,FriendlyName,InstanceId | Format-Table -AutoSize | Out-String | Write-Host }
} catch { Write-Host "  查询失败：$($_.Exception.Message)" }

Sec "NJB3 设备"
try {
    $dev = @(Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | Where-Object {
        $_.InstanceId -like '*VID_041e&PID_4101*' -or $_.FriendlyName -like '*Jukebox 3*'
    })
    if ($dev.Count -eq 0) { Write-Host "  （未连接或未装驱动）" -ForegroundColor Yellow }
    else { $dev | Select-Object Status,Class,FriendlyName,InstanceId | Format-Table -AutoSize | Out-String | Write-Host }
} catch { Write-Host "  查询失败：$($_.Exception.Message)" }

Sec "CMS5 软件痕迹"
$found = $false
foreach ($d in @("$env:ProgramFiles\Creative\MediaSource", "${env:ProgramFiles(x86)}\Creative\MediaSource")) {
    if (Test-Path $d) { Row "目录" $d; $found = $true }
}
$uninstall = @()
foreach ($k in @('HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
                 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*')) {
    $uninstall += Get-ItemProperty $k -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -like '*MediaSource*' }
}
$uninstall | ForEach-Object { Row "注册项" ($_.DisplayName + "  " + $_.DisplayVersion); $found = $true }
if (-not $found) { Write-Host "  （未发现 CMS5 安装痕迹）" -ForegroundColor Yellow }

Sec "工具链"
$sp = Join-Path $PSScriptRoot 'njb3_transfer.py'
Row "传输脚本" $(if (Test-Path $sp) { '存在' } else { '缺失' })
try {
    $py = (& python --version 2>&1 | Out-String).Trim()
    if ([string]::IsNullOrWhiteSpace($py)) { $py = 'python 可用（版本未回显）' }
    Row "Python" $py
} catch { Row "Python" "未找到" }

Write-Host "`n说明：本体检为只读。若 1394 控制器存在但 NJB3 经火线不出现，"
Write-Host "先运行 install_1394_host.ps1 -OnlyCheck 查看硬件 ID 是否被自带驱动覆盖。"
