@echo off
setlocal
cd /d "%~dp0"

echo ==========================================
echo   ClassPoints v2.13.16 - Windows Builder
echo ==========================================
echo.

set "PYTHON=C:\Python314\python.exe"
if not exist "%PYTHON%" (
    where py >nul 2>nul
    if errorlevel 1 (
        echo [ERROR] Python 3.14 was not found.
        echo Install 64-bit CPython 3.14 or use C:\Python314.
        pause
        exit /b 1
    )
    for /f "delims=" %%P in ('py -3.14 -c "import sys; print(sys.executable)" 2^>nul') do set "PYTHON=%%P"
)

if not exist "%PYTHON%" (
    echo [ERROR] Python 3.14 executable not found: %PYTHON%
    pause
    exit /b 1
)

"%PYTHON%" -c "import sys; print('Python:',sys.version); assert sys.version_info[:2] == (3,14), 'Python 3.14 is required'"
if errorlevel 1 (
    echo [ERROR] Unsupported Python version.
    pause
    exit /b 1
)

echo.
echo [1/3] Installing build dependencies...
"%PYTHON%" -m pip install --upgrade pip
if errorlevel 1 goto :failed
"%PYTHON%" -m pip install --upgrade PyInstaller PyQt5 openpyxl python-docx lxml segno
if errorlevel 1 goto :failed

echo.
echo [2/3] Checking PowerShell script encoding...
"%PYTHON%" _tools\ensure_bom.py --fix
if errorlevel 1 goto :failed

echo.
echo [3/3] Building application, uninstaller and setup.exe...
powershell -NoProfile -ExecutionPolicy Bypass -File packaging\build_installer.ps1 -Python "%PYTHON%"
if errorlevel 1 goto :failed

echo.
echo Build complete. Check the dist folder for:
echo ClassPoints installer v2.13.16
pause
exit /b 0

:failed
echo.
echo [FAILED] Build stopped. Keep the error output for troubleshooting.
pause
exit /b 1
