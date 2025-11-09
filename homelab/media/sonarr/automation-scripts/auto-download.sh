#!/bin/bash

# ==============================================================================
# Sonarr Native Installation Script for Debian/Ubuntu
# This script adds the official Sonarr repository, installs the application,
# and ensures the service is running and enabled.
# ==============================================================================

# --- Configuration ---
SONARR_USER="sonarr"
SONARR_GROUP="sonarr"
REPOSITORY_URL="https://apt.sonarr.tv/debian"
KEY_URL="https://keyserver.ubuntu.com/pks/lookup?op=get&search=0x2199D79E7806507E"
GPG_KEY_FILE="/etc/apt/trusted.gpg.d/sonarr.gpg"
REPO_SOURCE_FILE="/etc/apt/sources.list.d/sonarr.list"

# --- Functions ---

# Check if the script is run as root
check_root() {
    if [ "$EUID" -ne 0 ]; then
        echo "Please run this script as root or with sudo."
        exit 1
    fi
}

# Install necessary prerequisites
install_prerequisites() {
    echo "Installing required prerequisites (curl, gnupg, apt-transport-https)..."
    apt update -y
    apt install -y curl gnupg apt-transport-https
    if [ $? -ne 0 ]; then
        echo "Failed to install prerequisites. Exiting."
        exit 1
    fi
    echo "Prerequisites installed successfully."
}

# Add Sonarr GPG key and repository
setup_repository() {
    echo "Setting up Sonarr repository..."

    # 1. Add the GPG Key
    echo "1. Fetching and adding GPG key..."
    curl -s -L $KEY_URL | gpg --dearmor | tee $GPG_KEY_FILE > /dev/null
    chmod 644 $GPG_KEY_FILE

    if [ $? -ne 0 ]; then
        echo "Error: Failed to fetch or add GPG key."
        exit 1
    fi

    # 2. Add the repository source
    echo "2. Adding Sonarr repository source to $REPO_SOURCE_FILE..."
    echo "deb [signed-by=$GPG_KEY_FILE] $REPOSITORY_URL master main" | tee $REPO_SOURCE_FILE > /dev/null

    if [ $? -ne 0 ]; then
        echo "Error: Failed to add repository source."
        exit 1
    fi

    echo "Repository setup complete."
}

# Install Sonarr package
install_sonarr() {
    echo "Updating package list and installing Sonarr..."
    apt update -y
    apt install -y sonarr
    if [ $? -ne 0 ]; then
        echo "Failed to install sonarr package. Exiting."
        exit 1
    fi
    echo "Sonarr installed successfully."
}

# Configure and start the service
configure_and_start_service() {
    echo "Configuring and starting Sonarr service..."

    # Ensure the user/group exists for safety, though the package usually creates it
    if ! id -u "$SONARR_USER" >/dev/null 2>&1; then
        useradd -r -s /bin/false -M "$SONARR_USER"
        echo "Created user $SONARR_USER."
    fi

    # Enable and start the service
    systemctl enable sonarr
    systemctl start sonarr

    if [ $? -ne 0 ]; then
        echo "Warning: Failed to start Sonarr service. Check logs manually."
        return
    fi

    echo "Sonarr service enabled and started."
    echo "Verification: Service status (should be 'active (running)'):"
    systemctl status sonarr --no-pager | grep "Active"
}

# Main execution
main() {
    check_root
    echo "--- Starting Sonarr Native Installation ---"

    install_prerequisites
    setup_repository
    install_sonarr
    configure_and_start_service

    echo ""
    echo "=========================================================="
    echo " Installation Complete!"
    echo " Sonarr is now running and should be accessible at:"
    echo " http://<YourServerIP>:8989"
    echo ""
    echo " Please make sure to configure firewall access (port 8989) if applicable."
    echo "=========================================================="
}

main