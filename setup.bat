@echo off
chcp 65001 >nul
cd /d %~dp0
echo [StemScore] Python 仮想環境を作成して必要なライブラリをインストールします (数GB・数分かかります)
py -3.11 -m venv .venv || python -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pip install --no-deps basic-pitch
echo.
echo セットアップ完了。start.bat で起動します。
pause
