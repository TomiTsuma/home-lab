#!/bin/bash

python3.11 -m pip install spotDL

python3.11 -m spotdl --download-ffmpeg

python3.11 -m spotdl download --output "/media/music" https://open.spotify.com/playlist/77lCtD1ewN3KxWLg4AkJfQ

python3.11 -m spotdl download --output "/media/music" https://open.spotify.com/playlist/7aIc59Tt6c56iV5gwRR0YJ