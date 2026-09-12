@echo off
REM ImageSelector.exe 빌드 — ui.html 을 고친 뒤에는 반드시 다시 실행하세요.
cd /d "%~dp0"
echo [1/2] 아이콘 생성
python make_icon.py || goto :err
echo [2/2] exe 빌드
python -m PyInstaller --onefile --name ImageSelector --icon icon.ico --add-data "ui.html;." --clean --noconfirm server.py || goto :err
echo.
echo 완료 : %~dp0dist\ImageSelector.exe
pause
exit /b 0

:err
echo.
echo [!] 빌드 실패. PyInstaller 와 Pillow 가 설치되어 있는지 확인하세요:
echo     pip install pyinstaller pillow
pause
exit /b 1
