import pytest

from blt.schemas import MISSING_STATUS, classify_status


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("Running", ("running", True)),
        ("Queued", ("queued", True)),
        ("Waiting", ("waiting", True)),
        ("Suspended", ("suspended", True)),
        ("Kill Pending", ("stopping", True)),
        ("Completed", ("completed", False)),
        ("Completed w/ one or more errors", ("completed_with_errors", False)),
        ("Failed", ("failed", False)),
        ("Killed", ("killed", False)),
        (MISSING_STATUS, ("missing", False)),
        # Unrecognised stays active, so it keeps getting re-checked.
        ("Some New Status", ("unknown", True)),
    ],
)
def test_classify_status(status: str, expected: tuple[str, bool]) -> None:
    assert classify_status(status) == expected
