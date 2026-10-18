# Vanta

Vanta is a local launcher for Minecraft Java. It keeps profiles, a local play launch, mods, skins, server folders, and HUD settings on this machine. Version 1.0.0 can start a Java process for a LOCAL TEST ACCOUNT when `client.jar` is already installed. See docs/VANTA-LAUNCH.md.

## New in 1.7.0

- **A cleaner PLAY screen.** One hero card holds your account, instance, a big PLAY button and memory. Below it: Jump back in, and a compact friends and servers strip.
- **Accounts window.** Click your name to switch or add a local account. Nothing about accounts sits on the PLAY screen any more.
- **Things moved to their own tabs:** Add instance and version details are in INSTANCES, saved and recent servers are in SERVERS, What's new and the background picker are in SETTINGS, and Logs is a drawer.
- **One design everywhere:** one card style, one button style, a single icon set (no emoji), a small type scale and shorter text.
- **Smaller crash card** with one main button (Check mods, or Show log for vanilla). Copy, Get help and Open folder are under More.
- **Keyboard:** arrow keys, Home and End move between tabs. Esc closes the Accounts window and returns focus.
- **Mac and Linux fixes:** Add instance folder and Choose background now open a native picker (AppleScript on Mac, zenity or kdialog on Linux). On a Mac, a server that stopped no longer shows as running. Background errors are now logged or shown instead of hidden.

## New in 1.6.8

- **Mac fix: PLAY works again.** On macOS, Vanta checked whether the game was running by reading `/proc`, which only exists on Linux. The check always failed, so Vanta stopped a healthy Minecraft 0.6 s after PLAY and showed "The Java process exited before it stayed alive (exit None)". Vanta now trusts the process it started, and uses `ps` on macOS when it reconnects to a running game.
- When Java really stops at start-up, the message shows the real exit code (or the signal), and **Show game log** starts with the Java path, the CPU and the command (classpath shortened, no secrets).
- Mac: the Java that Vanta downloads gets its executable bits checked and the quarantine flag removed before PLAY.

## New in 1.6.7

- **Ctrl K finds instances**: type an instance name, or anything from its notes (a seed, base coordinates, a server IP), and Enter opens that instance in INSTANCES.

## New in 1.6.6

- **Crash card › Check mods** runs the mod check on the instance that crashed, with the same quick fixes.
- **Mod check**: when a needed mod (for example Fabric API) is in the mods folder but turned off, it offers **Turn it back on** instead of a download. Duplicate copies keep the original over a browser copy like `mod (1).jar`.
- **Home** shows playtime this week next to the total, and a 📝 icon on instances that have notes.
- **INSTANCES › Reset playtime** clears one instance's playtime (asks first; worlds and mods are not touched).
- **Crash helper** knows 4 more crashes: damaged game/mod files, incomplete Minecraft install, access denied (antivirus/permissions) and network port already in use.
- Keyboard focus stays inside the mod check dialog and returns to the button that opened it.

## New in 1.6.5

- **Mod check quick fixes**: in the mod check, turn off the older copy of a duplicate mod, turn off Forge or damaged jars (renamed to `.jar.disabled`, undo in INSTANCES › Mods), install Fabric API, or jump to MODS to find a missing mod.
- The mod check also runs before **joining a server** from BROWSE SERVERS or the PLAY server cards.
- **Crash card › Find a fix online** opens the website's troubleshooting search with the error filled in (only the error line is in the link).
- **Faster start**: the 3D skin library (~470 KB) loads after Vanta opens instead of before.
- SKINS no longer shows "Could not load the skin preview" before Minecraft 1.21.11 is downloaded; the default skins appear once it is.

## New in 1.6.4

- **Mod check before launch**: PLAY reads the mods in the instance first and warns about duplicate mods, Forge mods in a Fabric instance, missing Fabric API or other required mods, mods made for another Minecraft version, version mismatches between mods, known clashes (OptiFine + Sodium, ...) and damaged jars. Launch anyway, open MODS, or cancel. INSTANCES › Check mods runs it any time. Read-only: nothing is changed.
- **Playtime**: time in game per instance, counted by Vanta on this computer, on Home and in INSTANCES.
- **Instance notes**: a notes box per instance (seeds, IPs, to-dos), saved as you type, stays on this computer.
- **Offline MODS**: when Modrinth can't be reached, MODS shows the saved rows and searches saved results, with a clear note. Installing still needs the internet.
- **Crash helper**: 14 more known crashes (duplicate mods, missing Fabric API, OptiFine, Forge mods, Java too new, shader compile errors, graphics driver crashes, broken config files, damaged worlds, ticking entities, disk full, ...).
- **Faster start**: Home fills in about 0.15 s instead of about 2.3 s on a cold start (release notes now refresh in the background); the page and assets are revalidated instead of re-sent.

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
