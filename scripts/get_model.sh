#!/usr/bin/env sh
# Download the speech models for running without Docker (Docker fetches them by itself).
set -eu
cd "$(dirname "$0")/.."
BASE="https://github.com/L0adn9/flyrank-recitation-checker/releases/download"
fetch() {  # release, file, sha256, size
  if [ ! -f "models/$2" ]; then
    echo "Downloading $2 ($4)..."
    curl -fL --retry 3 -o "models/$2.part" "$BASE/$1/$2"
    mv "models/$2.part" "models/$2"
  fi
  echo "$3  models/$2" | sha256sum -c -
}
fetch model-v2 ggml-tarbiyah-small-q8_0.bin 923b8b290113507ef85449cc867adecb93797fec5fd9c0774039987186327937 "264 MB"
fetch model-v1 ggml-tarteel-base-q8_0.bin 72194195f7d280adebec57acf2c6e01e209484322ec1cec21c275a3c0b1e3d77 "82 MB"
