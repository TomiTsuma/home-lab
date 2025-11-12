#!/bin/bash

docker run -d --name open-webui -p 3000:3005 -v open-webui:/app/backend/data --restart always ghcr.io/open-webui/open-webui:main