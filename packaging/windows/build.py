#!/usr/bin/env python3
"""Build the Windows installer for D&D 3.5 Spellbook.

The installer bundles the official Windows embeddable Python, the pinned
wheels from requirements.txt, and this repository's app, so users need
nothing else installed. It installs per user, without administrator rights.

Runs on Windows, macOS, or Linux with Python 3.11+ and pip, and needs
internet access and NSIS 3 (``makensis`` on PATH, or ``--makensis``).
"""

from __future__ import annotations

import argparse
import compileall
import hashlib
import os
import py_compile
import shutil
import subprocess
import sys
import tomllib
import urllib.request
import zipfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PYTHON_VERSION = "3.13.15"
PYTHON_EMBED_SHA256 = "d1f04d990aee1253d8569e8e5104e30fa9f5fa830899f14843448872d936a2cf"
# Published only as source distributions; they are pure Python and built into wheels here.
SOURCE_ONLY = {"proxy-tools"}


def default_cache() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "dnd35-spellbook-build" / "windows"


def download(url: str, target: Path, sha256: str) -> Path:
    if not target.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".part")
        print(f"Downloading {url}")
        with urllib.request.urlopen(url) as response, partial.open("wb") as handle:
            shutil.copyfileobj(response, handle)
        partial.replace(target)
    if hashlib.sha256(target.read_bytes()).hexdigest() != sha256:
        target.unlink()
        raise SystemExit(f"Checksum mismatch for {target.name}; it was deleted, so run the build again")
    return target


def pinned_requirements() -> list[tuple[str, str]]:
    pins = []
    for line in (HERE / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            name, version = line.split("==")
            pins.append((name.lower(), version))
    return pins


def fetch_wheels(wheel_dir: Path) -> list[Path]:
    pins = pinned_requirements()
    binary = [f"{name}=={version}" for name, version in pins if name not in SOURCE_ONLY]
    source = [f"{name}=={version}" for name, version in pins if name in SOURCE_ONLY]
    pip = [sys.executable, "-m", "pip", "--disable-pip-version-check"]
    python_minor = ".".join(PYTHON_VERSION.split(".")[:2])
    subprocess.run(
        [*pip, "download", "--no-deps", "--only-binary=:all:", "--platform", "win_amd64", "--python-version", python_minor, "--implementation", "cp", "--dest", str(wheel_dir), *binary],
        check=True,
    )
    if source:
        subprocess.run([*pip, "wheel", "--no-deps", "--no-binary=:all:", "--wheel-dir", str(wheel_dir), *source], check=True)

    wheels = []
    for name, version in pins:
        prefix = f"{name.replace('-', '_')}-{version}-"
        matches = [wheel for wheel in wheel_dir.glob("*.whl") if wheel.name.lower().startswith(prefix)]
        if len(matches) != 1:
            raise SystemExit(f"Expected one wheel for {name}=={version}, found {[wheel.name for wheel in matches]}")
        wheels.append(matches[0])
    return wheels


def install_wheel(wheel: Path, site_packages: Path) -> None:
    with zipfile.ZipFile(wheel) as archive:
        for member in archive.infolist():
            parts = member.filename.split("/")
            if ".." in parts:
                raise SystemExit(f"Unsafe path {member.filename} in {wheel.name}")
            if parts[0].endswith(".data"):
                # Only importable code from .data is needed; scripts and headers are not.
                if len(parts) < 3 or parts[1] not in {"purelib", "platlib"}:
                    continue
                parts = parts[2:]
            if member.is_dir() or not parts[-1]:
                continue
            target = site_packages.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open("wb") as handle:
                shutil.copyfileobj(source, handle)


def build_stage(stage: Path, cache: Path) -> None:
    python_dir = stage / "python"
    embed = download(
        f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip",
        cache / f"python-{PYTHON_VERSION}-embed-amd64.zip",
        PYTHON_EMBED_SHA256,
    )
    with zipfile.ZipFile(embed) as archive:
        archive.extractall(python_dir)

    # The ._pth file keeps Python isolated from any other Python on the machine.
    tag = "".join(PYTHON_VERSION.split(".")[:2])
    (python_dir / f"python{tag}._pth").write_text(f"python{tag}.zip\n.\nLib\\site-packages\nimport site\n", encoding="utf-8")

    site_packages = python_dir / "Lib" / "site-packages"
    for wheel in fetch_wheels(cache / "wheels"):
        install_wheel(wheel, site_packages)
    shutil.copytree(ROOT / "src" / "spellbook_builder", site_packages / "spellbook_builder", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copyfile(site_packages / "spellbook_builder" / "static" / "icon.ico", stage / "Spellbook.ico")

    if f"{sys.version_info.major}.{sys.version_info.minor}" == ".".join(PYTHON_VERSION.split(".")[:2]):
        # Bytecode made by the same Python version shortens the first start.
        compileall.compile_dir(site_packages, quiet=1, workers=0, invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH)
    else:
        print(f"Skipping bytecode compilation: run with Python {PYTHON_VERSION.rsplit('.', 1)[0]} to include it")


def build_installer(stage: Path, dist: Path, version: str, makensis: str) -> Path:
    dist.mkdir(parents=True, exist_ok=True)
    installer = dist / f"DnD35Spellbook-{version}-Setup.exe"
    subprocess.run(
        [makensis, "-V2", f"-DAPP_VERSION={version}", f"-DSTAGE_DIR={stage.as_posix()}", f"-DOUT_FILE={installer}", str(HERE / "installer.nsi")],
        check=True,
    )
    return installer


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Windows installer into dist/")
    parser.add_argument("--dist", type=Path, default=ROOT / "dist", help="output directory")
    parser.add_argument("--cache", type=Path, default=default_cache(), help="download cache directory")
    parser.add_argument("--makensis", default=os.environ.get("MAKENSIS", "makensis"), help="NSIS compiler command")
    args = parser.parse_args()
    if shutil.which(args.makensis) is None:
        raise SystemExit("NSIS 3 is required: apt install nsis, brew install makensis, or https://nsis.sourceforge.io")

    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    stage = ROOT / "build" / "windows" / "stage"
    shutil.rmtree(stage, ignore_errors=True)
    build_stage(stage, args.cache)
    installer = build_installer(stage, args.dist, version, args.makensis)
    print(f"Built {installer}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
