@echo off

echo.
echo ================================================
echo   KBS 16CH Video Monitoring System v2
echo   Library Installation Script
echo ================================================
echo.

python --version > nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed.
    echo.
    echo Please install Python 3.10 or higher:
    echo   https://www.python.org/downloads/
    echo.
    echo IMPORTANT: Check "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo [OK] Python %PYVER% detected

echo.
echo [1/4] Upgrading pip...
python -m pip install --upgrade pip --quiet
if %errorlevel% neq 0 (
    echo [ERROR] pip upgrade failed. Check internet connection.
    pause
    exit /b 1
)
echo       Done.

echo.
echo [2/4] Installing required libraries...
echo       PySide6, OpenCV, sounddevice, numpy, psutil, GPUtil, pycaw
echo       This may take a few minutes on first install.
echo.
python -m pip install -r "%~dp0kbs_monitor\requirements.txt"
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Library installation failed.
    echo Check internet connection or run as administrator.
    pause
    exit /b 1
)

echo.
echo [3/4] Installing ffmpeg (audio recording support)...
ffmpeg -version > nul 2>&1
if %errorlevel% equ 0 (
    echo       ffmpeg already installed. Skipped.
) else (
    winget --version > nul 2>&1
    if %errorlevel% neq 0 (
        echo [WARN] winget not found. ffmpeg installation skipped.
        echo        Recording will save video only (no audio).
        echo        To enable audio: install ffmpeg manually and add to PATH.
    ) else (
        winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
        ffmpeg -version > nul 2>&1
        if %errorlevel% equ 0 (
            echo       ffmpeg installed successfully.
        ) else (
            echo [WARN] ffmpeg install may require reopening cmd to take effect.
            echo        If audio is missing in recordings, re-run this script once.
        )
    )
)

echo.
echo [4/4] Verifying installation...
python -c "import PySide6, cv2, numpy, psutil" > nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Core library verification failed.
    pause
    exit /b 1
)
echo       Done.

echo.
echo ================================================
echo   Installation Complete!
echo   Double-click run.pyw to start the program.
echo ================================================
echo.
pause
