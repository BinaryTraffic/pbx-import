# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ['oh_cal_import_db_sc.py'],
    pathex=['..'],
    binaries=[],
    datas=[('../.env', '.'), ('C:\\\\Users\\\\hshim\\\\AppData\\\\Local\\\\Programs\\\\Python\\\\Python312\\\\Lib\\\\site-packages\\\\seleniumwire\\\\ca.crt', 'seleniumwire')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='oh_cal_import_db_sc',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
