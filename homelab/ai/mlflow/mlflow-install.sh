#!/usr/bin/env bash

set -e

CURRENT_USER=$(whoami)
MLFLOW_DIR="/opt/mlflow"

echo "=== Creating MLflow directories under /opt/mlflow ==="
sudo mkdir -p $MLFLOW_DIR/artifacts
sudo mkdir -p $MLFLOW_DIR/venv
sudo mkdir -p $MLFLOW_DIR/logs
sudo mkdir -p $MLFLOW_DIR/db

sudo chown -R $CURRENT_USER:$CURRENT_USER $MLFLOW_DIR

echo "=== Creating Python virtual environment ==="
python3.11 -m pip install mlflow

echo "=== Creating systemd service file ==="
sudo tee /etc/systemd/system/mlflow.service > /dev/null << EOF
[Unit]
Description=MLflow Tracking Server
After=network.target

[Service]
User=$CURRENT_USER
Group=$CURRENT_USER
WorkingDirectory=$MLFLOW_DIR
Environment="PATH=$MLFLOW_DIR/venv/bin"
ExecStart=mlflow server \
    --backend-store-uri sqlite:///$MLFLOW_DIR/db/mlflow.db \
    --default-artifact-root $MLFLOW_DIR/artifacts \
    --host 0.0.0.0 \
    --port 5000

Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

echo "=== Reloading systemd ==="
sudo systemctl daemon-reload

echo "=== Enabling MLflow service ==="
sudo systemctl enable mlflow.service

echo "=== Starting MLflow service ==="
sudo systemctl start mlflow.service

echo "=== MLflow Installed Successfully! ==="
echo "Access MLflow at: http://192.168.1.108:5000"
echo "Logs: sudo journalctl -u mlflow -f"
