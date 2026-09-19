@echo off
call "C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Auxiliary\Build\vcvarsall.bat" x64

set SRCDIR=C:\Users\SocieticsTv\Documents\SFM\SFM\.C
set OUTDIR=C:\Users\SocieticsTv\Documents\SFM\SFM\.C\pyd

echo ========================================
echo  Compiling .c files to .pyd (MSVC x64)
echo ========================================
echo.

set OK=0
set FAIL=0

for %%f in ("%SRCDIR%\*.c") do (
    echo [%%~nf]
    cl.exe /nologo /LD /O2 /I"C:\Python314\Include" "%%f" /Fe"%OUTDIR%\%%~nf.pyd" /link /DLL "C:\Python314\libs\python314.lib" >nul 2>&1
    if errorlevel 1 (
        echo   FAIL
        set /a FAIL+=1
    ) else (
        echo   OK
        set /a OK+=1
    )
)

echo.
echo ========================================
echo  OK: %OK%  FAIL: %FAIL%
echo  Output: %OUTDIR%
echo ========================================
