#!/bin/bash
# ======================================================
# Install Sonarr Natively on Ubuntu (no Docker)
# Runs as current logged-in user
# Compatible with Ubuntu 22.04+ (noble, jammy)
# ======================================================

set -e

CURRENT_USER=$(whoami)
CONFIG_DIR="/home/$CURRENT_USER/.config/Sonarr"

echo "🚀 Installing Sonarr as user: $CURRENT_USER"

# --- 1️⃣ Update and dependencies ---
sudo apt update -y
sudo apt install -y curl mediainfo sqlite3 libchromaprint-tools gnupg apt-transport-https ca-certificates

# --- 2️⃣ Check network and DNS ---
if ! ping -c 1 apt.servarr.com &>/dev/null; then
  echo "⚠️ DNS resolution for apt.servarr.com failed!"
  echo "🔧 Trying to use Google DNS temporarily..."
  echo "nameserver 8.8.8.8" | sudo tee /etc/resolv.conf > /dev/null
fi

# --- 3️⃣ Add Sonarr repository ---
echo "🌐 Adding Servarr repository..."
sudo mkdir -p /etc/apt/keyrings
curl -fsSL https://apt.servarr.com/servarr.gpg | sudo gpg --dearmor -o /etc/apt/keyrings/servarr.gpg

echo "deb [signed-by=/etc/apt/keyrings/servarr.gpg] https://apt.servarr.com/ubuntu noble main" \
| sudo tee /etc/apt/sources.list.d/servarr.list

# --- 4️⃣ Install Sonarr ---
sudo apt update -y
sudo apt install -y sonarr

# --- 5️⃣ Create systemd service using current user ---
echo "⚙️ Creating systemd service..."
sudo bash -c "cat <<EOF > /etc/systemd/system/sonarr.service
[Unit]
Description=Sonarr Daemon
After=network.target

[Service]
User=$CURRENT_USER
Group=$CURRENT_USER
Type=simple
ExecStart=/usr/bin/mono --debug /opt/NzbDrone/Sonarr.exe -nobrowser -data=$CONFIG_DIR
Restart=on-failure
TimeoutStopSec=20

[Install]
WantedBy=multi-user.target
EOF"

# --- 6️⃣ Enable and start service ---
sudo systemctl daemon-reload
sudo systemctl enable sonarr
sudo systemctl start sonarr

# --- 7️⃣ Done ---
echo ""
echo "✅ Sonarr installation completed successfully!"
echo "📍 Access Sonarr at: http://<your-server-ip>:8989"
sudo systemctl status sonarr --no-pager
