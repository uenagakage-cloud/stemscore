@echo off
chcp 65001 >nul
cd /d %~dp0
if not exist .venv\Scripts\python.exe (
  echo 先に setup.bat を実行してください
  pause
  exit /b 1
)
echo [StemScore] 自宅用に起動します。スマホがモバイル回線でも使える URL と QR コードが下に表示されます
echo   ※ 学校のネットワークでは、外からの接続 (トンネル) は自動で使わないようになっています
echo   ※ 起動中は PC が自動でスリープしないようにします (ふたを閉じるとスリープすることがあるので、開いたままにしてください)
.venv\Scripts\python -m app.server --tunnel
pause
