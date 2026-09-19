@echo off
setlocal
call "C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Auxiliary\Build\vcvarsall.bat" x64
if errorlevel 1 exit /b 1

cl.exe /nologo /std:c++17 /EHsc /O2 /LD /I"C:\Python314\Include" "native_audio_meter.cpp" /Fe"native_audio_meter.cp314-win_amd64.pyd" /link /DLL "C:\Python314\libs\python314.lib"
if errorlevel 1 exit /b 1
echo Built native_audio_meter.cp314-win_amd64.pyd
