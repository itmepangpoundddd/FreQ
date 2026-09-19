@echo off
call "C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Auxiliary\Build\vcvarsall.bat" x64
if errorlevel 1 (
    echo FAILED to setup MSVC environment
    exit /b 1
)
echo MSVC environment ready
echo.
echo Testing compile of icons.c ...
cl.exe /nologo /LD /O2 /IC:\Python314\Include "C:\Users\SocieticsTv\Documents\SFM\SFM\.C\icons.c" /Fe"C:\Users\SocieticsTv\Documents\SFM\SFM\.C\pyd\icons.pyd" /link /DLL "C:\Python314\libs\python314.lib"
if errorlevel 1 (
    echo.
    echo COMPILE FAILED
) else (
    echo.
    echo COMPILE OK
)
