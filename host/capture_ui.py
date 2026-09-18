#!/usr/bin/env python3
"""Native UI for ESP32 UART-triggered USB camera capture."""

from __future__ import annotations

import logging
import queue
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, ttk

import cv2
import serial
from PIL import Image, ImageTk
from serial.tools import list_ports


APP_TITLE = "ESP32 Camera Trigger"
BG = "#0b1220"
PANEL = "#111b2e"
PANEL_ALT = "#162238"
TEXT = "#e5edf9"
MUTED = "#8ea0ba"
ACCENT = "#32d6a0"
BLUE = "#57a6ff"
ERROR = "#ff6b75"


def application_directory() -> Path:
    """Return the portable data directory for source and frozen builds."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


APP_DIR = application_directory()


def make_logger() -> logging.Logger:
    log_dir = APP_DIR / "logs"
    log_dir.mkdir(exist_ok=True)
    logger = logging.getLogger("capture_ui")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = logging.FileHandler(
            log_dir / "capture_ui.log", encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    return logger


class CaptureWorker(threading.Thread):
    def __init__(
        self,
        events: queue.Queue,
        stop_event: threading.Event,
        manual_event: threading.Event,
        port: str,
        camera_index: int,
        output_dir: Path,
        width: int,
        height: int,
    ) -> None:
        super().__init__(daemon=True)
        self.events = events
        self.stop_event = stop_event
        self.manual_event = manual_event
        self.port = port
        self.camera_index = camera_index
        self.output_dir = output_dir
        self.width = width
        self.height = height
        self.logger = make_logger()

    def emit(self, kind: str, message: str = "", payload=None) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self.events.put((kind, timestamp, message, payload))
        if kind == "error":
            self.logger.error(message)
        elif kind not in {"image", "status"}:
            self.logger.info(message)

    def open_camera(self) -> cv2.VideoCapture:
        backend = cv2.CAP_DSHOW if hasattr(cv2, "CAP_DSHOW") else cv2.CAP_ANY
        self.emit("log", f"Opening camera index {self.camera_index}...")
        camera = cv2.VideoCapture(self.camera_index, backend)
        if not camera.isOpened():
            camera.release()
            raise RuntimeError(f"camera index {self.camera_index} did not open")
        camera.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        actual_w = int(camera.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(camera.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.emit("log", f"Camera ready at {actual_w}x{actual_h}")
        return camera

    def capture(self, camera: cv2.VideoCapture, source: str) -> None:
        started = time.perf_counter()
        self.emit("log", f"Capture requested by {source}; reading fresh frames")
        frame = None
        for frame_number in range(1, 4):
            ok, candidate = camera.read()
            self.emit(
                "log",
                f"Camera read {frame_number}/3: "
                f"{'OK' if ok and candidate is not None else 'FAILED'}",
            )
            if ok and candidate is not None:
                frame = candidate
        if frame is None:
            raise RuntimeError("camera returned no image")

        self.output_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        target = self.output_dir / f"capture_{stamp}.jpg"
        ok = cv2.imwrite(
            str(target), frame, [cv2.IMWRITE_JPEG_QUALITY, 95]
        )
        if not ok:
            raise RuntimeError(f"OpenCV could not save {target}")

        elapsed_ms = (time.perf_counter() - started) * 1000
        height, width = frame.shape[:2]
        self.emit(
            "success",
            f"IMAGE SAVED: {target.resolve()} "
            f"({width}x{height}, {elapsed_ms:.0f} ms)",
        )
        self.emit("image", str(target.resolve()), frame.copy())

    def run(self) -> None:
        self.emit("status", "connecting")
        while not self.stop_event.is_set():
            camera = None
            uart = None
            try:
                camera = self.open_camera()
                self.emit("log", f"Opening UART {self.port} at 115200 baud...")
                uart = serial.Serial(self.port, 115200, timeout=0.20)
                time.sleep(0.5)
                uart.reset_input_buffer()
                self.emit(
                    "success",
                    f"READY — waiting for exact UART command SNAP on {self.port}",
                )
                self.emit("status", "ready")

                while not self.stop_event.is_set():
                    if self.manual_event.is_set():
                        self.manual_event.clear()
                        self.capture(camera, "MANUAL TEST")

                    raw = uart.readline()
                    if not raw:
                        continue

                    text = raw.decode("utf-8", errors="replace").strip()
                    hex_data = " ".join(f"{byte:02X}" for byte in raw)
                    self.emit(
                        "rx",
                        f"UART RX {len(raw)} byte(s): text={text!r} hex=[{hex_data}]",
                    )

                    normalized = text.upper()
                    if normalized == "SNAP":
                        self.emit("success", "Command matched: SNAP")
                        self.capture(camera, "UART")
                    else:
                        self.emit(
                            "warning",
                            f"Ignored command {text!r}; expected exactly 'SNAP'",
                        )
            except (serial.SerialException, OSError, RuntimeError, cv2.error) as exc:
                if not self.stop_event.is_set():
                    self.emit("error", f"DEVICE ERROR: {exc}")
                    self.emit("log", "Retrying connection in 3 seconds...")
                    self.emit("status", "error")
                    self.stop_event.wait(3)
            finally:
                if uart is not None and uart.is_open:
                    uart.close()
                if camera is not None:
                    camera.release()

        self.emit("status", "stopped")
        self.emit("log", "Capture worker stopped")


class CaptureApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1180x720")
        self.minsize(940, 600)
        self.configure(bg=BG)

        self.events: queue.Queue = queue.Queue()
        self.worker: CaptureWorker | None = None
        self.stop_event = threading.Event()
        self.manual_event = threading.Event()
        self.preview_photo = None
        self.latest_frame = None

        self.port_var = tk.StringVar(value="COM7")
        self.camera_var = tk.StringVar(value="1")
        self.output_var = tk.StringVar(value=str((APP_DIR / "captures").resolve()))
        self.status_var = tk.StringVar(value="STOPPED")
        self.image_info_var = tk.StringVar(value="Waiting for the first capture")

        self.make_styles()
        self.make_ui()
        self.refresh_ports()
        self.after(75, self.process_events)
        self.protocol("WM_DELETE_WINDOW", self.close_app)

    def make_styles(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure(
            "TLabel", background=BG, foreground=TEXT, font=("Segoe UI", 10)
        )
        style.configure(
            "Title.TLabel",
            background=BG,
            foreground=TEXT,
            font=("Segoe UI Semibold", 20),
        )
        style.configure(
            "Muted.TLabel", background=BG, foreground=MUTED, font=("Segoe UI", 9)
        )
        style.configure(
            "Panel.TLabel", background=PANEL, foreground=TEXT, font=("Segoe UI", 10)
        )
        style.configure(
            "Status.TLabel",
            background=PANEL_ALT,
            foreground=ACCENT,
            font=("Segoe UI Semibold", 10),
            padding=(12, 7),
        )
        style.configure(
            "TButton",
            background=PANEL_ALT,
            foreground=TEXT,
            borderwidth=0,
            padding=(13, 8),
            font=("Segoe UI Semibold", 9),
        )
        style.map("TButton", background=[("active", "#233554")])
        style.configure("Accent.TButton", background=ACCENT, foreground="#07130f")
        style.map("Accent.TButton", background=[("active", "#49e6b3")])
        style.configure(
            "TCombobox",
            fieldbackground=PANEL_ALT,
            background=PANEL_ALT,
            foreground=TEXT,
            arrowcolor=TEXT,
        )

    def make_ui(self) -> None:
        header = ttk.Frame(self, padding=(22, 17, 22, 12))
        header.pack(fill="x")
        title_box = ttk.Frame(header)
        title_box.pack(side="left")
        ttk.Label(title_box, text=APP_TITLE, style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            title_box,
            text="UART-triggered UVC image capture and diagnostics",
            style="Muted.TLabel",
        ).pack(anchor="w")
        ttk.Label(header, textvariable=self.status_var, style="Status.TLabel").pack(
            side="right"
        )

        controls = ttk.Frame(self, style="Panel.TFrame", padding=(18, 12))
        controls.pack(fill="x", padx=22, pady=(0, 14))

        ttk.Label(controls, text="SERIAL PORT", style="Panel.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self.port_combo = ttk.Combobox(
            controls, textvariable=self.port_var, width=10, state="normal"
        )
        self.port_combo.grid(row=1, column=0, padx=(0, 14), pady=(4, 0))

        ttk.Label(controls, text="CAMERA INDEX", style="Panel.TLabel").grid(
            row=0, column=1, sticky="w"
        )
        ttk.Combobox(
            controls,
            textvariable=self.camera_var,
            values=("0", "1", "2", "3"),
            width=8,
            state="normal",
        ).grid(row=1, column=1, padx=(0, 14), pady=(4, 0))

        ttk.Label(controls, text="OUTPUT DIRECTORY", style="Panel.TLabel").grid(
            row=0, column=2, sticky="w"
        )
        output_entry = tk.Entry(
            controls,
            textvariable=self.output_var,
            bg=PANEL_ALT,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            width=42,
            font=("Segoe UI", 9),
        )
        output_entry.grid(row=1, column=2, padx=(0, 8), pady=(4, 0), ipady=6)
        ttk.Button(controls, text="Browse", command=self.browse_output).grid(
            row=1, column=3, padx=(0, 18), pady=(4, 0)
        )

        self.start_button = ttk.Button(
            controls, text="Start listening", style="Accent.TButton", command=self.start
        )
        self.start_button.grid(row=1, column=4, padx=(0, 8), pady=(4, 0))
        self.stop_button = ttk.Button(
            controls, text="Stop", command=self.stop, state="disabled"
        )
        self.stop_button.grid(row=1, column=5, padx=(0, 8), pady=(4, 0))
        self.manual_button = ttk.Button(
            controls,
            text="Test capture",
            command=self.manual_capture,
            state="disabled",
        )
        self.manual_button.grid(row=1, column=6, pady=(4, 0))

        body = ttk.Panedwindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=22, pady=(0, 22))

        log_panel = ttk.Frame(body, style="Panel.TFrame", padding=14)
        preview_panel = ttk.Frame(body, style="Panel.TFrame", padding=14)
        body.add(log_panel, weight=2)
        body.add(preview_panel, weight=3)

        ttk.Label(log_panel, text="EVENT LOG", style="Panel.TLabel").pack(anchor="w")
        ttk.Label(
            log_panel,
            text="Every UART packet, command match, camera read, and save result",
            style="Panel.TLabel",
            foreground=MUTED,
        ).pack(anchor="w", pady=(1, 9))

        log_frame = ttk.Frame(log_panel, style="Panel.TFrame")
        log_frame.pack(fill="both", expand=True)
        scrollbar = ttk.Scrollbar(log_frame)
        scrollbar.pack(side="right", fill="y")
        self.log_text = tk.Text(
            log_frame,
            bg="#09111f",
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            wrap="word",
            font=("Cascadia Mono", 9),
            padx=10,
            pady=10,
            yscrollcommand=scrollbar.set,
            state="disabled",
        )
        self.log_text.pack(fill="both", expand=True)
        scrollbar.configure(command=self.log_text.yview)
        self.log_text.tag_configure("rx", foreground=BLUE)
        self.log_text.tag_configure("success", foreground=ACCENT)
        self.log_text.tag_configure("warning", foreground="#ffc857")
        self.log_text.tag_configure("error", foreground=ERROR)
        self.log_text.tag_configure("log", foreground=MUTED)

        preview_header = ttk.Frame(preview_panel, style="Panel.TFrame")
        preview_header.pack(fill="x")
        ttk.Label(preview_header, text="LATEST CAPTURE", style="Panel.TLabel").pack(
            side="left"
        )
        ttk.Label(
            preview_header, textvariable=self.image_info_var, style="Panel.TLabel"
        ).pack(side="right")

        self.preview = tk.Label(
            preview_panel,
            text="No image captured yet\n\nStart listening, then send SNAP over UART\nor click Test capture.",
            bg="#09111f",
            fg=MUTED,
            font=("Segoe UI", 12),
            justify="center",
        )
        self.preview.pack(fill="both", expand=True, pady=(10, 0))
        self.preview.bind("<Configure>", lambda _event: self.render_preview())

        self.append_log("log", "UI ready. Configure COM port and camera, then click Start.")
        self.append_log(
            "log", f"Persistent log file: {APP_DIR / 'logs' / 'capture_ui.log'}"
        )

    def refresh_ports(self) -> None:
        ports = [port.device for port in list_ports.comports()]
        self.port_combo["values"] = ports
        if "COM7" in ports:
            self.port_var.set("COM7")

    def browse_output(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.output_var.get())
        if selected:
            self.output_var.set(selected)

    def append_log(self, kind: str, message: str, timestamp: str | None = None) -> None:
        stamp = timestamp or datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"{stamp}  {message}\n", kind)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        try:
            camera_index = int(self.camera_var.get())
        except ValueError:
            self.append_log("error", "Camera index must be a number")
            return

        self.stop_event = threading.Event()
        self.manual_event = threading.Event()
        self.worker = CaptureWorker(
            self.events,
            self.stop_event,
            self.manual_event,
            self.port_var.get().strip(),
            camera_index,
            Path(self.output_var.get()),
            1920,
            1080,
        )
        self.worker.start()
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.manual_button.configure(state="normal")
        self.status_var.set("CONNECTING")

    def stop(self) -> None:
        self.stop_event.set()
        self.status_var.set("STOPPING")
        self.stop_button.configure(state="disabled")
        self.manual_button.configure(state="disabled")

    def manual_capture(self) -> None:
        self.manual_event.set()
        self.append_log("log", "Manual test-capture queued")

    def process_events(self) -> None:
        try:
            while True:
                kind, timestamp, message, payload = self.events.get_nowait()
                if kind == "status":
                    self.status_var.set(message.upper())
                    if message == "stopped":
                        self.start_button.configure(state="normal")
                        self.stop_button.configure(state="disabled")
                        self.manual_button.configure(state="disabled")
                elif kind == "image":
                    self.latest_frame = payload
                    self.image_info_var.set(Path(message).name)
                    self.render_preview()
                else:
                    self.append_log(kind, message, timestamp)
        except queue.Empty:
            pass
        self.after(75, self.process_events)

    def render_preview(self) -> None:
        if self.latest_frame is None:
            return
        available_w = max(100, self.preview.winfo_width() - 20)
        available_h = max(100, self.preview.winfo_height() - 20)
        rgb = cv2.cvtColor(self.latest_frame, cv2.COLOR_BGR2RGB)
        image = Image.fromarray(rgb)
        image.thumbnail((available_w, available_h), Image.Resampling.LANCZOS)
        self.preview_photo = ImageTk.PhotoImage(image)
        self.preview.configure(image=self.preview_photo, text="")

    def close_app(self) -> None:
        self.stop_event.set()
        self.destroy()


if __name__ == "__main__":
    CaptureApp().mainloop()
