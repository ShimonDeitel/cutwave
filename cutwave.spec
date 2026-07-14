# PyInstaller spec for the native cutwave.app. Build with:
#   source venv/bin/activate && pyinstaller cutwave.spec --noconfirm
import os

block_cipher = None
root = os.path.abspath(".")

a = Analysis(
    ["server/desktop_app.py"],
    pathex=[os.path.join(root, "server")],
    binaries=[],
    datas=[
        (os.path.join(root, "static"), "static"),
        (os.path.join(root, "models"), "models"),
    ],
    hiddenimports=[
        "faster_whisper",
        "ctranslate2",
        "onnxruntime",
        "av",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="cutwave",
    debug=False,
    strip=False,
    upx=False,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="cutwave",
)

app = BUNDLE(
    coll,
    name="cutwave.app",
    icon=None,
    bundle_identifier="com.cutwave.app",
    info_plist={
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "11.0",
        "CFBundleShortVersionString": "0.1.0",
    },
)
