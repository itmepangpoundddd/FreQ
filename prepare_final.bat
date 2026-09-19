@echo off
REM ═══════════════════════════════════════════════════════════════
REM  FreQ — Prepare Final Distribution
REM  Copies all files needed for distribution to _final folder
REM ═══════════════════════════════════════════════════════════════

echo.
echo ========================================
echo   FreQ - Prepare Distribution Files
echo ========================================
echo.

REM ── Step 1: Build app if not exists ──
if not exist "dist\FreQ\FreQ.exe" (
    echo [1/4] Building FreQ app...
    python -X utf8 build.py --clean
    if %errorlevel% neq 0 (
        echo [ERROR] Build failed!
        pause
        exit /b 1
    )
) else (
    echo [1/4] FreQ.exe already built
)

REM ── Step 2: Create _final folder ──
echo.
echo [2/4] Creating _final distribution folder...

if exist "_final" rmdir /S /Q "_final"
mkdir _final
mkdir _final\portable
mkdir _final\portable\FreQ

REM ── Step 3: Copy portable version ──
echo.
echo [3/4] Copying files...

REM Copy app
xcopy /E /I /Y "dist\FreQ\*" "_final\portable\FreQ\" >nul 2>&1

REM Copy docs
copy /Y "LICENSE" "_final\portable\FreQ\" >nul 2>&1
copy /Y "COMMERCIAL_LICENSE.md" "_final\portable\FreQ\" >nul 2>&1
copy /Y "PRICING.md" "_final\portable\FreQ\" >nul 2>&1
copy /Y "README.md" "_final\portable\FreQ\" >nul 2>&1

REM Copy logos
copy /Y "logo.ico" "_final\portable\FreQ\" >nul 2>&1
copy /Y "logo.png" "_final\portable\FreQ\" >nul 2>&1
copy /Y "logo.svg" "_final\portable\FreQ\" >nul 2>&1

REM Copy ffmpeg if available
if exist "deps\ffmpeg-essentials\ffmpeg.exe" (
    mkdir "_final\portable\FreQ\ffmpeg"
    xcopy /E /I /Y "deps\ffmpeg-essentials\*" "_final\portable\FreQ\ffmpeg\" >nul 2>&1
    echo   [OK] FFmpeg bundled
)

REM ── Step 4: Create installer (if Inno Setup available) ──
echo.
echo [4/4] Building installer...

set "ISCC="
if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" (
    set "ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
) else if exist "C:\Program Files\Inno Setup 6\ISCC.exe" (
    set "ISCC=C:\Program Files\Inno Setup 6\ISCC.exe"
) else (
    where ISCC.exe >nul 2>&1
    if %errorlevel% equ 0 set "ISCC=ISCC.exe"
)

if not "%ISCC%"=="" (
    "%ISCC%" installer.iss
    if %errorlevel% equ 0 (
        echo   [OK] Installer built
    ) else (
        echo   [WARN] Installer build failed
    )
) else (
    echo   [SKIP] Inno Setup not found — installer not built
    echo   Download: https://jrsoftware.org/isdl.php
)

REM ── Create README for distribution ──
(
echo # FreQ - Radio Playlist Manager v2.5.47
echo.
echo ## Quick Start
echo.
echo ### Portable Version
echo 1. Open the `portable\FreQ` folder
echo 2. Run `FreQ.exe`
echo 3. No installation required!
echo.
echo ### Installer Version
echo 1. Run `FreQ-Setup-2.5.47.exe`
echo 2. Follow the setup wizard
echo 3. Launch from Desktop or Start Menu
echo.
echo ## Requirements
echo - Windows 10 or later
echo - FFmpeg (included or install separately for streaming)
echo.
echo ## Links
echo - GitHub: https://github.com/SocieticsTv/freq
echo - License: GPL-3.0 (free for personal use)
echo - Commercial License: See COMMERCIAL_LICENSE.md
echo.
echo ## Support
echo - Issues: https://github.com/SocieticsTv/freq/issues
echo - Email: contact@societicstv.com
) > "_final\README.txt"

REM ── Summary ──
echo.
echo ========================================
echo   DISTRIBUTION READY!
echo ========================================
echo.
echo   Output folder: _final\
echo.
if exist "_final\portable\FreQ\FreQ.exe" (
    echo   [OK] Portable: _final\portable\FreQ\
)
if exist "_final\FreQ-Setup-2.5.47.exe" (
    echo   [OK] Installer: _final\FreQ-Setup-2.5.47.exe
)
echo   [OK] README: _final\README.txt
echo.

REM Calculate sizes
for /f "tokens=3" %%a in ('dir "_final\portable\FreQ" /s /-c 2^>nul ^| findstr /C:"File(s)"') do set "PSIZE=%%a"
echo   Portable size: ~%PSIZE:~0,-6% MB
echo.

echo   Distribution files:
dir /s /b "_final" 2>nul | find /c /v "" && echo   total files
echo.

pause
