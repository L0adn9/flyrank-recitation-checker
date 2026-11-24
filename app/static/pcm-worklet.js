// Turns microphone audio (usually 48 kHz float) into 16 kHz mono 16-bit PCM, posted in 100 ms chunks.
// Each output sample is the average of the input samples it covers, which also filters out the
// frequencies above 8 kHz that would otherwise alias; speech recognition only needs up to 8 kHz.
class Pcm16k extends AudioWorkletProcessor {
  constructor() {
    super();
    this.step = sampleRate / 16000;
    this.pos = 0;
    this.sum = 0;
    this.count = 0;
    this.out = new Int16Array(1600);
    this.n = 0;
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || !input[0]) return true;
    const ch = input[0];
    for (let i = 0; i < ch.length; i++) {
      this.sum += ch[i];
      this.count++;
      this.pos += 1;
      if (this.pos >= this.step) {
        this.pos -= this.step;
        const v = Math.max(-1, Math.min(1, this.sum / this.count));
        this.out[this.n++] = v < 0 ? v * 32768 : v * 32767;
        this.sum = 0;
        this.count = 0;
        if (this.n === this.out.length) {
          this.port.postMessage(this.out.buffer, [this.out.buffer]);
          this.out = new Int16Array(1600);
          this.n = 0;
        }
      }
    }
    return true;
  }
}

registerProcessor("pcm-16k", Pcm16k);
