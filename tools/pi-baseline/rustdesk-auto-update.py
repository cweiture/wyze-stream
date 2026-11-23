#!/usr/bin/python3
"""Install newer official stable RustDesk ARM64 releases on this Pi."""
import argparse
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

API = "https://api.github.com/repos/rustdesk/rustdesk/releases/latest"
MAX_DOWNLOAD = 256 * 1024 * 1024


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def request(url):
    return urllib.request.Request(url, headers={
        "User-Agent": "wyze-stream-rustdesk-updater",
        "Accept": "application/vnd.github+json" if url == API else "application/octet-stream",
    })


def release_info():
    with urllib.request.urlopen(request(API), timeout=60) as response:
        release = json.load(response)
    if release.get("draft") or release.get("prerelease"):
        raise RuntimeError("Refusing a draft or prerelease")
    tag = release["tag_name"]
    if not re.fullmatch(r"v?[0-9]+(?:\.[0-9]+){1,3}", tag):
        raise RuntimeError("Unrecognized stable release version")
    version = tag.removeprefix("v")
    name = f"rustdesk-{version}-aarch64.deb"
    assets = [asset for asset in release["assets"] if asset["name"] == name]
    if len(assets) != 1:
        raise RuntimeError("Expected exactly one official ARM64 Debian package")
    asset = assets[0]
    expected_url = f"https://github.com/rustdesk/rustdesk/releases/download/{tag}/{name}"
    if asset["browser_download_url"] != expected_url:
        raise RuntimeError("Unexpected package download URL")
    digest = asset.get("digest", "")
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise RuntimeError("Official SHA-256 digest missing or invalid; refusing update")
    if not 0 < asset["size"] <= MAX_DOWNLOAD:
        raise RuntimeError("Unexpected package download size")
    return version, asset


def verify_package(path, version, digest):
    with path.open("rb") as package:
        checksum = hashlib.sha256()
        for chunk in iter(lambda: package.read(1024 * 1024), b""):
            checksum.update(chunk)
        actual = checksum.hexdigest()
    if actual != digest.removeprefix("sha256:"):
        raise RuntimeError("Package SHA-256 mismatch; refusing installation")
    fields = run("dpkg-deb", "--field", str(path), "Package", "Version", "Architecture")
    metadata = dict(line.split(": ", 1) for line in fields.splitlines())
    if metadata != {"Package": "rustdesk", "Version": version, "Architecture": "arm64"}:
        raise RuntimeError("Downloaded package identity, version or architecture mismatch")


def backup_config(installed, config_homes):
    directory = Path("/var/backups/rustdesk-updater")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_version = re.sub(r"[^a-zA-Z0-9.+_-]", "_", installed)
    destination = directory / f"before-{safe_version}-{stamp}.tar.gz"
    with tarfile.open(destination, "w:gz") as archive:
        for path in sorted({Path('/root/.config/rustdesk'), *config_homes}):
            if path.exists():
                archive.add(path, arcname=str(path).lstrip("/"))
    destination.chmod(0o600)
    print(f"Private configuration backup: {destination}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Download and verify the latest release without installing")
    parser.add_argument("--config-home", action="append", type=Path, default=[],
                        help="Additional RustDesk configuration directory to back up (repeatable)")
    args = parser.parse_args()
    if any(not path.is_absolute() for path in args.config_home):
        raise RuntimeError("Configuration backup directories must be absolute paths")
    if os.geteuid() != 0:
        raise RuntimeError("Run as root")
    os.umask(0o077)
    with open("/run/lock/rustdesk-auto-update.lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Another RustDesk updater is already running", flush=True)
            return
        if run("dpkg", "--print-architecture") != "arm64":
            raise RuntimeError("This updater requires an ARM64 host")
        installed = run("dpkg-query", "-W", "-f=${Version}", "rustdesk")
        version, asset = release_info()
        newer = subprocess.run(["dpkg", "--compare-versions", version, "gt", installed]).returncode == 0
        print(f"Installed RustDesk: {installed}; latest official stable: {version}", flush=True)
        if not newer and not args.check:
            print("No newer version; nothing changed", flush=True)
            return
        with tempfile.TemporaryDirectory(prefix="rustdesk-update-", dir="/var/tmp") as directory:
            package = Path(directory) / asset["name"]
            total = 0
            with urllib.request.urlopen(request(asset["browser_download_url"]), timeout=60) as response, package.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_DOWNLOAD:
                        raise RuntimeError("Download exceeded size limit")
                    output.write(chunk)
            if total != asset["size"]:
                raise RuntimeError("Downloaded package size mismatch")
            verify_package(package, version, asset["digest"])
            print("Verified official SHA-256, package name, version and ARM64 architecture", flush=True)
            if args.check:
                print("Check completed; no packages or settings changed", flush=True)
                return
            backup_config(installed, args.config_home)
            # Permit APT's unprivileged download helper to read the verified local file.
            Path(directory).chmod(0o755)
            package.chmod(0o644)
            environment = dict(os.environ, DEBIAN_FRONTEND="noninteractive", APT_LISTCHANGES_FRONTEND="none")
            subprocess.run([
                "apt-get", "-y", "--no-remove", "-o", "DPkg::Lock::Timeout=900",
                "-o", "Dpkg::Options::=--force-confold", "install", str(package),
            ], env=environment, check=True)
            if run("dpkg-query", "-W", "-f=${Version}", "rustdesk") != version:
                raise RuntimeError("Installed version did not match the verified release")
            subprocess.run(["systemctl", "restart", "rustdesk.service"], check=True)
            subprocess.run(["systemctl", "is-active", "--quiet", "rustdesk.service"], check=True)
            print(f"RustDesk updated successfully to {version}; service is active", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"RustDesk update failed: {error}", file=sys.stderr, flush=True)
        sys.exit(1)
