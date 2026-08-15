FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HF_HOME=/data/hf-cache
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates libsndfile1 && rm -rf /var/lib/apt/lists/*
RUN python -m pip install --no-cache-dir --upgrade pip setuptools wheel
RUN python -m pip install --no-cache-dir torch==2.9.0 torchaudio==2.9.0 --index-url https://download.pytorch.org/whl/cpu
RUN python -m pip install --no-cache-dir speechbrain==1.1.0 'soundfile>=0.12,<1.0'
WORKDIR /app
COPY server.py /app/server.py
EXPOSE 8091
HEALTHCHECK --interval=30s --timeout=5s --retries=5 --start-period=600s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8091/health', timeout=3)"
CMD ["python", "/app/server.py"]
