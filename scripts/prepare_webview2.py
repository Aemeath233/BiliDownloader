"""Download an official x64 Fixed Version WebView2 CAB for self-contained builds."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import httpx

ROOT = Path(__file__).resolve().parents[1]
PAGE = "https://developer.microsoft.com/en-us/microsoft-edge/webview2/"


def main():
    vendor = (ROOT / "vendor").resolve()
    target = vendor / "webview2"
    if (target / "msedgewebview2.exe").exists():
        print(f"Runtime already prepared: {target}")
        return
    vendor.mkdir(exist_ok=True)
    with httpx.Client(follow_redirects=True, timeout=60) as client:
        page = client.get(PAGE)
        page.raise_for_status()
        html = page.text.replace(r"\u002F", "/")
        links = set(re.findall(r'https://[^\s"<>]+/Microsoft\.WebView2\.FixedVersionRuntime\.[\d.]+\.x64\.cab', html))
        if not links:
            raise RuntimeError(
                "No x64 CAB found on the official Microsoft page; prepare it manually per docs/BUILDING.md"
            )
        url = max(links, key=lambda link: tuple(map(int, re.search(r"Runtime\.([\d.]+)\.x64", link)[1].split("."))))
        if not (urlparse(url).hostname or "").endswith(".microsoft.com"):
            raise ValueError("Refusing runtime download from a non-Microsoft host")
        archive = vendor / Path(urlparse(url).path).name
        print(f"Downloading {archive.name}", flush=True)
        if not archive.exists():
            partial = archive.with_suffix(".cab.tmp")
            digest = hashlib.sha256()
            with client.stream("GET", url) as response, partial.open("wb") as output:
                response.raise_for_status()
                for chunk in response.iter_bytes(1024 * 1024):
                    output.write(chunk)
                    digest.update(chunk)
            partial.replace(archive)
        digest_hex = hashlib.sha256(archive.read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory(prefix="webview2-extract-", dir=vendor) as temporary:
        stage = Path(temporary).resolve()
        if not stage.is_relative_to(vendor):
            raise ValueError("Extraction staging directory must be inside vendor")
        subprocess.run(["expand.exe", "-F:*", str(archive), str(stage)], check=True, capture_output=True)
        executables = list(stage.rglob("msedgewebview2.exe"))
        if len(executables) != 1:
            raise RuntimeError("The CAB did not contain exactly one WebView2 runtime")
        executable = executables[0]
        # Verify Microsoft's signature before packaging executable code.
        quoted_executable = "'" + str(executable).replace("'", "''") + "'"
        verify_command = (
            "$signature = Get-AuthenticodeSignature -LiteralPath " + quoted_executable + "; "
            "$signature | Select-Object Status, StatusMessage, @{Name='Signer';Expression={$_.SignerCertificate.Subject}} | Format-List\n"
            "if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notlike '*Microsoft Corporation*') { exit 1 }\n"
        )
        signature = subprocess.run(
            [
                shutil.which("pwsh.exe") or "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                verify_command,
            ],
            capture_output=True,
            env={key: value for key, value in os.environ.items() if key.upper() != "PSMODULEPATH"},
        )
        if signature.returncode:
            raise RuntimeError(
                "Microsoft runtime signature verification failed; runtime was not packaged: "
                + (signature.stdout + signature.stderr).decode(errors="replace")
            )
        if not target.resolve().is_relative_to(vendor) or target.exists():
            raise RuntimeError("Refusing to overwrite an existing WebView2 directory")
        shutil.copytree(executable.parent, target)
        (target / "bili-runtime-source.json").write_text(
            json.dumps({"source": PAGE, "url": url, "cab_sha256": digest_hex}, indent=2), encoding="utf-8"
        )
    print(f"Prepared {target}; CAB SHA256 {digest_hex}", flush=True)


if __name__ == "__main__":
    main()
