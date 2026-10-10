# macOS build (Vanta-macOS.zip)

Vanta.app/Contents/
- MacOS/Vanta: this folder's launcher script (picks Python by `uname -m`)
- Info.plist
- Resources/app/: vanta/, assets/, pyproject.toml, README.md
- Resources/python/arm64 and Resources/python/x86_64: python-build-standalone
  cpython-3.12.15+20261009 `*-apple-darwin-install_only` (SHA256 checked against
  the release SHA256SUMS). test/, idlelib, tkinter, ensurepip, pip, tcl/tk are removed.

Zip with Unix modes and symlinks kept (Python zipfile, create_system=3).
Vanta is stdlib-only, so no pip install is needed. The UI opens in the default
browser (no pywebview). The first run downloads the game (vanta/launcher/minecraft/first_run.py).
