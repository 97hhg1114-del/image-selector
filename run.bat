@echo off
title Image Selector
cd /d "%~dp0"
python server.py
if errorlevel 1 (
  echo.
  echo [!] python 실행에 실패했습니다. Python 3 설치 여부를 확인해 주세요.
  pause
)
