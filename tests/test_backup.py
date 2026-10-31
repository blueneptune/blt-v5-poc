"""deploy/backup.sh, with podman faked: what it dumps, where, what it
keeps, and that a failed dump never replaces a good one."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

ENV = """\
BLT_API_KEY=key-1
POSTGRES_PASSWORD=pg-secret
BLT_PREFECT=true
PREFECT_DB_PASSWORD=prefect-secret
PREFECT_AUTH=admin:ui-secret
"""

# Answers just the podman calls backup.sh makes. FAKE_DUMP_FAILS names a
# database whose pg_dump fails; FAKE_NOT_RUNNING stops the container.
FAKE_PODMAN = """\
#!/usr/bin/env bash
echo "$*" >> "$FAKE_LOG"
case "$*" in
    "container inspect"*) [[ -n "${FAKE_NOT_RUNNING:-}" ]] && echo false || echo true ;;
    *"pg_dump"*)
        db="${@: -1}"
        [[ "$db" == "${FAKE_DUMP_FAILS:-}" ]] && exit 1
        echo "dump of $db" ;;
    *"pg_restore --list"*) cat > /dev/null ;;
    *) exit 99 ;;
esac
"""


def backup(tmp_path: Path, extra: str = "", **env: str) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    podman = bin_dir / "podman"
    podman.write_text(FAKE_PODMAN)
    podman.chmod(0o755)
    env_file = tmp_path / "blt.env"
    env_file.write_text(ENV + f"BLT_BACKUP_DIR={tmp_path / 'backups'}\n" + extra)
    return subprocess.run(
        ["bash", "deploy/backup.sh"],
        cwd=ROOT,
        env={
            # Without blt's own settings, so a developer's real blt.env
            # cannot leak in.
            **{
                k: v
                for k, v in os.environ.items()
                if not k.startswith(("BLT_", "PREFECT_", "POSTGRES_"))
            },
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "BLT_ENV_FILE": str(env_file),
            "FAKE_LOG": str(tmp_path / "podman.log"),
            **env,
        },
        capture_output=True,
        text=True,
    )


def dumps(tmp_path: Path, db: str) -> list[str]:
    return sorted(p.name for p in (tmp_path / "backups").glob(f"{db}-nightly-*.dump"))


def test_both_databases_are_dumped_privately(tmp_path: Path) -> None:
    result = backup(tmp_path)

    assert result.returncode == 0, result.stderr
    assert len(dumps(tmp_path, "blt")) == len(dumps(tmp_path, "prefect")) == 1
    blt = tmp_path / "backups" / dumps(tmp_path, "blt")[0]
    assert blt.read_text() == "dump of blt\n"
    # The prefect dump holds the blocks' secrets.
    assert stat.S_IMODE((tmp_path / "backups").stat().st_mode) == 0o700
    assert stat.S_IMODE(blt.stat().st_mode) == 0o600
    log = (tmp_path / "podman.log").read_text()
    assert "exec blt-postgres pg_dump -Fc -U blt -d blt" in log
    assert log.count("pg_restore --list") == 2


def test_with_prefect_off_only_blt_is_dumped(tmp_path: Path) -> None:
    assert backup(tmp_path, "BLT_PREFECT=false\n").returncode == 0
    assert len(dumps(tmp_path, "blt")) == 1 and dumps(tmp_path, "prefect") == []


def test_only_the_newest_nightly_dumps_are_kept(tmp_path: Path) -> None:
    folder = tmp_path / "backups"
    folder.mkdir()
    for day in range(1, 6):
        (folder / f"blt-nightly-202610{day:02d}-023000.dump").write_text("old")
    (folder / "blt-before-2026.10.30.dump").write_text("by hand")

    assert backup(tmp_path, "BLT_BACKUP_KEEP=3\n").returncode == 0

    kept = dumps(tmp_path, "blt")
    assert len(kept) == 3
    assert kept[:2] == ["blt-nightly-20261004-023000.dump", "blt-nightly-20261005-023000.dump"]
    assert (folder / "blt-before-2026.10.30.dump").exists()


def test_a_failed_dump_leaves_nothing_and_keeps_the_old_ones(tmp_path: Path) -> None:
    folder = tmp_path / "backups"
    folder.mkdir()
    for day in range(1, 4):
        (folder / f"prefect-nightly-202610{day:02d}-023000.dump").write_text("old")

    result = backup(tmp_path, "BLT_BACKUP_KEEP=1\n", FAKE_DUMP_FAILS="prefect")

    assert result.returncode == 1
    assert "pg_dump of prefect failed" in result.stderr
    assert len(dumps(tmp_path, "prefect")) == 3
    assert not list(folder.glob("*.partial"))
    assert len(dumps(tmp_path, "blt")) == 1  # the other database still backed up


def test_nothing_is_done_when_postgres_is_not_running(tmp_path: Path) -> None:
    result = backup(tmp_path, FAKE_NOT_RUNNING="1")
    assert result.returncode == 1
    assert "not running" in result.stderr


@pytest.mark.parametrize("bad", ["0", "lots"])
def test_a_bad_keep_count_is_refused(tmp_path: Path, bad: str) -> None:
    result = backup(tmp_path, f"BLT_BACKUP_KEEP={bad}\n")
    assert result.returncode == 1
    assert "BLT_BACKUP_KEEP" in result.stderr
