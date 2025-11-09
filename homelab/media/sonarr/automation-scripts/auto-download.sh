#!/bin/zsh
# Sonarr auto-download script


#!/bin/bash
# =======================================================
# Sonarr Installation Script for Ubuntu
# Works on Ubuntu 20.04, 22.04, and newer
# Author: Thomas Tsuma & GPT-5
# =======================================================

set -e

echo "🔄 Updating system packages..."
sudo apt update && sudo apt upgrade -y

echo "📦 Installing dependencies..."
sudo apt install -y curl gnupg apt-transport-https dirmngr ca-certificates software-properties-common

echo "🔑 Adding Mono repository (required by Sonarr)..."
sudo apt-key adv --keyserver hkp://keyserver.ubuntu.com:80 --recv-keys 3FA7E0328081BFF6A14DA29AA6A19B38D3D831EF
echo "deb https://download.mono-project.com/repo/ubuntu stable-$(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/mono-official-stable.list

echo "📦 Installing Mono..."
sudo apt update
sudo apt install -y mono-devel

echo "📥 Downloading and installing Sonarr..."
sudo apt install -y curl sqlite3
sudo mkdir -p /opt/sonarr
sudo chown -R $USER:$USER /opt/sonarr
curl -L -o /tmp/sonarr.tar.gz https://services.sonarr.tv/v1/download/main/latest?version=3&os=linux
tar -xvzf /tmp/sonarr.tar.gz -C /opt/sonarr --strip-components=1
rm /tmp/sonarr.tar.gz

echo "⚙️ Creating Sonarr systemd service..."
sudo tee /etc/systemd/system/sonarr.service > /dev/null << 'EOF'
[Unit]
Description=Sonarr Daemon
After=network.target

[Service]
User=sonarr
Group=sonarr
Type=simple
ExecStart=/usr/bin/mono --debug /opt/sonarr/Sonarr.exe -nobrowser
TimeoutStopSec=20
KillMode=process
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF

echo "👤 Creating Sonarr user and setting permissions..."
sudo useradd -r -s /bin/false sonarr || true
sudo chown -R sonarr:sonarr /opt/sonarr

echo "🚀 Enabling and starting Sonarr service..."
sudo systemctl daemon-reload
sudo systemctl enable sonarr
sudo systemctl start sonarr

echo "✅ Sonarr installation complete!"
echo "----------------------------------------------------"
echo "🌐 Access Sonarr at: http://localhost:8989"
echo "----------------------------------------------------"
echo "💡 Default service user: sonarr"
echo "   Media directories (adjust permissions as needed):"
echo "   sudo chown -R sonarr:sonarr /path/to/media"
echo "----------------------------------------------------"
