#!/usr/bin/env bash
set -e

echo "🚀 Starting Sonarr installation (GitHub method)..."

# Detect current user
CURRENT_USER=$(logname 2>/dev/null || echo $USER)
CURRENT_GROUP=$(id -gn "$CURRENT_USER")

echo "👤 Installing for user: $CURRENsT_USER"

# Install dependencies
echo "📦 Installing dependencies..."
sudo apt update -y
sudo apt install -y curl wget tar mediainfo sqlite3 libchromaprint-tools ca-certificates

# Download latest Sonarr release
echo "🌐 Fetching latest Sonarr release from GitHub..."
LATEST_URL=$(curl -s https://api.github.com/repos/Sonarr/Sonarr/releases/latest | grep browser_download_url | grep linux-core-x64 | cut -d '"' -f 4)

if [ -z "$LATEST_URL" ]; then
    echo "❌ Failed to get latest Sonarr release from GitHub. Check your internet connection."
    exit 1
fi

echo "📥 Downloading Sonarr from: $LATEST_URL"
wget -O /tmp/sonarr.tar.gz "$LATEST_URL"

# Extract to /opt/sonarr
echo "📂 Installing Sonarr to /opt/sonarr..."
sudo mkdir -p /opt/sonarr
sudo tar -xvzf /tmp/sonarr.tar.gz -C /opt/sonarr --strip-components=1
sudo rm /tmp/sonarr.tar.gz

# Set permissions
sudo chown -R "$CURRENT_USER":"$CURRENT_GROUP" /opt/sonarr

# Create systemd service file
echo "⚙️ Creating systemd service..."
SERVICE_FILE="/etc/systemd/system/sonarr.service"

sudo bash -c "cat > $SERVICE_FILE" <<EOL
[Unit]
Description=Sonarr Daemon
After=network.target

[Service]
User=$CURRENT_USER
Group=$CURRENT_GROUP
Type=simple
ExecStart=/opt/sonarr/Sonarr -nobrowser
Restart=on-failure
TimeoutStopSec=20
SyslogIdentifier=sonarr

[Install]
WantedBy=multi-user.target
EOL

# Enable and start Sonarr
echo "🔄 Enabling and starting Sonarr..."
sudo systemctl daemon-reload
sudo systemctl enable sonarr
sudo systemctl restart sonarr

# Confirm status
echo "✅ Sonarr installation complete!"
echo "🌍 Access Sonarr at: http://localhost:8989 or http://<your-server-ip>:8989"
sudo systemctl --no-pager status sonarr | grep Active
