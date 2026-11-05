#!/bin/bash
# Host-level Proxmox setup is mostly manual (ISO install + installer prompts).
# See REBUILD.md for the full procedure: network bridge config, storage.cfg,
# GPU passthrough prerequisites, ISO downloads, and the order to run
# vms/create-vm-*.sh in. This file intentionally has no commands of its own —
# the real provisioning lives in vms/create-vm-*.sh, which IS runnable.
