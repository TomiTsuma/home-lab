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
* For Docker environments, you’ll need the NVIDIA Container Toolkit (not covered in this file).

---
