"""Install only locked parser inputs during explicit Docker setup, never at runtime."""
from __future__ import annotations

import hashlib
import json
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path


DIRECTORY = Path(__file__).resolve().parent


def read_lock(directory=DIRECTORY):
    lock = json.loads((directory / "parser-build-lock.json").read_text())
    if (lock["version"] != "parser-build-lock-v1" or
            not re.fullmatch(r"python:[\w.-]+@sha256:[0-9a-f]{64}", lock["base"]) or
            not re.fullmatch(r"\d{8}T\d{6}Z", lock["debian_snapshot"]) or
            set(lock["packages"]) != {"poppler-utils", "tesseract-ocr", "tesseract-ocr-eng", "tesseract-ocr-osd"} or
            set(lock["pillow"]["wheels"]) != {"aarch64", "x86_64"} or
            set(lock["ocr_assets"]) != {"eng.traineddata", "osd.traineddata"}):
        raise ValueError("Incomplete parser build lock")
    for name, version in lock["packages"].items():
        if not re.fullmatch(r"[0-9][\w.+:~\-]*", version):
            raise ValueError(f"Invalid package version: {name}")
    for architecture, wheel in lock["pillow"]["wheels"].items():
        if (not re.fullmatch(r"https://files\.pythonhosted\.org/packages/[\w/.-]+\.whl", wheel["url"]) or
                f"pillow-{lock['pillow']['version']}-cp312-cp312-" not in wheel["url"] or
                f"_{architecture}.whl" not in wheel["url"]):
            raise ValueError("Unexpected locked wheel")
    for digest in [*lock["ocr_assets"].values(), *(wheel["sha256"] for wheel in lock["pillow"]["wheels"].values())]:
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Missing dependency SHA-256")
    return lock


def apt_sources(lock):
    stamp = lock["debian_snapshot"]
    return "\n\n".join(
        f"Types: deb\nURIs: https://snapshot.debian.org/archive/{archive}/{stamp}/\n"
        f"Suites: {suites}\nComponents: main\n"
        "Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg\nCheck-Valid-Until: no"
        for archive, suites in (("debian", "bookworm bookworm-updates"),
                                ("debian-security", "bookworm-security"))) + "\n"


def runtime_inventory():
    import PIL
    packages = subprocess.check_output(
        ["dpkg-query", "-W", "-f=${Package}=${Version}\n"], text=True).splitlines()
    assets = Path("/usr/share/tesseract-ocr/5/tessdata")
    return {"python": platform.python_version(), "machine": platform.machine(), "pillow": PIL.__version__,
            "packages": dict(line.split("=", 1) for line in packages),
            "ocr_assets": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                           for path in sorted(assets.glob("*.traineddata"))}}


def verify_inventory(lock, inventory):
    if inventory["machine"] not in lock["pillow"]["wheels"]:
        raise ValueError("Unsupported parser architecture")
    if inventory["python"] != lock["python"] or inventory["pillow"] != lock["pillow"]["version"]:
        raise ValueError("Python/Pillow differs from locked version")
    if any(inventory["packages"].get(name) != version for name, version in lock["packages"].items()):
        raise ValueError("Installed OCR/PDF packages differ from lock")
    if inventory["ocr_assets"] != lock["ocr_assets"]:
        raise ValueError("OCR language bytes differ from lock")


def main():
    lock = read_lock()
    if platform.python_version() != lock["python"]:
        raise ValueError("Base Python differs from lock")
    wheel = lock["pillow"]["wheels"][platform.machine()]
    # Replace the base's moving repositories; frozen Release signatures and
    # package hashes remain verified. Only the historical expiry is disabled.
    sources = Path("/etc/apt/sources.list.d")
    for path in sources.iterdir():
        if path.suffix in {".list", ".sources"}:
            path.unlink()
    Path("/etc/apt/sources.list").unlink(missing_ok=True)
    (sources / "debian.sources").write_text(apt_sources(lock))
    subprocess.run(["apt-get", "update"], check=True)
    subprocess.run(["apt-get", "install", "-y", "--no-install-recommends",
                    *(f"{name}={version}" for name, version in lock["packages"].items())], check=True)
    shutil.rmtree("/var/lib/apt/lists")
    requirements = DIRECTORY / "requirements.txt"
    requirements.write_text(f"Pillow @ {wheel['url']} --hash=sha256:{wheel['sha256']}\n")
    subprocess.run([sys.executable, "-m", "pip", "install", "--no-cache-dir", "--no-compile",
                    "--no-deps", "--only-binary=:all:", "--require-hashes", "--no-index",
                    "-r", str(requirements)], check=True)
    inventory = runtime_inventory()
    verify_inventory(lock, inventory)
    (DIRECTORY / "runtime.json").write_text(json.dumps(inventory, sort_keys=True, indent=2) + "\n")


if __name__ == "__main__":
    main()
