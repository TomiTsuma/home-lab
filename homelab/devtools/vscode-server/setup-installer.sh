#!/bin/bash

curl -fsSL https://code-server.dev/install.sh | sh

USER_NAME=$(whoami)

sudo systemctl enable --now code-server@$USER_NAME

# Exit if any command fails
set -e

# Detect current username


# Define paths
CONFIG_DIR="/home/$USER_NAME/.config/code-server"
CONFIG_FILE="$CONFIG_DIR/config.yaml"

# Define default values
PORT=8443
PASSWORD="yourpassword"

# Create the directory if it doesn't exist
mkdir -p "$CONFIG_DIR"

# Backup existing config if present
if [ -f "$CONFIG_FILE" ]; then
  echo "⚙️  Backing up existing config to $CONFIG_FILE.bak"
  cp "$CONFIG_FILE" "$CONFIG_FILE.bak"
fi

# Create new config.yaml
cat <<EOF > "$CONFIG_FILE"
bind-addr: 0.0.0.0:$PORT
auth: password
password: $PASSWORD
cert: false
EOF

# Set permissions
chmod 600 "$CONFIG_FILE"

# Display result
echo "✅ VS Code Server config created at: $CONFIG_FILE"
echo "--------------------------------------"
cat "$CONFIG_FILE"
echo "--------------------------------------"
echo "🔹 To restart the service, run:"
echo "    sudo systemctl restart code-server@$USER_NAME"
echo "🔹 Then access it via:"
echo "    http://<your-server-ip>:$PORT"
