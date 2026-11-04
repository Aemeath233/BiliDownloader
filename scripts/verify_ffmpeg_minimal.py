"""Verify stream-copy compatibility and packet hashes with generated local media."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> None:
    from bili_client.services.ffmpeg import FFmpegService

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ffmpeg", type=Path, default=ROOT / "vendor/ffmpeg-minimal/bin/ffmpeg.exe")
    args = parser.parse_args()
    generator = ROOT / "vendor/ffmpeg/bin/ffmpeg.exe"
    probe = generator.with_name("ffprobe.exe")
    env = dict(os.environ, PATH=str(Path(os.environ["SystemRoot"]) / "System32"))

    def run(executable, *arguments):
        return subprocess.check_output(
            [str(executable), *map(str, arguments)],
            stderr=subprocess.PIPE,
            env=env,
            timeout=90,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

    def hashes(path):
        result = json.loads(
            run(
                probe,
                "-v",
                "error",
                "-show_packets",
                "-show_data_hash",
                "sha256",
                "-show_entries",
                "packet=codec_type,data_hash",
                "-of",
                "json",
                path,
            )
        )
        return {
            kind: [p["data_hash"] for p in result["packets"] if p["codec_type"] == kind] for kind in ("video", "audio")
        }

    report = {}
    with tempfile.TemporaryDirectory(prefix="bili-remux-check-") as temporary:
        sandbox = Path(temporary)
        # The candidate is deliberately copied alone: no MinGW runtime or full FFmpeg DLLs.
        candidate = sandbox / "ffmpeg.exe"
        shutil.copy2(args.ffmpeg, candidate)
        run(candidate, "-version")
        service = FFmpegService(str(candidate))
        videos, audios = {}, {}
        for name, encoder, extra in (
            ("h264", "libx264", []),
            ("hevc", "libx265", ["-x265-params", "pools=1:frame-threads=1"]),
            ("av1", "libaom-av1", ["-cpu-used", "8"]),
        ):
            path = sandbox / f"{name}.m4s"
            run(
                generator,
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=s=64x64:r=5:d=0.4",
                "-an",
                "-c:v",
                encoder,
                *extra,
                "-movflags",
                "+frag_keyframe+empty_moov",
                "-f",
                "mp4",
                path,
            )
            videos[name] = path
        for name in ("aac", "eac3", "flac"):
            path = sandbox / f"{name}.m4a"
            run(
                generator,
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=0.4",
                "-vn",
                "-c:a",
                name,
                "-f",
                "mp4",
                path,
            )
            audios[name] = path
        for video_name, audio_name in (
            ("h264", "aac"),
            ("hevc", "aac"),
            ("av1", "aac"),
            ("h264", "eac3"),
            ("h264", "flac"),
            ("h264", None),
        ):
            label = f"{video_name}+{audio_name or 'no-audio'}"
            video, audio = videos[video_name], audios.get(audio_name)
            output = service.merge(video, audio, sandbox / f"{label}.mp4")
            actual = hashes(output)
            assert actual["video"] == hashes(video)["video"], label
            assert actual["audio"] == (hashes(audio)["audio"] if audio else []), label
            report[label] = "passed; all packet hashes match"
        legacy = sandbox / "legacy.flv"
        run(generator, "-v", "error", "-i", videos["h264"], "-i", audios["aac"], "-c", "copy", legacy)
        output = service.merge(legacy, None, sandbox / "legacy.mp4")
        assert hashes(output) == hashes(legacy)
        report["legacy-flv"] = "passed; all packet hashes match"
        for track, extension in (("audio", ".m4a"), ("video", ".mp4")):
            output = service.merge(legacy, None, sandbox / f"isolated-{track}{extension}", track=track)
            actual = hashes(output)
            self_kind = track
            other_kind = "video" if track == "audio" else "audio"
            assert actual[self_kind] == hashes(legacy)[self_kind] and actual[self_kind]
            assert actual[other_kind] == []
            report[f"extract-only-{track}"] = "passed; only selected track remains, all packet hashes match"
        for name, source in audios.items():
            output = service.merge(source, None, sandbox / f"audio-only-{name}.m4a", track="audio")
            assert hashes(output) == hashes(source)
            report[f"audio-only-{name}"] = "passed; M4A source codec and packet hashes preserved"
        for name, format_name, extension in (
            ("aac", "adts", "aac"),
            ("eac3", "eac3", "eac3"),
            ("flac", "flac", "flac"),
        ):
            raw = sandbox / f"raw.{extension}"
            run(generator, "-v", "error", "-i", audios[name], "-c", "copy", "-f", format_name, raw)
            output = service.merge(videos["h264"], raw, sandbox / f"raw-{name}.mp4")
            # ADTS headers are removed when AAC moves into MP4, so compare decoded audio samples too.
            expected = run(generator, "-v", "error", "-i", raw, "-map", "0:a", "-f", "s16le", "-")
            actual = run(generator, "-v", "error", "-i", output, "-map", "0:a", "-f", "s16le", "-")
            assert actual == expected and actual, name
            report[f"h264+raw-{name}"] = "passed; decoded audio samples match"
    destination = ROOT / "build/ffmpeg-minimal-verification.json"
    destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
