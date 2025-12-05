#!/bin/bash


# sudo gpg --keyserver keyserver.ubuntu.com --recv-keys DDCAE044F796ECB0

# sudo gpg --export --armor DDCAE044F796ECB0 | sudo tee /etc/apt/trusted.gpg.d/nvidia-toolkit.asc

# sudo apt update

# sudo apt install nvidia-container-toolkit

# sudo nvidia-ctk runtime configure --runtime=docker
# sudo systemctl restart docker

sudo apt-get update && sudo apt-get install -y --no-install-recommends \
   curl \
   gnupg2

curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg \
  && curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
    sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
    sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

sudo sed -i -e '/experimental/ s/^#//g' /etc/apt/sources.list.d/nvidia-container-toolkit.list

sudo apt-get update

sudo apt-get install -y nvidia-container-toolkit

sudo nvidia-ctk runtime configure --runtime=docker

sudo systemctl restart docker