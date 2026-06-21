@echo off
setlocal

cd /d "%~dp0"

echo.
echo [1/4] Checking Python...
python --version
if errorlevel 1 (
  echo Python was not found. Install Python 3.10+ and enable "Add to PATH".
  pause
  exit /b 1
)

echo.
echo [2/4] Installing dependencies...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo Failed to install dependencies.
  pause
  exit /b 1
)

echo.
echo [3/4] Cleaning old build output...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist PaperLens.spec del /q PaperLens.spec

echo.
echo [4/4] Building PaperLens.exe...
python -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onefile ^
  --windowed ^
  --name PaperLens ^
  --add-data "static;static" ^
  main.py

if errorlevel 1 (
  echo Build failed.
  pause
  exit /b 1
)

echo.
echo Done. Output:
echo %cd%\dist\PaperLens.exe
echo.
pause
