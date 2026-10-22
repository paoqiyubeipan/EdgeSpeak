@echo off
rem Build dist\EdgeSpeak-Setup-<version>.exe from dist\EdgeSpeak (run build.cmd first). Needs Inno Setup 6:
rem   winget install JRSoftware.InnoSetup
rem Usage: build_installer.cmd [version] [lite]
rem   lite: leave out the dictionaries (~1.7 GB); the app downloads them on first run
cd /d "%~dp0"
if not exist dist\EdgeSpeak\EdgeSpeak.exe (
    echo dist\EdgeSpeak not found, run build.cmd first.
    exit /b 1
)
set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" (
    echo Inno Setup 6 not found: winget install JRSoftware.InnoSetup
    exit /b 1
)
set "VERSION=%~1"
if "%VERSION%"=="" set "VERSION=1.0.0"
set "LITE="
if /i "%~2"=="lite" set "LITE=/DLite"
rem Compile through a short junction: nested files under a long project path exceed the 260-character limit
set "SHORT=%TEMP%\EdgeSpeak-dist"
if exist "%SHORT%" rmdir "%SHORT%"
mklink /J "%SHORT%" "%~dp0dist\EdgeSpeak" >nul || exit /b 1
"%ISCC%" /Q /DAppVersion=%VERSION% %LITE% "/DDistDir=%SHORT%" installer\EdgeSpeak.iss
set "RESULT=%ERRORLEVEL%"
rmdir "%SHORT%"
exit /b %RESULT%
