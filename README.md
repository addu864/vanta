# Vanta

Vanta is a local launcher for Minecraft Java. It keeps profiles, a local play launch, mods, skins, server folders, and HUD settings on this machine. Version 1.0.0 can start a Java process for a LOCAL TEST ACCOUNT when `client.jar` is already installed. See docs/VANTA-LAUNCH.md.

## Run

```bash
cd /workspace/vanta
python3 -m vanta
```

`python3 run.py` is the same entry. The process prints a URL, normally `http://127.0.0.1:8765/`. It listens on `127.0.0.1` only.

Tests:

```bash
/workspace/vanta/.venv/bin/python -m pytest -q --tb=short
```

## Limits

Read [docs/VANTA-LAUNCH.md](docs/VANTA-LAUNCH.md) for what PLAY actually starts. Read [docs/VANTA-1.0.md](docs/VANTA-1.0.md) for the 0.1–0.9 behavior that is still in this tree. In short: PLAY launches Java only when the client jar is already present, and `launched` is true only while that process is alive. There is no public join address, the HUD is not drawn in a world, resource packs are stubs, shaders stay `NOT_APPLIED`, and the controller check reports `NO_DEVICE` when no pad is present. The account you can select is a **LOCAL TEST ACCOUNT**, not online-mode auth.

Runtime files live in `.vanta-data` (or `VANTA_DATA_DIR`). That directory is user data. This release does not delete it.

## What the downloads contain (v1.3.1+)

The Windows and macOS zips contain only Vanta's code, its UI assets and a Python runtime. No Mojang files (client.jar, libraries, assets) and no third-party mods, resource packs or shaders are bundled: their licenses don't allow it (Bare Bones is All Rights Reserved; Fresh Animations and Complementary have custom terms; Sodium, Entity Culling and Not Enough Animations use restrictive custom licenses). On first launch Vanta downloads Minecraft 1.21.11 + Fabric from Mojang/Fabric and the mods and packs from Modrinth, hash-checked, with a progress bar on PLAY (`vanta/launcher/minecraft/first_run.py`). It is on by default on Windows; `VANTA_AUTO_INSTALL=0` turns it off, and the macOS launcher sets `VANTA_AUTO_INSTALL=1`.

Vanta's own code has no license file yet, so no license is granted for it.
