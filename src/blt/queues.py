"""Where a source's flow runs go: the Prefect work queue of the collector
that can reach it.

blt's own worker (the one in the blt pod) takes the work pool's
`default` queue. A remote collector - a worker somewhere that reaches
sources blt's own cannot, e.g. a tools server inside a production
network (docs/remote-collector.md) - takes a queue of its own, named
after it. A source's block says which collector reaches it (`collector`);
none means blt's own worker.

Shared by the flows (which start the per-source runs) and the API (which
starts checks and run-now runs), so nothing here needs Prefect.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

DEFAULT_QUEUE = "default"
WORK_POOL = "blt"

# As for block names: it becomes a work queue name and part of a
# container name.
COLLECTOR_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")

# The parameter that names a source, for each flow that works on one
# source, and the block type that source's block is in. A run of one of
# these flows goes to that source's collector.
SOURCE_PARAMETERS: dict[str, str] = {
    "commcell": "blt-commvault-commcell",
    "account": "blt-rubrik-account",
    "primary": "blt-netbackup-primary",
}


def check_collector(value: str | None) -> str | None:
    """A collector name, or None for blt's own worker. Raises ValueError."""
    if value is None or not value.strip():
        return None
    name = value.strip()
    if name == DEFAULT_QUEUE:
        # "default" is blt's own worker's queue: saying so is saying none.
        return None
    if not COLLECTOR_NAME.match(name):
        raise ValueError(
            "A collector's name: lower case letters, digits and dashes, starting with a letter "
            "or digit."
        )
    return name


def queue_for(block_data: Mapping[str, Any]) -> str:
    """The work queue for a source, from its block's data."""
    collector = block_data.get("collector")
    return collector if isinstance(collector, str) and collector else DEFAULT_QUEUE
