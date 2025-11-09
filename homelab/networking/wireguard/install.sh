#!/bin/bash

# WireGuard VPN Server Setup Script for Ubuntu
# This script sets up a WireGuard VPN server for remote access

set -e  # Exit on error

# Check if running as root
if [[ $EUID -ne 0 ]]; then
   echo "This script must be run as root (use sudo)"
   exit 1
fi

# Configuration
WG_DIR="/etc/wireguard"
WG_INTERFACE="wg0"
WG_PORT="51820"
SERVER_IP="10.8.0.1/24"
CLIENT_IP="10.8.0.2/24"

echo "=== WireGuard VPN Server Setup ==="
echo ""

# Get server's public IP
SERVER_PUBLIC_IP=$(curl -s ifconfig.me)
if [ -z "$SERVER_PUBLIC_IP" ]; then
    echo "Warning: Could not detect public IP automatically"
    read -p "Enter your server's public IP address: " SERVER_PUBLIC_IP
fi

echo "Server Public IP: $SERVER_PUBLIC_IP"
echo "VPN Network: 10.8.0.0/24"
echo "WireGuard Port: $WG_PORT"
echo ""

# Update system packages
echo "Updating system packages..."
apt-get update

# Install WireGuard
echo "Installing WireGuard..."
apt-get install -y wireguard wireguard-tools qrencode

# Enable IP forwarding
echo "Enabling IP forwarding..."
sed -i 's/#net.ipv4.ip_forward=1/net.ipv4.ip_forward=1/' /etc/sysctl.conf
sysctl -p

# Generate server keys
echo "Generating server keys..."
cd "$WG_DIR"
umask 077
wg genkey | tee server_private.key | wg pubkey > server_public.key
SERVER_PRIVATE_KEY=$(cat server_private.key)
SERVER_PUBLIC_KEY=$(cat server_public.key)

# Generate client keys
echo "Generating client keys..."
wg genkey | tee client_private.key | wg pubkey > client_public.key
CLIENT_PRIVATE_KEY=$(cat client_private.key)
CLIENT_PUBLIC_KEY=$(cat client_public.key)

# Detect primary network interface
PRIMARY_INTERFACE=$(ip route | grep default | awk '{print $5}' | head -n1)
echo "Primary network interface: $PRIMARY_INTERFACE"

# Create server configuration
echo "Creating server configuration..."
cat > "$WG_DIR/$WG_INTERFACE.conf" << EOF
[Interface]
Address = $SERVER_IP
ListenPort = $WG_PORT
PrivateKey = $SERVER_PRIVATE_KEY
PostUp = iptables -A FORWARD -i $WG_INTERFACE -j ACCEPT; iptables -t nat -A POSTROUTING -o $PRIMARY_INTERFACE -j MASQUERADE
PostDown = iptables -D FORWARD -i $WG_INTERFACE -j ACCEPT; iptables -t nat -D POSTROUTING -o $PRIMARY_INTERFACE -j MASQUERADE

# Client configuration
[Peer]
PublicKey = $CLIENT_PUBLIC_KEY
AllowedIPs = $CLIENT_IP
EOF

# Set proper permissions
chmod 600 "$WG_DIR/$WG_INTERFACE.conf"
chmod 600 "$WG_DIR"/*.key

# Create client configuration file
echo "Creating client configuration..."
cat > "$WG_DIR/client.conf" << EOF
[Interface]
Address = $CLIENT_IP
PrivateKey = $CLIENT_PRIVATE_KEY
DNS = 1.1.1.1, 8.8.8.8

[Peer]
PublicKey = $SERVER_PUBLIC_KEY
Endpoint = $SERVER_PUBLIC_IP:$WG_PORT
AllowedIPs = 0.0.0.0/0, ::/0
PersistentKeepalive = 25
EOF

# Enable and start WireGuard
echo "Enabling and starting WireGuard..."
systemctl enable wg-quick@$WG_INTERFACE
systemctl start wg-quick@$WG_INTERFACE

# Configure firewall (if UFW is active)
if command -v ufw &> /dev/null && ufw status | grep -q "Status: active"; then
    echo "Configuring UFW firewall..."
    ufw allow $WG_PORT/udp
    ufw reload
fi

# Check if WireGuard is running
sleep 2
if systemctl is-active --quiet wg-quick@$WG_INTERFACE; then
    echo ""
    echo "=== Installation Complete ==="
    echo "WireGuard VPN server is running successfully!"
    echo ""
    echo "Server Status:"
    wg show
    echo ""
    echo "=== Client Configuration ==="
    echo "Client config file saved to: $WG_DIR/client.conf"
    echo ""
    echo "To connect from your device:"
    echo "1. Install WireGuard client on your device"
    echo "2. Copy the client configuration:"
    echo "   sudo cat $WG_DIR/client.conf"
    echo ""
    echo "3. Or scan this QR code with the WireGuard mobile app:"
    qrencode -t ansiutf8 < "$WG_DIR/client.conf"
    echo ""
    echo "Useful commands:"
    echo "  - Check status: sudo wg show"
    echo "  - Stop VPN: sudo systemctl stop wg-quick@$WG_INTERFACE"
    echo "  - Start VPN: sudo systemctl start wg-quick@$WG_INTERFACE"
    echo "  - View client config: sudo cat $WG_DIR/client.conf"
    echo ""
    echo "IMPORTANT: Make sure port $WG_PORT/UDP is open in your router/firewall!"
else
    echo ""
    echo "=== Installation Warning ==="
    echo "WireGuard service failed to start. Check logs with:"
    echo "sudo journalctl -u wg-quick@$WG_INTERFACE -xe"
    exit 1
fi