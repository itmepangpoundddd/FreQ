@echo off
setlocal

:: Setup MSVC environment
call "C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Auxiliary\Build\vcvarsall.bat" x64

set PYINC=C:\Python314\Include
set PYLIB=C:\Python314\libs\python314.lib
set CLIB=C:\Users\SocieticsTv\Documents\SFM\SFM\.C
set OUTDIR=C:\Users\SocieticsTv\Documents\SFM\SFM\.C\pyd
set CC=cl.exe

if not exist "%OUTDIR%" mkdir "%OUTDIR%"

echo ========================================
echo  Compiling .c files to .pyd (MSVC x64)
echo ========================================
echo.

set OK=0
set FAIL=0

for %%f in ("%CLIB%\*.c") do (
    echo [%%~nf] Compiling...
    %CC% /nologo /LD /O2 ^
        /I"%PYINC%" ^
        "%%f" ^
        /Fe"%OUTDIR%\%%~nf.pyd" ^
        /link /DLL "%PYLIB%" >nul 2>&1
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

endlocal
