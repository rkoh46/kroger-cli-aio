@echo off
REM Windows build script for kroger-aio.exe
REM Prerequisites: Python 3.11+, pip, Git installed on Windows
REM
REM This script:
REM 1. Installs dependencies
REM 2. Builds the Windows executable with PyInstaller
REM 3. Packages it for distribution
REM
REM Run this on a Windows machine with Administrator privileges.

echo === Building kroger-aio for Windows ===
echo.

REM Install uv (Python package manager)
echo Installing uv...
curl -LsSf https://astral.sh/uv/install.sh | sh 2>&1
echo.

REM Install Python dependencies
echo Installing Python dependencies...
uv sync
echo.

REM Install PyInstaller
echo Installing PyInstaller...
uv add --dev pyinstaller
echo.

REM Build the executable
echo Building Windows executable...
uv run pyinstaller kroger-aio.spec --clean --noconfirm
echo.

REM Check the result
if exist "dist\kroger-aio.exe" (
    echo === Build successful! ===
    echo Executable: dist\kroger-aio.exe
    echo Directory bundle: dist\kroger-aio-win\
    echo.
) else (
    echo === Build failed ===
    echo Check dist\ and build\ directories for errors.
    pause
)

REM Cleanup build artifacts
echo Cleaning up...
rd /s /q build
echo.

echo Done!
pause
