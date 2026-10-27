"""クラウド (Google Colab) で StemScore を起動するノートブックを作る。

    .venv\\Scripts\\python tools\\build_cloud.py

dist/StemScore_クラウド起動.ipynb ができる。Google ドライブに置いて Colab で開き、▶ を押すと
GitHub から最新のコードを取得して StemScore を起動する (アプリ本体の zip を置く必要はない)。

Google ドライブとの接続は 2 通り:
- マウント方式 (通常): 処理結果をドライブの「StemScore/data」に保存するので、次回も残る
- API 方式 (マウントできないブラウザ向けの予備): 「曲」フォルダの曲は使えるが、処理結果は Colab を止めると消える
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
REPO = "https://github.com/uenagakage-cloud/stemscore.git"

LAUNCH_CODE = r'''#@title ▶ StemScore を起動（初回は 3〜5 分、2 回目以降は 1〜2 分）
import os, sys, time, shutil, subprocess, threading, urllib.request
from google.colab.output import eval_js
from IPython.display import display, HTML

REPO = "__REPO__"
APP = '/content/stemscore'
INBOX_NAME = '曲'          # 曲を入れるドライブのフォルダ (画面の「Drive」タブに表示)

def sh(cmd):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if r.returncode:
        print(r.stdout[-3000:], r.stderr[-3000:])
        raise SystemExit('セットアップに失敗しました。もう一度 ▶ を押してください')

# ── 1. Google ドライブに接続 (マウントできなければ API 方式に切り替え) ──
MODE = 'mount'
try:
    from google.colab import drive
    drive.mount('/content/drive')
    ROOT = '/content/drive/MyDrive/StemScore'
    DATA = ROOT + '/data'
    INBOX = f'/content/drive/MyDrive/{INBOX_NAME}'
    TORCH_HOME = ROOT + '/models'
except Exception as e:
    print('ドライブのマウントに失敗したため、API 方式で接続します:', str(e)[:120])
    MODE = 'api'
    from google.colab import auth
    auth.authenticate_user()
    DATA = '/content/stemscore_data'
    INBOX = '/content/inbox'
    TORCH_HOME = '/content/torch'
os.makedirs(DATA, exist_ok=True)
os.makedirs(INBOX, exist_ok=True)

if MODE == 'api':
    # 「曲」フォルダの音声・動画を定期的に /content/inbox へコピーする
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload
    gdrive = build('drive', 'v3')
    EXT = ('.mp3', '.wav', '.flac', '.m4a', '.aac', '.ogg', '.opus', '.mp4', '.mov', '.mkv', '.webm')

    def inbox_folder_id():
        q = f"name = '{INBOX_NAME}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        res = gdrive.files().list(q=q, fields='files(id)', pageSize=1).execute().get('files', [])
        if res:
            return res[0]['id']
        return gdrive.files().create(body={'name': INBOX_NAME, 'mimeType': 'application/vnd.google-apps.folder'},
                                     fields='id').execute()['id']

    def sync_inbox():
        fid = inbox_folder_id()
        while True:
            try:
                files = gdrive.files().list(q=f"'{fid}' in parents and trashed = false",
                                            fields='files(id,name,size)', pageSize=200).execute().get('files', [])
                for f in files:
                    dst = os.path.join(INBOX, f['name'])
                    if not f['name'].lower().endswith(EXT) or (os.path.exists(dst) and os.path.getsize(dst) == int(f.get('size') or 0)):
                        continue
                    tmp = dst + '.part'
                    with open(tmp, 'wb') as fh:
                        dl = MediaIoBaseDownload(fh, gdrive.files().get_media(fileId=f['id']), chunksize=32 << 20)
                        done = False
                        while not done:
                            _, done = dl.next_chunk()
                    os.replace(tmp, dst)
            except Exception as e:
                print('「曲」フォルダの同期でエラー:', e)
            time.sleep(30)

    threading.Thread(target=sync_inbox, daemon=True).start()

# ── 2. GitHub から最新のコードを取得 ──
shutil.rmtree(APP, ignore_errors=True)
sh(f'git clone --depth 1 -q {REPO} {APP}')

# ── 3. ライブラリのインストール (このランタイムで初回のみ) ──
if not os.path.exists('/content/.stemscore_deps'):
    print('ライブラリをインストール中…（このランタイムで初回のみ、2〜3 分）')
    sh(f'pip install -q -r {APP}/requirements-colab.txt')
    sh('pip install -q --no-deps basic-pitch==0.4.0')
    open('/content/.stemscore_deps', 'w').close()

# ── 4. StemScore を起動 ──
os.makedirs(TORCH_HOME, exist_ok=True)
sh('pkill -f "app.server" || true')
env = dict(os.environ, STEMSCORE_CLOUD='1', STEMSCORE_DATA=DATA, STEMSCORE_INBOX=INBOX,
           TORCH_HOME=TORCH_HOME, PYTHONUNBUFFERED='1')
server = subprocess.Popen([sys.executable, '-m', 'app.server', '--local', '--no-browser'], cwd=APP, env=env,
                          stdout=open('/content/stemscore.log', 'w'), stderr=subprocess.STDOUT)
for _ in range(180):
    if server.poll() is not None:
        print(open('/content/stemscore.log').read()[-3000:])
        raise SystemExit('StemScore の起動に失敗しました')
    try:
        urllib.request.urlopen('http://127.0.0.1:8765/manifest.webmanifest', timeout=2)
        break
    except Exception:
        time.sleep(1)

import torch
gpu = torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
key = open(DATA + '/access_key.txt', encoding='utf-8').read().strip()
# Colab 標準のポート公開機能 (このブラウザでログイン中の Google アカウントだけが開ける URL)
url = eval_js('google.colab.kernel.proxyPort(8765)').rstrip('/')
link = f'{url}/?key={key}'
note = ('処理結果はドライブの「StemScore/data」に保存されます。' if MODE == 'mount' else
        '⚠ API 方式で接続しています。処理結果は Colab を止めると消えるので、必要なものはダウンロードしてください。')
display(HTML(f"""
<div style="font:16px/1.6 sans-serif;padding:16px;border-radius:12px;background:#6c5ce7;color:#fff;max-width:560px">
  <div style="font-size:20px;font-weight:700">🎵 StemScore が起動しました</div>
  <a href="{link}" target="_blank" style="display:inline-block;margin:12px 0;padding:12px 20px;background:#fff;
     color:#6c5ce7;border-radius:10px;font-weight:700;text-decoration:none">StemScore を開く ↗</a>
  <div style="font-size:13px;opacity:.9">
    処理: {'GPU (' + gpu + ')' if gpu else 'CPU（GPU が使えないため時間がかかります）'}<br>
    このタブ（Colab）は開いたままにしてください。閉じたりしばらく操作しないと停止します。<br>
    曲はドライブの「{INBOX_NAME}」フォルダに入れて、StemScore の「Drive」タブから選べます。<br>
    {note}
  </div>
</div>"""))
'''.replace("__REPO__", REPO)

# 「すべてのセルを実行」で起動直後に止まらないよう、チェックを入れたときだけ停止する
STOP_CODE = r'''#@title ■ 停止（使い終わったら、チェックを入れてこのセルの ▶ を押す）
停止する = False  #@param {type:"boolean"}
if 停止する:
    import subprocess
    subprocess.run('pkill -f "app.server"', shell=True)
    from google.colab import runtime
    runtime.unassign()  # ランタイムを解放して Colab の利用枠を節約
else:
    print('停止するには「停止する」にチェックを入れてから ▶ を押してください')
'''

INTRO = """# StemScore（クラウド版）

1. 上のメニュー **ランタイム → ランタイムのタイプを変更 → T4 GPU** になっていることを確認（初回のみ）
2. 下の **▶ StemScore を起動** を押す（Google ドライブへのアクセス許可を求められたら許可）
3. 表示される **「StemScore を開く」** を押す

- 毎回 GitHub から最新版を取得するので、アプリの更新作業は不要です
- このタブは開いたままにしてください（閉じたり長時間操作しないと Colab が停止します）
- 曲はスマホなどから Google ドライブの **「曲」** フォルダに入れると、StemScore の「Drive」タブから選べます
- 処理結果はドライブの「StemScore/data」に保存されます
"""


def build_notebook() -> dict:
    def cell(kind, src, **extra):
        c = {"cell_type": kind, "metadata": extra.pop("metadata", {}), "source": src.splitlines(True)}
        if kind == "code":
            c.update(execution_count=None, outputs=[])
        return c

    return {
        "nbformat": 4, "nbformat_minor": 0,
        "metadata": {
            "colab": {"provenance": [], "name": "StemScore_クラウド起動.ipynb", "gpuType": "T4"},
            "kernelspec": {"name": "python3", "display_name": "Python 3"},
            "accelerator": "GPU",
        },
        "cells": [
            cell("markdown", INTRO),
            cell("code", LAUNCH_CODE, metadata={"cellView": "form"}),
            cell("code", STOP_CODE, metadata={"cellView": "form"}),
        ],
    }


def main():
    DIST.mkdir(exist_ok=True)
    nb = DIST / "StemScore_クラウド起動.ipynb"
    nb.write_text(json.dumps(build_notebook(), ensure_ascii=False), encoding="utf-8")
    print(nb)


if __name__ == "__main__":
    main()
