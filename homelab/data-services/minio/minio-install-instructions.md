Here’s a ready-to-save **Markdown file** (`setup_minio_cluster.md`) with clear, step-by-step instructions for setting up a **2-node MinIO cluster** on Linux servers.

---

````markdown
# 🧊 Setting Up a Two-Node MinIO Cluster

This guide walks you through setting up a **2-node distributed MinIO cluster** for high availability and redundancy.

---

## 🧱 Prerequisites

### Hardware / System Requirements
- Two Linux servers (e.g., Ubuntu 20.04 or later)
- Each with:
  - At least 2 CPU cores
  - 4GB+ RAM
  - Storage disks for data (e.g., `/mnt/data`)
- Both servers must:
  - Have MinIO installed
  - Be reachable via hostname or IP over the network
  - Have the same access/secret keys configured

---

## ⚙️ Step 1: Install MinIO on Both Nodes

```bash
wget https://dl.min.io/server/minio/release/linux-amd64/minio
chmod +x minio
sudo mv minio /usr/local/bin/
````

Verify installation:

```bash
minio --version
```

---

## 🗝️ Step 2: Set Environment Variables

Create a MinIO environment file `/etc/default/minio` on **both nodes**:

```bash
sudo nano /etc/default/minio
```

Add the following (adjust the IPs accordingly):

```bash
# MINIO configuration
MINIO_ROOT_USER=minioadmin
MINIO_ROOT_PASSWORD=minioadmin123

# Data directories for this node
MINIO_VOLUMES="http://node1.local/mnt/data http://node2.local/mnt/data"

# Network binding address
MINIO_OPTS="--console-address :9001"
```

> Replace `node1.local` and `node2.local` with each server’s hostname or IP.

---

## 📦 Step 3: Create Data Directories

On both nodes:

```bash
sudo mkdir -p /mnt/data
sudo chown -R $USER:$USER /mnt/data
```

---

## 🚀 Step 4: Start MinIO Cluster

Run this command on **both nodes** simultaneously:

```bash
minio server http://node1.local/mnt/data http://node2.local/mnt/data --console-address ":9001"
```

MinIO will automatically form a **2-node distributed cluster**.

> Each node must be able to reach the other over the network on the ports MinIO uses (default: `9000` for API, `9001` for console).

---

## 🔄 Step 5: Access the Console

Open a browser and visit:

```
http://node1.local:9001
```

Login using the credentials:

```
Username: minioadmin
Password: minioadmin123
```

You should now see a **distributed MinIO cluster** with two nodes.

---

## 🔧 Step 6: (Optional) Create a Systemd Service

Create `/etc/systemd/system/minio.service`:

```ini
[Unit]
Description=MinIO
After=network.target

[Service]
User=minio-user
Group=minio-user
EnvironmentFile=/etc/default/minio
ExecStart=/usr/local/bin/minio server $MINIO_VOLUMES $MINIO_OPTS
Restart=always
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
```

Then enable and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable minio
sudo systemctl start minio
sudo systemctl status minio
```

---

## 🧩 Step 7: Verify Cluster Health

Run:

```bash
mc alias set myminio http://node1.local:9000 minioadmin minioadmin123
mc admin info myminio
```

You should see both nodes listed and in healthy state.

---

## ⚡ Notes

* Ensure both nodes have synchronized clocks (use `ntpd` or `chrony`).
* Avoid using `localhost` — use resolvable hostnames or IPs.
* Minimum of **4 disks or 4 nodes** is recommended for true fault tolerance.
* Always **back up** your MinIO configuration and data before upgrades.

---

