# macOS builds (Vanta-macOS-Intel.zip, Vanta-macOS-AppleSilicon.zip, Vanta-macOS.zip)

Vanta.app/Contents/
- MacOS/Vanta: this folder's launcher script (picks Python by the real CPU, `sysctl hw.optional.arm64`; leaves Rosetta)
- Info.plist
- Resources/app/: vanta/, assets/, pyproject.toml, README.md
- Resources/python/arm64 and Resources/python/x86_64: python-build-standalone
  cpython-3.12.15+20261009 `*-apple-darwin-install_only` (SHA256 checked against
  the release SHA256SUMS). test/, idlelib, tkinter, ensurepip, pip, tcl/tk are removed.

Zip with Unix modes and symlinks kept (Python zipfile, create_system=3).
Vanta is stdlib-only, so no pip install is needed. The UI opens in the default
browser (no pywebview). The first run downloads the game (vanta/launcher/minecraft/first_run.py).

Three zips from the same app files (built by /workspace/release/build_mac151.py):
- Intel: python/x86_64 only, LSMinimumSystemVersion 12.0, LSArchitecturePriority x86_64.
- AppleSilicon: python/arm64 only, LSMinimumSystemVersion 11.0, LSRequiresNativeExecution.
- Universal (Vanta-macOS.zip): both Pythons, LSMinimumSystemVersion 11.0.
The x86_64 Python is a native x86_64 Mach-O built for macOS 10.15+, so it runs on Monterey and newer.
