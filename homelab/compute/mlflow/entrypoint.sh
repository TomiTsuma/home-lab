#!/bin/sh
set -e

# Regenerated on every boot so MLFLOW_ADMIN_PASSWORD changes in .env take
# effect on container recreate. Only the admin bootstrap is affected —
# mlflow's own basic-auth user/permission tables persist in the
# mlflow_auth volume regardless.
cat > /mlflow/auth/basic_auth.ini <<INI
[mlflow]
default_permission = READ
database_uri = sqlite:////mlflow/auth/basic_auth.db
admin_username = ${MLFLOW_ADMIN_USERNAME}
admin_password = ${MLFLOW_ADMIN_PASSWORD}
authorization_function = mlflow.server.auth:authenticate_request_basic_auth
INI

export MLFLOW_AUTH_CONFIG_PATH=/mlflow/auth/basic_auth.ini

exec mlflow server \
  --backend-store-uri "${MLFLOW_BACKEND_STORE_URI}" \
  --artifacts-destination "${MLFLOW_ARTIFACTS_DESTINATION}" \
  --serve-artifacts \
  --host 0.0.0.0 \
  --port 5000 \
  --workers 1 \
  --app-name basic-auth \
  --allowed-hosts "${MLFLOW_ALLOWED_HOSTS}"
