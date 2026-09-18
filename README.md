# ESP32-S3 button-triggered USB camera capture

The camera and ESP32-S3 both connect directly to the Windows PC. The ESP32
does not carry camera data: it sends `SNAP` over USB serial, and the Python
program saves a frame from the UVC camera.

## Wiring

The default firmware uses the development board's **BOOT button (GPIO 0)**.
No extra wiring is needed. Do not hold BOOT while resetting or powering the
board, because that enters the ROM download mode.

For an external normally-open push button:

1. Connect one side to a safe GPIO.
2. Connect the other side to GND.
3. Change `-D BUTTON_PIN=0` in `platformio.ini` to that GPIO number.

The code enables the ESP32's internal pull-up, so no external resistor is
required. Avoid GPIO 0, 3, 45, and 46 for an external button because they are
boot strapping pins.

## Build and upload with PlatformIO

Open this entire folder (the folder containing `platformio.ini`) in VS Code.
Install the recommended **PlatformIO IDE** extension if VS Code prompts for it.

- Click the PlatformIO **checkmark** in the bottom status bar to compile.
- Or press `Ctrl+Shift+B`; **PlatformIO: Build ESP32-S3** is the default task.
- To upload, open the Command Palette and run
  **Tasks: Run Task > PlatformIO: Upload ESP32-S3**.

Do not use the generic C/C++ Runner triangle button; that invokes the desktop
C++ compiler rather than the ESP32 PlatformIO toolchain.

From a terminal, the equivalent commands are:

```powershell
$env:PLATFORMIO_CORE_DIR="$PWD\.platformio-core"
.\.venv\Scripts\platformio.exe run
.\.venv\Scripts\platformio.exe run --target upload
.\.venv\Scripts\platformio.exe device monitor --baud 115200
```

If upload auto-detection fails, identify the ESP32 port with `pio device list`
and add `upload_port = COMx` under the environment in `platformio.ini`.
Close the serial monitor before starting the Python program; only one program
can normally own a COM port at a time.

## Set up the PC capture program

From the repository root:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r host\requirements.txt
.\.venv\Scripts\python.exe host\capture.py --list-devices
```

Note the ESP32 COM port and the desired camera index. Start capture, replacing
`COM7` and `0` with those values:

```powershell
.\.venv\Scripts\python.exe host\capture.py --port COM7 --camera 0
```

### Desktop diagnostic UI

For a live UART log and captured-image preview:

```powershell
.\.venv\Scripts\python.exe host\capture_ui.py
```

Select the ESP32 COM port and USB camera index, then click **Start listening**.
The left panel shows every received UART packet in text and hexadecimal form,
command matching, camera reads, and file-save results. The right panel shows
the most recently captured image. **Test capture** verifies the camera without
waiting for UART. Persistent diagnostics are written to
`logs\capture_ui.log`.

### Standalone Windows executable

Build the portable executable with:

```powershell
powershell -ExecutionPolicy Bypass -File host\build_executable.ps1
```

The result is `dist\ESP32-Camera-Trigger.exe`. It includes Python and all host
libraries, so Python does not need to be installed on the destination PC.
Copy the EXE to a writable folder and run it. Captures and logs are created
beside the EXE. This build targets 64-bit Windows; the destination still needs
working Windows drivers for the UVC camera and ESP32 USB serial device.

Press the ESP32 button once. A timestamped JPEG will be written to `captures`.
The program keeps running until you press `Ctrl+C`. If the camera or ESP32 is
temporarily disconnected, it automatically retries every three seconds.
For a different resolution or directory:

```powershell
.\.venv\Scripts\python.exe host\capture.py --port COM7 --camera 1 --width 1280 --height 720 --output D:\CameraCaptures
```

The requested camera resolution is a preference; the program prints the
resolution actually accepted by the webcam. If the camera is busy, close the
Windows Camera app, Teams, browser camera tabs, and other camera software.

## Troubleshooting

- No COM port: try the ESP32's USB/OTG connector and a data-capable cable.
- Upload fails: hold BOOT, tap RESET, start upload, then release BOOT.
- Button causes reboot/download mode: use an external button on a non-strapping
  GPIO instead of GPIO 0.
- Wrong camera: rerun `--list-devices` and try the other reported index.
- Delayed image: increase or decrease `--flush-frames`; the default reads two
  frames after the trigger to reduce stale buffered frames.
