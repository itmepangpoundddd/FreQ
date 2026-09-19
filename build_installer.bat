@echo off
pushd "%~dp0"
python create_wizard_images.py
if errorlevel 1 (
    echo Failed to create installer wizard images.
    popd
    exit /b 1
)
"C:\Program Files\Inno Setup 6\ISCC.exe" "C:\Users\SocieticsTv\Documents\SFM\SFM\installer.iss"
echo Exit code: %errorlevel%
popd
