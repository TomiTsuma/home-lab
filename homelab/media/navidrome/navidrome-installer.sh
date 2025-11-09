#!/bin/bash
# ================================================
# Navidrome Installer (non-Docker)
# Works on Ubuntu/Debian
# ================================================

set -e

echo "🚀 Installing Navidrome (non-Docker)..."

# --- Update and install dependencies ---
echo "🔧 Installing prerequisites..."
sudo apt update -y
sudo apt install -y curl unzip

# --- Create user and directories ---
echo "👤 Creating navidrome user and directories..."
sudo useradd -r -s /bin/false navidrome || true
sudo mkdir -p /opt/navidrome /var/lib/navidrome /mnt/music
sudo chown -R navidrome:navidrome /opt/navidrome /var/lib/navidrome /mnt/music

# --- Download latest Navidrome release ---
echo "⬇️  Downloading latest Navidrome release..."
cd /opt/navidrome
NAVIDROME_VERSION=$(curl -s https://api.github.com/repos/navidrome/navidrome/releases/latest | grep tag_name | cut -d '"' -f4)
sudo curl -L -o navidrome.zip "https://github.com/navidrome/navidrome/releases/download/${NAVIDROME_VERSION}/navidrome_${NAVIDROME_VERSION}_Linux_x86_64.zip"
sudo unzip -o navidrome.zip
sudo rm navidrome.zip
sudo chown -R navidrome:navidrome /opt/navidrome

# --- Create configuration file ---
echo "⚙️  Creating configuration..."
sudo tee /etc/navidrome.env > /dev/null << 'EOF'
ND_MUSICFOLDER=/mnt/music
ND_DATAFOLDER=/var/lib/navidrome
ND_ADDRESS=0.0.0.0
ND_PORT=4533
ND_LOGLEVEL=info
EOF

# --- Create systemd service ---
echo "🧩 Setting up systemd service..."
sudo tee /etc/systemd/system/navidrome.service > /dev/null << 'EOF'
[Unit]
Description=Navidrome Music Server
After=network.target

[Service]
User=navidrome
Group=navidrome
EnvironmentFile=/etc/navidrome.env
ExecStart=/opt/navidrome/navidrome --configfile /etc/navidrome.env
Restart=on-failure
TimeoutStopSec=5

[Install]
WantedBy=multi-user.target
EOF

# --- Enable and start service ---
echo "🚀 Starting Navidrome service..."
sudo systemctl daemon-reload
sudo systemctl enable navidrome
sudo systemctl start navidrome

# --- Show status and instructions ---
echo "✅ Navidrome installation complete!"
echo ""
echo "👉 Access it at: http://$(hostname -I | awk '{print $1}'):4533"
echo "🎵 Default music folder: /mnt/music"
echo "📁 Data folder: /var/lib/navidrome"
echo ""
sudo systemctl status navidrome --no-pager
