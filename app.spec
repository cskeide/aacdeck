# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for AACdeck.
# Build with:  pyinstaller app.spec
# Output:      dist/aacdeck  (Linux) / dist/aacdeck.exe (Windows)

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        # reportlab sub-packages that are discovered at runtime
        "reportlab.graphics",
        "reportlab.pdfbase.cidfonts",
        "reportlab.pdfbase.pdfmetrics",
        "reportlab.pdfbase.ttfonts",
        "reportlab.pdfgen",
        "reportlab.pdfgen.canvas",
        "reportlab.lib.pagesizes",
        "reportlab.lib.units",
        "reportlab.lib.utils",
        # Pillow image plugins used by the script
        "PIL.JpegImagePlugin",
        "PIL.PngImagePlugin",
        "PIL.WebPImagePlugin",
        "PIL.AvifImagePlugin",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

# Single-file executable (onefile mode)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name="aacdeck",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # no terminal window — GUI only
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
