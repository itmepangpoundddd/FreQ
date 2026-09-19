@echo off
setlocal
REM Usage: build_native_meter.bat [module_stem]   (default: native_audio_meter)
set "STEM=%~1"
if "%STEM%"=="" set "STEM=native_audio_meter"

call "C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Auxiliary\Build\vcvarsall.bat" x64
if errorlevel 1 exit /b 1

cl.exe /nologo /std:c++17 /EHsc /O2 /LD /I"C:\Python314\Include" "%STEM%.cpp" /Fe"%STEM%.cp314-win_amd64.pyd" /link /DLL "C:\Python314\libs\python314.lib" ole32.lib
if errorlevel 1 exit /b 1
echo Built %STEM%.cp314-win_amd64.pyd
