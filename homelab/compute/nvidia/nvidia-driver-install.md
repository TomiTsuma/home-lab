Here’s a complete markdown file containing **Steps 1–3** for installing and verifying NVIDIA drivers on Ubuntu.

---

### 📝 `install-nvidia-driver.md`

````markdown
# 🧠 NVIDIA Driver Installation Guide (Ubuntu)

This guide explains how to properly install NVIDIA drivers on **Ubuntu 22.04+**, whether on bare metal or inside a virtual machine that has access to a physical GPU.

---

## 🧭 Step 1: Identify Your Setup

Before installation, confirm where you're running these commands:

| Scenario | Description |
|-----------|--------------|
| 🖥️ **Bare Metal** | Running Ubuntu directly on your physical machine |
| 🧱 **Virtual Machine (Proxmox)** | Ubuntu VM with GPU passthrough enabled |
| 🐳 **Docker** | Container running inside a host with GPU support |

> If you’re using Proxmox, ensure GPU passthrough is working before continuing.

---

## 🧩 Step 2: Check Driver Installation

Run these commands to check if your GPU and drivers are visible:

```bash
# Check that your NVIDIA GPU is detected at hardware level
lspci | grep -i nvidia

# Check installed NVIDIA packages (if any)
dpkg -l | grep nvidia
````

* If **no GPU** appears in `lspci`, your system or VM cannot see the GPU.
* If **no driver packages** appear, proceed to the installation step below.

---

## ⚙️ Step 3: Install or Reinstall NVIDIA Drivers

### 1. Clean Up Any Existing NVIDIA Installations

```bash
sudo apt-get purge nvidia*
sudo apt-get autoremove
sudo apt-get autoclean
```

### 2. Add the Official Graphics Drivers PPA

```bash
sudo add-apt-repository ppa:graphics-drivers/ppa
sudo apt update
```

### 3. Automatically Install the Recommended Driver

```bash
ubuntu-drivers devices
sudo ubuntu-drivers autoinstall
```

### 4. Reboot

```bash
sudo reboot
```

### 5. Test Installation

After reboot, verify your GPU and driver installation:

```bash
nvidia-smi
```

✅ If you see a table with GPU details, memory usage, and driver version, your installation is successful.

---

## 🧾 Notes

* You can check the kernel module status with:

  ```bash
  lsmod | grep nvidia
  ```
* If the driver is not loaded, try:

  ```bash
  sudo modprobe nvidia
  ```

---

## 🐳 Docker + NVIDIA Container Toolkit

Required for any `docker run --gpus all` or a compose file's
`deploy.resources.reservations.devices` GPU block to actually work.

```bash
# Add the repo and install (Ubuntu/Debian)
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit

# Register the "nvidia" runtime with Docker and restart it
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker

# Verify
docker run --rm --gpus all nvidia/cuda:12.6.0-base-ubuntu24.04 nvidia-smi
```

### ⚠️ Critical gotcha: Docker's cgroup driver

If `docker info | grep -i 'Cgroup Driver'` reports **`systemd`** (the Ubuntu
default under cgroups v2), any `systemctl daemon-reload` on the host — even
one unrelated to Docker or GPUs — silently **revokes already-running
containers' access to `/dev/nvidia*`**. The container keeps running, but
`nvidia-smi` inside it starts failing with
`Failed to initialize NVML: Unknown Error`, and whatever app was using the
GPU silently falls back to CPU. This is the single most time-consuming false
lead encountered while debugging an Ollama GPU passthrough issue on this
homelab's `aeacus-dl` host — it mimicked a VFIO/hardware problem perfectly.

Fix: switch Docker to the `cgroupfs` driver.

```bash
sudo tee /etc/docker/daemon.json >/dev/null <<'EOF'
{
    "exec-opts": ["native.cgroupdriver=cgroupfs"],
    "runtimes": { "nvidia": { "args": [], "path": "nvidia-container-runtime" } }
}
EOF
sudo systemctl restart docker
```

Verify it survives a `daemon-reload` (the thing that broke it before):

```bash
docker exec <container> nvidia-smi   # should work
sudo systemctl daemon-reload
docker exec <container> nvidia-smi   # should STILL work
```

See `../../ai/open-webui/host-gpu-prep.sh` for a script that applies this
fix (plus NVIDIA persistence mode, see `gpu-passthrough.md`).

---
