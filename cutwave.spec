# PyInstaller spec for the native cutwave.app. Build with:
#   source venv/bin/activate && pyinstaller cutwave.spec --noconfirm
#
# For a personal, unmetered build for the developer's own machine only
# (never for distribution):
#   CUTWAVE_BUILD_UNLOCKED=1 pyinstaller cutwave.spec --noconfirm
import os

block_cipher = None
root = os.path.abspath(".")
build_unlocked = os.environ.get("CUTWAVE_BUILD_UNLOCKED") == "1"

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

info_plist = {
    "NSHighResolutionCapable": True,
    "LSMinimumSystemVersion": "11.0",
    "CFBundleShortVersionString": "0.1.0",
}
if build_unlocked:
    info_plist["LSEnvironment"] = {"CUTWAVE_DEVELOPER_UNLOCK": "1"}

app = BUNDLE(
    coll,
    name="cutwave.app",
    icon=None,
    bundle_identifier="com.cutwave.app",
    info_plist=info_plist,
)

# macOS (15+) tags files copied during the build with a com.apple.provenance
# xattr, which codesign treats as disallowed "detritus" and invalidates the
# bundle's signature -- Gatekeeper then hard-rejects the app as "damaged"
# rather than showing the milder, bypassable unidentified-developer prompt.
# Strip xattrs and re-sign (ad-hoc for now, until a real Developer ID
# certificate is available) so the app is at least a *valid* signed bundle.
import subprocess
app_path = os.path.join(root, "dist", "cutwave.app")
subprocess.run(["xattr", "-cr", app_path], check=True)
subprocess.run(["codesign", "--deep", "--force", "--sign", "-", app_path], check=True)
