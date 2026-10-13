"""Windows smoke test: copy only the EXE, isolate settings, and restrict PATH."""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bili_client.services.ffmpeg import FFmpegService


class ProcessEntry(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


kernel32, user32 = ctypes.windll.kernel32, ctypes.windll.user32
kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
WindowCallback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [WindowCallback, wintypes.LPARAM]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]


def descendant_ids(parent: int) -> set[int]:
    snapshot = kernel32.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise ctypes.WinError()
    parents = {}
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        more = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            parents[entry.th32ProcessID] = entry.th32ParentProcessID
            more = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    descendants = {parent}
    while True:
        expanded = descendants | {pid for pid, owner in parents.items() if owner in descendants}
        if expanded == descendants:
            return descendants
        descendants = expanded


def app_window(process_id: int):
    owned = descendant_ids(process_id)
    handles = []

    def visit(handle, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
        if pid.value in owned and user32.IsWindowVisible(handle):
            title = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(handle, title, 256)
            if title.value == "BiliDownloader":
                handles.append(handle)
        return True

    user32.EnumWindows(WindowCallback(visit), 0)
    return handles[0] if handles else None


def main() -> None:
    executable = ROOT / "dist" / "portable" / "BiliDownloader-Portable.exe"
    if not executable.is_file():
        raise FileNotFoundError(executable)
    with socket.socket() as probe:
        probe.settimeout(0.1)
        if probe.connect_ex(("127.0.0.1", 48379)) == 0:
            raise RuntimeError("Close the running app before this smoke test; its settings are not touched.")

    report = {"size_mib": round(executable.stat().st_size / 1024**2, 2)}
    with tempfile.TemporaryDirectory(prefix="bili-portable-check-") as temporary:
        sandbox = Path(temporary)
        launch_dir = sandbox / "单文件 空目录"
        launch_dir.mkdir()
        copied = launch_dir / executable.name
        shutil.copy2(executable, copied)
        assert list(launch_dir.iterdir()) == [copied]
        runtime_temp = sandbox / "runtime"
        runtime_temp.mkdir()
        env = dict(
            os.environ,
            APPDATA=str(sandbox / "profile"),
            TEMP=str(runtime_temp),
            TMP=str(runtime_temp),
            PATH=str(Path(os.environ["SystemRoot"]) / "System32"),
        )
        for name in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "CONDA_PREFIX"):
            env.pop(name, None)
        started = time.perf_counter()
        process = subprocess.Popen([str(copied)], cwd=launch_dir, env=env, creationflags=subprocess.CREATE_NO_WINDOW)
        handle, startup_log = None, ""
        try:
            deadline = started + 45
            while time.perf_counter() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f"Portable app exited early: {process.returncode}")
                if handle is None:
                    handle = app_window(process.pid)
                    if handle is not None:
                        report["window_seconds"] = round(time.perf_counter() - started, 3)
                for log in (sandbox / "profile" / "BiliDownloader" / "logs").glob("*.log"):
                    startup_log = next(
                        (line for line in log.read_text(encoding="utf-8-sig").splitlines() if "启动耗时" in line), ""
                    )
                if handle is not None and startup_log:
                    break
                time.sleep(0.05)
            if handle is None or not startup_log:
                raise RuntimeError("Portable window or ready marker did not appear within 45 seconds")
            report["ready_seconds"] = round(time.perf_counter() - started, 3)
            report["startup_log"] = startup_log
            extracted = next(runtime_temp.glob("_MEI*"))
            for relative in (
                "python311.dll",
                "VCRUNTIME140.dll",
                "certifi/cacert.pem",
                "ffmpeg/ffmpeg.exe",
                "ffmpeg/LICENSE.txt",
            ):
                if not (extracted / relative).is_file():
                    raise RuntimeError(f"Missing bundled dependency: {relative}")

            bundled_ffmpeg = extracted / "ffmpeg" / "ffmpeg.exe"

            def run_ffmpeg(*arguments):
                result = subprocess.run(
                    [str(bundled_ffmpeg), *arguments],
                    env=env,
                    cwd=launch_dir,
                    capture_output=True,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    timeout=20,
                )
                if result.returncode:
                    raise RuntimeError(result.stderr.decode("utf-8", errors="replace"))
                return result.stdout.decode("utf-8", errors="replace")

            report["ffmpeg_version"] = run_ffmpeg("-version").splitlines()[0]
            video, audio, merged = (sandbox / name for name in ("video.mp4", "audio.m4a", "merged.mp4"))
            run_ffmpeg("-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.1", "-an", "-c:v", "mpeg4", "-y", str(video))
            run_ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=0.1", "-vn", "-c:a", "aac", "-y", str(audio))
            FFmpegService(str(bundled_ffmpeg)).merge(video, audio, merged)
            assert merged.is_file() and merged.stat().st_size > 0
            report["bundled_media_merge"] = "passed"
            report["exe_only_directory"] = not (launch_dir / "_internal").exists()
        finally:
            if handle is None and process.poll() is None:
                handle = app_window(process.pid)
            if handle is not None:
                user32.PostMessageW(handle, 0x0010, 0, 0)
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                subprocess.run(
                    [
                        str(Path(os.environ["SystemRoot"]) / "System32" / "taskkill.exe"),
                        "/PID",
                        str(process.pid),
                        "/T",
                        "/F",
                    ],
                    capture_output=True,
                )
                process.wait(timeout=8)
        report["runtime_cleaned_after_exit"] = not list(runtime_temp.glob("_MEI*"))
        if not report["runtime_cleaned_after_exit"]:
            raise RuntimeError("Runtime extraction was not cleaned up after normal exit")

    output = ROOT / "build" / "portable-verification.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
