#!/bin/bash

curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -
sudo apt install nodejs -y

sudo npm install n8n -g

n8n start



