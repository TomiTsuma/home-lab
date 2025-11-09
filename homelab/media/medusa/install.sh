#!/usr/bin/env bash
set -e

echo "📦 Installing Medusa TV Show Manager..."

# Detect current user and home
USER_NAME=$(whoami)
HOME_DIR=$(eval echo "~$USER_NAME")

echo "🧰 Updating system..."
sudo apt update && sudo apt install -y ffmpeg libmediainfo0v5

echo "📥 Cloning Medusa repository..."
cd "$HOME_DIR"
if [ ! -d "$HOME_DIR/Medusa" ]; then
    git clone https://github.com/pymedusa/Medusa.git
else
    echo "⚠️ Medusa folder already exists — skipping clone."
fi

cd "$HOME_DIR/Medusa"

echo "🐍 Setting up Python virtual environment..."
python3.11 -m venv venv
source venv/bin/activate
python3.11 -m pip install --upgrade pip
python3.11 -m pip install -r requirements.txt

echo "🧩 Creating systemd service..."
SERVICE_FILE="/etc/systemd/system/medusa@${USER_NAME}.service"
sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=Medusa TV Series Manager
After=network.target

[Service]
Type=simple
User=${USER_NAME}
WorkingDirectory=${HOME_DIR}/Medusa
ExecStart=${HOME_DIR}/Medusa/venv/bin/python3.11 Medusa.py --nolaunch --datadir ${HOME_DIR}/.config/Medusa
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable medusa@${USER_NAME}
sudo systemctl start medusa@${USER_NAME}

echo "✅ Medusa installed and running on port 8081!"
echo "🌐 Visit: http://localhost:8081"
