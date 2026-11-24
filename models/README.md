# Speech model

`ggml-tarteel-base-q8_0.bin` (82 MB) goes here. It is not in Git.

- Docker downloads it during the build if it is missing.
- For running without Docker: `./scripts/get_model.sh`.

It is [tarteel-ai/whisper-base-ar-quran](https://huggingface.co/tarteel-ai/whisper-base-ar-quran)
(Apache-2.0, by Tarteel AI) converted to whisper.cpp's ggml format with
`whisper.cpp/models/convert-h5-to-ggml.py` and quantised to q8_0 with `whisper-quantize`.
One gotcha when converting: the model's `config.json` has `max_length: 1024`, which the converter
reads as the text context; set `max_length` to 448 (= `max_target_positions`) in a copy of the config first.

SHA-256: `72194195f7d280adebec57acf2c6e01e209484322ec1cec21c275a3c0b1e3d77`
