from pathlib import Path

from blt.collector.lock import run_lock


def test_a_second_holder_is_refused_until_the_first_lets_go(tmp_path: Path) -> None:
    with run_lock(tmp_path / "locks", "prod.collection") as first:
        assert first is True
        with run_lock(tmp_path / "locks", "prod.collection") as second:
            assert second is False
        # A different kind, or a different CommCell, is a different lock.
        with run_lock(tmp_path / "locks", "prod.backfill") as other_kind:
            assert other_kind is True
        with run_lock(tmp_path / "locks", "dr.collection") as other_commcell:
            assert other_commcell is True
    with run_lock(tmp_path / "locks", "prod.collection") as again:
        assert again is True
