# One image for both the API and the worker.

# --- whisper.cpp, built from source for this machine's CPU ---
FROM python:3.14-slim AS whisper
ARG WHISPER_CPP_VERSION=v1.9.5
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential cmake git ca-certificates \
 && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch "$WHISPER_CPP_VERSION" https://github.com/ggml-org/whisper.cpp /src \
 && cmake -S /src -B /build -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF \
      -DWHISPER_BUILD_TESTS=OFF -DWHISPER_BUILD_SERVER=OFF \
 && cmake --build /build --target whisper-cli -j"$(nproc)"

# --- the app ---
FROM python:3.14-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && python -m playwright install --with-deps chromium \
 && rm -rf /var/lib/apt/lists/*
COPY --from=whisper /build/bin/whisper-cli /usr/local/bin/whisper-cli

# Speech models, converted to ggml q8_0 (see models/README.md):
#  accurate: Habib-HF/tarbiyah-ai-v1-1, Whisper small fine-tuned on many reciters (MIT), 264 MB
#  fast:     tarteel-ai/whisper-base-ar-quran (Apache-2.0), 82 MB, only to follow along while reciting live
# Local copies in models/ are used if present; otherwise they are downloaded. Either way checksums are verified.
ARG MODELS=https://github.com/L0adn9/flyrank-recitation-checker/releases/download
COPY models/ models/
RUN fetch() { test -f "models/$2" || curl -fL --retry 3 -o "models/$2" "$MODELS/$1/$2"; echo "$3  models/$2" | sha256sum -c -; } \
 && fetch model-v2 ggml-tarbiyah-small-q8_0.bin 923b8b290113507ef85449cc867adecb93797fec5fd9c0774039987186327937 \
 && fetch model-v1 ggml-tarteel-base-q8_0.bin 72194195f7d280adebec57acf2c6e01e209484322ec1cec21c275a3c0b1e3d77

COPY data data
COPY app app
RUN useradd --create-home recite && mkdir -p storage && chown recite storage
USER recite
ENV WHISPER_CLI=whisper-cli WHISPER_MODEL=/app/models/ggml-tarbiyah-small-q8_0.bin \
    WHISPER_FAST_MODEL=/app/models/ggml-tarteel-base-q8_0.bin STORAGE_DIR=/app/storage
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
