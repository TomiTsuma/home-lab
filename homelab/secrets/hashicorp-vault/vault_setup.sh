#!/usr/bin/env bash
set -e

VAULT_DIR="/opt/vault"
VAULT_CONFIG="$VAULT_DIR/config"
VAULT_DATA="$VAULT_DIR/data"
SERVICE_FILE="/etc/systemd/system/vault.service"
VAULT_IMAGE="hashicorp/vault:latest"

echo "▶ Checking Docker..."
if ! command -v docker &>/dev/null; then
  echo "▶ Docker not found. Installing..."
  curl -fsSL https://get.docker.com | sh
  systemctl enable docker
  systemctl start docker
fi

echo "▶ Creating Vault directories..."
mkdir -p "$VAULT_CONFIG" "$VAULT_DATA"
chmod -R 777 "$VAULT_DIR"

echo "▶ Writing Vault config..."
cat <<EOF > "$VAULT_CONFIG/vault.hcl"
storage "file" {
  path = "/vault/data"
}

listener "tcp" {
  address     = "0.0.0.0:8200"
  tls_disable = 1
}

ui = true
disable_mlock = true
EOF

echo "▶ Writing systemd service..."
cat <<EOF > "$SERVICE_FILE"
[Unit]
Description=HashiCorp Vault (Docker)
After=network-online.target docker.service
Requires=docker.service

/usr/bin/docker run --rm \
  --name vault \
  --cap-add=IPC_LOCK \
  -p 8200:8200 \
  -v /opt/vault/config:/vault/config \
  -v /opt/vault/data:/vault/data \
  hashicorp/vault:latest server -config=/vault/config/vault.hcl

[Service]
Restart=always
RestartSec=5
ExecStart= sudo /usr/bin/docker run --rm \
  --name vault \
  --cap-add=IPC_LOCK \
  -p 8200:8200 \
  -v $VAULT_CONFIG:/vault/config \
  -v $VAULT_DATA:/vault/data \
  $VAULT_IMAGE server -config=/vault/config/vault.hcl

ExecStop=/usr/bin/docker stop vault
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
EOF

echo "▶ Reloading systemd..."
systemctl daemon-reexec
systemctl daemon-reload

echo "▶ Enabling Vault service..."
systemctl enable vault

echo "▶ Starting Vault..."
systemctl start vault

echo "✔ Vault is now running as a Docker-backed systemd service"
echo "➡ Check status with: systemctl status vault"
echo "➡ Access Vault at: http://localhost:8200"
