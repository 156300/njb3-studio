<#
.SYNOPSIS
    NJB3 WinUSB 驱动安装器（libwdi/Zadig 同款签名机制，逐行对照 pki.c）
.DESCRIPTION
    v8 修复：签名证书优先 CurrentUser\My 并导出带私钥 PFX 交给 make_cat.exe，
      彻底规避精简版/跨账户提权场景下 LocalMachine 证书私钥 ACL 不可访问
      （SignerSignEx 0x80092006）的问题；make_cat.exe 同步升级到 v8（支持 PFX）。
      设备识别加 CIM 兜底，兼容缺少 PnpDevice 模块的精简系统。

    v7 修复 v6 遗留的致命 bug（SetupAPI 报 0x80096010 TRUST_E_BAD_DIGEST）：
      * PowerShell 的 CryptCATPutMemberInfo P/Invoke 把 SIPDATA 结构封送错位，
        CAT 里的 digest 变成 UTF-16 hex 字符串而非 20 字节二进制 → Windows 拒签。
        v7 改用随包携带的 make_cat.exe（Python ctypes 精确封送 libwdi 算法），
        生成经二进制级验证的 04 14 <20字节 SHA1> 结构。
      * 自检现在会用 WinVerifyTrust 确认 digest 非 0x80096010。

    v6 修复 v5 遗留的致命 bug（v5 会报退出码 7 / pnputil 0xE0000247 导致装不上）：
      * Trust-Cert 里 X509Store.Open() 的枚举名写成不存在的 StoreFlags，
        Open() 抛异常被吞 → 证书从未进 LocalMachine 信任库 → 签名链断裂 →
        pnputil 拒绝导入。改为字符串 'ReadWrite'（即 OpenFlags），并加写入复核。
      * 证书写库失败现在直接中止（退出码 11），不再带病继续到 pnputil。
      * 自检新增"证书写库 + 证书链建立"真实校验，杜绝此类 bug 逃逸。

    v5 修复两个致命 bug（v4 会报 0x80096010 TRUST_E_BAD_DIGEST 导致装不上）：
      1) SPC_LINK 必须用 CryptEncodeObject(SPC_CAB_DATA_OBJID) 生成
         正确 DER 为 A2 1E 80 1C ...，不能手工拼 30/02 01 03/1e。
      2) DigestAlgorithm.pszObjId 必须是 szOID_OIWSEC_sha1 = 1.3.14.3.2.26，
         v4 误用 1.3.6.1.4.1.311.2.1.26 导致 Windows 算错摘要。
      3) 证书要导入 Root/TrustedPublisher，X509Store 名字必须是 'Root'/'TrustedPublisher'
         （v4 误用 'LocalMachine\Root'，证书从未真正入库）。
    自检不再只验 CAT 自身签名，而是用 WinVerifyTrust(DRIVER_ACTION_VERIFY)
    区分 0x80096010(digest 错=失败) 与 0x800B0109(仅证书未入当前库=结构 OK)。
.PARAMETER Install   完整安装（需管理员，GUI 自动提权调用）。
.PARAMETER Status    只显示设备当前驱动绑定状态。
.PARAMETER SelfTest  自检：证书+合规CAT+digest校验（无需管理员）。
#>
param(
    [switch]$Install,
    [switch]$Status,
    [switch]$SelfTest
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = New-Object System.Text.UTF8Encoding($false)

$SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
$INF = Join-Path $SCRIPT_DIR 'njb3_winusb.inf'
if (-not (Test-Path $INF)) { $INF = Join-Path $SCRIPT_DIR '01_驱动\njb3_winusb.inf' }
$CAT = [IO.Path]::ChangeExtension($INF, '.cat')
$HWID = 'USB\VID_041E&PID_4101'
$SUBJECT = 'CN=NJB3 WinUSB Self-Signed Signing'

function Done([int]$code) {
    Write-Output "__NJB3_DRIVER_DONE__$code"
    try { Stop-Transcript | Out-Null } catch {}
    exit $code
}
# 日志函数必须用 Write-Host（写到 host，不进入 success stream），
# 否则会把日志文本混进函数返回值：Trust-Cert/Get-OrCreate-Cert 的返回值
# 变成"日志+对象"数组，导致 if(-not ...) 判断失效、Build() 类型不匹配。
# Write-Host 仍会被 Start-Transcript 和 GUI 的 stdout 读取捕获。
function Sec($t) { Write-Host "`n=== $t ===" }
function OK($t)  { Write-Host "[OK] $t" }
function Warn($t){ Write-Host "[!]  $t" }
function Err($t) { Write-Host "[X]  $t" }
function Inf($t) { Write-Host "     $t" }

function Test-Admin {
    $p = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Find-NJB3 {
    # 优先 Get-PnpDevice；精简版系统若无 PnpDevice 模块，回退 CIM（Win32_PnPEntity）
    $d = $null
    try {
        if (Get-Command Get-PnpDevice -EA SilentlyContinue) {
            $d = Get-PnpDevice -ErrorAction SilentlyContinue |
                 Where-Object { $_.InstanceId -like "$HWID*" } | Select-Object -First 1
        }
    } catch {}
    if (-not $d) {
        try {
            $ent = Get-CimInstance Win32_PnPEntity -ErrorAction SilentlyContinue |
                   Where-Object { $_.DeviceID -like "$HWID*" } | Select-Object -First 1
            if ($ent) {
                # 构造兼容对象：Show-Status 只用到 FriendlyName/InstanceId/Status
                $d = [pscustomobject]@{
                    FriendlyName = if ($ent.Name) { $ent.Name } else { 'NOMAD Jukebox 3' }
                    InstanceId   = $ent.DeviceID
                    Status       = if ($ent.Status) { $ent.Status } else { 'Unknown' }
                }
            }
        } catch {}
    }
    return $d
}

function Show-Status {
    $d = Find-NJB3
    if (-not $d) { Warn "未找到 NJB3 设备（$HWID）。"; return }
    $infp = (Get-PnpDeviceProperty -InstanceId $d.InstanceId -KeyName 'DEVPKEY_Device_DriverInfPath' -ErrorAction SilentlyContinue).Data
    $svc  = (Get-PnpDeviceProperty -InstanceId $d.InstanceId -KeyName 'DEVPKEY_Device_Service' -ErrorAction SilentlyContinue).Data
    $ver  = (Get-PnpDeviceProperty -InstanceId $d.InstanceId -KeyName 'DEVPKEY_Device_DriverVersion' -ErrorAction SilentlyContinue).Data
    OK "设备: $($d.FriendlyName)  [$($d.Status)]"
    Inf "实例 : $($d.InstanceId)"
    Inf "INF  : $infp"
    Inf "服务 : $svc"
    Inf "版本 : $ver"
    if ($svc -match 'winusb') { OK "已绑定 WinUSB —— 可以直接连接。" }
    else { Warn "当前不是 WinUSB（被 Creative 等内核驱动占用）。" }
}

# ---------- P/Invoke（逐行对照 libwdi pki.c）----------
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

[StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]
public struct NJB3_CATATTR {
    public uint cbStruct;
    public IntPtr pwszReferenceTag;
    public uint dwAttrTypeAndAction;
    public uint cbValue;
    public IntPtr pbValue;
    public uint dwReserved;
}
[StructLayout(LayoutKind.Sequential)]
public struct NJB3_BLOB { public uint cbData; public IntPtr pbData; }
[StructLayout(LayoutKind.Sequential, CharSet=CharSet.Ansi)]
public struct NJB3_ATTRTYPEVAL { public IntPtr pszObjId; public NJB3_BLOB Value; }
[StructLayout(LayoutKind.Sequential, CharSet=CharSet.Ansi)]
public struct NJB3_ALGID { public IntPtr pszObjId; public NJB3_BLOB Parameters; }
[StructLayout(LayoutKind.Sequential)]
public struct NJB3_SIPDATA { public NJB3_ATTRTYPEVAL Data; public NJB3_ALGID DigestAlgorithm; public NJB3_BLOB Digest; }
[StructLayout(LayoutKind.Sequential)]
public struct NJB3_FILEINFO { public uint cbSize; public IntPtr pwszFileName; public IntPtr hFile; }
[StructLayout(LayoutKind.Sequential)]
public struct NJB3_SUBJECTINFO { public uint cbSize; public IntPtr pdwIndex; public uint dwSubjectChoice; public IntPtr pSignerFileInfo; }
[StructLayout(LayoutKind.Sequential)]
public struct NJB3_CERTSTOREINFO { public uint cbSize; public IntPtr pSigningCert; public uint dwCertPolicy; public IntPtr hCertStore; }
[StructLayout(LayoutKind.Sequential)]
public struct NJB3_CERT { public uint cbSize; public uint dwCertChoice; public IntPtr pCertStoreInfo; public IntPtr hwnd; }
[StructLayout(LayoutKind.Sequential)]
public struct NJB3_SIGINFO { public uint cbSize; public uint algidHash; public uint dwAttrChoice; public IntPtr pAttrAuthcode; public IntPtr psAuthenticated; public IntPtr psUnauthenticated; }
[StructLayout(LayoutKind.Sequential)]
public struct NJB3_SIGNERCTX { public uint cbSize; public uint cbBlob; public IntPtr pbBlob; }

public static class NJB3Cat {
    [DllImport("wintrust.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    public static extern IntPtr CryptCATOpen(string pwszFileName, uint fdwOpenFlags, IntPtr hProv, uint dwPublicVersion, uint dwEncodingType);
    [DllImport("wintrust.dll", SetLastError=true)] public static extern bool CryptCATClose(IntPtr hCatalog);
    [DllImport("wintrust.dll", SetLastError=true)] public static extern bool CryptCATPersistStore(IntPtr hCatalog);
    [DllImport("wintrust.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    public static extern IntPtr CryptCATPutCatAttrInfo(IntPtr hCatalog, string tag, uint action, uint cbData, byte[] pbData);
    [DllImport("wintrust.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    public static extern IntPtr CryptCATPutMemberInfo(IntPtr hCatalog, string fileName, string refTag, ref Guid subjectType, uint dwCertVersion, uint cbSIP, ref NJB3_SIPDATA pSIP);
    [DllImport("wintrust.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    public static extern IntPtr CryptCATPutAttrInfo(IntPtr hCatalog, IntPtr member, string tag, uint action, uint cbData, byte[] pbData);
    [DllImport("wintrust.dll", SetLastError=true)]
    public static extern bool CryptCATAdminCalcHashFromFileHandle(IntPtr hFile, ref uint pcbHash, byte[] pbHash, uint dwFlags);
    [DllImport("crypt32.dll", SetLastError=true)]
    public static extern bool CryptEncodeObject(uint encoding, string structType, IntPtr pvStructInfo, byte[] pbEncoded, ref uint pcbEncoded);

    [DllImport("mssign32.dll")]
    public static extern int SignerSignEx(uint flags, ref NJB3_SUBJECTINFO subject, ref NJB3_CERT cert, ref NJB3_SIGINFO sig, IntPtr provider, IntPtr ts, IntPtr req, IntPtr sipData, out IntPtr ctx);
    [DllImport("mssign32.dll")]
    public static extern int SignerFreeSignerContext(IntPtr ctx);

    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    public static extern IntPtr CreateFileW(string path, uint access, uint share, IntPtr sec, uint disp, uint attrs, IntPtr tmpl);
    [DllImport("kernel32.dll", SetLastError=true)] public static extern bool CloseHandle(IntPtr h);
    [DllImport("advapi32.dll", SetLastError=true)]
    public static extern bool CryptAcquireContext(out IntPtr phProv, string container, string provider, uint type, uint flags);
    [DllImport("advapi32.dll", SetLastError=true)] public static extern bool CryptReleaseContext(IntPtr hProv, uint flags);
}
'@
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
[StructLayout(LayoutKind.Sequential)] public struct NJB3_VFILE { public uint cbStruct; public IntPtr pcwszFilePath; public IntPtr hFile; public IntPtr pgKnownSubject; }
[StructLayout(LayoutKind.Sequential)] public struct NJB3_VDATA {
  public uint cbStruct; public IntPtr pPolicyCallbackData; public IntPtr pSIPClientData;
  public uint dwUIChoice; public uint fdwRevocationChecks; public uint dwUnionChoice; public IntPtr pFile;
  public uint dwStateAction; public IntPtr hWVTStateData; public IntPtr pwszURLReference; public uint dwProvFlags; public uint dwUIContext;
}
public static class NJB3Verify {
  [DllImport("wintrust.dll", CharSet=CharSet.Unicode, SetLastError=true)]
  public static extern int WinVerifyTrust(IntPtr hwnd, ref Guid action, IntPtr data);
}
'@

# ---------- 常量 ----------
$HHEX = [System.Globalization.NumberStyles]::HexNumber
$CRYPTCAT_OPEN_CREATENEW = [uint32]1
$CRYPTCAT_ATTR_AUTHENTICATED = [uint32]0x10000000
$CRYPTCAT_ATTR_NAMEASCII = [uint32]1
$CRYPTCAT_ATTR_DATAASCII = [uint32]0x10000
$ATTR_FLAGS = $CRYPTCAT_ATTR_AUTHENTICATED -bor $CRYPTCAT_ATTR_NAMEASCII -bor $CRYPTCAT_ATTR_DATAASCII
$PROV_RSA_FULL = [uint32]1
$CRYPT_VERIFYCONTEXT = [uint32]::Parse('F0000000', $HHEX)
$GENERIC_READ = [uint32]::Parse('80000000', $HHEX)
$FILE_SHARE_READ = [uint32]1
$OPEN_EXISTING = [uint32]3
$FILE_ATTR_NORMAL = [uint32]0x80
$CALG_SHA_256 = [uint32]0x800c
$SIGNER_SUBJECT_FILE = [uint32]1
$SIGNER_CERT_STORE = [uint32]2
$SIGNER_CERT_POLICY_CHAIN = [uint32]2
$SIGNER_NO_ATTR = [uint32]0
$X509_ASN_ENCODING = [uint32]1
$OID_SPC_CAB = '1.3.6.1.4.1.311.2.1.25'   # SPC_CAB_DATA_OBJID
$OID_SHA1 = '1.3.14.3.2.26'                # szOID_OIWSEC_sha1 —— v4 这里写错导致 BAD_DIGEST
$inf_guid = [Guid]'DE351A42-8E59-11D0-8C47-00C04FC295EE'

function Get-OrCreate-Cert {
    param([bool]$LocalMachine)
    $loc = if ($LocalMachine) { 'LocalMachine' } else { 'CurrentUser' }
    $store = if ($LocalMachine) { 'Cert:\LocalMachine\My' } else { 'Cert:\CurrentUser\My' }
    $existing = Get-ChildItem $store -ErrorAction SilentlyContinue |
        Where-Object { $_.Subject -eq $SUBJECT } | Select-Object -First 1
    if ($existing) { Inf "复用已有签名证书: $($existing.Thumbprint) [$loc]"; return $existing }
    Inf "生成自签名证书 (RSA2048/SHA256, $loc)"
    $cert = New-SelfSignedCertificate -Type CodeSigning -Subject $SUBJECT `
        -KeyAlgorithm RSA -KeyLength 2048 -HashAlgorithm SHA256 `
        -CertStoreLocation $store -NotAfter (Get-Date).AddYears(10) `
        -KeyUsage DigitalSignature -ErrorAction Stop
    return $cert
}

function Trust-Cert {
    param($Cert, [string]$Scope = 'LocalMachine')
    $allOk = $true
    $loc = if ($Scope -eq 'CurrentUser') {
        [System.Security.Cryptography.X509Certificates.StoreLocation]::CurrentUser
    } else {
        [System.Security.Cryptography.X509Certificates.StoreLocation]::LocalMachine
    }
    # OpenFlags 是 X509Store.Open 的正确枚举；旧版误写 StoreFlags（不存在）导致
    # Open() 抛异常、证书从未入库，签名链无法建立，pnputil 报 0xE0000247。
    # 用字符串 'ReadWrite' 让 PowerShell 自行转换为 OpenFlags，规避类型名风险。
    foreach ($name in @('Root', 'TrustedPublisher')) {
        try {
            $store = New-Object System.Security.Cryptography.X509Certificates.X509Store($name, $loc)
            $store.Open('ReadWrite')
            if (-not ($store.Certificates | Where-Object { $_.Thumbprint -eq $Cert.Thumbprint })) {
                $store.Add($Cert)
            }
            $store.Close()
            # 复核：重新只读打开，确认证书确实落到库里（不再"以为成功"）
            $check = New-Object System.Security.Cryptography.X509Certificates.X509Store($name, $loc)
            $check.Open('ReadOnly')
            $present = [bool]($check.Certificates | Where-Object { $_.Thumbprint -eq $Cert.Thumbprint })
            $check.Close()
            if ($present) { OK "证书已加入 $Scope\$name（已复核）" }
            else { Err "证书未能写入 $Scope\$name —— 信任链无法建立！"; $allOk = $false }
        } catch { Err "加入 $Scope\$name 失败: $($_.Exception.Message)"; $allOk = $false }
    }
    return $allOk
}

function Build-Cat {
    param($Cert)
    if (-not (Test-Path $INF)) { throw "找不到 INF: $INF" }

    # v7: CAT 生成改由 make_cat.exe（Python ctypes 精确封送）完成。
    # PowerShell 版 CryptCATPutMemberInfo 的 SIPDATA 结构封送错位，把 digest
    # 写成 UTF-16 hex 字符串而非 20 字节二进制 → SetupAPI 报 0x80096010
    # TRUST_E_BAD_DIGEST。Python ctypes 版已验证生成正确的 04 14 <20字节> 结构。
    $makeCat = Join-Path $SCRIPT_DIR 'make_cat.exe'
    if (-not (Test-Path $makeCat)) { $makeCat = Join-Path $SCRIPT_DIR '02_工具\make_cat.exe' }
    if (-not (Test-Path $makeCat)) { throw "找不到 make_cat.exe，请检查发布包完整性。" }

    $der = Join-Path $env:TEMP 'njb3_sign_cert.der'
    [IO.File]::WriteAllBytes($der, $Cert.RawData)
    # 判断证书所在 store，传给 make_cat.exe 让它按 thumbprint 取带私钥的证书
    $storeTag = 'LM'
    if ($Cert.Thumbprint -eq (Get-ChildItem Cert:\CurrentUser\My -EA SilentlyContinue |
        Where-Object { $_.Thumbprint -eq $Cert.Thumbprint } | Select-Object -First 1).Thumbprint) {
        $storeTag = 'CU'
    }
    # v8：优先导出带私钥 PFX 传给 make_cat.exe，规避精简系统/跨账户下
    # LocalMachine 证书私钥 ACL 不可访问（0x80092006）的问题。
    $pfx = Join-Path $env:TEMP 'njb3_sign_cert.pfx'
    $pfxPass = 'njb3sign'
    $pfxOk = $false
    try {
        $secPass = ConvertTo-SecureString -String $pfxPass -Force -AsPlainText
        Export-PfxCertificate -Cert $Cert -FilePath $pfx -Password $secPass -Force -ErrorAction Stop | Out-Null
        $pfxOk = $true
        Inf "已导出带私钥签名 PFX（跨账户/精简系统兼容）"
    } catch {
        Warn "PFX 导出不可用（$($_.Exception.Message)），回退到证书存储 thumbprint 方式。"
    }
    try {
        Remove-Item $CAT -Force -ErrorAction SilentlyContinue
        if ($pfxOk) {
            $out = & $makeCat $INF $CAT $der $Cert.Thumbprint $storeTag $pfx $pfxPass 2>&1
        } else {
            $out = & $makeCat $INF $CAT $der $Cert.Thumbprint $storeTag 2>&1
        }
        $out | ForEach-Object { Inf $_ }
        $rc = $LASTEXITCODE
        if ($rc -ne 0 -or -not (Test-Path $CAT)) {
            throw "make_cat.exe 失败 (退出码 $rc)：$($out -join ' ')"
        }
        OK "合规 CAT 已生成（Python ctypes / libwdi 算法，$((Get-Item $CAT).Length) 字节）"
    } finally {
        Remove-Item $der -Force -ErrorAction SilentlyContinue
        Remove-Item $pfx -Force -ErrorAction SilentlyContinue
    }
}

# 用与 SetupAPI 相同的 WinVerifyTrust(DRIVER_ACTION_VERIFY) 验证 CAT
# 返回 0 = 全通过；0x800B0109 = digest/结构 OK，只差证书信任；0x80096010 = digest 错（失败）
function Verify-Cat {
    $ga = [Guid]::Parse('F750E6C3-38EE-11D1-85E5-00C04FC295EE')
    $vf = New-Object NJB3_VFILE
    $vf.cbStruct = [uint32][Runtime.InteropServices.Marshal]::SizeOf([type]'NJB3_VFILE')
    $vf.pcwszFilePath = [Runtime.InteropServices.Marshal]::StringToHGlobalUni($CAT)
    $pvf = [Runtime.InteropServices.Marshal]::AllocHGlobal([Runtime.InteropServices.Marshal]::SizeOf([type]'NJB3_VFILE'))
    [Runtime.InteropServices.Marshal]::StructureToPtr($vf, $pvf, $false)
    $vd = New-Object NJB3_VDATA
    $vd.cbStruct = [uint32][Runtime.InteropServices.Marshal]::SizeOf([type]'NJB3_VDATA')
    $vd.dwUIChoice = 2; $vd.fdwRevocationChecks = 0; $vd.dwUnionChoice = 1
    $vd.pFile = $pvf; $vd.dwStateAction = 0
    $pvd = [Runtime.InteropServices.Marshal]::AllocHGlobal([Runtime.InteropServices.Marshal]::SizeOf([type]'NJB3_VDATA'))
    [Runtime.InteropServices.Marshal]::StructureToPtr($vd, $pvd, $false)
    $hr = [NJB3Verify]::WinVerifyTrust([IntPtr]::Zero, [ref]$ga, $pvd) -band 0xffffffff
    return $hr
}

# ================================ 主流程 ================================
Sec 'NJB3 WinUSB 驱动安装器 v8 (PFX私钥签名 + 精简系统兼容)'

if ($Status -or (-not $Install -and -not $SelfTest)) { Show-Status; exit 0 }

if ($SelfTest) {
    Sec '自检：证书写库 + 合规CAT + digest 校验（无需管理员）'
    $cert = Get-OrCreate-Cert -LocalMachine $false
    # 真实验证证书能被写入信任库（v5 的 StoreFlags bug 就是在这里逃逸的）
    if (-not (Trust-Cert -Cert $cert -Scope CurrentUser)) {
        Err '证书无法写入信任库 —— 自检失败（这正是 v5 退出码 7 的根因）。'; Done 12
    }
    $chain = New-Object System.Security.Cryptography.X509Certificates.X509Chain
    $chain.ChainPolicy.RevocationMode = 'NoCheck'
    $chainBuilt = $chain.Build($cert)
    $chain.Reset()
    if (-not $chainBuilt) { Err '证书链无法建立 —— 自检失败。'; Done 12 }
    OK '证书写库 + 证书链建立验证通过。'
    Build-Cat -Cert $cert
    $hr = Verify-Cat
    Inf ("WinVerifyTrust = 0x{0:X8}" -f $hr)
    if ($hr -eq 0x80096010) { Err 'TRUST_E_BAD_DIGEST —— CAT digest 构造错误，自检失败。'; Done 9 }
    if ($hr -eq 0x800B0100) { Err 'TRUST_E_NOSIGNATURE —— CAT 未签名，自检失败。'; Done 9 }
    if ($hr -eq 0 -or $hr -eq 0x800B0109) {
        OK 'CAT 结构与 digest 校验通过（0x800B0109 仅表示自签证书未入 LocalMachine 信任库，安装时脚本会以管理员导入）。'
        Done 0
    }
    Err ("CAT 验证出现未预期错误 0x{0:X8}" -f $hr); Done 9
}

if ($Install) {
    if (-not (Test-Admin)) {
        Err '安装驱动需要管理员权限。请在以下任一入口以管理员身份重试：'
        Err '  1) 直接双击「安装 WinUSB 驱动.bat」（会自动弹 UAC 提权）；'
        Err '  2) 或右键该 bat → 以管理员身份运行；'
        Err '  3) 在 NJB3 Studio 软件里点「安装/修复 WinUSB 驱动」按钮。'
        Err '若当前账户本身无管理员权限（精简版系统常见），请改用管理员账户登录。'
        Done 2
    }
    # 全程写日志，便于排错（GUI 里也能看到实时输出）
    $LOG = Join-Path $SCRIPT_DIR '安装日志.txt'
    try { Start-Transcript -Path $LOG -Force -ErrorAction Stop | Out-Null; Inf "日志: $LOG" } catch { Warn "无法写日志: $($_.Exception.Message)" }
    $d = Find-NJB3
    if (-not $d) {
        Err '未找到 NJB3 硬件（USB\VID_041E&PID_4101）。'
        Err '请确认：1) USB 线已插好且能供电；2) 换一个 USB 口（优先主板后置口）；'
        Err '        3) 设备管理器里是否出现未知 USB 设备。插好后重试。'
        Done 3
    }
    Show-Status

    Sec '1/4 - 准备签名证书'
    # v8：签名证书优先放在 CurrentUser\My —— 私钥始终可访问，避免精简系统/
    # 跨账户提权场景下 LocalMachine 私钥 ACL 不可访问导致签名失败（0x80092006）。
    # 信任链仍需写入 LocalMachine 的 Root/TrustedPublisher（下方 Trust-Cert）。
    try {
        $cert = Get-OrCreate-Cert -LocalMachine $false
        OK '签名证书位于 CurrentUser\My（私钥可访问）'
    } catch {
        Warn "CurrentUser 证书不可用（$($_.Exception.Message)），回退到 LocalMachine。"
        $cert = Get-OrCreate-Cert -LocalMachine $true
    }
    if (-not (Trust-Cert -Cert $cert)) {
        Err '签名证书未能导入 Root/TrustedPublisher，驱动包必被 Windows 拒绝（0xE0000247）。已中止。'
        Done 11
    }
    # 验证证书链确实能建立（信任根已生效），而不是只看"Add 没报错"
    try {
        $chain = New-Object System.Security.Cryptography.X509Certificates.X509Chain
        $chain.ChainPolicy.RevocationMode = 'NoCheck'
        $built = $chain.Build($cert)
        $untrusted = [bool]($chain.ChainStatus | Where-Object { $_.Status -match 'UntrustedRoot|PartialChain' })
        $chain.Reset()
        if ($built -and -not $untrusted) { OK '证书信任链验证通过（Root 已生效）。' }
        else { Warn "证书链仍未建立 (built=$built, untrusted=$untrusted)，后续驱动导入可能失败。" }
    } catch { Warn "证书链验证不可用: $($_.Exception.Message)" }

    Sec '2/4 - 生成合规签名 CAT（libwdi 算法）'
    try { Build-Cat -Cert $cert } catch { Err "生成 CAT 失败: $($_.Exception.Message)"; Done 8 }
    # 关键：用 Windows 自身的验证器确认 CAT 结构/digest 合法。
    # 0x80096010(BAD_DIGEST)/0x800B0100(NOSIGNATURE) 必须立即失败，不再盲目继续。
    $vh = Verify-Cat
    Inf ("CAT 预检 WinVerifyTrust = 0x{0:X8}" -f $vh)
    if ($vh -eq 0x80096010) { Err 'CAT digest 校验失败 (0x80096010)，中止安装。'; Done 10 }
    if ($vh -eq 0x800B0100) { Err 'CAT 无有效签名 (0x800B0100)，中止安装。'; Done 10 }
    OK 'CAT 结构与 digest 预检通过。'

    Sec '3/4 - 加入驱动库并强制安装'
    pnputil /add-driver "`"$INF`"" 2>&1 | ForEach-Object { Inf $_ }
    $reboot = $false
    $apiOk = $false
    try {
        Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class NJB3DevInst {
    [DllImport("newdev.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    public static extern bool UpdateDriverForPlugAndPlayDevices(IntPtr hwnd, string hwid, string inf, uint flags, out bool reboot);
}
'@
        $apiOk = [NJB3DevInst]::UpdateDriverForPlugAndPlayDevices([IntPtr]::Zero, $HWID, $INF, [uint32]1, [ref]$reboot)
        if ($apiOk) { OK '驱动安装 API 成功。' }
        else { Warn ("API 失败, 0x{0:X8}" -f [Runtime.InteropServices.Marshal]::GetLastWin32Error()) }
    } catch { Warn ("安装 API 不可用: " + $_.Exception.Message) }

    if (-not $apiOk) {
        $txt = (pnputil /enum-drivers 2>&1 | Out-String) -split "`r?`n"
        $pn = $null
        for ($i = 0; $i -lt $txt.Length; $i++) {
            if ($txt[$i] -match 'njb3_winusb\.inf') {
                for ($j = [Math]::Max(0, $i - 10); $j -lt $i; $j++) {
                    if ($txt[$j] -match '(oem\d+\.inf)') { $pn = $Matches[1]; break }
                }
                break
            }
        }
        if ($pn) { pnputil /install-driver $pn 2>&1 | ForEach-Object { Inf $_ } }
        else { Warn '未在驱动库找到 njb3_winusb.inf 的发布名。' }
    }

    Sec '4/4 - 验证'
    Start-Sleep -Seconds 2
    $d2 = Find-NJB3
    if (-not $d2) { Warn '设备暂时消失，拔插一次 USB 后再看状态。'; Done 6 }
    Show-Status
    $svc2 = (Get-PnpDeviceProperty -InstanceId $d2.InstanceId -KeyName 'DEVPKEY_Device_Service' -ErrorAction SilentlyContinue).Data
    if ($svc2 -match 'winusb') { OK '成功！设备已绑定 WinUSB，回到 GUI 点连接设备。'; Done 0 }
    else { Err '切换未生效，请把全部输出发给开发者。'; Done 7 }
}
