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

## This homelab's actual config (captured 2026-11-05, updated 2026-11-07)

- Host `pve`, VM 101 (`aeacus-dl`): NVIDIA GeForce RTX 3060 at PCI address
  `0000:01:00`, attached as `hostpci0: 0000:01:00,pcie=1` in the VM config
  (not "Primary GPU", no ROM-BAR override needed — this is a pure compute
  card, no display passthrough). Driver in the VM: 580.173.02, CUDA 13.0.
- Find the address on new hardware with `lspci -nn | grep -i nvidia`, then
  pass it with `qm set 101 --hostpci0 <bus:slot.func>,pcie=1` (see
  `vms/create-vm-101-aeacus-dl.sh`, which does this for you).
- Only this one VM uses PCI passthrough; VMs 100 and 102 (TrueNAS) use
  SCSI/SATA disk passthrough instead (`/dev/disk/by-id/...` paths), which is
  a different mechanism and doesn't need IOMMU groups — see
  `vms/create-vm-102-aeacus-backup.sh`.

### `machine: q35` + `pcie=1`, not the default i440fx

The VM must use `--machine q35` (a real PCIe root complex) with
`pcie=1` on the `hostpciN` line (attaches the device to a PCIe root port
instead of legacy PCI). The default i440fx machine type only has legacy PCI
slots — a GPU still "works" enough to show up in `nvidia-smi` and report
correct info, but this is the wrong topology for a modern GPU and isn't
worth troubleshooting around.

**Caveat from direct experience on this homelab**: fixing this (migrating an
already-running VM from i440fx to q35) did **not**, by itself, fix a real
GPU-compute hang (model loads into VRAM, then sits at 0% utilization
forever). That hang's actual causes were a memory-starved VM and Docker's
cgroup driver silently revoking the container's GPU access — see
`../../ai/open-webui/README.md` ("GPU gotchas") and
`../../docs/changelog/071126-091000_ollama_gpu_and_claude_integration_changelog.md`
for the full diagnostic trail. Don't assume the PCIe topology is the problem
just because it's the most commonly cited one online — verify with a direct
CUDA driver-API probe (bypassing whatever app is "hanging") before spending
more time on VFIO config.

**Also watch for**: changing `machine` type changes the guest's PCI topology,
which can trigger Linux's predictable network-interface naming to rename the
NIC (e.g. `ens18` → `enp6s18` here), silently breaking the guest's network
until `/etc/netplan/*.yaml` is updated to match the new name and `netplan
apply` is re-run. This VM has no QEMU guest agent and no serial console, so
if this happens, the Proxmox noVNC console is the only way in.

### Recovering a degraded/wedged GPU without a host reboot

If the GPU was passed through across several guest power-cycles without the
*host* ever rebooting, it can accumulate a degraded state (e.g. PCIe link
stuck at the lowest speed even under load, or compute hanging with no error
at all). A function-level/bus reset from the host, with the VM stopped
(releases the device back to `vfio-pci`, idle), often clears this without a
full host reboot:

```sh
qm stop 101
echo 1 > /sys/bus/pci/devices/0000:01:00.0/reset   # check /sys/.../reset_method first
qm start 101
```

Confirm the reset method is actually supported first:
`cat /sys/bus/pci/devices/<addr>/reset_method` (should list `flr` and/or
`bus`). Check link recovery with `lspci -vvv -s <addr> | grep LnkSta`.
