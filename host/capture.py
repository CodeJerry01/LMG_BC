#!/usr/bin/env python3
"""Save a webcam frame whenever an ESP32 sends SNAP over serial."""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

import cv2
import serial
from serial.tools import list_ports


def available_ports() -> list[str]:
    ports = list(list_ports.comports())
    if not ports:
        return ["  (no serial ports found)"]
    return [
        f"  {p.device}: {p.description} [{p.hwid}]"
        for p in sorted(ports, key=lambda item: item.device)
    ]


def camera_backend() -> int:
    return cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY


def open_camera(index: int, width: int, height: int) -> cv2.VideoCapture:
    camera = cv2.VideoCapture(index, camera_backend())
    if not camera.isOpened():
        raise RuntimeError(f"Could not open camera index {index}")

    camera.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return camera


def list_cameras(max_index: int) -> int:
    print("Probing camera indices (Windows may briefly activate each camera):")
    found = 0
    for index in range(max_index + 1):
        camera = cv2.VideoCapture(index, camera_backend())
        if camera.isOpened():
            ok, frame = camera.read()
            if ok and frame is not None:
                height, width = frame.shape[:2]
                print(f"  index {index}: available, {width}x{height}")
                found += 1
        camera.release()
    if not found:
        print("  (no readable cameras found)")
        return 1
    return 0


def flush_camera(camera: cv2.VideoCapture, count: int) -> object:
    """Discard queued frames and return the newest readable frame."""
    frame = None
    for _ in range(max(1, count)):
        ok, candidate = camera.read()
        if ok:
            frame = candidate
    return frame


def output_path(directory: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    return directory / f"capture_{stamp}.jpg"


def run(args: argparse.Namespace) -> int:
    if args.list_devices:
        print("Serial ports:")
        print("\n".join(available_ports()))
        print()
        return list_cameras(args.max_camera_index)

    if not args.port:
        print("error: --port is required unless --list-devices is used", file=sys.stderr)
        print("\n".join(available_ports()), file=sys.stderr)
        return 2

    args.output.mkdir(parents=True, exist_ok=True)
    print(f"Images will be saved to: {args.output.resolve()}")
    print(
        f"Will keep running and reconnect every {args.retry_delay:g} seconds "
        "if a device disconnects."
    )
    print("Press Ctrl+C to stop manually.")

    try:
        while True:
            camera = None
            esp = None
            try:
                camera = open_camera(args.camera, args.width, args.height)
                actual_width = int(camera.get(cv2.CAP_PROP_FRAME_WIDTH))
                actual_height = int(camera.get(cv2.CAP_PROP_FRAME_HEIGHT))
                esp = serial.Serial(args.port, args.baud, timeout=0.25)

                print(
                    f"Listening on {args.port} at {args.baud} baud; "
                    f"camera {args.camera} at {actual_width}x{actual_height}"
                )

                # Native USB CDC may reset/re-enumerate when opened. Give it a
                # moment, then discard boot text and start on a complete line.
                time.sleep(0.5)
                esp.reset_input_buffer()

                while True:
                    raw = esp.readline()
                    if not raw:
                        continue
                    command = raw.decode("utf-8", errors="replace").strip()
                    if command != "SNAP":
                        print(f"ESP32: {command}")
                        continue

                    frame = flush_camera(camera, args.flush_frames)
                    if frame is None:
                        raise RuntimeError("camera stopped returning frames")

                    target = output_path(args.output)
                    params = [cv2.IMWRITE_JPEG_QUALITY, args.jpeg_quality]
                    if cv2.imwrite(str(target), frame, params):
                        print(f"Saved {target.resolve()}")
                    else:
                        print(
                            f"Capture failed: could not write {target}",
                            file=sys.stderr,
                        )
            except (RuntimeError, serial.SerialException, OSError, cv2.error) as error:
                print(
                    f"Device error: {error}. Retrying in "
                    f"{args.retry_delay:g} seconds...",
                    file=sys.stderr,
                )
                time.sleep(args.retry_delay)
            finally:
                if esp is not None and esp.is_open:
                    esp.close()
                if camera is not None:
                    camera.release()
    except KeyboardInterrupt:
        print("\nStopped manually.")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", help="ESP32 serial port, for example COM7")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--camera", type=int, default=0, help="OpenCV camera index")
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--jpeg-quality", type=int, choices=range(1, 101), default=95)
    parser.add_argument(
        "--flush-frames",
        type=int,
        default=2,
        help="frames to read after SNAP to reduce buffered-frame latency",
    )
    parser.add_argument(
        "--output", type=Path, default=Path("captures"), help="image directory"
    )
    parser.add_argument(
        "--retry-delay",
        type=float,
        default=3.0,
        help="seconds before reconnecting after a device error",
    )
    parser.add_argument("--list-devices", action="store_true")
    parser.add_argument("--max-camera-index", type=int, default=10)
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))
