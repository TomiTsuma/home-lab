#!/bin/bash

# Update package list and install prerequisites
sudo apt-get update
sudo apt-get install -y software-properties-common

# Add deadsnakes PPA for Python 3.11
sudo add-apt-repository ppa:deadsnakes/ppa -y
sudo apt-get update

# Install Python 3.11
sudo apt-get install -y python3.11 python3.11-venv python3.11-dev

# Verify installation
python3.11 --version

#Install pip
curl -sS https://bootstrap.pypa.io/get-pip.py -o get-pip.py

sudo python3.11 get-pip.py