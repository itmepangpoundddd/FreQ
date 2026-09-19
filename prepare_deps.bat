@echo off
REM ═══════════════════════════════════════════════════════════════
REM  FreQ — Download Dependencies
REM  Downloads FFmpeg and Python packages for installer bundling
REM ═══════════════════════════════════════════════════════════════

echo.
echo ========================================
echo   FreQ - Download Dependencies
echo ========================================
echo.

REM Create deps folder
if not exist "deps" mkdir deps
if not exist "deps\ffmpeg-essentials" mkdir deps\ffmpeg-essentials

REM ── FFmpeg ──
echo [1/3] Downloading FFmpeg...

if exist "deps\ffmpeg-essentials\ffmpeg.exe" (
    echo   [SKIP] FFmpeg already downloaded
) else (
    echo   Downloading from gyan.dev...
    powershell -Command "$ProgressPreference = 'SilentlyContinue'; Invoke-WebRequest -Uri 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' -OutFile 'deps\ffmpeg.zip'"
    if %errorlevel% neq 0 (
        echo   [ERROR] Failed to download FFmpeg
        echo   Manual download: https://www.gyan.dev/ffmpeg/builds/
    ) else (
        echo   Extracting...
        powershell -Command "$ProgressPreference = 'SilentlyContinue'; Expand-Archive -Path 'deps\ffmpeg.zip' -DestinationPath 'deps\ffmpeg-temp' -Force"
        REM Move bin files to ffmpeg-essentials
        for /d %%d in (deps\ffmpeg-temp\ffmpeg-*) do (
            xcopy /Y /E "%%d\bin\*" "deps\ffmpeg-essentials\" >nul 2>&1
        )
        REM Cleanup
        rmdir /S /Q deps\ffmpeg-temp >nul 2>&1
        del /Q deps\ffmpeg.zip >nul 2>&1
        echo   [OK] FFmpeg ready
    )
)

REM ── Python packages (wheel format for offline install) ──
echo.
echo [2/3] Downloading Python packages...

pip download -r requirements.txt -d "deps\packages" --only-binary=:all: --python-version 3.14 --platform win_amd64 2>nul
if %errorlevel% neq 0 (
    echo   [WARN] Some packages may not be available as wheels
    echo   Trying without platform restriction...
    pip download -r requirements.txt -d "deps\packages" --only-binary=:all: 2>nul
)

echo   [OK] Packages downloaded to deps\packages\

REM ── Create requirements.txt for offline install ──
echo.
echo [3/3] Creating install script...

(
echo @echo off
echo REM ═══════════════════════════════════════════════════════════════
echo REM  FreQ - Offline Dependency Installer
echo REM  Run this after installing FreQ if streaming features are needed
echo REM ═══════════════════════════════════════════════════════════════
echo.
echo echo Installing Python packages for streaming...
echo.
echo REM Check if pip is available
echo where pip ^>nul 2^>^&1
echo if %%errorlevel%% neq 0 ^(
echo     echo [ERROR] pip not found. Please install Python first.
echo     echo Download: https://www.python.org/downloads/
echo     pause
echo     exit /b 1
echo ^)
echo.
echo REM Install from bundled packages
echo if exist "packages" ^(
echo     echo Installing from local packages...
echo     pip install --no-index --find-links=packages -r requirements.txt
echo ^) else ^(
echo     echo Installing from PyPI...
echo     pip install -r requirements.txt
echo ^)
echo.
echo echo.
echo echo ========================================
echo echo   Installation Complete!
echo echo ========================================
echo echo.
echo echo Run FreQ.exe to start.
echo pause
) > deps\install_deps.bat

echo   [OK] Created deps\install_deps.bat

REM ── Summary ──
echo.
echo ========================================
echo   DONE!
echo ========================================
echo.
echo   Output:
if exist "deps\ffmpeg-essentials\ffmpeg.exe" (
    echo     [OK] FFmpeg - deps\ffmpeg-essentials\
) else (
    echo     [!!] FFmpeg - NOT downloaded
)
echo     [OK] Packages - deps\packages\
echo     [OK] Installer - deps\install_deps.bat
echo.
echo   Next: Run build_installer.bat
echo.
pause
