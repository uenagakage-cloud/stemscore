"""実行環境ごとの設定 (環境変数で切り替え)。

- STEMSCORE_DATA:  データ (処理結果・アクセスキー) の保存先。Colab では Google ドライブ上のフォルダ
- STEMSCORE_CLOUD: "1" ならクラウド公開モード。トンネル経由のアクセスは 127.0.0.1 から来るため、
                   「この PC からはキー不要」の扱いを無効にして必ずアクセスキーを要求する
- STEMSCORE_INBOX: 曲を置くフォルダ (例: Google ドライブの「曲」)。画面の「Drive」タブから選んで処理できる
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("STEMSCORE_DATA") or ROOT / "data")
CLOUD = os.environ.get("STEMSCORE_CLOUD") == "1"
INBOX = Path(os.environ["STEMSCORE_INBOX"]) if os.environ.get("STEMSCORE_INBOX") else None
# 1 回にアップロードできる上限 (MB)。経由するプロキシに制限がある場合に設定する
UPLOAD_LIMIT_MB = int(os.environ["STEMSCORE_UPLOAD_LIMIT"]) if os.environ.get("STEMSCORE_UPLOAD_LIMIT") else None
# 指定した日数より古い曲を自動で削除する (クラウドの容量節約用。未指定なら削除しない)
KEEP_DAYS = float(os.environ["STEMSCORE_KEEP_DAYS"]) if os.environ.get("STEMSCORE_KEEP_DAYS") else None
# メンバー 1 人が同時に順番待ち・処理中にできる曲の数 (管理者は無制限)
MEMBER_MAX_ACTIVE = int(os.environ.get("STEMSCORE_MEMBER_MAX_ACTIVE") or 2)

MEDIA_EXT = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma", ".aiff", ".aif",
             ".mp4", ".mov", ".mkv", ".webm"}
