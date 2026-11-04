"""Prepare Microsoft's signed, small Evergreen installer for Setup prerequisites."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
URL = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"


def main() -> None:
    directory = ROOT / "vendor" / "webview2-bootstrapper"
    directory.mkdir(parents=True, exist_ok=True)
    executable = directory / "MicrosoftEdgeWebview2Setup.exe"
    if not executable.exists():
        with urlopen(URL, timeout=60) as response:
            payload = response.read(8 * 1024 * 1024 + 1)
            if len(payload) > 8 * 1024 * 1024:
                raise ValueError("Expected the small Evergreen bootstrapper, not a full runtime")
        executable.write_bytes(payload)
    quoted = "'" + str(executable).replace("'", "''") + "'"
    command = (
        "$signature = Get-AuthenticodeSignature -LiteralPath " + quoted + "; "
        "if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject "
        "-notlike '*Microsoft Corporation*') { exit 1 }"
    )
    subprocess.run(
        [shutil.which("pwsh.exe") or "powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        env={key: value for key, value in os.environ.items() if key.upper() != "PSMODULEPATH"},
        check=True,
    )
    (directory / "SOURCE.json").write_text(
        json.dumps({"url": URL, "sha256": hashlib.sha256(executable.read_bytes()).hexdigest()}, indent=2),
        encoding="utf-8",
    )
    print(f"Verified Microsoft bootstrapper: {executable.stat().st_size / 1024**2:.2f} MiB")


if __name__ == "__main__":
    main()
