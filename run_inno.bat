@echo off
cd /d "C:\Users\SocieticsTv\Documents\SFM\SFM"
echo Current dir: %CD%
python create_wizard_images.py
if errorlevel 1 (
    echo Failed to create installer wizard images.
    exit /b 1
)
echo Starting Inno Setup compilation...
"C:\Program Files\Inno Setup 6\ISCC.exe" "C:\Users\SocieticsTv\Documents\SFM\SFM\installer.iss"
echo.
echo Exit code: %errorlevel%
pause
