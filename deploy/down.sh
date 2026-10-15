#!/usr/bin/env bash
# Stop and remove the "blt" pod, however it was started (deploy/up.sh or
# deploy/kube/up.sh). The blt-pgdata volume - the job data, and Prefect's
# database - is left alone; remove it yourself with
# `podman volume rm blt-pgdata`.
set -euo pipefail
podman pod rm -f --ignore blt
# deploy/kube/up.sh keeps the passwords in a podman secret.
podman secret rm --ignore blt-secrets >/dev/null 2>&1 || true
