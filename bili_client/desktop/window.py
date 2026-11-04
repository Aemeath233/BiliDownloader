from __future__ import annotations

import base64
import ctypes
import sys
import threading
from pathlib import Path


def asset_html() -> str:
    assets = Path(__file__).parent / "assets"
    # An in-memory document avoids a local HTTP server and file:// permissions.
    return (
        (assets / "index.html")
        .read_text(encoding="utf-8")
        .replace("/* APP_STYLES */", (assets / "app.css").read_text(encoding="utf-8"))
        .replace("/* APP_SCRIPT */", (assets / "app.js").read_text(encoding="utf-8"))
        .replace(
            "__APP_BRAND_IMAGE__",
            "data:image/png;base64," + base64.b64encode((assets / "brand.png").read_bytes()).decode("ascii"),
        )
    )


def fixed_runtime_path() -> Path | None:
    roots = [Path(sys.executable).parent, Path(getattr(sys, "_MEIPASS", Path(__file__).parents[2]))]
    roots.append(Path(__file__).parents[2] / "vendor")
    for root in roots:
        candidate = root / "webview2"
        if (candidate / "msedgewebview2.exe").is_file():
            return candidate.resolve()
    return None


def run_desktop(runtime, single_instance=None, *, debug=False, on_loaded=None) -> None:
    import webview

    from bili_client.desktop.bridge import DesktopBridge

    bridge = DesktopBridge(runtime)
    webview.settings["ALLOW_FILE_URLS"] = False
    webview.settings["ALLOW_DOWNLOADS"] = False
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False
    webview.settings["OPEN_DEVTOOLS_IN_DEBUG"] = False
    fixed = fixed_runtime_path()
    if fixed:
        webview.settings["WEBVIEW2_RUNTIME_PATH"] = str(fixed)
    window = webview.create_window(
        "BiliDownloader",
        html=asset_html(),
        js_api=bridge,
        width=1180,
        height=800,
        min_size=(760, 560),
        background_color="#f6f9fc",
        text_select=True,
    )
    bridge._window = window
    window._allow_close = False

    def closing():
        if window._allow_close or runtime.manager is None or not runtime.manager.has_unfinished_tasks():
            return True
        threading.Thread(target=lambda: window.evaluate_js("window.desktopRequestClose()"), daemon=True).start()
        return False

    window.events.closing += closing

    def loaded():
        if sys.platform == "win32":

            def guard(_sender, args):
                if str(args.Uri) != "about:blank":
                    args.Cancel = True

            window.native.browser.webview.NavigationStarting += guard
        if on_loaded:
            on_loaded(window, runtime)

    window.events.loaded += loaded
    if single_instance is not None:

        def activate():
            window.restore()
            window.show()

        single_instance.set_activate_callback(activate)
    icon = Path(__file__).parents[2] / "logo.ico"
    try:
        webview.start(
            func=runtime.initialize,
            gui="edgechromium",
            debug=debug,
            private_mode=True,
            icon=str(icon) if icon.exists() else None,
        )
    except Exception as exc:
        if sys.platform == "win32":
            ctypes.windll.user32.MessageBoxW(
                None,
                "内嵌界面启动失败。\n请重新运行安装程序修复依赖，或安装 Microsoft WebView2 Runtime。\n\n" + str(exc),
                "BiliDownloader",
                0x10,
            )
        raise
