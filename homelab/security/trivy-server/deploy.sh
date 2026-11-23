#!/usr/bin/env bash
# Create or update the "trivy-server" custom app on TrueNAS from docker-compose.yml.
#
# Usage: ./deploy.sh            (reads TRIVY_TOKEN from ./.env)
#
# Runs midclt over SSH as truenas_admin (in builtin_administrators, so no sudo).
# The rendered compose (with the token) is never written on this machine; on TrueNAS
# it sits in a mktemp file for the few seconds of the call, then only in the
# root-only app config.
set -euo pipefail
cd "$(dirname "$0")"

TRUENAS="${TRUENAS:-truenas_admin@100.100.133.10}"
APP=trivy-server

[ -f .env ] && set -a && . ./.env && set +a
: "${TRIVY_TOKEN:?set TRIVY_TOKEN in .env (see .env.example)}"

python3 - "$APP" <<'PY' | ssh "$TRUENAS" 'f=$(mktemp); cat > "$f"; python3 - "$f" <<"EOF"
import json, subprocess, sys
p = json.load(open(sys.argv[1]))
exists = json.loads(subprocess.check_output(
    ["midclt", "call", "app.query", json.dumps([["name", "=", p["app_name"]]])]))
if exists:
    args = ["app.update", p["app_name"], json.dumps({"custom_compose_config_string": p["custom_compose_config_string"]})]
else:
    args = ["app.create", json.dumps(p)]
print(("updating" if exists else "creating"), p["app_name"], flush=True)
subprocess.run(["midclt", "call", "--job", *args], check=True, stdout=subprocess.DEVNULL)
print("done")
EOF
rm -f "$f"'
import json, os, sys
compose = open("docker-compose.yml").read()
for var in ("TRIVY_TOKEN",):
    compose = compose.replace("${" + var + "}", os.environ[var])
print(json.dumps({"app_name": sys.argv[1], "custom_app": True, "custom_compose_config_string": compose}))
PY
