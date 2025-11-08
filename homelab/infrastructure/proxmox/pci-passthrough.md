# PCI Passthrough

Instructions and notes for enabling PCI passthrough in Proxmox.

## Steps to Enable PCI Passthrough

1. **Enable IOMMU in GRUB**
   - For Intel CPUs, run:
     ```sh
     sudo nano /etc/default/grub
     # Add or edit:
     GRUB_CMDLINE_LINUX_DEFAULT="quiet intel_iommu=on"
     ```
   - For AMD CPUs, run:
     ```sh
     sudo nano /etc/default/grub
     # Add or edit:
     GRUB_CMDLINE_LINUX_DEFAULT="quiet amd_iommu=on"
     ```
   - Update GRUB:
     ```sh
     sudo update-grub
     sudo reboot
     ```

2. **Verify IOMMU is enabled**
   ```sh
   dmesg | grep -e DMAR -e IOMMU
   ```

3. **Edit Proxmox kernel modules**
   ```sh
   echo 'vfio' | sudo tee -a /etc/modules
   echo 'vfio_iommu_type1' | sudo tee -a /etc/modules
   echo 'vfio_pci' | sudo tee -a /etc/modules
   echo 'vfio_virqfd' | sudo tee -a /etc/modules
   ```

4. **Blacklist conflicting drivers (optional)**
   ```sh
   echo 'blacklist nouveau' | sudo tee /etc/modprobe.d/blacklist.conf
   echo 'blacklist nvidia' | sudo tee -a /etc/modprobe.d/blacklist.conf
   ```

5. **Reboot and assign PCI device to VM in Proxmox UI**
   - Go to VM > Hardware > Add > PCI Device
   - Select the device and enable "All Functions" and "Primary GPU" if needed.

## Troubleshooting
- Check IOMMU groups:
  ```sh
  find /sys/kernel/iommu_groups/ -type l
  ```
- If VM fails to start, check logs and ensure no host driver is using the device.

---
For more details, see the [Proxmox PCI Passthrough documentation](https://pve.proxmox.com/wiki/PCI_Passthrough).
