#!/usr/bin/env bash
set -e

### CONFIGURATION ###
VAULT_VERSION="1.15.6"
VAULT_USER="vault"
VAULT_GROUP="vault"

VAULT_BIN="/usr/local/bin/vault"
VAULT_CONFIG_DIR="/etc/vault"
VAULT_DATA_DIR="/var/lib/vault"
VAULT_LOG_DIR="/var/log/vault"
VAULT_TLS_DIR="/etc/vault/tls"
VAULT_SERVICE="/etc/systemd/system/vault.service"

VAULT_PORT="8200"
VAULT_ADDR_BIND="0.0.0.0"

### PRECHECK ###
if [[ $EUID -ne 0 ]]; then
  echo "❌ Run as root (sudo)"
  exit 1
fi

echo "▶ Installing dependencies..."
apt-get update -y
apt-get install -y curl unzip ca-certificates openssl

echo "▶ Downloading Vault ${VAULT_VERSION}..."
cd /tmp
curl -fsSL https://releases.hashicorp.com/vault/${VAULT_VERSION}/vault_${VAULT_VERSION}_linux_amd64.zip -o vault.zip
unzip -o vault.zip
mv vault ${VAULT_BIN}
chmod +x ${VAULT_BIN}

echo "▶ Creating Vault user..."
if ! id "${VAULT_USER}" &>/dev/null; then
  useradd --system --home ${VAULT_DATA_DIR} --shell /bin/false ${VAULT_USER}
fi

echo "▶ Creating directories..."
mkdir -p \
  ${VAULT_CONFIG_DIR} \
  ${VAULT_DATA_DIR} \
  ${VAULT_LOG_DIR} \
  ${VAULT_TLS_DIR}

chown -R ${VAULT_USER}:${VAULT_GROUP} \
  ${VAULT_CONFIG_DIR} \
  ${VAULT_DATA_DIR} \
  ${VAULT_LOG_DIR}

chmod 750 ${VAULT_CONFIG_DIR} ${VAULT_DATA_DIR}

echo "▶ Generating self-signed TLS certificate..."
openssl req -x509 -newkey rsa:4096 \
  -keyout ${VAULT_TLS_DIR}/vault.key \
  -out ${VAULT_TLS_DIR}/vault.crt \
  -days 365 \
  -nodes \
  -subj "/CN=vault.local"

chown -R ${VAULT_USER}:${VAULT_GROUP} ${VAULT_TLS_DIR}
chmod 600 ${VAULT_TLS_DIR}/vault.key
chmod 644 ${VAULT_TLS_DIR}/vault.crt

echo "▶ Writing Vault configuration..."
cat <<EOF > ${VAULT_CONFIG_DIR}/vault.hcl
storage "file" {
  path = "${VAULT_DATA_DIR}"
}

listener "tcp" {
  address         = "${VAULT_ADDR_BIND}:${VAULT_PORT}"
  tls_disable     = 0
  tls_cert_file  = "${VAULT_TLS_DIR}/vault.crt"
  tls_key_file   = "${VAULT_TLS_DIR}/vault.key"
}

ui = true
disable_mlock = false
EOF

chown ${VAULT_USER}:${VAULT_GROUP} ${VAULT_CONFIG_DIR}/vault.hcl
chmod 640 ${VAULT_CONFIG_DIR}/vault.hcl

echo "▶ Writing systemd service..."
cat <<EOF > ${VAULT_SERVICE}
[Unit]
Description=HashiCorp Vault (Secure)
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

echo "▶ Enabling Vault service..."
systemctl daemon-reexec
systemctl daemon-reload
systemctl enable vault
systemctl start vault

echo ""
echo "✅ Vault installation complete"
echo ""
echo "🔐 Vault is running with TLS enabled"
echo "🌐 Accessible on: https://<HOST_IP>:${VAULT_PORT}"
echo ""
echo "Next steps:"
echo "  export VAULT_ADDR=https://<HOST_IP>:${VAULT_PORT}"
echo "  vault operator init"
echo "  vault operator unseal (3x)"
