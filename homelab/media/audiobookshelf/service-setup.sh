#!/bin/bash

# ---- CONFIG ----
APP_DIR="/home/minos/audiobookshelf"
USER_NAME=$(whoami)
PORT=3333
NODE_VERSION=20


# ---- 6️⃣ Create a systemd service ----
echo "🧩 Creating systemd service..."
sudo tee /etc/systemd/system/audiobookshelf.service > /dev/null <<EOF
[Unit]
Description=Audiobookshelf Server
After=network.target

[Service]
User=${USER_NAME}
WorkingDirectory=${APP_DIR}
ExecStart=/usr/bin/npm start
Restart=always
Environment=NODE_ENV=production
Environment=PORT=${PORT}

[Install]
WantedBy=multi-user.target
EOF

# ---- 7️⃣ Enable and start the service ----
echo "🚀 Starting Audiobookshelf service..."
sudo systemctl daemon-reload
sudo systemctl enable audiobookshelf
sudo systemctl start audiobookshelf