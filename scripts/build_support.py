"""Shared resource collection for developer builds; never imported by the app."""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files


def app_data(root: Path, *, require_ffmpeg: bool = False) -> list[tuple[str, str]]:
    data = [(str(root / "logo.ico"), ".")]
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        if (root / name).is_file():
            data.append((str(root / name), "."))
    data.extend(collect_data_files("customtkinter"))
    executable = root / "vendor" / "ffmpeg" / "bin" / "ffmpeg.exe"
    license_file = executable.parent.parent / "LICENSE.txt"
    if require_ffmpeg and (not executable.is_file() or not license_file.is_file()):
        raise FileNotFoundError("Prepare FFmpeg first: python scripts/prepare_ffmpeg.py")
    if executable.is_file() and license_file.is_file():
        data.extend([(str(executable), "ffmpeg"), (str(license_file), "ffmpeg")])
        data.extend((str(path), "ffmpeg") for path in sorted(executable.parent.glob("*.dll")))
    return data


def deduplicate_ffmpeg(binaries: list, root: Path) -> list:
    directory = (root / "vendor" / "ffmpeg" / "bin").resolve()
    names = {path.name.casefold() for path in directory.glob("*.dll")}
    return [
        entry
        for entry in binaries
        if not (entry[0].casefold() in names and Path(entry[1]).resolve().parent == directory)
    ]
