#!/bin/bash

SERVICE_NAME="download-mover.service"
SCRIPT_PATH="/home/minos/download_mover.sh"
LOG_PATH="/home/minos/download_mover.log"
SERVICE_PATH="/etc/systemd/system/$SERVICE_NAME"

echo ">>> Creating download_mover.sh script..."

cat << 'EOF' > $SCRIPT_PATH
#!/bin/bash

SOURCE="/home/minos/Downloads"
DEST="/mnt/nas/data-store/Downloads"
INTERVAL_MINUTES=30

mkdir -p "$DEST"

echo "Starting Downloads mover service..."
echo "Monitoring $SOURCE every $INTERVAL_MINUTES minutes"
echo "Moving to $DEST"

while true; do
    if [ "$(ls -A "$SOURCE")" ]; then
        echo "$(date): Content found. Moving files..."
        mv "$SOURCE"/* "$DEST"/ 2>/dev/null
        echo "$(date): Move completed."
    else
        echo "$(date): No content found."
    fi

    sleep $((INTERVAL_MINUTES * 60))
done
EOF

echo ">>> Making script executable..."
chmod +x $SCRIPT_PATH


echo ">>> Creating systemd service file..."

sudo bash -c "cat << 'EOF' > $SERVICE_PATH
[Unit]
Description=Downloads Folder Auto-Mover Service
After=network.target

[Service]
Type=simple
ExecStart=$SCRIPT_PATH
Restart=always
RestartSec=10
User=minos
Group=minos
StandardOutput=append:$LOG_PATH
StandardError=append:$LOG_PATH

[Install]
WantedBy=multi-user.target
EOF"


echo ">>> Reloading systemd..."
sudo systemctl daemon-reload

echo ">>> Enabling service on startup..."
sudo systemctl enable $SERVICE_NAME

echo ">>> Starting service now..."
sudo systemctl start $SERVICE_NAME

echo ">>> Done!"
echo "Service status:"
sudo systemctl status $SERVICE_NAME --no-pager
