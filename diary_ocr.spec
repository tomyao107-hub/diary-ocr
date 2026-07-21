# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec: Windows development portable build (onedir).
# Local PP-OCR (paddle) is optional and not fully frozen — use source+venv for that.

from pathlib import Path

block_cipher = None
root = Path(SPECPATH)

a = Analysis(
    [str(root / "diary_ocr_app.py")],
    pathex=[str(root)],
    binaries=[],
    datas=[],
    hiddenimports=[
        "PyQt6",
        "PyQt6.QtCore",
        "PyQt6.QtGui",
        "PyQt6.QtWidgets",
        "PIL",
        "PIL.Image",
        "PIL.ImageOps",
        "PIL.ImageEnhance",
        "openai",
        "fitz",
        "dotenv",
        "docx",
        "diary_ocr",
        "diary_ocr.app",
        "diary_ocr.editor",
        "diary_ocr.engines",
        "diary_ocr.engines.local",
        "diary_ocr.engines.cloud",
        "diary_ocr.engines.registry",
        "diary_ocr.ui.main_window",
        "diary_ocr.ui.project_home",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Keep portable size reasonable; local PP-OCR runs from source venv.
        "paddle",
        "paddleocr",
        "paddlex",
        "torch",
        "tensorflow",
        "matplotlib",
        "tkinter",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DiaryOCR",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="DiaryOCR",
)
