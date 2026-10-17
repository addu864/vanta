Vanta for macOS - v1.5.1-mac

Pick the zip for your Mac (Apple menu > About This Mac):
  - "Intel" processor   -> Vanta-macOS-Intel.zip        (macOS 12 Monterey or newer)
  - "Apple M1/M2/M3/M4" -> Vanta-macOS-AppleSilicon.zip (macOS 11 or newer)
  - Not sure            -> Vanta-macOS.zip (universal, works on both)

1. Unzip, then drag Vanta.app to Applications (optional).
2. First time: right-click Vanta.app > Open > Open. The app is unsigned, so
   Gatekeeper blocks a normal double-click the first time.
   (If macOS still refuses: System Settings > Privacy & Security > "Open Anyway".)
3. Vanta opens in your default browser at http://127.0.0.1:8765/
4. Java: if no suitable Java is installed, Vanta downloads Eclipse Temurin for
   your CPU (x64 on Intel; arm64 on Apple Silicon, x64 via Rosetta 2 for
   Minecraft before 1.19) into ~/Library/Application Support/Vanta/runtime,
   checking its SHA-256. You can also install it yourself:
   https://adoptium.net/temurin/releases/?os=mac
5. First run downloads Minecraft 1.21.11 + Fabric + the default mods, packs and
   sounds into ~/Library/Application Support/Vanta (several hundred MB; check
   install.log / first-run-install.json there). Press PLAY after it finishes.

Intel Macs: Vanta picks 3-6 GB of RAM depending on your memory, and on Intel
Iris/UHD graphics new instances start with render distance 8, Fast graphics,
60 FPS and VSync. Trailer style (shaders) warns before turning on.

Local (offline) accounts only; no Microsoft sign-in. Not yet tested on a real Mac.
