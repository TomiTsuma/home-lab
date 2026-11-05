// Terraform main configuration
//
// NOT YET IMPLEMENTED. The current source of truth for Proxmox VM
// provisioning is ../proxmox/vms/create-vm-*.sh (shell scripts wrapping
// `qm create`/`qm set`, matching the exact live VM configs as of 2026-11-05).
// A future migration to a real Proxmox Terraform provider (e.g. bpg/proxmox)
// would need an API token created on the pve host first. See ../proxmox/REBUILD.md.
