"""クラウド (Google Colab) 用の配布物を作る。

    .venv\\Scripts\\python tools\\build_cloud.py

dist/ に次の 2 つができる。どちらも Google ドライブの「StemScore」フォルダに置く。
- StemScore_cloud.zip       アプリ本体
- StemScore_クラウド起動.ipynb  Colab で開いて ▶ を押すと StemScore が起動するノートブック
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"

LAUNCH_CODE = r'''#@title ▶ StemScore を起動（初回は 3〜5 分、2 回目以降は 1〜2 分）
import os, sys, time, shutil, subprocess, urllib.request
from google.colab import drive
from google.colab.output import eval_js
from IPython.display import display, HTML

try:
    drive.mount('/content/drive')
except ValueError:
    raise SystemExit('Google ドライブに接続できませんでした。許可の画面（別ウィンドウ）が出たら最後まで進めてください。'
                     'ポップアップがブロックされている場合は許可してから、もう一度 ▶ を押してください')
ROOT = '/content/drive/MyDrive/StemScore'   # アプリ本体と処理結果
INBOX = '/content/drive/MyDrive/曲'          # 曲を入れるフォルダ (画面の「Drive」タブに表示)
APP = '/content/stemscore'
os.makedirs(ROOT + '/data', exist_ok=True)
os.makedirs(INBOX, exist_ok=True)

def sh(cmd):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if r.returncode:
        print(r.stdout[-3000:], r.stderr[-3000:])
        raise SystemExit('セットアップに失敗しました。もう一度実行してください')

zip_path = ROOT + '/StemScore_cloud.zip'
if not os.path.exists(zip_path):
    raise SystemExit('Google ドライブの「StemScore」フォルダに StemScore_cloud.zip を置いてから実行してください')
shutil.rmtree(APP, ignore_errors=True)
sh(f'unzip -q -o "{zip_path}" -d {APP}')

if not os.path.exists('/content/.stemscore_deps'):
    print('ライブラリをインストール中…（このランタイムで初回のみ）')
    sh(f'pip install -q -r {APP}/requirements-colab.txt')
    sh('pip install -q --no-deps basic-pitch==0.4.0')
    open('/content/.stemscore_deps', 'w').close()

# 分離モデルは Google ドライブに保存して、次回からダウンロードを省く
os.makedirs(ROOT + '/models', exist_ok=True)
sh('pkill -f "app.server" || true')
env = dict(os.environ, STEMSCORE_CLOUD='1', STEMSCORE_DATA=ROOT + '/data', STEMSCORE_INBOX=INBOX,
           TORCH_HOME=ROOT + '/models', PYTHONUNBUFFERED='1')
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
key = open(ROOT + '/data/access_key.txt', encoding='utf-8').read().strip()
# Colab 標準のポート公開機能 (このブラウザでログイン中の Google アカウントだけが開ける URL)
url = eval_js('google.colab.kernel.proxyPort(8765)').rstrip('/')
link = f'{url}/?key={key}'
display(HTML(f"""
<div style="font:16px/1.6 sans-serif;padding:16px;border-radius:12px;background:#6c5ce7;color:#fff;max-width:560px">
  <div style="font-size:20px;font-weight:700">🎵 StemScore が起動しました</div>
  <a href="{link}" target="_blank" style="display:inline-block;margin:12px 0;padding:12px 20px;background:#fff;
     color:#6c5ce7;border-radius:10px;font-weight:700;text-decoration:none">StemScore を開く ↗</a>
  <div style="font-size:13px;opacity:.9">
    処理: {'GPU (' + gpu + ')' if gpu else 'CPU（GPU が使えないため時間がかかります）'}<br>
    このタブ（Colab）は開いたままにしてください。閉じたりしばらく操作しないと停止します。<br>
    曲はドライブの「曲」フォルダに入れて、StemScore の「Drive」タブから選べます。
  </div>
</div>"""))
'''

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

- このタブは開いたままにしてください（閉じたり長時間操作しないと Colab が停止します。処理結果はドライブに残ります）
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


def build_zip(path: Path) -> int:
    n = 0
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in sorted((ROOT / "app").rglob("*")):
            if f.is_file() and "__pycache__" not in f.parts:
                z.write(f, f.relative_to(ROOT).as_posix())
                n += 1
        z.write(ROOT / "requirements-colab.txt", "requirements-colab.txt")
    return n + 1


def main():
    DIST.mkdir(exist_ok=True)
    zp = DIST / "StemScore_cloud.zip"
    n = build_zip(zp)
    nb = DIST / "StemScore_クラウド起動.ipynb"
    nb.write_text(json.dumps(build_notebook(), ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{zp} ({n} files, {zp.stat().st_size // 1024} KB)")
    print(nb)


if __name__ == "__main__":
    main()
