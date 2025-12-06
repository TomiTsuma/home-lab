#!/bin/bash

set -e

echo "Fetching public IP..."
PUBLIC_IP=$(curl -s https://api.ipify.org)
echo "Public IP detected: $PUBLIC_IP"

echo "Creating directories..."
mkdir -p ~/wireguard/config

echo "Starting WireGuard Docker container..."
docker run -d \
  --name=wireguard \
  --cap-add=NET_ADMIN \
  --cap-add=SYS_MODULE \
  -e PUID=$(id -u) \
  -e PGID=$(id -g) \
  -e TZ=Africa/Nairobi \
  -e SERVERURL=$PUBLIC_IP \
  -e SERVERPORT=51820 \
  -e PEERS=3 \
  -e PEERDNS=auto \
  -e INTERNAL_SUBNET=10.13.13.0 \
  -p 51820:51820/udp \
  -v ~/wireguard/config:/config \
  --sysctl="net.ipv4.conf.all.src_valid_mark=1" \
  --sysctl="net.ipv4.ip_forward=1" \
  --restart unless-stopped \
  lscr.io/linuxserver/wireguard:latest

echo "Waiting for WireGuard to generate peer configs..."
sleep 5

echo "=== WireGuard Installed Successfully ==="
echo "Peer configs located in: ~/wireguard/config"
echo "To show peer 1:"
echo "  docker exec wireguard /app/show-peer 1"
