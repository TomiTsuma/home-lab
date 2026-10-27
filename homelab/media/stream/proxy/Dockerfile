FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY hls_proxy.py .

EXPOSE 5050

CMD ["python", "hls_proxy.py"]
