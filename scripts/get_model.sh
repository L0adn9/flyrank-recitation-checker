#!/usr/bin/env sh
# Download the speech model for running without Docker (Docker fetches it by itself).
set -eu
cd "$(dirname "$0")/.."
URL="https://github.com/L0adn9/flyrank-recitation-checker/releases/download/model-v1/ggml-tarteel-base-q8_0.bin"
SHA="72194195f7d280adebec57acf2c6e01e209484322ec1cec21c275a3c0b1e3d77"
OUT="models/ggml-tarteel-base-q8_0.bin"
if [ ! -f "$OUT" ]; then
  echo "Downloading the model (82 MB)..."
  curl -fL --retry 3 -o "$OUT.part" "$URL"
  mv "$OUT.part" "$OUT"
fi
echo "$SHA  $OUT" | sha256sum -c -
