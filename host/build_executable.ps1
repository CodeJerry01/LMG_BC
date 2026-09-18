$ErrorActionPreference = "Stop"
$workspace = Split-Path -Parent $PSScriptRoot
$python = Join-Path $workspace ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Project virtual environment not found: $python"
}

Push-Location $workspace
try {
    & $python -m PyInstaller `
        --noconfirm `
        --clean `
        --onefile `
        --windowed `
        --name "ESP32-Camera-Trigger" `
        --collect-all cv2 `
        --collect-all PIL `
        "host\capture_ui.py"

    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller build failed with exit code $LASTEXITCODE"
    }

    Write-Host ""
    Write-Host "Standalone executable created:"
    Write-Host (Join-Path $workspace "dist\ESP32-Camera-Trigger.exe")
} finally {
    Pop-Location
}
