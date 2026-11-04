"""Exercise the release Setup in an isolated install directory and remove it afterwards."""

from __future__ import annotations

import ctypes
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import winreg
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{A845D3E4-8D4E-48A2-9D1E-6B59E248947D}_is1"


def installed_location() -> str | None:
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, UNINSTALL_KEY, 0, winreg.KEY_READ | view) as key:
                    return winreg.QueryValueEx(key, "InstallLocation")[0]
            except FileNotFoundError:
                pass
    return None


def shell_folder(identifier: int) -> Path:
    result = ctypes.create_unicode_buffer(32768)
    if ctypes.windll.shell32.SHGetFolderPathW(None, identifier, None, 0, result):
        raise RuntimeError("Unable to resolve Windows shortcut folder")
    return Path(result.value)


def main() -> None:
    if installed_location() is not None:
        raise RuntimeError("An existing BiliDownloader install was found; refusing to alter it")
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", 48379)) == 0:
            raise RuntimeError("Close BiliDownloader before the isolated install test")
    setup = ROOT / "dist/installer/BiliDownloader-Setup.exe"
    group = "BiliDownloader"
    shortcut = shell_folder(2) / group / "BiliDownloader.lnk"
    if shortcut.exists():
        raise RuntimeError("An existing Start menu shortcut was found; refusing to replace it")
    desktop = shell_folder(0x10) / "BiliDownloader.lnk"
    test_desktop = not desktop.exists()
    report = {"setup_size_mib": round(setup.stat().st_size / 1024**2, 2)}
    with tempfile.TemporaryDirectory(prefix="bili-installer-check-") as temporary:
        sandbox = Path(temporary)
        target = sandbox / "安装 空目录"
        profile = sandbox / "profile"
        env = dict(os.environ, APPDATA=str(profile))
        args = [
            str(setup),
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            "/SP-",
            f"/DIR={target}",
            f"/GROUP={group}",
            "/LANG=chinesesimplified",
            "/TASKS=" + ("desktopicon" if test_desktop else ""),
        ]
        uninstaller = target / "unins000.exe"
        try:
            subprocess.run([*args, f"/LOG={sandbox / 'install.log'}"], env=env, check=True, timeout=90)
            (ROOT / "build/installer-install.log").write_bytes((sandbox / "install.log").read_bytes())
            sandbox_shortcut = profile / "Microsoft/Windows/Start Menu/Programs" / group / "BiliDownloader.lnk"
            if sandbox_shortcut.exists():
                shortcut = sandbox_shortcut
            assert Path(installed_location()).resolve() == target.resolve()
            assert shortcut.is_file()
            if test_desktop:
                assert desktop.is_file()
            assert "WebView2 runtime already installed; skipping bootstrapper." in (sandbox / "install.log").read_text(
                encoding="utf-8-sig"
            )
            assert not (target / "_internal/webview2").exists()
            assert not list((target / "_internal/ffmpeg").glob("*.dll"))
            report["install_and_shortcuts"] = "passed"
            report["system_webview2_reused"] = True
            executable = target / "BiliDownloader.exe"
            startup = []
            for _ in range(3):
                subprocess.run(
                    [sys.executable, str(ROOT / "scripts/verify_portable.py"), "--installed", str(executable)],
                    check=True,
                    timeout=90,
                    capture_output=True,
                )
                checked = json.loads((ROOT / "build/installed-verification.json").read_text(encoding="utf-8"))
                startup.append(checked["ready_seconds"])
                assert checked["bundled_media_merge"] == "passed"
                assert checked["runtime_cleaned_after_exit"]
            report["ready_seconds_three_launches"] = startup
            report["installed_media_merge"] = "passed"
            marker = target / "_internal/stale-upgrade-test.dll"
            marker.write_text("owned test dependency", encoding="utf-8")
            video = target / "video/keep-test.mp4"
            video.parent.mkdir(exist_ok=True)
            video.write_bytes(b"synthetic-download-sentinel")
            account = profile / "BiliDownloader/keep-test.txt"
            account.parent.mkdir(parents=True, exist_ok=True)
            account.write_text("synthetic-user-data", encoding="utf-8")
            subprocess.run([*args, f"/LOG={sandbox / 'upgrade.log'}"], env=env, check=True, timeout=90)
            assert not marker.exists()
            assert video.read_bytes() == b"synthetic-download-sentinel"
            assert account.read_text(encoding="utf-8") == "synthetic-user-data"
            report["upgrade_cleans_old_dependencies_preserves_data"] = "passed"
            report["installed_size_mib"] = round(
                sum(p.stat().st_size for p in target.rglob("*") if p.is_file()) / 1024**2, 2
            )
        finally:
            if uninstaller.exists():
                subprocess.run(
                    [
                        str(uninstaller),
                        "/VERYSILENT",
                        "/SUPPRESSMSGBOXES",
                        "/NORESTART",
                        f"/LOG={sandbox / 'uninstall.log'}",
                    ],
                    env=env,
                    check=True,
                    timeout=90,
                )
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    uninstall_log = sandbox / "uninstall.log"
                    if (
                        not uninstaller.exists()
                        and uninstall_log.exists()
                        and "Log closed." in uninstall_log.read_text(encoding="utf-8-sig")
                    ):
                        break
                    time.sleep(0.1)
        assert installed_location() is None
        assert not shortcut.exists()
        if test_desktop:
            assert not desktop.exists()
        assert not (target / "BiliDownloader.exe").exists()
        assert video.read_bytes() == b"synthetic-download-sentinel"
        assert account.read_text(encoding="utf-8") == "synthetic-user-data"
        report["uninstall_preserves_user_data_removes_program_and_shortcuts"] = "passed"
    (ROOT / "build/installer-verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
