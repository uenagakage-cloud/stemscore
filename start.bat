@echo off
chcp 65001 >nul
cd /d %~dp0
if not exist .venv\Scripts\python.exe (
  echo 先に setup.bat を実行してください
  pause
  exit /b 1
)
echo [StemScore] 起動します。スマホからは画面右上の「スマホで開く」か、下に表示される QR コードを使ってください
.venv\Scripts\python -m app.server
