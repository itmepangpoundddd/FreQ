@echo off
REM ═══════════════════════════════════════════════════════════════
REM  FreQ — Portable Build
REM  Creates a zip file for portable distribution
REM ═══════════════════════════════════════════════════════════════

echo.
echo ========================================
echo   FreQ - Portable Build
echo ========================================
echo.

REM Check if dist/FreQ exists
if not exist "dist\FreQ\FreQ.exe" (
    echo [ERROR] dist\FreQ\FreQ.exe not found!
    echo Build the app first: python build.py
    pause
    exit /b 1
)

echo [OK] Found FreQ.exe
echo.

REM Create portable zip
echo [BUILD] Creating portable zip...

REM Use PowerShell to create zip (available on all modern Windows)
powershell -Command "Compress-Archive -Path 'dist\FreQ\*' -DestinationPath 'FreQ-2.5.47-Portable.zip' -Force"

if %errorlevel% equ 0 (
    echo.
    echo ========================================
    echo   BUILD SUCCESSFUL!
    echo ========================================
    echo.
    echo   Output: FreQ-2.5.47-Portable.zip
    echo.
    echo   To use: Unzip and run FreQ.exe
    echo.
) else (
    echo [ERROR] Build failed!
)

pause
