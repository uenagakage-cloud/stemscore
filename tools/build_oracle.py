"""Oracle Cloud の VM 作成時に貼り付ける初期化スクリプトを作る (管理者キーを新しく生成して埋め込む)。

    .venv\Scripts\python tools\build_oracle.py

dist/oracle-cloud-init.sh ができる。管理者キーの控えは data/oracle_admin_key.txt。
"""
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = "https://github.com/uenagakage-cloud/stemscore.git"


def main():
    keyfile = ROOT / "data" / "oracle_admin_key.txt"
    keyfile.parent.mkdir(exist_ok=True)
    key = keyfile.read_text(encoding="utf-8").strip() if keyfile.exists() else secrets.token_urlsafe(12)
    keyfile.write_text(key + "\n", encoding="utf-8")
    tpl = (ROOT / "deploy" / "oracle" / "cloud-init.sh.template").read_text(encoding="utf-8")
    out = ROOT / "dist" / "oracle-cloud-init.sh"
    out.parent.mkdir(exist_ok=True)
    out.write_bytes(tpl.replace("__ADMIN_KEY__", key).replace("__REPO__", REPO).encode("utf-8"))
    print(f"作成: {out}\n管理者キーの控え: {keyfile}")


if __name__ == "__main__":
    main()
