@echo off
rem Build dist\EdgeSpeak\EdgeSpeak.exe (folder mode: starts fast, no unpacking on each launch)
rem Dictionaries go to dist\EdgeSpeak\dict (copied, not packed); the app updates them in the background later.
cd /d "%~dp0"
if not exist .venv (
    python -m venv .venv || exit /b 1
    .venv\Scripts\python.exe -m pip install -r requirements.txt || exit /b 1
)
if not exist dict\manifest.json (
    .venv\Scripts\python.exe build_dict.py || exit /b 1
)
.venv\Scripts\python.exe -c "import comtypes.client; comtypes.client.GetModule('UIAutomationCore.dll')" || exit /b 1
.venv\Scripts\python.exe -c "import edge_speak; edge_speak.make_icon_image(256).save('edgespeak.ico', sizes=[(16,16),(32,32),(48,48),(256,256)])" || exit /b 1
.venv\Scripts\pyinstaller.exe --noconfirm --onedir --noconsole --name EdgeSpeak --icon edgespeak.ico ^
    --hidden-import pystray._win32 --hidden-import _cffi_backend ^
    --collect-all opencc --collect-all fugashi --collect-all unidic_lite ^
    --collect-submodules comtypes.gen --collect-all winrt --collect-all cohere --add-data "uk_english.json;." ^
    edge_speak.py || exit /b 1
robocopy dict dist\EdgeSpeak\dict /MIR /XD tmp-* /NFL /NDL /NJH /NP
if errorlevel 8 exit /b 1
exit /b 0
