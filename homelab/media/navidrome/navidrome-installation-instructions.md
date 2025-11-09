Here’s how you can **install Navidrome without Docker** — i.e., natively on your Linux system (Ubuntu/Debian example).

---

## 🧠 What is Navidrome?

Navidrome is a lightweight self-hosted music streaming server compatible with Subsonic/Airsonic clients.
It runs as a single binary — no database setup or complex dependencies required.

---

## 🧰 Prerequisites

Before installation, make sure you have:

```bash
sudo apt update
sudo apt install -y curl unzip
```

You’ll also need:

* A music directory (e.g. `/mnt/music`)
* A non-root user to run Navidrome securely

---

## ⚙️ Step-by-Step Installation

### 1. Create a user for Navidrome

```bash
sudo useradd -r -s /bin/false navidrome
```

### 2. Create directories

```bash
sudo mkdir -p /opt/navidrome /var/lib/navidrome /mnt/music
sudo chown -R navidrome:navidrome /opt/navidrome /var/lib/navidrome /mnt/music
```

### 3. Download Navidrome binary

Visit [https://github.com/navidrome/navidrome/releases](https://github.com/navidrome/navidrome/releases)
or use this one-liner to get the latest version automatically:

```bash
cd /opt/navidrome
NAVIDROME_VERSION=$(curl -s https://api.github.com/repos/navidrome/navidrome/releases/latest | grep tag_name | cut -d '"' -f4)
sudo curl -L -o navidrome.zip "https://github.com/navidrome/navidrome/releases/download/${NAVIDROME_VERSION}/navidrome_${NAVIDROME_VERSION}_Linux_x86_64.zip"
sudo unzip navidrome.zip
sudo rm navidrome.zip
sudo chown -R navidrome:navidrome /opt/navidrome
```

---

## ⚙️ Step 4: Configure Navidrome

Create a configuration file:

```bash
sudo nano /etc/navidrome.env
```

Add:

```bash
ND_MUSICFOLDER=/mnt/music
ND_DATAFOLDER=/var/lib/navidrome
ND_ADDRESS=0.0.0.0
ND_PORT=4533
ND_LOGLEVEL=info
```

---

## 🧩 Step 5: Create a systemd service

```bash
sudo tee /etc/systemd/system/navidrome.service > /dev/null << 'EOF'
[Unit]
Description=Navidrome Music Server
After=network.target

[Service]
User=navidrome
Group=navidrome
EnvironmentFile=/etc/navidrome.env
ExecStart=/opt/navidrome/navidrome --configfile /etc/navidrome.env
Restart=on-failure
TimeoutStopSec=5

[Install]
WantedBy=multi-user.target
EOF
```

---

## 🚀 Step 6: Enable and start Navidrome

```bash
sudo systemctl daemon-reload
sudo systemctl enable navidrome
sudo systemctl start navidrome
```

---

## ✅ Step 7: Verify installation

Check if it’s running:

```bash
sudo systemctl status navidrome
```

Then visit:

```
http://192.168.1.113:4533
```
