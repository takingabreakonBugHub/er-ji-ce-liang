"""Measure playback-to-microphone latency with configurable signal thresholds.

Requires: pip install numpy sounddevice
Put the speaker/headphone near the microphone, then run: python latency_test.py
"""

import argparse
import csv
import queue
from collections import deque
from datetime import datetime
from pathlib import Path

import numpy as np
import sounddevice as sd


def amplitude(value):
    number = float(value)
    if not 0 < number < 1:
        raise argparse.ArgumentTypeError("threshold must be between 0 and 1")
    return number


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("count must be at least 1")
    return number


def device_name_or_index(value):
    return int(value) if value.isdecimal() else value


def get_args():
    parser = argparse.ArgumentParser(description="测量播放声音到麦克风收到声音的延迟")
    parser.add_argument("--output-threshold", type=amplitude, help="播放端阈值，0 到 1")
    parser.add_argument("--input-threshold", type=amplitude, help="麦克风阈值，0 到 1")
    parser.add_argument("--count", type=positive_int, default=5, help="测量次数，默认 5")
    parser.add_argument("--samplerate", type=positive_int, default=48000)
    parser.add_argument("--input-device", type=device_name_or_index, help="输入设备名称或编号")
    parser.add_argument("--output-device", type=device_name_or_index, help="输出设备名称或编号")
    parser.add_argument("--csv", type=Path, default=Path("latency_results.csv"))
    args = parser.parse_args()
    if args.output_threshold is None:
        args.output_threshold = amplitude(input("播放端阈值 (0-1，默认 0.05): ").strip() or "0.05")
    if args.input_threshold is None:
        args.input_threshold = amplitude(input("麦克风阈值 (0-1，默认 0.03): ").strip() or "0.03")
    return args


class LatencyMeter:
    def __init__(self, sample_rate, count, output_threshold, input_threshold):
        self.sample_rate = sample_rate
        self.count = count
        self.output_threshold = output_threshold
        self.input_threshold = input_threshold
        self.events = queue.SimpleQueue()
        self.frame_position = 0
        self.next_pulse = sample_rate // 2
        self.pulse_spacing = int(sample_rate * 1.5)
        self.pulse = self.make_pulse()
        self.sent = 0
        self.pending = None
        self.active_pulse = None
        self.input_peak = 0.0
        self.level_peak = 0.0
        self.level_frames = 0
        self.recent_levels = deque(maxlen=2)
        self.estimated_timing = False
        self.input_was_above = False

    def make_pulse(self):
        frames = int(self.sample_rate * 0.1)
        t = np.arange(frames) / self.sample_rate
        fade_frames = max(1, int(self.sample_rate * 0.005))
        envelope = np.ones(frames)
        envelope[:fade_frames] = np.linspace(0, 1, fade_frames)
        envelope[-fade_frames:] = np.linspace(1, 0, fade_frames)
        return (0.3 * np.sin(2 * np.pi * 1000 * t) * envelope).astype(np.float32)

    def callback(self, indata, outdata, frames, timing, status):
        outdata.fill(0)
        if status:
            self.events.put(("status", str(status)))
        block_peak = float(np.max(np.abs(indata[:, 0])))
        self.level_peak = max(self.level_peak, block_peak)
        self.level_frames += frames
        if self.level_frames >= self.sample_rate // 10:
            self.events.put(("level", self.level_peak))
            self.recent_levels.append(self.level_peak)
            self.level_peak = 0.0
            self.level_frames = 0

        block_start = self.frame_position
        block_end = block_start + frames
        if self.sent < self.count and block_end > self.next_pulse:
            self.active_pulse = self.next_pulse
            self.next_pulse += self.pulse_spacing
            crossing = np.flatnonzero(np.abs(self.pulse) >= self.output_threshold)
            if not len(crossing):
                self.events.put(("error", "播放端阈值高于测试音的峰值 0.3"))
                raise sd.CallbackAbort
            output_time = timing.outputBufferDacTime + (
                self.active_pulse + int(crossing[0]) - block_start
            ) / self.sample_rate
            self.sent += 1
            self.input_peak = 0.0
            self.events.put(("sent", self.sent))
            baseline = max((*self.recent_levels, self.level_peak), default=0.0)
            if baseline >= self.input_threshold:
                self.events.put(("result", self.sent, None, round(baseline, 6), "background"))
            else:
                self.pending = (self.sent, output_time)
                self.input_was_above = False
        if self.active_pulse is not None:
            pulse_end = self.active_pulse + len(self.pulse)
            start = max(block_start, self.active_pulse)
            end = min(block_end, pulse_end)
            if start < end:
                outdata[start - block_start:end - block_start, 0] = self.pulse[
                    start - self.active_pulse:end - self.active_pulse
                ]
            if block_end >= pulse_end:
                self.active_pulse = None
        self.frame_position = block_end

        if self.pending is None:
            return
        trial, output_time = self.pending
        input_start = timing.inputBufferAdcTime
        if not np.isfinite(input_start) or input_start <= 0:
            if not self.estimated_timing:
                self.estimated_timing = True
                self.events.put(("timing_fallback",))
            input_start = timing.currentTime - frames / self.sample_rate
        input_times = input_start + np.arange(frames) / self.sample_rate
        valid = (input_times >= output_time) & (input_times <= output_time + 0.5)
        if np.any(valid):
            self.input_peak = max(self.input_peak, float(np.max(np.abs(indata[valid, 0]))))
        above = np.abs(indata[:, 0]) >= self.input_threshold
        previous = np.concatenate(([self.input_was_above], above[:-1]))
        crossings = np.flatnonzero(valid & above & ~previous)
        self.input_was_above = bool(above[-1])
        if len(crossings):
            delay_ms = (input_times[crossings[0]] - output_time) * 1000
            self.events.put(("result", trial, round(delay_ms, 3), round(self.input_peak, 6), "ok"))
            self.pending = None
        elif input_times[-1] > output_time + 0.5:
            self.events.put(("result", trial, None, round(self.input_peak, 6), "timeout"))
            self.pending = None

def main():
    args = get_args()
    if args.output_threshold >= 0.3:
        raise SystemExit("播放端阈值必须低于测试音的峰值 0.3")
    meter = LatencyMeter(args.samplerate, args.count, args.output_threshold, args.input_threshold)
    device = (args.input_device, args.output_device)
    rows = []
    print("将扬声器或耳机靠近麦克风，保持环境安静。按 Ctrl+C 提前结束。")
    try:
        with sd.Stream(samplerate=args.samplerate, channels=(1, 1), dtype="float32",
                       device=device, callback=meter.callback):
            while len(rows) < args.count:
                event = meter.events.get(timeout=10)
                if event[0] == "sent":
                    print(f"第 {event[1]} 次测试已播放")
                elif event[0] == "result":
                    _, trial, delay, peak, outcome = event
                    rows.append((datetime.now().isoformat(timespec="seconds"), trial,
                                 args.output_threshold, args.input_threshold, delay, peak,
                                 meter.estimated_timing, outcome))
                    if outcome == "background":
                        print(f"第 {trial} 次: 播放前背景电平已超过阈值（峰值 {peak:.6f}）")
                    elif outcome == "timeout":
                        print(f"第 {trial} 次: 500 ms 内未达到麦克风阈值（峰值 {peak:.6f}）")
                    else:
                        print(f"第 {trial} 次延迟: {delay:.3f} ms")
                elif event[0] == "timing_fallback":
                    print("输入设备未提供时间戳，延迟为估算值；建议选择 WASAPI 设备。")
                elif event[0] == "level":
                    pass
                elif event[0] == "error":
                    raise RuntimeError(event[1])
                else:
                    print(f"音频状态: {event[1]}")
    except KeyboardInterrupt:
        print("已停止")
    except queue.Empty:
        print("音频设备没有返回测量结果；请检查设备连接和采样率。")
    except sd.PortAudioError as exc:
        raise SystemExit(f"无法打开音频设备: {exc}") from exc
    finally:
        if rows:
            write_header = not args.csv.exists() or args.csv.stat().st_size == 0
            with args.csv.open("a", newline="", encoding="utf-8-sig") as file:
                writer = csv.writer(file)
                if write_header:
                    writer.writerow(("time", "trial", "output_threshold", "input_threshold",
                                     "latency_ms", "input_peak", "timing_estimated", "outcome"))
                writer.writerows(rows)
            measured = [row[4] for row in rows if row[4] is not None]
            if measured:
                print(f"平均延迟: {np.mean(measured):.3f} ms")
            print(f"结果已保存: {args.csv.resolve()}")


if __name__ == "__main__":
    main()
