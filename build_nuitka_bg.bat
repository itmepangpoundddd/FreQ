@echo off
call "C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Auxiliary\Build\vcvarsall.bat" x64

echo Starting Nuitka build at %TIME% ...
echo Log: build_nuitka.log

python -m nuitka ^
    --standalone ^
    --enable-plugin=tk-inter ^
    --include-package=customtkinter ^
    --include-package=pygame ^
    --include-package=flask ^
    --include-package=yt_dlp ^
    --include-package=werkzeug ^
    --assume-yes-for-downloads ^
    --include-package=numpy ^
    --include-package=darkdetect ^
    --include-package=packaging ^
    --include-module=mutagen ^
    --windows-console-mode=disable ^
    --output-dir=build_nuitka ^
    --output-filename=FreQ.exe ^
    --nofollow-import-to=tkinter.test ^
    --nofollow-import-to=unittest ^
    --nofollow-import-to=pytest ^
    --nofollow-import-to=nose ^
    --nofollow-import-to=IPython ^
    --nofollow-import-to=jupyter ^
    --nofollow-import-to=setuptools ^
    --nofollow-import-to=distutils ^
    --nofollow-import-to=lib2to3 ^
    --nofollow-import-to=ensurepip ^
    --nofollow-import-to=venv ^
    --nofollow-import-to=pip ^
    --nofollow-import-to=wheel ^
    --nofollow-import-to=docutils ^
    --nofollow-import-to=sphinx ^
    gui.py > build_nuitka.log 2>&1

echo.
echo Finished at %TIME%
if exist "build_nuitka\gui.dist\FreQ.exe" (
    echo BUILD SUCCESS!
    dir /s "build_nuitka\gui.dist\FreQ.exe"
) else (
    echo BUILD FAILED - check build_nuitka.log
)
