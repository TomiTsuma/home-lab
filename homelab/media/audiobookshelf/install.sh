#!/bin/bash
# ===============================================================
# Audiobookshelf Installation Script (No Docker)
# Compatible with: Ubuntu / Debian
# Author: ChatGPT (for your homelab setup)
# ===============================================================

# Stop on any error
set -e

# ---- CONFIG ----
APP_DIR="/opt/audiobookshelf"
USER_NAME=$(whoami)
PORT=3333
NODE_VERSION=20

echo "📚 Installing Audiobookshelf (no Docker) as user: $USER_NAME"

# ---- 1️⃣ Update system ----
echo "🔄 Updating system packages..."
sudo apt update && sudo apt upgrade -y

# ---- 2️⃣ Install dependencies ----
echo "⚙️ Installing dependencies (curl, git, build-essential)..."
sudo apt install -y curl git build-essential ufw

# ---- 3️⃣ Install Node.js ----
echo "🟢 Installing Node.js v$NODE_VERSION..."
curl -fsSL https://deb.nodesource.com/setup_${NODE_VERSION}.x | sudo -E bash -
sudo apt install -y nodejs

# Verify
node -v
npm -v

# ---- 4️⃣ Clone Audiobookshelf ----
if [ ! -d "$APP_DIR" ]; then
    echo "📦 Cloning Audiobookshelf into $APP_DIR..."
    sudo git clone https://github.com/advplyr/audiobookshelf.git "$APP_DIR"
else
    echo "📦 Audiobookshelf directory already exists, pulling latest changes..."
    cd "$APP_DIR"
    sudo git pull
fi

# ---- 5️⃣ Install and build ----
cd "$APP_DIR"
echo "📦 Installing npm dependencies..."
sudo npm install

echo "⚙️ Building Audiobookshelf..."
sudo npm run build


# ---- 8️⃣ Setup firewall ----
echo "🧱 Configuring firewall for port ${PORT}..."
sudo ufw allow ${PORT}/tcp
sudo ufw reload

# ---- ✅ Summary ----
echo "✅ Audiobookshelf installed successfully!"
echo "-------------------------------------------------------------"
echo "Access it at: http://$(hostname -I | awk '{print $1}'):${PORT}"
echo "Service: sudo systemctl status audiobookshelf"
echo "Logs: journalctl -u audiobookshelf -f"
echo "App directory: ${APP_DIR}"
echo "-------------------------------------------------------------"
