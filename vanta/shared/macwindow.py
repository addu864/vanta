"""Native macOS window for Vanta: the local UI inside a WKWebView (pyobjc). Never opens a browser.

Run as `python -m vanta.shared.macwindow`. The HTTP server runs on a background thread
in this same process; closing the window quits Vanta. If pyobjc or WebKit can't load,
Vanta shows a native alert and exits instead of falling back to a browser.
"""
from __future__ import annotations

import os
import threading
import time
import urllib.request

PORT_ENV = "VANTA_PORT"


def _wait_for(url: str, seconds: float = 30.0) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=1):
                return True
        except Exception:  # noqa: BLE001 - server still starting
            time.sleep(0.2)
    return False


def server_url() -> str:
    return f"http://127.0.0.1:{os.environ.get(PORT_ENV, '8765')}/"


def start_server() -> None:
    os.environ["VANTA_OPEN_BROWSER"] = "0"  # this process shows its own window
    from vanta.launcher.ui.server import serve
    threading.Thread(target=serve, name="vanta-server", daemon=True).start()


def _alert(message: str) -> None:
    import subprocess
    subprocess.run(["/usr/bin/osascript", "-e", f'display alert "Vanta" message "{message}"'], check=False)


def run_window(url: str) -> None:
    import objc  # noqa: F401
    from AppKit import (NSApp, NSApplication, NSApplicationActivationPolicyRegular, NSBackingStoreBuffered, NSMakeRect,
                        NSMenu, NSMenuItem, NSWindow, NSWindowStyleMaskClosable, NSWindowStyleMaskMiniaturizable,
                        NSWindowStyleMaskResizable, NSWindowStyleMaskTitled)
    from Foundation import NSObject, NSURL, NSURLRequest
    from WebKit import WKWebView, WKWebViewConfiguration

    class Delegate(NSObject):
        def applicationShouldTerminateAfterLastWindowClosed_(self, app):  # noqa: N802
            return True

        # Links that leave Vanta (GitHub, Modrinth…) open in the user's browser; Vanta itself never does.
        def webView_decidePolicyForNavigationAction_decisionHandler_(self, web, action, handler):  # noqa: N802
            target = action.request().URL()
            host = str(target.host() or "") if target else ""
            if target is not None and host not in ("127.0.0.1", "localhost", "") and str(target.scheme()) in ("http", "https"):
                from AppKit import NSWorkspace
                NSWorkspace.sharedWorkspace().openURL_(target)
                handler(0)  # WKNavigationActionPolicyCancel
                return
            handler(1)  # WKNavigationActionPolicyAllow

        def webView_createWebViewWithConfiguration_forNavigationAction_windowFeatures_(self, web, cfg, action, feats):  # noqa: N802
            target = action.request().URL()
            if target is not None:
                from AppKit import NSWorkspace
                NSWorkspace.sharedWorkspace().openURL_(target)
            return None

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    delegate = Delegate.alloc().init()
    app.setDelegate_(delegate)

    # Minimal menu so Cmd+Q, Cmd+C/V/X/A and Cmd+W work.
    bar = NSMenu.alloc().init()
    for title, items in (("Vanta", [("Quit Vanta", "terminate:", "q")]),
                         ("Edit", [("Undo", "undo:", "z"), ("Redo", "redo:", "Z"), ("Cut", "cut:", "x"), ("Copy", "copy:", "c"),
                                   ("Paste", "paste:", "v"), ("Select All", "selectAll:", "a")]),
                         ("Window", [("Close", "performClose:", "w"), ("Minimize", "performMiniaturize:", "m")])):
        top = NSMenuItem.alloc().init()
        menu = NSMenu.alloc().initWithTitle_(title)
        for label, action, key in items:
            menu.addItemWithTitle_action_keyEquivalent_(label, action, key)
        top.setSubmenu_(menu)
        bar.addItem_(top)
    app.setMainMenu_(bar)

    style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskResizable
    win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(NSMakeRect(0, 0, 1320, 860), style, NSBackingStoreBuffered, False)
    win.setTitle_("VANTA")
    win.setMinSize_((960, 640))
    win.center()
    win.setFrameAutosaveName_("VantaMain")
    cfg = WKWebViewConfiguration.alloc().init()
    web = WKWebView.alloc().initWithFrame_configuration_(win.contentView().bounds(), cfg)
    web.setAutoresizingMask_(18)  # width + height sizable
    web.setNavigationDelegate_(delegate)
    web.setUIDelegate_(delegate)
    win.setContentView_(web)
    web.loadRequest_(NSURLRequest.requestWithURL_(NSURL.URLWithString_(url)))
    win.makeKeyAndOrderFront_(None)
    app.activateIgnoringOtherApps_(True)
    app.run()


def main() -> None:
    url = server_url()
    already = _wait_for(url + "api/servers", 0.5)
    if not already:
        start_server()
        if not _wait_for(url + "api/servers", 40):
            _alert("Vanta could not start. See launcher-ui.log in ~/Library/Application Support/Vanta.")
            raise SystemExit(1)
    try:
        run_window(url)
    except ImportError as exc:
        _alert(f"Vanta's window could not load ({exc}). Please download Vanta again from addu864.github.io/vanta.")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
