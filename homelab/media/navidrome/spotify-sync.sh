#!/bin/bash

sudo apt update
sudo apt install -y python3-pip python3-dev python3.11-venv build-essential

sudo apt install -y libffi-dev libxml2-dev libxslt1-dev zlib1g-dev

python3.11 -m pip install spotDL

python3.11 -m spotdl --download-ffmpeg

python3.11 -m spotdl download --output "/media/music" https://open.spotify.com/playlist/77lCtD1ewN3KxWLg4AkJfQ

python3.11 -m spotdl download --output "/media/music" https://open.spotify.com/playlist/7aIc59Tt6c56iV5gwRR0YJ