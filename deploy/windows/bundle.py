"""Build the Windows collector bundle: blt-collector-windows-<version>.zip.

    python3 deploy/windows/bundle.py --out DIR [--cache DIR]
    python3 deploy/windows/bundle.py --fetch-only [--cache DIR]

Run from the repository, on Linux, with uv and git on PATH. build-bundle.sh
runs it in a container, so a machine needs only podman; CI runs it as it is.

What it makes, for a Windows server with no internet and no Python:

    blt-collector-windows-<version>/
        install.ps1, blt-collector.xml, README.txt
        payload.zip     python/        CPython 3.12 for Windows (python-build-standalone),
                                       with blt and everything it needs in Lib/site-packages
                        blt-collector-service.exe   WinSW, the service wrapper
                        licences/, VERSION

The payload is a zip in the zip so that install.ps1 unpacks the
thousands of files straight into Program Files, with .NET: unpacked by
hand into a Downloads folder, Prefect's longest paths go past Windows'
260 characters, and Explorer would mark every file as from the internet.

Every package is the version in uv.lock, installed for Windows from
Linux by uv (--python-platform). The build fails if any package has no
Windows wheel, or comes out built for Linux: a bundle that would only
fail on the Windows server is worse than none.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent

# Pinned: a new release is a change here, with its sha256 from the release's
# SHA256SUMS (python-build-standalone) or from hashing the asset (WinSW).
PYTHON_RELEASE = "20261110"
PYTHON_VERSION = "3.12.16"
PYTHON_URL = (
    "https://github.com/astral-sh/python-build-standalone/releases/download/"
    f"{PYTHON_RELEASE}/cpython-{PYTHON_VERSION}%2B{PYTHON_RELEASE}"
    "-x86_64-pc-windows-msvc-install_only.tar.gz"
)
PYTHON_SHA256 = "e23fe5b16926de1d81661b1a97352e89fb167549c55a6825796ccd0f32ec7f1b"
# WinSW 2.12.0, the last stable release, built for .NET Framework 4.6.1:
# Windows Server 2019 has 4.7.2, and this build is 0.6 MB where the
# self-contained one is 18.
WINSW_URL = "https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW.NET461.exe"
WINSW_SHA256 = "b5066b7bbdfba1293e5d15cda3caaea88fbeab35bd5b38c41c913d492aadfc4f"

PLATFORM = "x86_64-pc-windows-msvc"
# The tags a wheel for 64-bit Windows CPython 3.12 may carry.
WINDOWS_TAGS = ("-none-any", "-win_amd64")


def fail(message: str) -> None:
    sys.exit(f"bundle: {message}")


def fetch(url: str, sha256: str, cache: Path) -> Path:
    """The file at `url`, from the cache if it is there with the right hash."""
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / url.rsplit("/", 1)[1].replace("%2B", "+")
    if not path.exists() or _sha256(path) != sha256:
        print(f"Downloading {url}", flush=True)
        part = path.with_suffix(path.suffix + ".part")
        with urllib.request.urlopen(url, timeout=120) as answer, open(part, "wb") as out:
            shutil.copyfileobj(answer, out)
        got = _sha256(part)
        if got != sha256:
            part.unlink()
            fail(f"{url} has sha256 {got}, not the pinned {sha256}.")
        part.replace(path)
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def run(*command: str | Path, cwd: Path = ROOT) -> None:
    print("+", " ".join(str(c) for c in command), flush=True)
    result = subprocess.run([str(c) for c in command], cwd=cwd)
    if result.returncode != 0:
        fail(f"{command[0]} {command[1]} failed (exit {result.returncode}).")


def check_windows_only(site: Path) -> None:
    """Every installed wheel is for Windows (or for any platform), and
    nothing built for Linux slipped in - a package that had only an sdist
    and was built here, say."""
    wrong = []
    for wheel in site.glob("*.dist-info/WHEEL"):
        tags = [
            line.split(":", 1)[1].strip()
            for line in wheel.read_text().splitlines()
            if line.startswith("Tag:")
        ]
        if not tags or not any(tag.endswith(WINDOWS_TAGS) for tag in tags):
            wrong.append(f"{wheel.parent.name} ({', '.join(tags) or 'no tag'})")
    for suffix in ("*.so", "*.so.*", "*.dylib"):
        wrong += [str(p.relative_to(site)) for p in site.rglob(suffix)]
    if wrong:
        fail("not for Windows:\n  " + "\n  ".join(sorted(wrong)))
    for package in ("blt", "prefect", "sdk_primer", "commvault_sdk", "httpx", "certifi"):
        if not (site / package).is_dir():
            fail(f"{package} is missing from the bundle.")


def make_zip(source: Path, target: Path, top: str) -> None:
    """Zip `source`'s contents under `top`/, in a fixed order."""
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as out:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                # The payload, already compressed, is stored as it is.
                how = zipfile.ZIP_STORED if path.suffix == ".zip" else zipfile.ZIP_DEFLATED
                name = f"{top}/{path.relative_to(source).as_posix()}".lstrip("/")
                out.write(path, name, compress_type=how)


def build(out: Path, cache: Path) -> Path:
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    name = f"blt-collector-windows-{version}"
    with tempfile.TemporaryDirectory(prefix="blt-bundle-") as scratch:
        work = Path(scratch)
        payload = work / "payload"
        payload.mkdir()

        with tarfile.open(fetch(PYTHON_URL, PYTHON_SHA256, cache)) as tar:
            tar.extractall(payload, filter="data")
        python = payload / "python"
        if not (python / "python.exe").is_file():
            fail("the Python download has no python/python.exe.")
        # Debug symbols: a third of its size, of use to no one here.
        for pdb in python.rglob("*.pdb"):
            pdb.unlink()
        site = python / "Lib" / "site-packages"

        # Exactly uv.lock's versions, Windows' markers applied by uv.
        requirements = work / "requirements.txt"
        run(
            "uv", "export", "--frozen", "--no-dev", "--extra", "prefect",
            "--no-hashes", "--no-emit-project", "--no-header", "--output-file", requirements,
        )  # fmt: skip
        wheels = work / "wheels"
        run("uv", "build", "--wheel", "--out-dir", wheels, ROOT)
        install = (
            "uv", "pip", "install", "--target", site, "--no-deps", "--link-mode", "copy",
            "--python-platform", PLATFORM, "--python-version", "3.12",
        )  # fmt: skip
        run(*install, "-r", requirements)
        (blt_wheel,) = wheels.glob("blt-*.whl")
        run(*install, blt_wheel)
        # Console-script launchers: the service runs `python -m`, not these.
        shutil.rmtree(site / "bin", ignore_errors=True)
        check_windows_only(site)

        shutil.copy(fetch(WINSW_URL, WINSW_SHA256, cache), payload / "blt-collector-service.exe")
        licences = payload / "licences"
        licences.mkdir()
        shutil.copy(HERE / "WinSW-LICENSE.txt", licences / "WinSW.txt")
        shutil.copy(python / "LICENSE.txt", licences / "Python.txt")
        (payload / "VERSION").write_text(version + "\n")

        bundle = work / "bundle"
        bundle.mkdir()
        make_zip(payload, bundle / "payload.zip", "")
        for file in ("install.ps1", "blt-collector.xml", "README.txt"):
            # CRLF: Notepad on an old Windows Server shows LF-only files as one line.
            text = (HERE / file).read_text().replace("\r\n", "\n").replace("\n", "\r\n")
            (bundle / file).write_bytes(text.encode("utf-8"))
        (bundle / "VERSION").write_text(version + "\r\n")

        out.mkdir(parents=True, exist_ok=True)
        target = out / f"{name}.zip"
        make_zip(bundle, target.with_suffix(".part"), name)
        target.with_suffix(".part").replace(target)
    # A stable name to link to, beside the versioned one, each with its hash.
    latest = out / "blt-collector-windows.zip"
    shutil.copy(target, latest)
    for path in (target, latest):
        Path(f"{path}.sha256").write_text(f"{_sha256(path)}  {path.name}\n")
    size = target.stat().st_size / 1e6
    print(f"Built {target} ({size:.0f} MB), and {latest.name}", flush=True)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, help="where the zip goes")
    parser.add_argument(
        "--fetch-only", action="store_true", help="only download Python and WinSW to the cache"
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "blt-bundle",
        help="where the Python and WinSW downloads are kept",
    )
    args = parser.parse_args()
    if args.fetch_only:
        fetch(PYTHON_URL, PYTHON_SHA256, args.cache)
        fetch(WINSW_URL, WINSW_SHA256, args.cache)
        return
    if args.out is None:
        parser.error("--out is needed")
    build(args.out.resolve(), args.cache.resolve())


if __name__ == "__main__":
    main()
