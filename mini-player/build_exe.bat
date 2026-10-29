@echo off
setlocal
cd /d "%~dp0"
title Construction de Mini Player

set "PY="
where py >nul 2>nul && set "PY=py"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
    echo Python est introuvable. Installe-le depuis python.org en cochant "Add to PATH".
    pause
    exit /b 1
)

echo [1/3] Installation des outils...
%PY% -m pip install --upgrade pyinstaller -r requirements.txt
if errorlevel 1 goto :fail

echo.
echo [2/3] Fermeture de l'ancien exe s'il tourne...
taskkill /f /im "Mini Player.exe" >nul 2>nul

echo.
echo [3/3] Creation de "Mini Player.exe" (1 a 2 minutes)...
%PY% -m PyInstaller --noconfirm --clean --onefile --windowed ^
    --name "Mini Player" --icon assets\mini_player.ico --add-data "assets\mini_player.ico;." ^
    --collect-all winrt --collect-all pycaw --collect-all comtypes ^
    --hidden-import winrt.windows.media.control ^
    --hidden-import winrt.windows.storage.streams ^
    --hidden-import winrt.windows.foundation ^
    mini_player.pyw
if errorlevel 1 goto :fail

copy /y "dist\Mini Player.exe" "Mini Player.exe" >nul
rmdir /s /q build >nul 2>nul
rmdir /s /q dist >nul 2>nul
del "Mini Player.spec" >nul 2>nul

echo.
echo Termine ! "Mini Player.exe" est dans ce dossier.
echo Il se lancera tout seul au demarrage de Windows.
start "" "Mini Player.exe"
pause
exit /b 0

:fail
echo.
echo La construction a echoue.
pause
exit /b 1
