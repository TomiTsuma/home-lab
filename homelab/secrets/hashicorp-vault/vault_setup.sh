#!/usr/bin/env bash
set -e

VAULT_VERSION="1.15.6"
VAULT_USER="vault"
VAULT_GROUP="vault"

VAULT_BIN="/usr/local/bin/vault"
VAULT_CONFIG_DIR="/etc/vault"
VAULT_DATA_DIR="/var/lib/vault"
VAULT_LOG_DIR="/var/log/vault"
VAULT_SERVICE="/etc/systemd/system/vault.service"

echo "▶ Installing dependencies..."
apt-get update -y
apt-get install -y curl unzip ca-certificates

echo "▶ Downloading Vault ${VAULT_VERSION}..."
cd /tmp
curl -fsSL https://releases.hashicorp.com/vault/${VAULT_VERSION}/vault_${VAULT_VERSION}_linux_amd64.zip -o vault.zip
unzip -o vault.zip
mv vault ${VAULT_BIN}
chmod +x ${VAULT_BIN}

echo "▶ Creating vault user..."
if ! id "${VAULT_USER}" &>/dev/null; then
  useradd --system --home ${VAULT_DATA_DIR} --shell /bin/false ${VAULT_USER}
fi

echo "▶ Creating directories..."
mkdir -p ${VAULT_CONFIG_DIR} ${VAULT_DATA_DIR} ${VAULT_LOG_DIR}
chown -R ${VAULT_USER}:${VAULT_GROUP} \
  ${VAULT_CONFIG_DIR} \
  ${VAULT_DATA_DIR} \
  ${VAULT_LOG_DIR}
chmod 750 ${VAULT_CONFIG_DIR} ${VAULT_DATA_DIR}

echo "▶ Writing Vault configuration..."
cat <<EOF > ${VAULT_CONFIG_DIR}/vault.hcl
storage "file" {
  path = "${VAULT_DATA_DIR}"
}

listener "tcp" {
  address     = "127.0.0.1:8200"
  tls_disable = 1
}

ui = true
disable_mlock = false
EOF

chown ${VAULT_USER}:${VAULT_GROUP} ${VAULT_CONFIG_DIR}/vault.hcl
chmod 640 ${VAULT_CONFIG_DIR}/vault.hcl

echo "▶ Writing systemd service..."
cat <<EOF > ${VAULT_SERVICE}
[Unit]
Description=HashiCorp Vault
Documentation=https://www.vaultproject.io/docs
After=network-online.target
Requires=network-online.target

[Service]
User=${VAULT_USER}
Group=${VAULT_GROUP}
ExecStart=${VAULT_BIN} server -config=${VAULT_CONFIG_DIR}/vault.hcl
ExecReload=/bin/kill --signal HUP \$MAINPID
Restart=on-failure
RestartSec=5
LimitNOFILE=65536
CapabilityBoundingSet=CAP_IPC_LOCK
AmbientCapabilities=CAP_IPC_LOCK
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF

echo "▶ Enabling and starting Vault..."
systemctl daemon-reexec
systemctl daemon-reload
systemctl enable vault
systemctl start vault

echo "✔ Vault installation complete"
echo "➡ Status: systemctl status vault"
echo "➡ Initialize with:"
echo "   export VAULT_ADDR=http://127.0.0.1:8200"
echo "   vault operator init"
