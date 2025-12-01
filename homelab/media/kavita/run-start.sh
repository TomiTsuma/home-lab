#!/bin/bash

set -e

echo "=== Creating directories ==="
mkdir -p /opt/kavita
cd /opt/kavita

echo "=== Creating docker-compose.yml ==="
cat <<EOF > /opt/kavita/docker-compose.yml
version: "3.9"

services:
  kavita:
    image: jvmilazz0/kavita:latest
    container_name: kavita
    restart: unless-stopped
    ports:
      - "5000:5000"
    volumes:
      - ./config:/kavita/config
      - ./data:/books
    environment:
      - TZ=Africa/Nairobi
EOF

echo "=== Creating run_kavita.sh ==="
cat <<EOF > /opt/kavita/run_kavita.sh
#!/bin/bash
cd /opt/kavita
/usr/bin/docker compose pull
/usr/bin/docker compose up -d
EOF

chmod +x /opt/kavita/run_kavita.sh

echo "=== Creating systemd service ==="
cat <<EOF > /etc/systemd/system/kavita.service
[Unit]
Description=Kavita Self-Hosted Reader
After=network.target docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/opt/kavita/run_kavita.sh
WorkingDirectory=/opt/kavita
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
EOF

echo "=== Reloading systemd ==="
systemctl daemon-reload

echo "=== Enabling Kavita service at boot ==="
systemctl enable kavita

echo "=== Starting Kavita ==="
systemctl start kavita

echo "=== Done! Kavita is running on port 5000 ==="
echo "Visit: http://<your-server-ip>:5000"
