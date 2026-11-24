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

# Speech model: Tarteel's whisper-base-ar-quran (Apache-2.0) converted to ggml q8_0.
# A local copy in models/ is used if present; otherwise it is downloaded. Either way the checksum is verified.
ARG MODEL_URL=https://github.com/L0adn9/flyrank-recitation-checker/releases/download/model-v1/ggml-tarteel-base-q8_0.bin
ARG MODEL_SHA256=72194195f7d280adebec57acf2c6e01e209484322ec1cec21c275a3c0b1e3d77
COPY models/ models/
RUN test -f models/ggml-tarteel-base-q8_0.bin \
      || curl -fL --retry 3 -o models/ggml-tarteel-base-q8_0.bin "$MODEL_URL" \
 && echo "$MODEL_SHA256  models/ggml-tarteel-base-q8_0.bin" | sha256sum -c -

COPY data data
COPY app app
RUN useradd --create-home recite && mkdir -p storage && chown recite storage
USER recite
ENV WHISPER_CLI=whisper-cli WHISPER_MODEL=/app/models/ggml-tarteel-base-q8_0.bin STORAGE_DIR=/app/storage
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
