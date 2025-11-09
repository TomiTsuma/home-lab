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
# Using the direct key URL for a more reliable import process
KEY_URL="https://apt.sonarr.tv/debian/sonarr.key" 
# Use the official, modern keyrings directory path for APT
GPG_KEY_FILE="/usr/share/keyrings/sonarr-archive-keyring.gpg"
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
    echo "Installing required prerequisites (curl, gnupg, apt-transport-https, install)..."
    apt update -y
    apt install -y curl gnupg apt-transport-https install
    if [ $? -ne 0 ]; then
        echo "Failed to install prerequisites. Exiting."
        exit 1
    fi
    echo "Prerequisites installed successfully."
}

# Add Sonarr GPG key and repository
setup_repository() {
    echo "Setting up Sonarr repository (using modern keyrings method)..."

    # 1. Fetch the GPG Key, dearmor it, and securely install it to the keyrings directory
    echo "1. Fetching GPG key from $KEY_URL and adding to keyrings..."
    curl -s -L $KEY_URL | gpg --dearmor | install -m 0644 -o root -g root /dev/stdin "$GPG_KEY_FILE"

    if [ $? -ne 0 ]; then
        echo "Error: Failed to fetch or add GPG key. Check the key URL or system GPG setup."
        exit 1
    fi

    # 2. Add the repository source, referencing the key from the new keyrings location
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