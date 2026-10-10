Vanta for macOS (Apple Silicon and Intel) - v1.1.0-mac

1. Unzip, then drag Vanta.app to Applications (optional).
2. First time: right-click Vanta.app > Open > Open. The app is unsigned, so
   Gatekeeper blocks a normal double-click the first time.
   (If macOS still refuses: System Settings > Privacy & Security > "Open Anyway".)
3. Vanta opens in your default browser at http://127.0.0.1:8765/
4. Install Java 21 or newer: https://adoptium.net/temurin/releases/?os=mac
5. First run downloads Minecraft 1.21.11 + Fabric + the default mods, packs and
   sounds into ~/Library/Application Support/Vanta (several hundred MB; check
   install.log / first-run-install.json there). Press PLAY after it finishes.

Local (offline) accounts only; no Microsoft sign-in. Untested on a real Mac.
