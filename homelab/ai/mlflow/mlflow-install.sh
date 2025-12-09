#!/usr/bin/env bash

set -e

CURRENT_USER=$(whoami)
MLFLOW_DIR="/opt/mlflow"

echo "=== Creating MLflow directories under /opt/mlflow ==="
sudo mkdir -p $MLFLOW_DIR/artifacts
sudo mkdir -p $MLFLOW_DIR/logs
sudo mkdir -p $MLFLOW_DIR/db

sudo chown -R $CURRENT_USER:$CURRENT_USER $MLFLOW_DIR


echo "=== Installing MLflow globally using Python 3.11 ==="
# Use python3.11 explicitly
# python3.11 -m pip install --upgrade pip
python3.11 -m pip install --ignore-installed blinker mlflow


echo "=== Finding absolute paths ==="
PYTHON_BIN=$(which python3.11)
MLFLOW_BIN=$(python3.11 -c "import shutil; print(shutil.which('mlflow'))")


echo "=== Creating MLflow systemd service ==="
sudo tee /etc/systemd/system/mlflow.service > /dev/null << EOF
[Unit]
Description=MLflow Tracking Server (Python 3.11)
After=network.target

[Service]
User=$CURRENT_USER
Group=$CURRENT_USER
WorkingDirectory=$MLFLOW_DIR

Environment="PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/bin"

ExecStart=$PYTHON_BIN -m mlflow server \
    --backend-store-uri sqlite:///$MLFLOW_DIR/db/mlflow.db \
    --default-artifact-root file://$MLFLOW_DIR/artifacts \
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
echo "Access MLflow at: http://YOUR-SERVER-IP:5000"
echo "Follow logs with: sudo journalctl -u mlflow -f"
