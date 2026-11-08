#!/usr/bin/env bash
# Superseded 2026-11-08: this script described a native systemd install
# (SQLite backend, local-file artifacts, port 5000) that was never actually
# deployed. The real MLflow tracking server lives in
# ../../compute/mlflow/ — a dockerized server backed by the shared
# ereuna_postgres (dedicated `mlflow` DB) and the native MinIO on
# aeacus-data-handler, exposed at https://tracking.ereuna.org. See
# ../../compute/mlflow/README.md for the live deployment.
echo "See homelab/compute/mlflow/README.md for the real, deployed MLflow setup." >&2
exit 1
