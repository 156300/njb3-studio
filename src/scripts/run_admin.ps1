<#
.SYNOPSIS
    以管理员权限运行另一个 PowerShell 脚本（供 njb3_setup.cmd 调用）。

.DESCRIPTION
    批处理里不写复杂的 Start-Process 嵌套引号，统一由本脚本提权：
      - 已是管理员：直接调用目标脚本；
      - 非管理员：弹 UAC，在新窗口中运行目标脚本（窗口保留）。
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Script,
    [string[]]$Arguments = @(),
    [switch]$Execute
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path $Script)) {
    Write-Host "[-] 找不到脚本：$Script" -ForegroundColor Red
    exit 1
}

$identity  = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
$isAdmin   = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if ($isAdmin) {
    # 注意：数组展开 @Arguments 是「位置参数」，会把 -Execute 塞进第一个位置参数。
    # 因此开关必须显式写成命名参数。
    # 另外：@() 作为空数组展开会传入一个空位置参数，覆盖目标脚本的默认值，
    # 所以没有附加参数时必须直接调用，不能展开。
    if ($Execute) {
        if ($Arguments.Count -gt 0) { & $Script -Execute @Arguments }
        else { & $Script -Execute }
    } else {
        if ($Arguments.Count -gt 0) { & $Script @Arguments }
        else { & $Script }
    }
    exit $LASTEXITCODE
}

# 构造提权参数：目标路径必须作为单个带引号的参数传递
$argList = @(
    '-NoProfile',
    '-ExecutionPolicy', 'Bypass',
    '-NoExit',
    '-File', ('"' + $Script + '"')
)
if ($Execute) {
    $argList += '-Execute'
}
if ($Arguments.Count -gt 0) {
    $argList += $Arguments
}

try {
    Start-Process -FilePath (Join-Path $PSHOME 'powershell.exe') -Verb RunAs -ArgumentList $argList | Out-Null
    Write-Host "[+] 已请求管理员权限，请在弹出的窗口中继续。" -ForegroundColor Green
} catch {
    Write-Host "[-] 提权失败：$($_.Exception.Message)" -ForegroundColor Red
    Write-Host "    请手动：右键 PowerShell → 以管理员身份运行 → 执行目标脚本。" -ForegroundColor Yellow
    exit 1
}
