#!/bin/bash

mkdir -p ~/readarr/config
mkdir -p ~/audiobooks
mkdir -p ~/downloads


docker run --rm \
    --name readarr \
    -p 8787:8787 \
    -e PUID=1000 \
    -e PGID=1000 \
    -e UMASK=002 \
    -e TZ="Etc/UTC" \
    -v ~/readarr/config:/config \
    -v ~/downloads:/downloads \
    -v ~/audiobooks:/books \
    ghcr.io/hotio/readarr
