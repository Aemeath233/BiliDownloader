"""Build a local-file remux-only FFmpeg with Git Bash/MSYS make and MinGW GCC."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMIT = "f044c5e62710a151e001ae097fd3a07b88540766"
SOURCE_SHA256 = "842f845c8e7e5d338b60c3eeaeeefc0dc2da15401a7d12fb8a52dd56af2181df"
OPTIONS = [
    "--target-os=mingw32",
    "--arch=x86_64",
    "--cc=gcc",
    "--ar=ar",
    "--ranlib=ranlib",
    "--disable-autodetect",
    "--disable-iconv",
    "--disable-everything",
    "--disable-network",
    "--disable-doc",
    "--disable-debug",
    "--disable-shared",
    "--enable-static",
    "--disable-x86asm",
    "--disable-ffplay",
    "--disable-ffprobe",
    "--enable-ffmpeg",
    "--enable-small",
    "--disable-pthreads",
    "--enable-w32threads",
    "--extra-ldflags=-static",
    "--enable-protocol=file",
    "--enable-demuxer=mov,flv,matroska,aac,flac,ac3,eac3",
    "--enable-muxer=mp4",
    "--enable-decoder=aac,ac3,eac3,flac",
    "--enable-parser=h264,hevc,av1,aac,ac3,flac,mpegaudio",
    "--enable-bsf=aac_adtstoasc,extract_extradata",
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path, help="Pinned upstream source .tar.gz")
    parser.add_argument("--bash", default="bash")
    parser.add_argument("--make", default="make", help="MSYS GNU make (not mingw32-make)")
    parser.add_argument("--toolchain-dir", type=Path, help="Directory containing MinGW GCC and binutils")
    parser.add_argument("--jobs", default=min(os.cpu_count() or 2, 8), type=int)
    args = parser.parse_args()
    archive = args.source.resolve()
    if hashlib.sha256(archive.read_bytes()).hexdigest() != SOURCE_SHA256:
        raise ValueError("Source SHA256 differs from the pinned FFmpeg archive; refusing to build")
    work = ROOT / "build" / "ffmpeg-minimal"
    source = work / "source"
    source.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as tar:
        tar.extractall(source, filter="data")
    source_root = next(source.glob("*/configure")).parent
    output = ROOT / "vendor" / "ffmpeg-minimal"
    if output.exists():
        raise FileExistsError(f"Move the previous build aside before rebuilding: {output}")
    compile_dir = work / "compile"
    compile_dir.mkdir(exist_ok=True)
    env = os.environ.copy()
    if args.toolchain_dir:
        env["PATH"] = str(args.toolchain_dir.resolve()) + os.pathsep + env["PATH"]
    bash = shutil.which(args.bash) or args.bash
    make = str(Path(shutil.which(args.make) or args.make).resolve())
    subprocess.run([bash, str(source_root / "configure"), *OPTIONS], cwd=compile_dir, env=env, check=True)
    # Regenerate the embedded version even on incremental builds; never use the parent app's Git revision.
    (source_root / "ffbuild/version.sh").touch()
    # Bash supplies the MSYS utilities and DLL search path used by MSYS make.
    subprocess.run(
        [bash, "-c", 'exec "$@"', "build", make, f"-j{args.jobs}", f"REVISION=bili-remux-{COMMIT[:10]}"],
        cwd=compile_dir,
        env=env,
        check=True,
    )
    executable = compile_dir / "ffmpeg.exe"
    version = subprocess.check_output([str(executable), "-version"], text=True, env=env)
    # No GPL or nonfree components are enabled; keep the upstream LGPL text and source provenance.
    if "--enable-gpl" in version or "--enable-nonfree" in version:
        raise RuntimeError("Unexpected FFmpeg license configuration")
    (output / "bin").mkdir(parents=True)
    shutil.copy2(executable, output / "bin" / "ffmpeg.exe")
    shutil.copy2(source_root / "COPYING.LGPLv2.1", output / "LICENSE.txt")
    shutil.copy2(archive, output / "FFmpeg-source-f044c5e627.tar.gz")
    (output / "BUILD-INFO.txt").write_text(version, encoding="utf-8")
    (output / "SOURCE.json").write_text(
        json.dumps(
            {
                "commit": COMMIT,
                "archive_sha256": SOURCE_SHA256,
                "source_url": f"https://api.github.com/repos/FFmpeg/FFmpeg/tarball/{COMMIT}",
                "configure": OPTIONS,
                "compiler": subprocess.check_output(["gcc", "--version"], text=True, env=env),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Remux FFmpeg: {executable.stat().st_size / 1024**2:.2f} MiB; installed in {output}")


if __name__ == "__main__":
    main()
