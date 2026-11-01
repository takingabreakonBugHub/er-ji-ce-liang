"""Continuous sine-wave output for headphone testing."""

import numpy as np
import sounddevice as sd


SAMPLE_RATE = 48000


class ToneGenerator:
    def __init__(self, frequency=400, volume=1.0, sample_rate=SAMPLE_RATE, device=None):
        self.frequency = frequency
        self.volume = volume
        self.sample_rate = sample_rate
        self.device = device
        self.phase = 0.0
        self.stream = None

    def callback(self, outdata, frames, timing, status):
        phase_step = 2 * np.pi * self.frequency / self.sample_rate
        phase = self.phase + phase_step * np.arange(frames)
        outdata[:, 0] = (self.volume * np.sin(phase)).astype(np.float32)
        self.phase = float((self.phase + phase_step * frames) % (2 * np.pi))

    def start(self):
        if self.stream is not None:
            return
        stream = sd.OutputStream(
            samplerate=self.sample_rate, channels=1, dtype="float32",
            device=self.device, callback=self.callback,
        )
        try:
            stream.start()
        except Exception:
            stream.close()
            raise
        self.stream = stream

    def stop(self):
        if self.stream is not None:
            stream = self.stream
            self.stream = None
            stream.stop()
            stream.close()


def main():
    tone = ToneGenerator()
    try:
        tone.start()
        print("正在播放")
        print(f"频率：{tone.frequency} Hz")
        print("数字输出：0 dBFS（满幅）")
        input("按 Enter 停止")
    finally:
        tone.stop()


if __name__ == "__main__":
    main()
