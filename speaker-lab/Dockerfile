FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV HF_HOME=/models/hf-cache

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install \
    --no-cache-dir \
    --upgrade \
    pip \
    setuptools \
    wheel

RUN python -m pip install \
    --no-cache-dir \
    torch==2.9.0 \
    torchaudio==2.9.0 \
    --index-url https://download.pytorch.org/whl/cpu

RUN python -m pip install \
    --no-cache-dir \
    speechbrain==1.1.0 \
    soundfile

WORKDIR /lab

COPY calibrate.py /lab/calibrate.py

CMD ["python", "/lab/calibrate.py"]
