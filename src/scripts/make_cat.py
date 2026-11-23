# -*- coding: utf-8 -*-
"""严格按照 libwdi pki.c 的 create_cat + sign 流程生成 INF 的签名 CAT。
用 ctypes 直接调用 wintrust.dll / mssign32.dll / crypt32.dll。
"""
import ctypes, ctypes.wintypes as w, sys, os
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# 命令行参数: make_cat.exe <INF> <CAT输出> [证书DER文件] [thumbprint] [store: LM|CU] [PFX文件] [PFX口令]
# 取证书优先级：PFX 文件（自带私钥，跨账户/精简系统最可靠）
#              → thumbprint 从指定 store（带私钥）
#              → subject 从指定 store → DER 文件（无私钥，仅调试）
# v8：新增 PFX 路径。精简版系统或非管理员环境下，LocalMachine 证书的私钥可能
#     无法被引擎访问（0x80092006）；由安装脚本先 Export-PfxCertificate 导出
#     带私钥 PFX 再传入，可彻底规避 store 私钥 ACL 问题。
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INF = sys.argv[1] if len(sys.argv) >= 2 else os.path.join(_ROOT, "release", "01_驱动", "njb3_winusb.inf")
CAT = sys.argv[2] if len(sys.argv) >= 3 else os.path.join(_ROOT, "release", "01_驱动", "njb3_winusb.cat")
CERT_DER = sys.argv[3] if len(sys.argv) >= 4 and sys.argv[3] else None
THUMBPRINT = sys.argv[4] if len(sys.argv) >= 5 and sys.argv[4] else None
STORE = sys.argv[5] if len(sys.argv) >= 6 and sys.argv[5] else "LM"
CERT_PFX = sys.argv[6] if len(sys.argv) >= 7 and sys.argv[6] else None
PFX_PASS = sys.argv[7] if len(sys.argv) >= 8 and sys.argv[7] else "njb3sign"

wintrust = ctypes.WinDLL("wintrust", use_last_error=True)
crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_lastError=True) if False else ctypes.WinDLL("advapi32", use_last_error=True)

# ---- 常量 ----
CRYPTCAT_OPEN_CREATENEW = 0x00000001
CRYPTCAT_ATTR_AUTHENTICATED = 0x10000000
CRYPTCAT_ATTR_NAMEASCII = 0x00000001
CRYPTCAT_ATTR_DATAASCII = 0x00010000
ATTR_FLAGS = CRYPTCAT_ATTR_AUTHENTICATED | CRYPTCAT_ATTR_NAMEASCII | CRYPTCAT_ATTR_DATAASCII
PROV_RSA_FULL = 1
CRYPT_VERIFYCONTEXT = 0xF0000000
GENERIC_READ = 0x80000000
FILE_SHARE_READ = 1
OPEN_EXISTING = 3
FILE_ATTRIBUTE_NORMAL = 0x80
CALG_SHA_256 = 0x0000800c
SIGNER_SUBJECT_FILE = 1
SIGNER_CERT_STORE = 2
SIGNER_CERT_POLICY_CHAIN = 2
SIGNER_NO_ATTR = 0
X509_ASN_ENCODING = 1
OID_SPC_CAB = b"1.3.6.1.4.1.311.2.1.25"
OID_SHA1 = b"1.3.14.3.2.26"

# ---- 结构 ----
class CRYPT_ATTR_BLOB(ctypes.Structure):
    _fields_ = [("cbData", w.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

class CRYPT_ATTRIBUTE_TYPE_VALUE(ctypes.Structure):
    _fields_ = [("pszObjId", ctypes.c_char_p), ("Value", CRYPT_ATTR_BLOB)]

class CRYPT_ALGORITHM_IDENTIFIER(ctypes.Structure):
    _fields_ = [("pszObjId", ctypes.c_char_p), ("Parameters", CRYPT_ATTR_BLOB)]

class SIP_INDIRECT_DATA(ctypes.Structure):
    _fields_ = [("Data", CRYPT_ATTRIBUTE_TYPE_VALUE),
                ("DigestAlgorithm", CRYPT_ALGORITHM_IDENTIFIER),
                ("Digest", CRYPT_ATTR_BLOB)]

class SPC_LINK(ctypes.Structure):
    _fields_ = [("dwLinkChoice", w.DWORD), ("pwszUrl", ctypes.c_wchar_p)]

# Inf GUID (SPC_INDIRECT_DATA_OBJID for INF files, from libwdi)
try:
    GUID_TYPE = ctypes.GUID
except AttributeError:
    from ctypes import _SimpleCData
    class GUID_TYPE(ctypes.Structure):
        _fields_ = [("Data1", w.DWORD), ("Data2", w.WORD), ("Data3", w.WORD),
                    ("Data4", ctypes.c_byte * 8)]
        def __init__(self, s=None):
            super().__init__()
            if s:
                import uuid
                u = uuid.UUID(s)
                self.Data1 = u.time_low; self.Data2 = u.time_mid; self.Data3 = u.time_hi_version
                for i, b in enumerate(u.bytes[8:]): self.Data4[i] = b
INF_GUID = GUID_TYPE("{DE351A42-8E59-11D0-8C47-00C04FC295EE}")

# ---- 函数签名 ----
advapi32.CryptAcquireContextW.argtypes = [ctypes.POINTER(w.HANDLE), w.LPCWSTR, w.LPCWSTR, w.DWORD, w.DWORD]
advapi32.CryptAcquireContextW.restype = w.BOOL
advapi32.CryptReleaseContext.argtypes = [w.HANDLE, w.DWORD]
kernel32.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD, w.DWORD, w.HANDLE]
kernel32.CreateFileW.restype = w.HANDLE
kernel32.CloseHandle.argtypes = [w.HANDLE]
wintrust.CryptCATAdminCalcHashFromFileHandle.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD), ctypes.POINTER(ctypes.c_byte), w.DWORD]
wintrust.CryptCATAdminCalcHashFromFileHandle.restype = w.BOOL
wintrust.CryptCATOpen.argtypes = [w.LPCWSTR, w.DWORD, w.HANDLE, w.DWORD, w.DWORD]
wintrust.CryptCATOpen.restype = w.HANDLE
wintrust.CryptCATPutCatAttrInfo.argtypes = [w.HANDLE, w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p]
wintrust.CryptCATPutCatAttrInfo.restype = w.HANDLE
wintrust.CryptCATPutMemberInfo.argtypes = [w.HANDLE, w.LPCWSTR, w.LPCWSTR, ctypes.POINTER(GUID_TYPE), w.DWORD, w.DWORD, ctypes.c_void_p]
wintrust.CryptCATPutMemberInfo.restype = w.HANDLE
wintrust.CryptCATPutAttrInfo.argtypes = [w.HANDLE, w.HANDLE, w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p]
wintrust.CryptCATPutAttrInfo.restype = w.HANDLE
wintrust.CryptCATPersistStore.argtypes = [w.HANDLE]
wintrust.CryptCATPersistStore.restype = w.BOOL
wintrust.CryptCATClose.argtypes = [w.HANDLE]
crypt32.CryptEncodeObject.argtypes = [w.DWORD, ctypes.c_char_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_byte), ctypes.POINTER(w.DWORD)]
crypt32.CryptEncodeObject.restype = w.BOOL

def check(cond, msg):
    if not cond:
        err = ctypes.get_last_error()
        raise RuntimeError(f"{msg} (err=0x{err:08X})")

def main():
    # v8 修复：删除旧的 CurrentUser\My 硬编码前置检查。
    # 安装脚本把签名证书建在 LocalMachine\My，旧代码只查 CurrentUser，找不到就
    # 直接 return 9（NO_CERT），导致后面按 thumbprint/store 的三级兜底永远走不到。
    # 证书获取统一交给下方“thumbprint → subject → DER 文件”三级逻辑处理。

    # hash INF
    hProv = w.HANDLE()
    check(advapi32.CryptAcquireContextW(ctypes.byref(hProv), None, None, PROV_RSA_FULL, CRYPT_VERIFYCONTEXT),
          "CryptAcquireContext")
    hFile = kernel32.CreateFileW(INF, GENERIC_READ, FILE_SHARE_READ, None, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, None)
    check(hFile != w.HANDLE(-1).value, "CreateFile INF")
    cbHash = w.DWORD(20)
    pbHash = (ctypes.c_byte * 20)()
    check(wintrust.CryptCATAdminCalcHashFromFileHandle(hFile, ctypes.byref(cbHash), pbHash, 0),
          "CalcHashFromFileHandle")
    kernel32.CloseHandle(hFile)
    hash_bytes = bytes(pbHash)[:cbHash.value]
    hash_hex = hash_bytes.hex().upper()
    print("INF SHA1 =", hash_hex)
    wszHash = ctypes.c_wchar_p(hash_hex)

    if os.path.exists(CAT): os.remove(CAT)
    hCat = wintrust.CryptCATOpen(CAT, CRYPTCAT_OPEN_CREATENEW, hProv, 0, 0)
    check(hCat not in (None, w.HANDLE(-1).value, 0), "CryptCATOpen")

    try:
        hwid = ctypes.c_wchar_p("usb\\vid_041e&pid_4101")
        os_list = ctypes.c_wchar_p("7_X86,7_X64,8_X86,8_X64,8_ARM,10_X86,10_X64,10_ARM")
        for tag, val in [("HWID1", hwid.value), ("OS", os_list.value)]:
            buf = ctypes.create_unicode_buffer(val)
            r = wintrust.CryptCATPutCatAttrInfo(hCat, tag, ATTR_FLAGS,
                                                  ctypes.sizeof(buf), buf)
            check(r, f"PutCatAttrInfo {tag}")

        # SPC_LINK for <<<Obsolete>>>
        link = SPC_LINK(dwLinkChoice=3, pwszUrl="<<<Obsolete>>>")
        encoded = (ctypes.c_byte * 256)()
        cbEnc = w.DWORD(256)
        check(crypt32.CryptEncodeObject(X509_ASN_ENCODING, OID_SPC_CAB,
                                        ctypes.byref(link), encoded, ctypes.byref(cbEnc)),
              "CryptEncodeObject SPC_LINK")
        print("SPC_LINK =", bytes(encoded)[:cbEnc.value].hex().upper())

        pbLink = (ctypes.c_byte * cbEnc.value).from_buffer_copy(bytes(encoded)[:cbEnc.value])
        pbHashBuf = (ctypes.c_byte * cbHash.value).from_buffer_copy(hash_bytes)

        sip = SIP_INDIRECT_DATA()
        sip.Data.pszObjId = OID_SPC_CAB
        sip.Data.Value.cbData = cbEnc.value
        sip.Data.Value.pbData = ctypes.cast(pbLink, ctypes.POINTER(ctypes.c_byte))
        sip.DigestAlgorithm.pszObjId = OID_SHA1
        sip.DigestAlgorithm.Parameters.cbData = 0
        sip.DigestAlgorithm.Parameters.pbData = None
        sip.Digest.cbData = cbHash.value
        sip.Digest.pbData = ctypes.cast(pbHashBuf, ctypes.POINTER(ctypes.c_byte))
        print("sizeof SIP =", ctypes.sizeof(sip))

        member = wintrust.CryptCATPutMemberInfo(hCat, None, wszHash,
                                                  ctypes.byref(INF_GUID), 0x200,
                                                  ctypes.sizeof(sip), ctypes.byref(sip))
        check(member, "CryptCATPutMemberInfo")

        fname = ctypes.create_unicode_buffer("njb3_winusb.inf")
        osattr = ctypes.create_unicode_buffer("2:5.1,2:5.2,2:6.0,2:6.1")
        check(wintrust.CryptCATPutAttrInfo(hCat, member, "File", ATTR_FLAGS,
              ctypes.sizeof(fname), fname), "PutAttrInfo File")
        check(wintrust.CryptCATPutAttrInfo(hCat, member, "OSAttr", ATTR_FLAGS,
              ctypes.sizeof(osattr), osattr), "PutAttrInfo OSAttr")

        check(wintrust.CryptCATPersistStore(hCat), "CryptCATPersistStore")
        print("CAT created, size =", os.path.getsize(CAT))
    finally:
        wintrust.CryptCATClose(hCat)
        advapi32.CryptReleaseContext(hProv, 0)

    # ---- 获取签名证书 PCCERT_CONTEXT ----
    CERT_SYSTEM_STORE_CURRENT_USER = 0x00010000
    CERT_STORE_READONLY_FLAG = 0x00008000
    crypt32.CertOpenStore.argtypes = [ctypes.c_void_p, w.DWORD, w.HANDLE, w.DWORD, ctypes.c_void_p]
    crypt32.CertOpenStore.restype = ctypes.c_void_p
    crypt32.CertFindCertificateInStore.argtypes = [ctypes.c_void_p, w.DWORD, w.DWORD, w.DWORD, ctypes.c_void_p, ctypes.c_void_p]
    crypt32.CertFindCertificateInStore.restype = ctypes.c_void_p
    crypt32.CertCreateCertificateContext.argtypes = [w.DWORD, ctypes.c_void_p, w.DWORD]
    crypt32.CertCreateCertificateContext.restype = ctypes.c_void_p
    crypt32.CertCloseStore.argtypes = [ctypes.c_void_p, w.DWORD]
    crypt32.CertFreeCertificateContext.argtypes = [ctypes.c_void_p]

    hStore = None
    hPfxStore = None
    pCert = None
    CERT_STORE_PROV_SYSTEM_A = 9
    store_flag = (0x00020000 if STORE == "LM" else CERT_SYSTEM_STORE_CURRENT_USER)  # CERT_SYSTEM_STORE_LOCAL_MACHINE=0x20000

    # 最高优先级：从 PFX 导入证书+私钥（自带私钥，不依赖 store 的 ACL）。
    # CRYPT_DATA_BLOB + PFXImportCertStore(CRYPT_EXPORTABLE)，签名后私钥随 context 可用。
    if CERT_PFX and os.path.exists(CERT_PFX):
        try:
            class BLOB(ctypes.Structure):
                _fields_ = [("cbData", w.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]
            pfx_bytes = open(CERT_PFX, "rb").read()
            pfx_buf = (ctypes.c_byte * len(pfx_bytes)).from_buffer_copy(pfx_bytes)
            blob = BLOB(len(pfx_bytes), ctypes.cast(pfx_buf, ctypes.POINTER(ctypes.c_byte)))
            crypt32.PFXImportCertStore.argtypes = [ctypes.POINTER(BLOB), w.LPCWSTR, w.DWORD]
            crypt32.PFXImportCertStore.restype = ctypes.c_void_p
            crypt32.CertFindCertificateInStore.argtypes = [ctypes.c_void_p, w.DWORD, w.DWORD, w.DWORD, ctypes.c_void_p, ctypes.c_void_p]
            crypt32.CertFindCertificateInStore.restype = ctypes.c_void_p
            # CRYPT_EXPORTABLE=0x1, CRYPT_USER_KEYSET=0x1000, CERT_FIND_ANY=0
            hPfxStore = crypt32.PFXImportCertStore(ctypes.byref(blob), ctypes.c_wchar_p(PFX_PASS), 0x00000001)
            if hPfxStore:
                pCert = crypt32.CertFindCertificateInStore(hPfxStore, X509_ASN_ENCODING, 0, 0, None, None)
                if pCert:
                    print("Cert loaded from PFX (with private key)")
        except Exception as e:
            print("PFX import failed:", e)

    # 优先：按 thumbprint 从指定 store 找证书（带私钥关联，最可靠）
    if not pCert and THUMBPRINT:
        try:
            hStore = crypt32.CertOpenStore(ctypes.c_void_p(CERT_STORE_PROV_SYSTEM_A), 0, None,
                           store_flag | CERT_STORE_READONLY_FLAG, ctypes.c_char_p(b"My"))
            # CERT_FIND_SHA1_HASH = 0x00010000, pvPara = CRYPT_HASH_BLOB
            class HASHBLOB(ctypes.Structure):
                _fields_ = [("cbData", w.DWORD), ("pbData", ctypes.c_void_p)]
            raw = bytes.fromhex(THUMBPRINT)
            # 必须把 buffer 绑定到变量，防止 ctypes 临时对象在签名前被回收导致指针悬空
            raw_buf = ctypes.create_string_buffer(raw)
            hb = HASHBLOB(len(raw), ctypes.cast(raw_buf, ctypes.c_void_p))
            pCert = crypt32.CertFindCertificateInStore(hStore, X509_ASN_ENCODING, 0,
                        0x00010000, ctypes.byref(hb), None)
            if pCert:
                print(f"Cert loaded from {STORE}\\My by thumbprint")
        except Exception as e:
            print("thumbprint lookup failed:", e)
    # 兜底1：按 subject 从 store 找
    if not pCert:
        try:
            if not hStore:
                hStore = crypt32.CertOpenStore(ctypes.c_void_p(CERT_STORE_PROV_SYSTEM_A), 0, None,
                               store_flag | CERT_STORE_READONLY_FLAG, ctypes.c_char_p(b"My"))
            pCert = crypt32.CertFindCertificateInStore(hStore, X509_ASN_ENCODING, 0,
                        0x00080007, ctypes.c_wchar_p("NJB3 WinUSB Self-Signed Signing"), None)
            if pCert:
                print(f"Cert loaded from {STORE}\\My by subject")
        except Exception as e:
            print("subject lookup failed:", e)
    # 兜底2：从 DER 文件创建 context
    if not pCert and CERT_DER and os.path.exists(CERT_DER):
        der = open(CERT_DER, "rb").read()
        pCert = crypt32.CertCreateCertificateContext(X509_ASN_ENCODING, der, len(der))
        if pCert:
            print("Cert loaded from DER file")
    check(pCert, "no signing certificate found")

    # ---- SIGNER_* 结构 ----
    class SIGNER_FILE_INFO(ctypes.Structure):
        _fields_ = [("cbSize", w.DWORD), ("pwszFileName", ctypes.c_wchar_p), ("hFile", w.HANDLE)]
    class SIGNER_SUBJECT_INFO(ctypes.Structure):
        _fields_ = [("cbSize", w.DWORD), ("pdwIndex", ctypes.POINTER(w.DWORD)),
                    ("dwSubjectChoice", w.DWORD), ("pSignerFileInfo", ctypes.POINTER(SIGNER_FILE_INFO))]
    class SIGNER_CERT_STORE_INFO(ctypes.Structure):
        _fields_ = [("cbSize", w.DWORD), ("pSigningCert", ctypes.c_void_p),
                    ("dwCertPolicy", w.DWORD), ("hCertStore", ctypes.c_void_p)]
    class SIGNER_CERT(ctypes.Structure):
        _fields_ = [("cbSize", w.DWORD), ("dwCertChoice", w.DWORD),
                    ("pCertStoreInfo", ctypes.POINTER(SIGNER_CERT_STORE_INFO)), ("hwnd", w.HANDLE)]
    class SIGNER_SIGNATURE_INFO(ctypes.Structure):
        _fields_ = [("cbSize", w.DWORD), ("algidHash", w.DWORD), ("dwAttrChoice", w.DWORD),
                    ("pAttrAuthcode", ctypes.c_void_p), ("psAuthenticated", ctypes.c_void_p),
                    ("psUnauthenticated", ctypes.c_void_p)]

    fi = SIGNER_FILE_INFO(ctypes.sizeof(SIGNER_FILE_INFO), CAT, None)
    idx = w.DWORD(0)
    si = SIGNER_SUBJECT_INFO()
    si.cbSize = ctypes.sizeof(SIGNER_SUBJECT_INFO)
    si.pdwIndex = ctypes.pointer(idx)
    si.dwSubjectChoice = SIGNER_SUBJECT_FILE
    si.pSignerFileInfo = ctypes.pointer(fi)
    csi = SIGNER_CERT_STORE_INFO()
    csi.cbSize = ctypes.sizeof(SIGNER_CERT_STORE_INFO)
    csi.pSigningCert = pCert
    csi.dwCertPolicy = SIGNER_CERT_POLICY_CHAIN
    # 证书来自哪个 store 就把句柄传进去；PFX 导入的证书私钥关联依赖此句柄
    csi.hCertStore = hPfxStore if hPfxStore else hStore
    sc = SIGNER_CERT()
    sc.cbSize = ctypes.sizeof(SIGNER_CERT)
    sc.dwCertChoice = SIGNER_CERT_STORE
    sc.pCertStoreInfo = ctypes.pointer(csi)
    sc.hwnd = None
    ssi = SIGNER_SIGNATURE_INFO()
    ssi.cbSize = ctypes.sizeof(SIGNER_SIGNATURE_INFO)
    ssi.algidHash = CALG_SHA_256
    ssi.dwAttrChoice = SIGNER_NO_ATTR
    ssi.pAttrAuthcode = None
    ssi.psAuthenticated = None
    ssi.psUnauthenticated = None

    mssign32 = ctypes.WinDLL("mssign32", use_last_error=True)
    mssign32.SignerSignEx.argtypes = [w.DWORD, ctypes.POINTER(SIGNER_SUBJECT_INFO),
        ctypes.POINTER(SIGNER_CERT), ctypes.POINTER(SIGNER_SIGNATURE_INFO),
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    mssign32.SignerSignEx.restype = ctypes.c_long
    pCtx = ctypes.c_void_p()
    hr = mssign32.SignerSignEx(0, ctypes.byref(si), ctypes.byref(sc), ctypes.byref(ssi),
                               None, None, None, None, ctypes.byref(pCtx))
    hr_u = hr & 0xFFFFFFFF
    check(hr_u == 0, f"SignerSaveToCatalog/SignerSignEx failed hr=0x{hr_u:08X}")
    print("CAT signed, size =", os.path.getsize(CAT))

    crypt32.CertFreeCertificateContext(pCert)
    if hStore:
        crypt32.CertCloseStore(hStore, 0)
    if hPfxStore:
        crypt32.CertCloseStore(hPfxStore, 0)
    return 0

if __name__ == "__main__":
    sys.exit(main())
