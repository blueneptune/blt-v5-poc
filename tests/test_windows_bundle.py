"""The Windows collector bundle (deploy/windows): what can be checked on
Linux. CI's windows-collector job runs the bundle itself on Windows."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parent.parent
WINDOWS = ROOT / "deploy/windows"


def bundle() -> ModuleType:
    spec = importlib.util.spec_from_file_location("bundle", WINDOWS / "bundle.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def site_with(tmp_path: Path, wheels: dict[str, str]) -> Path:
    site = tmp_path / "site-packages"
    for package in ("blt", "prefect", "sdk_primer", "commvault_sdk", "httpx", "certifi"):
        (site / package).mkdir(parents=True)
    for name, tag in wheels.items():
        info = site / f"{name}.dist-info"
        info.mkdir()
        (info / "WHEEL").write_text(f"Wheel-Version: 1.0\nTag: {tag}\n")
    return site


def test_windows_and_pure_python_wheels_are_accepted(tmp_path: Path) -> None:
    site = site_with(
        tmp_path,
        {
            "httpx-0.28.1": "py3-none-any",
            "pywin32-312": "cp312-cp312-win_amd64",
            "cryptography-46": "cp311-abi3-win_amd64",
        },
    )
    bundle().check_windows_only(site)


def test_a_linux_wheel_or_library_fails_the_build(tmp_path: Path) -> None:
    site = site_with(tmp_path, {"orjson-3": "cp312-cp312-manylinux_2_17_x86_64"})
    with pytest.raises(SystemExit, match="orjson-3.dist-info"):
        bundle().check_windows_only(site)
    site = site_with(tmp_path / "2", {"x-1": "py3-none-any"})
    (site / "x" / "_speedups.cpython-312-x86_64-linux-gnu.so").parent.mkdir()
    (site / "x" / "_speedups.cpython-312-x86_64-linux-gnu.so").write_bytes(b"")
    with pytest.raises(SystemExit, match="_speedups"):
        bundle().check_windows_only(site)


def test_downloads_are_pinned_by_hash() -> None:
    module = bundle()
    for url, sha in (
        (module.PYTHON_URL, module.PYTHON_SHA256),
        (module.WINSW_URL, module.WINSW_SHA256),
    ):
        assert url.startswith("https://github.com/") and "/releases/download/" in url
        assert "latest" not in url
        assert re.fullmatch(r"[0-9a-f]{64}", sha)
    assert "-x86_64-pc-windows-msvc-install_only.tar.gz" in module.PYTHON_URL
    assert "cpython-3.12." in module.PYTHON_URL


def test_install_ps1_fills_every_placeholder_of_the_service_template() -> None:
    template = (WINDOWS / "blt-collector.xml").read_text()
    install = (WINDOWS / "install.ps1").read_text()
    placeholders = set(re.findall(r"@[A-Z]+@", template))
    assert placeholders
    for placeholder in placeholders:
        assert f"'{placeholder}'" in install, placeholder


def test_install_ps1_takes_no_secret_on_its_command_line() -> None:
    install = (WINDOWS / "install.ps1").read_text()
    params = install.split("param(", 1)[1].split(")\n", 1)[0]
    assert not re.search(r"password|auth|key|secret|token", params, re.IGNORECASE), params
    assert "Read-Host -Prompt $Prompt -AsSecureString" in install
    # Nothing that would write what is typed to a transcript or the screen.
    assert "Start-Transcript" not in install
    assert not re.search(r"Write-(Host|Output|Verbose)[^\n]*\$(auth|key)\b", install)


def test_the_collector_never_turns_certificate_checks_off() -> None:
    sources = [WINDOWS / "install.ps1", ROOT / "src/blt/flows/collector.py"]
    for path in sources:
        text = path.read_text()
        assert "TLS_INSECURE_SKIP_VERIFY" not in text
        assert "verify=False" not in text
    settings = (WINDOWS / "install.ps1").read_text()
    assert "$settings['SSL_CERT_FILE'] = $CaBundle" in settings
    assert "$settings['PREFECT_API_SSL_CERT_FILE'] = $CaBundle" in settings


def test_up_serves_the_bundle_only_over_https() -> None:
    env = (ROOT / "deploy/env.sh").read_text()
    wanted = env.split("windows_bundle_wanted() {", 1)[1].split("}", 1)[0]
    assert '"$UI_TLS" == on' in wanted and '"$UI_ENABLED" == true' in wanted
    assert "podman build -t blt-collector-windows -f deploy/windows/Containerfile ." in env
    up = (ROOT / "deploy/up.sh").read_text()
    tls = up.split('if [[ "$UI_TLS" == on ]]; then', 1)[1].split("\n    fi\n", 1)[0]
    assert "type=image,source=localhost/blt-collector-windows,destination=/bundle" in tls
    assert "BLT_DOWNLOADS=/bundle/downloads" in tls
    move = (ROOT / "deploy/move.sh").read_text()
    assert "OPTIONAL_IMAGES=(localhost/blt-collector-windows)" in move
