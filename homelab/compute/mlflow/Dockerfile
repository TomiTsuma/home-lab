FROM python:3.11-slim

# sqlalchemy pinned <2.1: mlflow 3.7.0's db/utils.py imports
# FallbackAsyncAdaptedQueuePool, which SQLAlchemy 2.1.x removed/renamed.
# mlflow[auth] (not plain mlflow) pulls in Flask-WTF, required by the
# basic-auth app for CSRF validation — omitting it fails at import time.
RUN pip install --no-cache-dir 'sqlalchemy<2.1' 'mlflow[auth]==3.7.0' psycopg2-binary==2.9.9 boto3==1.34.* gunicorn

RUN mkdir -p /mlflow/auth
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 5000
ENTRYPOINT ["/entrypoint.sh"]
