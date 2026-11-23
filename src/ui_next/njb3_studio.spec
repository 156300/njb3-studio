# -*- mode: python ; coding: utf-8 -*-
"""NJB3 Studio 新版 UI 打包配置（不改动原 njb3_gui.spec）。"""
import os

HERE = os.path.dirname(os.path.abspath(SPEC))
ROOT = os.path.dirname(HERE)
SCRIPTS = os.path.join(ROOT, "scripts")

a = Analysis(
    ['njb3_studio.py'],
    pathex=[HERE, SCRIPTS],
    binaries=[(os.path.join(ROOT, "runtime", "libusb-1.0.dll"), '.')],
    datas=[],
    hiddenimports=[
        'tkinter', 'tkinter.ttk', 'tkinter.filedialog', 'tkinter.messagebox',
        'tkinter.simpledialog',
        'njb3_theme', 'njb3_dock',
        'njb3_gui', 'njb3_pde', 'njb3_explorer', 'njb3_layout', 'njb3_skin',
        'njb3_transfer',
        'usb.backend.libusb1', 'libusb_package',
    ],
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[],
    noarchive=False, optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name='njb3_studio',
    debug=False, bootloader_ignore_signals=False, strip=False, upx=True,
    upx_exclude=['tk86t.dll', 'tcl86t.dll', '_tkinter.pyd'],
    runtime_tmpdir=None, console=False,
    disable_windowed_traceback=False, argv_emulation=False, target_arch=None,
    codesign_identity=None, entitlements_file=None,
)
