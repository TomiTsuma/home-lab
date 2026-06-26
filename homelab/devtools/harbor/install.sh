#!/usr/bin/env bash
set -e

########################################
# Configuration
########################################

HARBOR_VERSION="2.14.1"
INSTALL_DIR="/opt/harbor"

HOSTNAME="$(hostname -I | awk '{print $1}')"

echo "Installing Harbor ${HARBOR_VERSION}"
echo "Hostname: ${HOSTNAME}"

########################################
# Install prerequisites
########################################

sudo apt update

sudo apt install -y \
    curl \
    wget \
    openssl \
    tar

########################################
# Install Docker if missing
########################################

if ! command -v docker &>/dev/null; then
    echo "Installing Docker..."
    curl -fsSL https://get.docker.com | sudo sh
fi

sudo systemctl enable docker
sudo systemctl start docker

########################################
# Download Harbor
########################################

sudo mkdir -p ${INSTALL_DIR}
cd /tmp

wget https://github.com/goharbor/harbor/releases/download/v${HARBOR_VERSION}/harbor-online-installer-v${HARBOR_VERSION}.tgz

tar xzf harbor-online-installer-v${HARBOR_VERSION}.tgz

sudo rm -rf ${INSTALL_DIR}

sudo mv harbor ${INSTALL_DIR}

########################################
# Configure Harbor
########################################

cd ${INSTALL_DIR}

cp harbor.yml.tmpl harbor.yml

sed -i "s/hostname: reg.mydomain.com/hostname: ${HOSTNAME}/" harbor.yml

sed -i "s/^https:/#https:/" harbor.yml

sed -i "/certificate:/ s/^/#/" harbor.yml
sed -i "/private_key:/ s/^/#/" harbor.yml

sed -i "s/^harbor_admin_password:.*/harbor_admin_password: Harbor12345/" harbor.yml

########################################
# Install Harbor
########################################

sudo ./install.sh

echo
echo "========================================"
echo "Harbor Installed"
echo
echo "URL:"
echo "http://${HOSTNAME}"
echo
echo "Username: admin"
echo "Password: Harbor12345"
echo "========================================"