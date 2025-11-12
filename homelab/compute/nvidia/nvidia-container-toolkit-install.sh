#!/bin/bash


sudo gpg --keyserver keyserver.ubuntu.com --recv-keys DDCAE044F796ECB0

sudo gpg --export --armor DDCAE044F796ECB0 | sudo tee /etc/apt/trusted.gpg.d/nvidia-toolkit.asc

sudo apt update

sudo apt install nvidia-container-toolkit

sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
