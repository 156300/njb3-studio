<#
.SYNOPSIS
    Windows 10/11 IEEE-1394 主机控制器（火线卡）适配与校验器。

.DESCRIPTION
    针对便宜的 VIA / TI 1394a PCIe 卡：

      VIA  VT6306/7/8  = PCI\VEN_1106&DEV_3044
      VIA  VT6315N     = PCI\VEN_1106&DEV_3403   ← 常见 PCIe 4 口卡
      TI   TSB12LV22   = PCI\VEN_104C&DEV_8009
      TI   TSB12LV23   = PCI\VEN_104C&DEV_8019
      TI   TSB12LV26   = PCI\VEN_104C&DEV_8020
      TI   TSB43AB22A  = PCI\VEN_104C&DEV_8023   ← 常见 PCI 卡
      TI   TSB43AB23   = PCI\VEN_104C&DEV_8024
      TI   XIO2200A    = PCI\VEN_104C&DEV_8235

    关键事实（来自本机 C:\Windows\INF\1394.inf 原文）：
    Windows 自带的、已签名的 1394ohci.sys 已经通过 class code `PCI\CC_0C0010`
    和厂商 ID 覆盖了上述所有芯片。因此这里【不】安装第三方未签名驱动，
    而是强制系统重新绑定自带驱动，并在需要时安装 Creative 的 1394 设备端驱动。

    流程：检测卡片 → 报告硬件 ID 与覆盖状态 → 触发自带驱动重装/重扫 →
          调用 install_win11_driver.ps1 -Mode IEEE1394 安装设备端驱动。

.PARAMETER OnlyCheck
    只做只读检测与报告，不安装任何东西。

.EXAMPLE
    .\install_1394_host.ps1 -OnlyCheck
.EXAMPLE
    .\install_1394_host.ps1
#>
[CmdletBinding()]
param(
    [switch]$OnlyCheck,
    [string]$TargetRoot = "$env:ProgramFiles\NJB3-Win11"
)

$ErrorActionPreference = 'Stop'

function Write-Step($msg) { Write-Host "`n[*] $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "[+] $msg" -ForegroundColor Green }
function Write-Warn2($msg){ Write-Host "[!] $msg" -ForegroundColor Yellow }
function Write-Err($msg)  { Write-Host "[-] $msg" -ForegroundColor Red }

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

Write-Step "检测 IEEE-1394 主机控制器"
$all = @(Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue)
$candidates = @($all | Where-Object {
    ($_.Class -eq '1394') -or
    ($_.InstanceId -like '*VEN_1106*' -and $_.InstanceId -like '*CC_0C0010*') -or
    ($_.InstanceId -like '*VEN_104C*' -and $_.InstanceId -like '*CC_0C0010*') -or
    ($_.FriendlyName -like '*1394*') -or
    ($_.FriendlyName -like '*FireWire*')
})

$supported = @(
    'DEV_3044','DEV_3403',           # VIA VT630x / VT6315N
    'DEV_8009','DEV_8019','DEV_8020','DEV_8023','DEV_8024','DEV_8025','DEV_8235'  # TI
)

if ($candidates.Count -eq 0) {
    Write-Warn2 "未检测到已启用的 1394 主机控制器。"
    Write-Host @"
  请检查：
    1. 卡片是否插紧、PCIe/PCI 槽是否在 BIOS 中启用；
    2. 设备管理器 → 查看 → 显示隐藏的设备，是否有带感叹号的 1394 控制器；
    3. 笔记本/一体机可能没有可用插槽，需使用 PCIe 转接或外置方案。
"@
    if ($OnlyCheck) { exit 2 }
}

$rows = foreach ($d in $candidates) {
    $id = $d.InstanceId
    $chip = '未知/其他 OHCI 卡'
    foreach ($k in $supported) { if ($id -like ("*" + $k + "*")) { $chip = $k } }
    if ($id -like '*VEN_1106*') { $chip = 'VIA ' + $chip }
    if ($id -like '*VEN_104C*') { $chip = 'TI  ' + $chip }
    $covered = ($id -like '*CC_0C0010*') -or
               ($id -like '*DEV_3044*') -or ($id -like '*DEV_8009*') -or ($id -like '*DEV_8019*')
    [pscustomobject]@{
        Status = $d.Status
        Name   = $d.FriendlyName
        Chip   = $chip
        Covered = $(if ($covered) { '自带驱动覆盖' } else { '需手动验证' })
        InstanceId = $id
    }
}
$rows | Format-Table -AutoSize
$rows | ForEach-Object { Write-Host ("    " + $_.InstanceId) }

Write-Ok ("检测到 " + $candidates.Count + " 个 1394 相关设备")
Write-Host @"
  说明：Windows 自带 C:\Windows\INF\1394.inf 中包含：
    [VIA.NTamd64] PCI\VEN_1106&DEV_3044 与 PCI\VEN_1106&CC_0C0010
    [TI.NTamd64]  PCI\VEN_104C&DEV_8009 / DEV_8019 与 PCI\VEN_104C&CC_0C0010
  VT6315N(3403)、TSB43AB22A(8023)、TSB43AB23(8024) 通过 class code 0C0010 覆盖，
  所以不需要、也不应使用第三方未签名驱动。
"@

if ($OnlyCheck) { exit 0 }
if (-not $isAdmin) { Write-Err "执行安装需要管理员权限。"; exit 1 }

Write-Step "触发 Windows 自带 1394 驱动重新绑定/重扫"
try {
    $sysInf = Join-Path $env:WINDIR 'INF\1394.inf'
    $out = & pnputil /add-driver $sysInf /install 2>&1
    $out | ForEach-Object { Write-Host "    $_" }
} catch {
    Write-Warn2 "对系统 INF 执行 add-driver 失败（可忽略，属正常保护）：$($_.Exception.Message)"
}
try {
    & pnputil /scan-devices | ForEach-Object { Write-Host "    $_" }
    Write-Ok "已触发 PnP 重扫。"
} catch { Write-Warn2 "PnP 重扫失败：$($_.Exception.Message)" }

Write-Step "安装 Creative 1394 设备端驱动（NJB3 经火线连接时使用）"
$script = Join-Path $PSScriptRoot 'install_win11_driver.ps1'
if (-not (Test-Path $script)) { Write-Err "找不到 $script"; exit 1 }
& $script -Mode IEEE1394 -TargetRoot $TargetRoot

Write-Step "稳定性建议（需手动，脚本不自动改注册表）"
@"
  · 火线卡供电：VT6315N 卡若接大功率设备，请接好 4-pin 辅助供电；NJB3 一般无需。
  · 电源管理：设备管理器 → 1394 主机控制器 → 属性 → 电源管理 → 取消『允许计算机关闭此设备以节约电源』。
  · 若网卡/声卡共用 IRQ 导致断续，换一个 PCIe 插槽通常即可解决。
  · 若设备管理器里出现『1394 OHCI Compliant Host Controller (Legacy)』选项，
    且 Creative 驱动识别异常，可手动切换到 Legacy 后重启（Win11 新版通常不提供该项）。
  · 连接 NJB3 后应出现『NOMAD Jukebox 3 1394』；若仍是未知设备，
    右键 → 更新驱动 → 从磁盘安装 → $TargetRoot\creative_pde_1394\ctpd1394.inf
"@ | Write-Host

Write-Host "`n完成。详见 ..\docs\05_1394火线卡适配说明.md" -ForegroundColor Cyan
