# Vanta

Vanta is a local launcher for Minecraft Java. It keeps profiles, a local play launch, mods, skins, server folders, and HUD settings on this machine. Version 1.0.0 can start a Java process for a LOCAL TEST ACCOUNT when `client.jar` is already installed. See docs/VANTA-LAUNCH.md.

## New in 1.6.3

- **Copy mods to another instance** (INSTANCES): adds this instance's Modrinth mods, shaders and resource packs to another Fabric instance, each downloaded for that version and hash-checked. Mods without a build for that version are skipped and listed. Nothing is removed. Shows in the Downloads panel.

## New in 1.6.2

- **Downloads panel** (top bar): everything Vanta is downloading in one place. Minecraft downloads can be paused, resumed or cancelled; a cancelled download removes only the unfinished version folder and keeps the shared assets.
- **Instance names**: INSTANCES › Name, memory & Java. The name shows in PLAY, the dashboard and lists.
- **Cape and elytra preview** on the 3D skin, with built-in designs or your own 64×32 PNG. Preview only: it doesn't change what other players see.
- Loading placeholders, clearer empty states, plain error messages and a reconnect banner if the background service stops answering.
- Accessibility: every control has a name (axe-core clean on all tabs).
- Fix: the What's new popup now fetches fresh notes right after an update, and has built-in notes when offline.

## New in 1.6.1

- What's new popup after an update; logs viewer with search and an errors-only filter; full-size screenshot viewer with copy and show in folder; server favourites with sort by players, ping or name; preview images on shader and resource pack cards.

## New in 1.6.0

- Home dashboard on PLAY (recent instances, friends online on your network, release notes)
- Modrinth modpacks: search or import `.mrpack` (hash-checked, old mods backed up, main 1.21.11 instance never replaced) and Export `.mrpack`
- Worlds: list, back up, restore (never overwrites), delete (always backs up first)
- MODS: Update all; crash helper with suspected mods; Suggest RAM
- Appearance (6 purple accents, text size, high contrast, reduce motion), Ctrl+K quick jump, welcome tour
- macOS: native window (WKWebView via bundled pyobjc), never opens the browser

Guides: https://addu864.github.io/vanta/guides.html · Changelog: https://addu864.github.io/vanta/changelog.html

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
