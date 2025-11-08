# 🧰 How to Resize Disk Space for a Proxmox VM Using LVM (Ubuntu)

This guide walks you through **increasing the disk size** of a Proxmox virtual machine that uses **LVM** (Logical Volume Manager) for storage.  
It covers both the **Proxmox side** (hypervisor) and the **Ubuntu VM side**.

---

## 📍 Overview

Most Ubuntu Proxmox templates use LVM by default, where your root filesystem `/` is mounted on:

```
/dev/mapper/ubuntu--vg-ubuntu--lv
```

- `ubuntu-vg` → Volume Group  
- `ubuntu-lv` → Logical Volume  
- `/dev/mapper/` → Device mapper directory for virtual block devices  

---

## ⚙️ Step 1: Increase Disk Size from Proxmox

1. **Shut down your VM**  
   From the Proxmox web UI:
   ```
   Shutdown → Confirm
   ```

2. **Select the VM → Hardware → Hard Disk**  
   Click on the disk (e.g., `scsi0` or `sata0`).

3. **Click “Resize Disk”**  
   - Add the desired amount (e.g., `+20G`)
   - Click **Resize Disk**

4. **Start the VM again**  
   ```
   Start → Console / SSH
   ```

---

## 🖥️ Step 2: Verify the New Disk Size Inside the VM

SSH into the VM or open the Proxmox console and run:

```bash
sudo lsblk
```

You should see something like this:

```
sda       8:0    0   80G  0 disk
├─sda1    8:1    0  512M  0 part /boot/efi
├─sda2    8:2    0    2G  0 part /boot
└─sda3    8:3    0   78G  0 part
  ├─ubuntu--vg-ubuntu--lv 253:0 0   48G  0 lvm  /
```

> Notice that `/dev/sda` is now larger (80G in this example), but `/dev/sda3` and the logical volume are still smaller.

---

## 🧱 Step 3: Resize the Partition

You need to extend the partition that LVM uses (e.g., `/dev/sda3`).

1. Start the partition tool:
   ```bash
   sudo fdisk /dev/sda
   ```

2. Delete and recreate the LVM partition:
   - Press `p` to print the current partition table.
   - Note the **start sector** of `/dev/sda3`.
   - Press `d` → Choose partition number `3`.
   - Press `n` → Choose `primary` → Use the same **start sector**.
   - Press `w` to write changes and exit.

3. Reboot the VM:
   ```bash
   sudo reboot
   ```

---

## 📦 Step 4: Resize the Physical Volume (PV)

After rebooting, tell LVM that the physical volume is larger:

```bash
sudo pvresize /dev/sda3
```

---

## 🧩 Step 5: Extend the Logical Volume (LV)

Extend the root logical volume to use all available free space:

```bash
sudo lvextend -l +100%FREE /dev/mapper/ubuntu--vg-ubuntu--lv
```

To verify:

```bash
sudo lvs
```

---

## 🧾 Step 6: Resize the Filesystem

Finally, expand the filesystem to fill the logical volume:

```bash
sudo resize2fs /dev/mapper/ubuntu--vg-ubuntu--lv
```

> For systems using `xfs`, use:
> ```bash
> sudo xfs_growfs /
> ```

---

## ✅ Step 7: Verify the New Disk Usage

Check that the root partition now reflects the new size:

```bash
df -h
```

Example output:

```
Filesystem                         Size  Used Avail Use% Mounted on
/dev/mapper/ubuntu--vg-ubuntu--lv   78G   45G   33G  58% /
```

---

## 🧠 Summary of Key Commands

| Action | Command |
|--------|----------|
| Check disk info | `lsblk` or `fdisk -l` |
| Resize PV | `pvresize /dev/sda3` |
| Extend LV | `lvextend -l +100%FREE /dev/mapper/ubuntu--vg-ubuntu--lv` |
| Resize filesystem | `resize2fs /dev/mapper/ubuntu--vg-ubuntu--lv` |
| Check space | `df -h` |

---

## ⚠️ Notes

- Always **back up** important data before resizing partitions.
- LVM names (`ubuntu--vg`, `ubuntu--lv`, `/dev/sda3`) may differ slightly based on your setup.
- You can view LVM structure using:
  ```bash
  sudo pvs
  sudo vgs
  sudo lvs
  ```

---

### 🏁 Done!
Your Proxmox VM should now have additional disk space available for use on the root filesystem (`/`).
