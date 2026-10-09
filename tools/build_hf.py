"""Hugging Face Spaces (無料の CPU: 2 vCPU / 16GB) 用のファイル一式を作り、必要ならアップロードする。

    .venv\\Scripts\\python tools\\build_hf.py              # dist/hf_space/ に作るだけ
    .venv\\Scripts\\python tools\\build_hf.py --upload <ユーザー名>/stemscore

--upload は `hf auth login` でログイン済みであることが前提。Space がなければ作成し、
管理者キー (STEMSCORE_ACCESS_KEY) が未設定なら自動生成して Space の Secret に登録する。
"""
from __future__ import annotations

import argparse
import secrets
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dist" / "hf_space"

README = """---
title: StemScore
emoji: 🎵
colorFrom: indigo
colorTo: purple
sdk: docker
app_port: 7860
pinned: false
short_description: 楽器別の音源分離と自動採譜
---

# StemScore

曲をボーカル・ドラム・ベースなどのパートに分け、パートごとの楽譜（音名つき）と MIDI を作るアプリです。

利用には招待リンク（アクセスキー）が必要です。招待された人は、このページではなく
`https://<ユーザー名>-stemscore.hf.space/?key=...` の形のリンクを開いてください。
"""

DOCKERFILE = """# Hugging Face Spaces 用 (Docker Space)。Spaces はユーザー ID 1000 で実行される
FROM python:3.11-slim

RUN useradd -m -u 1000 user
ENV HOME=/home/user \\
    PYTHONUNBUFFERED=1 \\
    PIP_NO_CACHE_DIR=1 \\
    PIP_DISABLE_PIP_VERSION_CHECK=1 \\
    TORCH_HOME=/home/user/torch \\
    STEMSCORE_CLOUD=1 \\
    STEMSCORE_DATA=/home/user/data \\
    STEMSCORE_KEEP_DAYS=7 \\
    STEMSCORE_MEMBER_MAX_ACTIVE=2

WORKDIR /home/user/app
COPY requirements.txt .
RUN pip install -r requirements.txt && pip install --no-deps basic-pitch==0.4.0

USER user
# 分離モデル (4パート標準・6パート) をイメージに含める。高品質モデルは初回使用時にダウンロード
RUN python -c "from demucs.pretrained import get_model; get_model('htdemucs'); get_model('htdemucs_6s')"
COPY --chown=user app ./app

EXPOSE 7860
CMD ["sh", "-c", "uvicorn app.server:app --host 0.0.0.0 --port 7860 --workers 1 --proxy-headers --forwarded-allow-ips='*' --log-level warning"]
"""


def build() -> Path:
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True)
    shutil.copytree(ROOT / "app", OUT / "app", ignore=shutil.ignore_patterns("__pycache__"))
    # ローカル用の truststore (社内プロキシ対策) は不要なので除く
    reqs = [line for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
            if line.strip() and line.strip() != "truststore"]
    (OUT / "requirements.txt").write_text("\n".join(reqs) + "\n", encoding="utf-8")
    (OUT / "Dockerfile").write_text(DOCKERFILE, encoding="utf-8")
    (OUT / "README.md").write_text(README, encoding="utf-8")
    return OUT


def upload(repo_id: str) -> None:
    from huggingface_hub import HfApi

    api = HfApi()
    api.create_repo(repo_id, repo_type="space", space_sdk="docker", private=False, exist_ok=True)
    # Secret は後から値を読み出せないので、控えのファイルがない (= 初回) ときだけ生成して登録する
    keyfile = ROOT / "data" / "hf_admin_key.txt"
    if not keyfile.exists() or keyfile.read_text(encoding="utf-8").split()[0] != repo_id:
        key = secrets.token_urlsafe(12)
        api.add_space_secret(repo_id, "STEMSCORE_ACCESS_KEY", key, description="StemScore の管理者キー")
        keyfile.parent.mkdir(exist_ok=True)
        keyfile.write_text(f"{repo_id}\n{key}\n", encoding="utf-8")
        print(f"管理者キーを生成して Space の Secret に登録しました (控え: {keyfile})")
    api.upload_folder(repo_id=repo_id, repo_type="space", folder_path=str(OUT),
                      commit_message="StemScore を更新", delete_patterns=["app/**"])
    sub = repo_id.replace("/", "-").replace("_", "-").lower()
    print(f"アップロードしました: https://huggingface.co/spaces/{repo_id}")
    print(f"アプリの URL: https://{sub}.hf.space")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--upload", metavar="USER/SPACE")
    args = ap.parse_args()
    out = build()
    print(f"作成: {out}")
    if args.upload:
        upload(args.upload)


if __name__ == "__main__":
    main()
