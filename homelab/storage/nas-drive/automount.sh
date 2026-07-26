#!/usr/bin/env bash
#
# setup-nas-mount.sh — create a systemd unit that mounts the TrueNAS SMB share
# //192.168.1.141/storage at /mnt/nas.
#
# Usage:  sudo ./setup-nas-mount.sh
#
set -euo pipefail

# ---------------------------------------------------------------- configuration
SERVER="//192.168.1.141/storage"
MOUNTPOINT="/mnt/nas"
CIFS_USER="aeacus"
SMB_VERS="3.0"
CRED_DIR="/etc/cifs-credentials"
CRED_FILE="${CRED_DIR}/nas.cred"

# true  -> mount lazily on first access (survives the NAS being offline at boot)
# false -> mount eagerly at boot
USE_AUTOMOUNT=true
# ------------------------------------------------------------------------------

if [[ $EUID -ne 0 ]]; then
    echo "Elevating with sudo..."
    exec sudo -- "$0" "$@"
fi

# Files should belong to the invoking user, not root.
OWNER="${SUDO_USER:-root}"
OWNER_UID="$(id -u "$OWNER")"
OWNER_GID="$(id -g "$OWNER")"

echo "==> Installing cifs-utils"
apt-get update -qq
apt-get install -y -qq cifs-utils >/dev/null

echo "==> Writing credentials file"
install -d -m 0700 -o root -g root "$CRED_DIR"

if [[ -f "$CRED_FILE" ]]; then
    echo "    ${CRED_FILE} already exists, leaving it alone."
else
    read -rsp "    SMB password for ${CIFS_USER}@${SERVER}: " SMB_PASS
    echo
    [[ -n "$SMB_PASS" ]] || { echo "Empty password, aborting." >&2; exit 1; }

    ( umask 077; printf 'username=%s\npassword=%s\n' "$CIFS_USER" "$SMB_PASS" > "$CRED_FILE" )
    chmod 0600 "$CRED_FILE"
    chown root:root "$CRED_FILE"
    unset SMB_PASS
    echo "    Wrote ${CRED_FILE} (root-only, 0600)."
fi

echo "==> Creating mount point"
mkdir -p "$MOUNTPOINT"

# systemd requires the unit filename to match the mount path: /mnt/nas -> mnt-nas.mount
MOUNT_UNIT="$(systemd-escape --path --suffix=mount "$MOUNTPOINT")"
AUTOMOUNT_UNIT="$(systemd-escape --path --suffix=automount "$MOUNTPOINT")"

MOUNT_OPTS="credentials=${CRED_FILE}"
MOUNT_OPTS+=",vers=${SMB_VERS}"
MOUNT_OPTS+=",uid=${OWNER_UID},gid=${OWNER_GID}"
MOUNT_OPTS+=",file_mode=0664,dir_mode=0775"
MOUNT_OPTS+=",iocharset=utf8,noperm,soft"

echo "==> Writing /etc/systemd/system/${MOUNT_UNIT}"
cat > "/etc/systemd/system/${MOUNT_UNIT}" <<EOF
[Unit]
Description=TrueNAS storage share (${SERVER})
Requires=network-online.target
After=network-online.target

[Mount]
What=${SERVER}
Where=${MOUNTPOINT}
Type=cifs
Options=${MOUNT_OPTS}
TimeoutSec=30

[Install]
WantedBy=multi-user.target
EOF

if [[ "$USE_AUTOMOUNT" == true ]]; then
    echo "==> Writing /etc/systemd/system/${AUTOMOUNT_UNIT}"
    cat > "/etc/systemd/system/${AUTOMOUNT_UNIT}" <<EOF
[Unit]
Description=Automount TrueNAS storage share

[Automount]
Where=${MOUNTPOINT}
TimeoutIdleSec=600

[Install]
WantedBy=multi-user.target
EOF
fi

echo "==> Reloading systemd"
systemctl daemon-reload

if [[ "$USE_AUTOMOUNT" == true ]]; then
    systemctl enable --now "$AUTOMOUNT_UNIT"
    echo "==> Triggering mount by touching ${MOUNTPOINT}"
    ls "$MOUNTPOINT" >/dev/null 2>&1 || true
else
    systemctl enable --now "$MOUNT_UNIT"
fi

echo
if mountpoint -q "$MOUNTPOINT"; then
    echo "SUCCESS: ${SERVER} is mounted at ${MOUNTPOINT}"
    findmnt "$MOUNTPOINT"
else
    echo "The share is not mounted yet. Check:" >&2
    echo "  systemctl status ${MOUNT_UNIT}" >&2
    echo "  journalctl -u ${MOUNT_UNIT} -n 50 --no-pager" >&2
    exit 1
fi