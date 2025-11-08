#!/bin/bash

# setup.sh - auto configure .env for current user
USER_NAME=$(whoami)
USER_HOME="/home/${USER_NAME}"
PUID=$(id -u)
PGID=$(id -g)
TZ="Africa/Nairobi"
PASSWORD="Tobirama13@"

cat > .env <<EOF
USER_NAME=${USER_NAME}
USER_HOME=${USER_HOME}
PUID=${PUID}
PGID=${PGID}
TZ=${TZ}
PASSWORD=${PASSWORD}
EOF

echo "✅ Environment configured for ${USER_NAME}"
echo "Home directory set to ${USER_HOME}"
