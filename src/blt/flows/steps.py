"""The step every flow run starts with (prefect.yaml, `pull`).

blt's code is already installed wherever a flow run starts, so all a run
needs is the right working directory: /app in the worker image and the
cluster's pods. A Windows collector has no /app - Prefect's own
set_working_directory would fail there ("/app" is C:\\app) - and its
runs start in the folder the worker gives them, which is just as good:
the entrypoints are modules (blt.flows.flows:collect_jobs), found on
the installed package's path whatever the directory.
"""

from __future__ import annotations

import os


def app_directory(directory: str = "/app") -> dict[str, str]:
    """Change to `directory` if this machine has it; stay put if not."""
    if os.path.isdir(directory):
        os.chdir(directory)
    return {"directory": os.getcwd()}
