#!/bin/bash
# ========================================
# Sonarr Native Installation Script
# Runs as current user (no dedicated user)
# Tested on Ubuntu 20.04+
# ========================================

set -e

CURRENT_USER=$(whoami)
INSTALL_DIR="/opt/sonarr"
CONFIG_DIR="/home/$CURRENT_USER/.config/Sonarr"

echo "🚀 Installing Sonarr as user: $CURRENT_USER"

# --- 1️⃣ Update system ---
echo "🔄 Updating system packages..."
sudo apt update && sudo apt upgrade -y

# --- 2️⃣ Install dependencies ---
echo "📦 Installing dependencies..."
sudo apt install -y curl mediainfo sqlite3 libchromaprint-tools gnupg apt-transport-https

# --- 3️⃣ Add Sonarr repository and install (Debian/Ubuntu method) ---
echo "🌐 Adding Sonarr repository..."
sudo mkdir -p /etc/apt/keyrings
sudo curl -fsSL https://apt.sonarr.tv/sonarr-release.gpg | sudo gpg --dearmor -o /etc/apt/keyrings/sonarr-release.gpg

echo "deb [signed-by=/etc/apt/keyrings/sonarr-release.gpg] https://apt.sonarr.tv/ubuntu jammy main" | sudo tee /etc/apt/sources.list.d/sonarr.list

echo "🔄 Updating package list..."
sudo apt update

echo "⬇️ Installing Sonarr..."
sudo apt install -y sonarr

# --- 4️⃣ Create installation directory ---
echo "📁 Setting up Sonarr directory at $INSTALL_DIR..."
sudo mkdir -p $INSTALL_DIR
sudo cp -r /usr/lib/sonarr/* $INSTALL_DIR || echo "Sonarr files already copied."
sudo chown -R $CURRENT_USER:$CURRENT_USER $INSTALL_DIR

# --- 5️⃣ Create systemd service file ---
echo "⚙️ Creating systemd service for Sonarr..."
sudo bash -c "cat <<EOF > /etc/systemd/system/sonarr.service
[Unit]
Description=Sonarr Daemon
After=network.target

[Service]
User=$CURRENT_USER
Group=$CURRENT_USER
Type=simple
ExecStart=/usr/bin/mono --debug $INSTALL_DIR/Sonarr.exe -nobrowser -data=$CONFIG_DIR
TimeoutStopSec=20
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF"

# --- 6️⃣ Reload, enable, and start the service ---
echo "🚦 Enabling and starting Sonarr..."
sudo systemctl daemon-reload
sudo systemctl enable sonarr
sudo systemctl start sonarr

# --- 7️⃣ Completion message ---
echo ""
echo "✅ Sonarr installation completed!"
echo "📍 Access Sonarr at: http://<your-server-ip>:8989"
echo "📂 Config directory: $CONFIG_DIR"
echo "⚙️ Service file: /etc/systemd/system/sonarr.service"
echo ""
sudo systemctl status sonarr --no-pager
