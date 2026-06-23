FROM python:3.13-slim

WORKDIR /app
COPY main.py .

EXPOSE 8002
HEALTHCHECK --interval=10s --timeout=5s --retries=5 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8002/health')"]
CMD ["python", "main.py"]
