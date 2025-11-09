#!/bin/bash

# Radarr Installation Script
# This script installs Radarr and sets it up as a systemd service

set -e  # Exit on error

# Check if running as root
if [[ $EUID -ne 0 ]]; then
   echo "This script must be run as root (use sudo)"
   exit 1
fi

# Configuration
RADARR_USER="$SUDO_USER"  # Use the user who ran sudo
RADARR_DIR="/opt/Radarr"
RADARR_DATA_DIR="$HOME/.config/Radarr"
RADARR_VERSION="latest"

echo "=== Radarr Installation Script ==="
echo "Installing Radarr to: $RADARR_DIR"
echo "Data directory: $RADARR_DATA_DIR"
echo "Running as user: $RADARR_USER"

# Update system packages
echo "Updating system packages..."
apt-get update

# Install required dependencies
echo "Installing dependencies..."
apt-get install -y curl sqlite3 libmediainfo0v5

# Create directories
echo "Creating directories..."
mkdir -p "$RADARR_DIR"
mkdir -p "$RADARR_DATA_DIR"

# Download and extract Radarr
echo "Downloading Radarr..."
ARCH=$(dpkg --print-architecture)
RADARR_URL="https://radarr.servarr.com/v1/update/master/updatefile?os=linux&runtime=netcore&arch=$ARCH"

wget --content-disposition "$RADARR_URL" -O /tmp/Radarr.tar.gz

echo "Extracting Radarr..."
tar -xzf /tmp/Radarr.tar.gz -C "$RADARR_DIR" --strip-components=1
rm /tmp/Radarr.tar.gz

# Set permissions
echo "Setting permissions..."
chown -R "$RADARR_USER":"$RADARR_USER" "$RADARR_DIR"
chown -R "$RADARR_USER":"$RADARR_USER" "$RADARR_DATA_DIR"
chmod +x "$RADARR_DIR/Radarr"

# Create systemd service file
echo "Creating systemd service..."
cat > /etc/systemd/system/radarr.service << EOF
[Unit]
Description=Radarr Daemon
After=network.target

[Service]
User=$RADARR_USER
Group=$RADARR_USER
Type=simple
ExecStart=$RADARR_DIR/Radarr -nobrowser -data=$RADARR_DATA_DIR
TimeoutStopSec=20
KillMode=process
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF

# Reload systemd daemon
echo "Reloading systemd..."
systemctl daemon-reload

# Enable and start Radarr service
echo "Enabling and starting Radarr service..."
systemctl enable radarr
systemctl start radarr

# Wait a moment for the service to start
sleep 3

# Check service status
if systemctl is-active --quiet radarr; then
    echo ""
    echo "=== Installation Complete ==="
    echo "Radarr has been installed and started successfully!"
    echo ""
    echo "Access Radarr at: http://localhost:7878"
    echo ""
    echo "Useful commands:"
    echo "  - Check status: sudo systemctl status radarr"
    echo "  - Stop service: sudo systemctl stop radarr"
    echo "  - Start service: sudo systemctl start radarr"
    echo "  - Restart service: sudo systemctl restart radarr"
    echo "  - View logs: sudo journalctl -u radarr -f"
else
    echo ""
    echo "=== Installation Warning ==="
    echo "Radarr service failed to start. Check logs with:"
    echo "sudo journalctl -u radarr -xe"
    exit 1
fi