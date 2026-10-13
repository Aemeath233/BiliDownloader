from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR_DIR = ROOT / "vendor" / "ffmpeg"
BIN_DIR = VENDOR_DIR / "bin"


def find_zip() -> Path:
    candidates = sorted(ROOT.glob("ffmpeg*.zip"))
    if not candidates:
        raise FileNotFoundError("未找到 ffmpeg*.zip，请把 FFmpeg 压缩包放在项目根目录。")
    return candidates[0]


def main() -> None:
    archive_path = find_zip()
    if not VENDOR_DIR.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError("FFmpeg 目录必须位于当前项目内，拒绝操作外部目录。")
    if VENDOR_DIR.exists():
        shutil.rmtree(VENDOR_DIR)
    BIN_DIR.mkdir(parents=True, exist_ok=True)

    extracted = 0
    with zipfile.ZipFile(archive_path) as archive:
        for entry in archive.infolist():
            entry_path = Path(entry.filename)
            if entry.is_dir():
                continue

            if entry_path.parent.name == "bin" and entry_path.suffix.lower() in {".exe", ".dll"}:
                if entry_path.name.lower() == "ffplay.exe":
                    continue
                target = BIN_DIR / entry_path.name
            elif entry_path.name.lower() == "license.txt":
                target = VENDOR_DIR / "LICENSE.txt"
            else:
                continue

            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(entry) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
            extracted += 1

    if not (BIN_DIR / "ffmpeg.exe").exists():
        raise FileNotFoundError("压缩包里没有找到 bin/ffmpeg.exe。")

    print(f"已从 {archive_path.name} 提取 {extracted} 个 FFmpeg 文件到 {VENDOR_DIR}")


if __name__ == "__main__":
    main()
