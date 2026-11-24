// Turns microphone audio into 16 kHz mono 16-bit PCM, posted in 100 ms chunks.
// The page asks the browser for a 16 kHz AudioContext, so usually the browser has already resampled
// (with a proper filter) and this only converts to 16-bit. Where the browser can't, the context runs at
// the device rate (often 48 kHz) and this low-pass filters below 7.2 kHz (a 63-tap windowed-sinc FIR)
// before taking every n-th sample, so higher frequencies do not fold back into the speech band.
const OUT_RATE = 16000;
const TAPS = 63;

function lowpass(rate) {
  const fc = 7200 / rate;
  const h = new Float32Array(TAPS);
  let sum = 0;
  for (let i = 0; i < TAPS; i++) {
    const m = i - (TAPS - 1) / 2;
    const sinc = m === 0 ? 2 * fc : Math.sin(2 * Math.PI * fc * m) / (Math.PI * m);
    const blackman = 0.42 - 0.5 * Math.cos((2 * Math.PI * i) / (TAPS - 1)) + 0.08 * Math.cos((4 * Math.PI * i) / (TAPS - 1));
    h[i] = sinc * blackman;
    sum += h[i];
  }
  for (let i = 0; i < TAPS; i++) h[i] /= sum;
  return h;
}

class Pcm16k extends AudioWorkletProcessor {
  constructor() {
    super();
    this.step = sampleRate / OUT_RATE;      // 1 when the browser already resampled
    this.filter = this.step > 1 ? lowpass(sampleRate) : null;
    this.hist = new Float32Array(TAPS);      // last TAPS input samples (ring buffer)
    this.h = 0;
    this.pos = 0;
    this.out = new Int16Array(1600);
    this.n = 0;
  }

  emit(v) {
    v = Math.max(-1, Math.min(1, v));
    this.out[this.n++] = v < 0 ? v * 32768 : v * 32767;
    if (this.n === this.out.length) {
      this.port.postMessage(this.out.buffer, [this.out.buffer]);
      this.out = new Int16Array(1600);
      this.n = 0;
    }
  }

  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    if (!this.filter) {
      for (let i = 0; i < ch.length; i++) this.emit(ch[i]);
      return true;
    }
    for (let i = 0; i < ch.length; i++) {
      this.hist[this.h] = ch[i];
      this.h = (this.h + 1) % TAPS;
      this.pos += 1;
      if (this.pos >= this.step) {
        this.pos -= this.step;
        let acc = 0;
        for (let k = 0; k < TAPS; k++) acc += this.filter[k] * this.hist[(this.h + k) % TAPS];
        this.emit(acc);
      }
    }
    return true;
  }
}

registerProcessor("pcm-16k", Pcm16k);
