# Speech models

Two models go here. They are not in Git: Docker downloads them during the build if they are missing,
and `./scripts/get_model.sh` does the same for running without Docker. Checksums are verified.

| File | Model | Licence | Size | Used for |
|---|---|---|---|---|
| `ggml-tarbiyah-small-q8_0.bin` | [Habib-HF/tarbiyah-ai-v1-1](https://huggingface.co/Habib-HF/tarbiyah-ai-v1-1): Whisper small fine-tuned on Quran recitations by many reciters | MIT | 264 MB | every decision: uploads, and each phrase of a live check once the reciter pauses |
| `ggml-tarteel-base-q8_0.bin` | [tarteel-ai/whisper-base-ar-quran](https://huggingface.co/tarteel-ai/whisper-base-ar-quran) | Apache-2.0 | 82 MB | live only: following along inside the phrase being said, about 5x faster; it never marks a word wrong |

Why two: the small model is clearly more accurate (on 150 test recordings by 10 reciters, and on a
non-professional voice), but takes about 1.5 s per phrase on a laptop CPU, which is too slow to update
the words several times a second while someone recites. Whisper medium (Habib-HF/tarbiyah-ai-whisper-medium-merged)
was tried too: no better than small with the passage as context, and about 5 s per phrase.

Both were converted with [`scripts/convert_model.py`](../scripts/convert_model.py) (no PyTorch needed)
and quantised to q8_0 with whisper.cpp's `whisper-quantize`. One gotcha: the converter must take the
text context from `max_target_positions` (448), not from `max_length` in `config.json`.

SHA-256:
- `ggml-tarbiyah-small-q8_0.bin`: `923b8b290113507ef85449cc867adecb93797fec5fd9c0774039987186327937`
- `ggml-tarteel-base-q8_0.bin`: `72194195f7d280adebec57acf2c6e01e209484322ec1cec21c275a3c0b1e3d77`
