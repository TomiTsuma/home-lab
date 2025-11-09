#!/bin/bash
# ===========================================
# Sonarr Native Installation Script
# Works on: Ubuntu 20.04+, Debian 11+
# ===========================================

set -e

echo "==========================================="
echo "🚀 Installing Sonarr (Native installation)"
echo "==========================================="

# --- Step 1: Update system packages ---
echo "[1/6] Updating system packages..."
sudo apt update -y && sudo apt upgrade -y

# --- Step 2: Install required dependencies ---
echo "[2/6] Installing dependencies..."
sudo apt install -y curl mediainfo sqlite3 libchromaprint-tools apt-transport-https dirmngr gnupg ca-certificates

# --- Step 3: Add the Sonarr GPG key and repository ---
echo "[3/6] Adding Sonarr repository..."
sudo mkdir -p /etc/apt/keyrings
curl -fsSL https://apt.sonarr.tv/sonarr.key | gpg --dearmor | sudo tee /etc/apt/keyrings/sonarr.gpg > /dev/null

echo "deb [signed-by=/etc/apt/keyrings/sonarr.gpg] https://apt.sonarr.tv/ubuntu focal main" | sudo tee /etc/apt/sources.list.d/sonarr.list

# --- Step 4: Install Sonarr ---
echo "[4/6] Installing Sonarr..."
sudo apt update -y
sudo apt install -y sonarr

# --- Step 5: Enable and start Sonarr service ---
echo "[5/6] Enabling and starting Sonarr service..."
sudo systemctl enable sonarr
sudo systemctl start sonarr

# --- Step 6: Display info ---
echo "[6/6] Installation complete!"
echo "==========================================="
echo "✅ Sonarr has been installed successfully."
echo "-------------------------------------------"
echo "🔹 To access Sonarr, open your browser and go to:"
echo "     http://localhost:8989"
echo "     or http://<your-server-ip>:8989"
echo "-------------------------------------------"
echo "📁 Default config location: /var/lib/sonarr"
echo "⚙️  Systemd service: sonarr.service"
echo "-------------------------------------------"
echo "💡 Tip: To view logs, run:"
echo "     sudo journalctl -u sonarr -f"
echo "==========================================="
