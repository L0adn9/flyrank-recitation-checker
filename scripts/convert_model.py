"""Convert a Hugging Face Whisper model (safetensors) to whisper.cpp's ggml format, without PyTorch.

Writes the same file as whisper.cpp's models/convert-h5-to-ggml.py (MIT), which this follows, but reads
the weights with numpy + safetensors, so no torch install is needed. Then quantise with whisper-quantize.

    pip install numpy safetensors
    curl -LO https://raw.githubusercontent.com/openai/whisper/main/whisper/assets/mel_filters.npz
    python scripts/convert_model.py <hf model dir> mel_filters.npz ggml-model-f16.bin
    whisper-quantize ggml-model-f16.bin ggml-model-q8_0.bin q8_0
"""
import json, struct, sys
from pathlib import Path
import numpy as np
from safetensors.numpy import load_file

conv_map = {
    'self_attn.k_proj': 'attn.key', 'self_attn.q_proj': 'attn.query', 'self_attn.v_proj': 'attn.value',
    'self_attn.out_proj': 'attn.out', 'self_attn_layer_norm': 'attn_ln',
    'encoder_attn.q_proj': 'cross_attn.query', 'encoder_attn.v_proj': 'cross_attn.value',
    'encoder_attn.out_proj': 'cross_attn.out', 'encoder_attn_layer_norm': 'cross_attn_ln',
    'fc1': 'mlp.0', 'fc2': 'mlp.2', 'final_layer_norm': 'mlp_ln',
    'encoder.layer_norm.bias': 'encoder.ln_post.bias', 'encoder.layer_norm.weight': 'encoder.ln_post.weight',
    'encoder.embed_positions.weight': 'encoder.positional_embedding',
    'decoder.layer_norm.bias': 'decoder.ln.bias', 'decoder.layer_norm.weight': 'decoder.ln.weight',
    'decoder.embed_positions.weight': 'decoder.positional_embedding',
    'decoder.embed_tokens.weight': 'decoder.token_embedding.weight',
}

def bytes_to_unicode():
    bs = list(range(ord("!"), ord("~") + 1)) + list(range(ord("¡"), ord("¬") + 1)) + list(range(ord("®"), ord("ÿ") + 1))
    cs = bs[:]; n = 0
    for b in range(256):
        if b not in bs:
            bs.append(b); cs.append(256 + n); n += 1
    return dict(zip(bs, [chr(c) for c in cs]))

d, mel, out = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
hp = json.load(open(d / "config.json"))
max_len = hp.get("max_target_positions", 448)   # not config max_length (see models/README.md)
tokens = json.load(open(d / "vocab.json", encoding="utf8"))
filters = np.load(mel)[f"mel_{hp['num_mel_bins']}"]
sd = load_file(str(d / "model.safetensors"))
f = open(out, "wb")
for v in [0x67676d6c, hp["vocab_size"], hp["max_source_positions"], hp["d_model"], hp["encoder_attention_heads"],
          hp["encoder_layers"], max_len, hp["d_model"], hp["decoder_attention_heads"], hp["decoder_layers"],
          hp["num_mel_bins"], 1]:
    f.write(struct.pack("i", v))
f.write(struct.pack("i", filters.shape[0])); f.write(struct.pack("i", filters.shape[1]))
f.write(filters.astype(np.float32).tobytes())
bd = {v: k for k, v in bytes_to_unicode().items()}
f.write(struct.pack("i", len(tokens)))
for key, _ in sorted(tokens.items(), key=lambda x: x[1]):
    text = bytearray([bd[c] for c in key])
    f.write(struct.pack("i", len(text))); f.write(text)
n = 0
for src, data in sd.items():
    if src == "proj_out.weight":
        continue
    nn = src.split(".")[1:]
    if nn[1] == "layers":
        nn[1] = "blocks"
        sub = ".".join(nn[3:-1])
        mapped = ("attn.key" if nn[0] == "encoder" else "cross_attn.key") if sub == "encoder_attn.k_proj" else conv_map[sub]
        name = ".".join(nn[:3] + [mapped] + nn[-1:])
    else:
        name = ".".join(nn); name = conv_map.get(name, name)
    data = np.squeeze(data).astype(np.float16)
    if name in ("encoder.conv1.bias", "encoder.conv2.bias"):
        data = data.reshape(data.shape[0], 1)
    ftype = 1
    if data.ndim < 2 or name in ("encoder.conv1.bias", "encoder.conv2.bias", "encoder.positional_embedding", "decoder.positional_embedding"):
        data = data.astype(np.float32); ftype = 0
    nb = name.encode()
    f.write(struct.pack("iii", data.ndim, len(nb), ftype))
    for i in range(data.ndim):
        f.write(struct.pack("i", data.shape[data.ndim - 1 - i]))
    f.write(nb)
    f.write(data.tobytes()); n += 1
f.close()
print("wrote", out, n, "tensors")
