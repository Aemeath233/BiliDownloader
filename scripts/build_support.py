"""Shared resource collection for developer builds; never imported by the app."""

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files


def ffmpeg_directory(root: Path) -> Path:
    return root / "vendor" / ("ffmpeg" if os.environ.get("BILI_FULL_FFMPEG") == "1" else "ffmpeg-minimal")


def app_data(root: Path, *, require_ffmpeg: bool = False) -> list[tuple[str, str]]:
    data = [(str(root / "logo.ico"), ".")]
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        if (root / name).is_file():
            data.append((str(root / name), "."))
    data.extend(collect_data_files("webview"))
    data.append((str(root / "bili_client" / "desktop" / "assets"), "bili_client/desktop/assets"))
    if os.environ.get("BILI_BUNDLE_WEBVIEW2") == "1":
        runtime = root / "vendor" / "webview2"
        if not (runtime / "msedgewebview2.exe").is_file():
            raise FileNotFoundError("Extract the Microsoft x64 Fixed Version runtime to vendor/webview2 first")
        data.append((str(runtime), "webview2"))
    executable = ffmpeg_directory(root) / "bin" / "ffmpeg.exe"
    license_file = executable.parent.parent / "LICENSE.txt"
    if require_ffmpeg and (not executable.is_file() or not license_file.is_file()):
        raise FileNotFoundError(
            "Prepare remux-only FFmpeg first; see scripts/build_ffmpeg_minimal.py and docs/BUILDING.md"
        )
    if executable.is_file() and license_file.is_file():
        data.extend([(str(executable), "ffmpeg"), (str(license_file), "ffmpeg")])
        data.extend((str(path), "ffmpeg") for path in sorted(executable.parent.glob("*.dll")))
        for name in ("BUILD-INFO.txt", "SOURCE.json"):
            metadata = executable.parent.parent / name
            if metadata.is_file():
                data.append((str(metadata), "ffmpeg"))
    return data


def deduplicate_ffmpeg(binaries: list, root: Path) -> list:
    directory = (ffmpeg_directory(root) / "bin").resolve()
    names = {path.name.casefold() for path in directory.glob("*.dll")}
    return [
        entry
        for entry in binaries
        if not (entry[0].casefold() in names and Path(entry[1]).resolve().parent == directory)
    ]
