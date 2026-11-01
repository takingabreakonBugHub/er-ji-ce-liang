"""Desktop UI for the tone generator and audio latency meter."""

import csv
import queue
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import sounddevice as sd

from headphone_lab import SAMPLE_RATE, ToneGenerator
from latency_test import LatencyMeter


RED = "#e4002b"
INK = "#1b1b1b"
MUTED = "#666666"
LINE = "#d9d9d9"
BG = "#ffffff"


class AudioLabUI:
    def __init__(self, root):
        self.root = root
        self.root.title("音频实验台")
        self.root.geometry("940x680")
        self.root.minsize(740, 570)
        self.root.configure(bg=BG)
        self.tone = None
        self.meter = None
        self.latency_stream = None
        self.rows = []

        self.frequency = tk.StringVar(value="400")
        self.volume = tk.DoubleVar(value=0.2)
        self.volume_text = tk.StringVar(value="20%")
        self.output_device = tk.StringVar(value="系统默认")
        self.input_device = tk.StringVar(value="系统默认")
        self.latency_output_device = tk.StringVar(value="系统默认")
        self.output_threshold = tk.StringVar(value="0.05")
        self.input_threshold = tk.StringVar(value="0.03")
        self.count = tk.StringVar(value="5")
        self.csv_path = tk.StringVar(value=str(Path(__file__).with_name("latency_results.csv")))
        self.status = tk.StringVar(value="就绪")
        self.progress = tk.StringVar(value="0 / 5")
        self.average = tk.StringVar(value="-- ms")
        self.input_level = tk.StringVar(value="麦克风电平 --")
        self.timing_estimated = False

        self._styles()
        self._build()
        self.refresh_devices()
        self.frequency.trace_add("write", self._update_tone)
        self.root.after(50, self._poll_meter)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def _styles(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=INK, font=("Microsoft YaHei UI", 10))
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 22, "bold"))
        style.configure("Number.TLabel", font=("Segoe UI", 45, "bold"))
        style.configure("Summary.TLabel", font=("Segoe UI", 24, "bold"))
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=BG, foreground=MUTED,
                        font=("Microsoft YaHei UI", 11), padding=(24, 8), borderwidth=0)
        style.map("TNotebook.Tab", foreground=[("selected", RED)],
                  background=[("selected", BG)])
        style.configure("TEntry", padding=6)
        style.configure("TCombobox", padding=6)
        style.configure("TSpinbox", padding=6)
        style.configure("Primary.TButton", background=RED, foreground=BG,
                        padding=(22, 8), font=("Microsoft YaHei UI", 10, "bold"), borderwidth=0)
        style.map("Primary.TButton", background=[("active", "#b90024"), ("disabled", LINE)])
        style.configure("Quiet.TButton", background=BG, foreground=INK,
                        padding=(18, 8), font=("Microsoft YaHei UI", 10), borderwidth=1)
        style.configure("Treeview", background=BG, fieldbackground=BG, foreground=INK,
                        rowheight=30, font=("Segoe UI", 10), borderwidth=0)
        style.configure("Treeview.Heading", background="#f7f7f8", foreground=INK,
                        font=("Microsoft YaHei UI", 10, "bold"), padding=8)
        style.map("Treeview", background=[("selected", "#ffe7ec")],
                  foreground=[("selected", INK)])

    def _build(self):
        outer = ttk.Frame(self.root, padding=(32, 16, 32, 16))
        outer.pack(fill="both", expand=True)
        header = ttk.Frame(outer)
        header.pack(fill="x")
        ttk.Label(header, text="音频实验台", style="Title.TLabel").pack(side="left")
        ttk.Label(header, textvariable=self.status, style="Muted.TLabel").pack(side="right")
        ttk.Separator(outer).pack(fill="x", pady=(12, 8))

        self.tabs = ttk.Notebook(outer)
        self.tabs.pack(fill="both", expand=True)
        tone_tab = ttk.Frame(self.tabs, padding=(2, 12, 2, 0))
        latency_tab = ttk.Frame(self.tabs, padding=(2, 12, 2, 0))
        self.tabs.add(tone_tab, text="测试音")
        self.tabs.add(latency_tab, text="延迟测量")
        self._build_tone(tone_tab)
        self._build_latency(latency_tab)

    def _build_tone(self, parent):
        parent.columnconfigure(1, weight=1)
        readout = ttk.Frame(parent)
        readout.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 24))
        ttk.Label(readout, textvariable=self.frequency, style="Number.TLabel").pack(side="left")
        ttk.Label(readout, text="Hz", style="Muted.TLabel").pack(side="left", padx=(9, 0), pady=(24, 0))
        ttk.Separator(parent).grid(row=1, column=0, columnspan=3, sticky="ew", pady=(0, 22))

        ttk.Label(parent, text="频率").grid(row=2, column=0, sticky="w", pady=10)
        self.frequency_input = ttk.Spinbox(parent, from_=20, to=20000, increment=10,
                                            textvariable=self.frequency, width=14)
        self.frequency_input.grid(row=2, column=1, sticky="w", padx=(24, 0), pady=10)
        ttk.Label(parent, text="Hz", style="Muted.TLabel").grid(row=2, column=2, sticky="e")

        ttk.Label(parent, text="输出音量").grid(row=3, column=0, sticky="w", pady=10)
        volume_row = ttk.Frame(parent)
        volume_row.grid(row=3, column=1, columnspan=2, sticky="ew", padx=(24, 0), pady=10)
        volume_row.columnconfigure(0, weight=1)
        ttk.Scale(volume_row, from_=0, to=1, variable=self.volume,
                  command=self._update_volume).grid(row=0, column=0, sticky="ew")
        ttk.Label(volume_row, textvariable=self.volume_text, width=6).grid(row=0, column=1, padx=(18, 0))

        ttk.Label(parent, text="输出设备").grid(row=4, column=0, sticky="w", pady=10)
        self.tone_device_box = ttk.Combobox(parent, textvariable=self.output_device, state="readonly")
        self.tone_device_box.grid(row=4, column=1, columnspan=2, sticky="ew", padx=(24, 0), pady=10)

        buttons = ttk.Frame(parent)
        buttons.grid(row=5, column=0, columnspan=3, sticky="w", pady=(28, 0))
        self.tone_start = ttk.Button(buttons, text="开始播放", style="Primary.TButton", command=self.start_tone)
        self.tone_start.pack(side="left")
        self.tone_stop = ttk.Button(buttons, text="停止", style="Quiet.TButton", command=self.stop_tone,
                                    state="disabled")
        self.tone_stop.pack(side="left", padx=(12, 0))

    def _build_latency(self, parent):
        parent.columnconfigure(1, weight=1)
        parent.columnconfigure(3, weight=1)
        ttk.Label(parent, text="播放端阈值").grid(row=0, column=0, sticky="w", pady=2)
        self.output_threshold_input = ttk.Entry(parent, textvariable=self.output_threshold, width=12)
        self.output_threshold_input.grid(row=0, column=1, sticky="ew", padx=(18, 30), pady=2)
        ttk.Label(parent, text="麦克风阈值").grid(row=0, column=2, sticky="w", pady=2)
        self.input_threshold_input = ttk.Entry(parent, textvariable=self.input_threshold, width=12)
        self.input_threshold_input.grid(row=0, column=3, sticky="ew", padx=(18, 0), pady=2)

        ttk.Label(parent, text="测量次数").grid(row=1, column=0, sticky="w", pady=2)
        self.count_input = ttk.Spinbox(parent, from_=1, to=100, textvariable=self.count, width=12)
        self.count_input.grid(row=1, column=1, sticky="ew", padx=(18, 30), pady=2)
        ttk.Label(parent, text="输入设备").grid(row=1, column=2, sticky="w", pady=2)
        self.input_device_box = ttk.Combobox(parent, textvariable=self.input_device, state="readonly")
        self.input_device_box.grid(row=1, column=3, sticky="ew", padx=(18, 0), pady=2)

        ttk.Label(parent, text="输出设备").grid(row=2, column=0, sticky="w", pady=2)
        self.latency_output_box = ttk.Combobox(parent, textvariable=self.latency_output_device, state="readonly")
        self.latency_output_box.grid(row=2, column=1, columnspan=3, sticky="ew", padx=(18, 0), pady=2)

        ttk.Label(parent, text="CSV 文件").grid(row=3, column=0, sticky="w", pady=2)
        self.csv_input = ttk.Entry(parent, textvariable=self.csv_path)
        self.csv_input.grid(row=3, column=1, columnspan=2, sticky="ew", padx=(18, 18), pady=2)
        self.csv_browse = ttk.Button(parent, text="浏览", style="Quiet.TButton", command=self.browse_csv)
        self.csv_browse.grid(row=3, column=3, sticky="e", pady=2)

        controls = ttk.Frame(parent)
        controls.grid(row=4, column=0, columnspan=4, sticky="ew", pady=(10, 8))
        self.latency_start = ttk.Button(controls, text="开始测量", style="Primary.TButton",
                                         command=self.start_latency)
        self.latency_start.pack(side="left")
        self.latency_stop = ttk.Button(controls, text="停止", style="Quiet.TButton",
                                        command=self.stop_latency, state="disabled")
        self.latency_stop.pack(side="left", padx=(12, 0))
        ttk.Label(controls, textvariable=self.progress, style="Muted.TLabel").pack(side="right")

        ttk.Separator(parent).grid(row=5, column=0, columnspan=4, sticky="ew", pady=(0, 8))
        summary = ttk.Frame(parent)
        summary.grid(row=6, column=0, columnspan=4, sticky="ew", pady=(0, 6))
        ttk.Label(summary, text="平均延迟", style="Muted.TLabel").pack(side="left", pady=(14, 0))
        ttk.Label(summary, textvariable=self.average, style="Summary.TLabel").pack(side="left", padx=(20, 0))
        ttk.Label(summary, textvariable=self.input_level, style="Muted.TLabel").pack(side="right", pady=(14, 0))

        result_frame = ttk.Frame(parent)
        result_frame.grid(row=7, column=0, columnspan=4, sticky="nsew")
        parent.rowconfigure(7, weight=1)
        self.results = ttk.Treeview(result_frame, columns=("trial", "delay", "peak"), show="headings", height=6)
        self.results.heading("trial", text="次数")
        self.results.heading("delay", text="延迟 (ms)")
        self.results.heading("peak", text="麦克风峰值")
        self.results.column("trial", width=80, anchor="w")
        self.results.column("delay", width=150, anchor="w")
        self.results.column("peak", width=150, anchor="w")
        self.results.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(result_frame, orient="vertical", command=self.results.yview)
        scrollbar.pack(side="right", fill="y")
        self.results.configure(yscrollcommand=scrollbar.set)

    def refresh_devices(self):
        try:
            devices = sd.query_devices()
        except sd.PortAudioError as exc:
            self.status.set(f"设备读取失败: {exc}")
            return
        inputs = ["系统默认"]
        outputs = ["系统默认"]
        host_names = [host["name"] for host in sd.query_hostapis()]
        for index, device in enumerate(devices):
            label = f"{index}: {device['name']} [{host_names[device['hostapi']]}]"
            if device["max_input_channels"]:
                inputs.append(label)
            if device["max_output_channels"]:
                outputs.append(label)
        self.input_device_box["values"] = inputs
        self.tone_device_box["values"] = outputs
        self.latency_output_box["values"] = outputs
        for kind, variable, options in ((0, self.input_device, inputs),
                                        (1, self.latency_output_device, outputs)):
            default_index = sd.default.device[kind]
            if default_index < 0:
                continue
            default_name = devices[default_index]["name"]
            preferred = next((i for i, device in enumerate(devices)
                              if host_names[device["hostapi"]] == "Windows WASAPI"
                              and (device["name"] == default_name
                                   or device["name"].startswith(default_name)
                                   or default_name.startswith(device["name"]))
                              and device["max_input_channels" if kind == 0 else "max_output_channels"]), None)
            if preferred is not None:
                variable.set(next(option for option in options if option.startswith(f"{preferred}: ")))

    @staticmethod
    def _device(value):
        return None if value == "系统默认" else int(value.split(":", 1)[0])

    def _update_tone(self, *_):
        if self.tone is None:
            return
        try:
            frequency = float(self.frequency.get())
            if 20 <= frequency <= 20000:
                self.tone.frequency = frequency
        except ValueError:
            pass

    def _update_volume(self, *_):
        value = self.volume.get()
        self.volume_text.set(f"{value:.0%}")
        if self.tone is not None:
            self.tone.volume = value

    def start_tone(self):
        try:
            frequency = float(self.frequency.get())
            if not 20 <= frequency <= 20000:
                raise ValueError("频率必须在 20 到 20000 Hz 之间")
            self.stop_latency()
            tone = ToneGenerator(frequency=frequency, volume=self.volume.get(),
                                 device=self._device(self.output_device.get()))
            tone.start()
            self.tone = tone
        except (ValueError, sd.PortAudioError) as exc:
            messagebox.showerror("无法播放", str(exc), parent=self.root)
            return
        self.tone_start.configure(state="disabled")
        self.tone_stop.configure(state="normal")
        self.tone_device_box.configure(state="disabled")
        self.status.set("正在播放测试音")

    def stop_tone(self):
        if self.tone is None:
            return
        try:
            self.tone.stop()
        finally:
            self.tone = None
            self.tone_start.configure(state="normal")
            self.tone_stop.configure(state="disabled")
            self.tone_device_box.configure(state="readonly")
            self.status.set("就绪")

    def browse_csv(self):
        path = filedialog.asksaveasfilename(parent=self.root, defaultextension=".csv",
                                            filetypes=[("CSV 文件", "*.csv")],
                                            initialfile=Path(self.csv_path.get()).name)
        if path:
            self.csv_path.set(path)

    def start_latency(self):
        try:
            output_threshold = float(self.output_threshold.get())
            input_threshold = float(self.input_threshold.get())
            count = int(self.count.get())
            if not 0 < output_threshold < 0.3:
                raise ValueError("播放端阈值必须大于 0 且小于 0.3")
            if not 0 < input_threshold < 1:
                raise ValueError("麦克风阈值必须在 0 到 1 之间")
            if not 1 <= count <= 100:
                raise ValueError("测量次数必须在 1 到 100 之间")
            if not self.csv_path.get().strip():
                raise ValueError("请选择 CSV 文件")
            self.stop_tone()
            meter = LatencyMeter(SAMPLE_RATE, count, output_threshold, input_threshold)
            stream = sd.Stream(samplerate=SAMPLE_RATE, channels=(1, 1), dtype="float32",
                               device=(self._device(self.input_device.get()),
                                       self._device(self.latency_output_device.get())),
                               callback=meter.callback)
            try:
                stream.start()
            except Exception:
                stream.close()
                raise
        except (ValueError, sd.PortAudioError) as exc:
            messagebox.showerror("无法测量", str(exc), parent=self.root)
            return
        self.meter = meter
        self.latency_stream = stream
        self.rows = []
        for item in self.results.get_children():
            self.results.delete(item)
        self.average.set("-- ms")
        self.input_level.set("麦克风电平 --")
        self.timing_estimated = False
        self.progress.set(f"0 / {count}")
        self.latency_start.configure(state="disabled")
        self.latency_stop.configure(state="normal")
        for control in (self.output_threshold_input, self.input_threshold_input,
                        self.count_input, self.input_device_box, self.latency_output_box,
                        self.csv_input, self.csv_browse):
            control.configure(state="disabled")
        self.status.set("正在测量延迟")

    def _poll_meter(self):
        if self.meter is not None:
            while True:
                try:
                    event = self.meter.events.get_nowait()
                except queue.Empty:
                    break
                if event[0] == "result":
                    _, trial, delay, peak, outcome = event
                    self.rows.append((datetime.now().isoformat(timespec="seconds"), trial,
                                      self.meter.output_threshold, self.meter.input_threshold,
                                      delay, peak, self.meter.estimated_timing, outcome))
                    label = ("背景过高" if outcome == "background" else
                             "超时" if outcome == "timeout" else f"{delay:.3f}")
                    self.results.insert("", "end", values=(
                        trial, label, f"{peak:.6f}"))
                    self.progress.set(f"{len(self.rows)} / {self.meter.count}")
                    measured = [row[4] for row in self.rows if row[4] is not None]
                    if measured:
                        self.average.set(f"{sum(measured) / len(measured):.3f} ms")
                    if len(self.rows) >= self.meter.count:
                        self.stop_latency()
                        break
                elif event[0] == "error":
                    self.stop_latency()
                    messagebox.showerror("测量出错", event[1], parent=self.root)
                    break
                elif event[0] == "status":
                    self.status.set(f"音频状态: {event[1]}")
                elif event[0] == "level":
                    self.input_level.set(f"麦克风电平 {event[1]:.5f}")
                elif event[0] == "timing_fallback":
                    self.timing_estimated = True
                    self.status.set("输入时间戳不可用；延迟为估算值")
        self.root.after(50, self._poll_meter)

    def stop_latency(self):
        if self.meter is None:
            return
        if self.latency_stream is not None:
            stream = self.latency_stream
            self.latency_stream = None
            stream.stop()
            stream.close()
        self.timing_estimated = self.meter.estimated_timing
        self.meter = None
        self.latency_start.configure(state="normal")
        self.latency_stop.configure(state="disabled")
        for control in (self.output_threshold_input, self.input_threshold_input,
                        self.count_input, self.csv_input, self.csv_browse):
            control.configure(state="normal")
        self.input_device_box.configure(state="readonly")
        self.latency_output_box.configure(state="readonly")
        self.status.set("就绪")
        if self.rows:
            try:
                path = Path(self.csv_path.get())
                with path.open("a", newline="", encoding="utf-8-sig") as file:
                    writer = csv.writer(file)
                    if file.tell() == 0:
                        writer.writerow(("time", "trial", "output_threshold",
                                         "input_threshold", "latency_ms", "input_peak",
                                         "timing_estimated", "outcome"))
                    writer.writerows(self.rows)
                suffix = "（时间戳估算）" if self.timing_estimated else ""
                self.status.set(f"结果已保存: {path.name}{suffix}")
            except OSError as exc:
                messagebox.showerror("保存失败", str(exc), parent=self.root)
            self.rows = []

    def close(self):
        self.stop_latency()
        self.stop_tone()
        self.root.destroy()


def main():
    root = tk.Tk()
    AudioLabUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
