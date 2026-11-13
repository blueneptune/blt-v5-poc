"""A remote collector's start (blt.flows.collector), the same on Linux
and Windows, and the step every flow run starts with (blt.flows.steps)."""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from prefect.exceptions import ObjectAlreadyExists, ObjectNotFound

from blt.flows import collector, steps


@pytest.mark.parametrize("name", ["uk", "omaha-2", "a", "9z"])
def test_good_collector_names(name: str) -> None:
    assert collector.collector_name(name) == name


@pytest.mark.parametrize("name", [None, "", "default", "Tools01", "-x", "a b", "uk_1", "a" * 64])
def test_bad_collector_names_are_refused(name: str | None) -> None:
    with pytest.raises(ValueError, match="lower case letters"):
        collector.collector_name(name)


def test_settings_come_from_the_environment() -> None:
    assert collector.settings_from({"BLT_COLLECTOR": "uk"}) == ("uk", "blt", "process")
    assert collector.settings_from(
        {"BLT_COLLECTOR": "uk", "BLT_WORK_POOL": "lab", "BLT_WORKER_TYPE": "kubernetes"}
    ) == ("uk", "lab", "kubernetes")
    with pytest.raises(ValueError, match="BLT_WORKER_TYPE"):
        collector.settings_from({"BLT_COLLECTOR": "uk", "BLT_WORKER_TYPE": "docker"})


def test_a_settings_file_goes_into_the_environment_and_only_names_are_said(
    tmp_path: Path,
) -> None:
    secret = "admin:p'a\"s#s $x\\y"
    path = tmp_path / "collector.json"
    # As Windows PowerShell 5.1 writes UTF-8: with a BOM.
    path.write_bytes(
        b"\xef\xbb\xbf"
        + json.dumps({"BLT_COLLECTOR": "uk", "PREFECT_API_AUTH_STRING": secret}).encode()
    )
    env: dict[str, str] = {"PREFECT_API_AUTH_STRING": "old"}
    names = collector.load_settings(path, env)
    assert names == ["BLT_COLLECTOR", "PREFECT_API_AUTH_STRING"]
    assert env == {"BLT_COLLECTOR": "uk", "PREFECT_API_AUTH_STRING": secret}


def test_a_settings_file_of_anything_but_strings_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "collector.json"
    path.write_text(json.dumps({"BLT_COLLECTOR": 1}))
    with pytest.raises(ValueError, match="names to strings"):
        collector.load_settings(path, {})


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_it_waits_for_prefect_for_as_long_as_it_takes_saying_why_now_and_then(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = Clock()
    answers = iter([ConnectionError("certificate verify failed")] * 400 + [None])
    with caplog.at_level(logging.INFO, logger="blt.collector"):
        collector.wait_for_prefect(
            0, healthcheck=lambda: next(answers), clock=clock, sleep=clock.sleep
        )
    still = [r.getMessage() for r in caplog.records if "Still waiting" in r.getMessage()]
    # 400 tries 3 s apart is 20 minutes: said at once, then every 5.
    assert len(still) == 4
    assert "ConnectionError: certificate verify failed" in still[0]
    assert "The Prefect server answers." in caplog.text


def test_with_a_limit_it_gives_up() -> None:
    clock = Clock()
    with pytest.raises(SystemExit, match="did not answer within 180 s"):
        collector.wait_for_prefect(
            180, healthcheck=lambda: OSError("refused"), clock=clock, sleep=clock.sleep
        )
    assert 180 < clock.now < 190


class Client:
    def __init__(self, exists: bool, race: bool = False) -> None:
        self.exists, self.race = exists, race
        self.made: list[tuple[str, str | None]] = []

    def read_work_queue_by_name(self, name: str, work_pool_name: str | None = None) -> Any:
        if not self.exists:
            raise ObjectNotFound(http_exc=Exception())
        return object()

    def create_work_queue(self, name: str, work_pool_name: str | None = None) -> Any:
        if self.race:
            raise ObjectAlreadyExists(http_exc=Exception())
        self.made.append((name, work_pool_name))
        return object()


def test_the_queue_is_made_only_when_missing() -> None:
    missing, there, raced = Client(exists=False), Client(exists=True), Client(False, race=True)
    assert collector.ensure_queue("uk", "blt", missing) is True
    assert missing.made == [("uk", "blt")]
    assert collector.ensure_queue("uk", "blt", there) is False
    assert there.made == []
    # Another machine running the same collector made it first.
    assert collector.ensure_queue("uk", "blt", raced) is False


def test_the_worker_takes_only_the_collector_s_queue_with_this_python() -> None:
    command = collector.worker_command("uk", "blt", "process", "tools01")
    assert command[:3] == [sys.executable, "-m", "prefect"]
    assert command[3:] == [
        "worker",
        "start",
        "--pool",
        "blt",
        "--type",
        "process",
        "--work-queue",
        "uk",
        "--name",
        "blt-collector-uk-tools01",
    ]


def test_a_bad_name_stops_it_before_it_waits(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("BLT_COLLECTOR", "default")

    def never() -> None:
        raise AssertionError("waited")

    monkeypatch.setattr(collector, "wait_for_prefect", lambda *a, **k: never())
    assert collector.main([]) == 1
    assert "not 'default'" in caplog.text


def test_main_waits_makes_the_queue_and_runs_the_worker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = tmp_path / "collector.json"
    settings.write_text(json.dumps({"BLT_COLLECTOR": "uk", "BLT_WORK_POOL": "lab"}))
    for name in ("BLT_COLLECTOR", "BLT_WORK_POOL", "BLT_WORKER_TYPE"):
        monkeypatch.delenv(name, raising=False)
    calls: list[Any] = []
    monkeypatch.setattr(collector, "wait_for_prefect", lambda limit: calls.append(("wait", limit)))
    monkeypatch.setattr(
        collector, "ensure_queue", lambda name, pool: calls.append(("queue", name, pool))
    )
    monkeypatch.setattr(collector, "run_worker", lambda command: calls.append(command) or 0)
    monkeypatch.setattr(collector.socket, "gethostname", lambda: "TOOLS01.example.test")
    assert collector.main(["--settings", str(settings)]) == 0
    assert calls[:2] == [("wait", 0), ("queue", "uk", "lab")]
    assert calls[2][-4:] == ["--work-queue", "uk", "--name", "blt-collector-uk-tools01"]
    assert os.environ["BLT_WORK_POOL"] == "lab"


def test_check_says_what_is_wrong_and_fails(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(collector, "check", lambda pool: ["Prefect at x: refused"])
    assert collector.main(["--check"]) == 1
    assert "Prefect at x: refused" in caplog.text
    monkeypatch.setattr(collector, "check", lambda pool: [])
    assert collector.main(["--check"]) == 0


def test_a_flow_run_starts_in_app_where_there_is_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    app = tmp_path / "app"
    app.mkdir()
    assert steps.app_directory(str(app)) == {"directory": str(app)}
    # A Windows collector has no /app: the run stays where the worker put it.
    assert steps.app_directory(str(tmp_path / "missing")) == {"directory": str(app)}
