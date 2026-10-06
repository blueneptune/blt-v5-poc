#!/usr/bin/env bash
# Stop and remove the "blt" pod. The blt-pgdata volume (the job data) is
# left alone; remove it yourself with `podman volume rm blt-pgdata`.
set -euo pipefail
podman pod rm -f blt
