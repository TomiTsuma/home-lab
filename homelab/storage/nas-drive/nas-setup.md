
---

# **NAS Installation & Configuration Guide**

*A complete reference for installing, configuring, and mounting a TrueNAS server on a home lab or Proxmox VM.*

---

# **1. Installing TrueNAS (Core/Scale)**

### **1.1. Install TrueNAS on Bare Metal**

1. Download ISO from TrueNAS website.
2. Flash ISO to USB using Rufus/BalenaEtcher.
3. Boot the NAS machine from USB.
4. Select **Install/Upgrade**.
5. Choose installation disk (⚠️ wipes disk).
6. Create root password.
7. Reboot and remove USB.

### **1.2. Install TrueNAS on Proxmox**

1. Create new VM in Proxmox.
2. Set:

   * **System → BIOS:** UEFI
   * **Machine Type:** q35
   * **SCSI Controller:** VirtIO-SCSI Single
3. Add disks using **VirtIO Block** or **SCSI**.
4. Boot from TrueNAS ISO.
5. Install normally.

### **1.3. First Boot Web UI Access**

Once installed:

* Navigate to: `http://<truenas-ip>`
* Login using:

  * **Username:** root
  * **Password:** (set during install)

---

# **2. Configuring TrueNAS for the First Time**

### **2.1. Create Storage Pool**

1. Go to **Storage → Pools → Add**.
2. Choose:

   * **Create new pool**
   * Select disks and choose RAID level (Mirror, RAIDZ1, etc.)
3. Click **Create**.

### **2.2. Create Dataset**

1. Storage → Pools → *YourPool* → **Add Dataset**
2. Set:

   * Name: `data` (or anything)
   * Case sensitivity: `Sensitive`
   * Share type: **SMB**

### **2.3. Create a User**

1. Accounts → Users → Add.
2. Set:

   * Username (e.g., `aeacus`)
   * Password
   * Shell: nologin (optional)
3. Check:

   * **Permit Sudo (optional)**
   * **Create Home Directory**

---

# **3. Enable & Configure SMB (Windows File Sharing)**

### **3.1. Create SMB Share**

1. Sharing → Windows Shares (SMB) → **Add**
2. Select your dataset path:

   ```
   /mnt/<pool>/<dataset>
   ```
3. Set:

   * Name: e.g., `data`
   * Purpose: **Default SMB Share**
4. Save and enable SMB service.

### **3.2. Check Share Name**

This name is what you mount in Linux (important):

Example:

```
Sharename: data
```

---

# **4. Testing SMB Share from Ubuntu**

### **4.1. List Shares**

```bash
smbclient -L <truenas-ip> -U <username>
```

You should see something like:

```
Sharename       Type
---------       ----
data            Disk
```

---

# **5. Mounting TrueNAS Dataset on Ubuntu**

### **5.1. Install SMB Tools**

```bash
sudo apt update
sudo apt install cifs-utils
```

### **5.2. Create Mount Point**

```bash
sudo mkdir -p /mnt/nas
```

### **5.3. Mount Command**

Use the **SMB share name**, NOT the dataset name:

```bash
sudo mount -t cifs //<truenas-ip>/<sharename> /mnt/nas \
-o username=<username>,vers=3.0
```

Example:

```bash
sudo mount -t cifs //192.168.1.168/storage /mnt/nas -o username=aeacus,vers=3.0
```

---

# **6. Fixing Common SMB Mount Errors**

### ❌ **Error: No such file or directory**

You used a **dataset name** instead of the **SMB share name**.

### ❌ **Error: Permission denied**

Fix permissions:

* Storage → Pool → Dataset → Edit Permissions
* Set ACL:

  * **User:** <your username>
  * **Group:** users
  * Apply recursively

### ❌ **Error: could not connect, unable to find suitable address**

SMB service is not enabled:

TrueNAS → Services → SMB → Enable → Start

---

# **7. Auto-Mount at Boot (Optional)**

### **7.1. Store Credentials**

Create file:

```bash
sudo nano /etc/smb-creds
```

Add:

```
username=aeacus
password=YOUR_PASSWORD
```

Secure it:

```bash
sudo chmod 600 /etc/smb-creds
```

### **7.2. Edit /etc/fstab**

```bash
sudo nano /etc/fstab
```

Add:

```
# TrueNAS mount
//192.168.1.168/data /mnt/nas cifs credentials=/etc/smb-creds,vers=3.0 0 0
```

Mount all:

```bash
sudo mount -a
```

---

# **8. Useful Commands**

### **8.1. Check TrueNAS IP**

```bash
ip addr
```

### **8.2. Restart SMB service**

In TrueNAS:

```
Services → SMB → Restart
```

### **8.3. Troubleshoot mount**

```bash
dmesg | tail
```

---

# **9. Troubleshooting Summary**

| Error                     | Cause                 | Fix                             |
| ------------------------- | --------------------- | ------------------------------- |
| No such file or directory | Wrong SMB share name  | Use the name from SMB → Sharing |
| Permission denied         | Wrong ACL permissions | Edit dataset permissions        |
| Could not connect         | SMB service off       | Enable SMB in TrueNAS           |
| mount OK but empty        | Wrong dataset mapped  | Fix share path                  |

---