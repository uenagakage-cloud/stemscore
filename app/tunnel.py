"""自宅の PC をスマホ (モバイル回線) から使うためのトンネル。

Cloudflare の Quick Tunnel (アカウント不要) で https://xxxx.trycloudflare.com の URL を発行する。
URL は起動のたびに変わる。アクセスにはこれまでどおりアクセスキーが必要。
学校など、プロキシのあるネットワークでは使わない (ネットワークの管理者のルールを優先する)。
"""
from __future__ import annotations

import os
import re
import subprocess
import threading
import urllib.request
from pathlib import Path

BIN_DIR = Path.home() / ".cache" / "stemscore" / "bin"
EXE = BIN_DIR / "cloudflared.exe"
# Cloudflare 公式の配布元 (GitHub Releases)
DOWNLOAD_URL = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

url: str | None = None


def behind_proxy() -> bool:
    return bool(os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy"))


def _ensure_exe(log) -> Path:
    if EXE.exists():
        return EXE
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    log("  cloudflared (Cloudflare 公式) を初回だけダウンロードします…")
    tmp = EXE.with_suffix(".part")
    urllib.request.urlretrieve(DOWNLOAD_URL, tmp)
    tmp.replace(EXE)
    return EXE


def start(port: int, on_ready, log=print) -> bool:
    """トンネルを起動し、URL が決まったら on_ready(url) を呼ぶ。起動しなかったら False。"""
    if behind_proxy():
        log("  学校などプロキシのあるネットワークなので、トンネル (外からの接続) は使いません。")
        return False
    try:
        exe = _ensure_exe(log)
    except Exception as e:  # noqa: BLE001
        log(f"  cloudflared を用意できませんでした: {e}")
        return False
    proc = subprocess.Popen(
        [str(exe), "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def watch():
        global url
        for line in proc.stdout:
            m = URL_RE.search(line)
            if m and url is None:
                url = m.group(0)
                on_ready(url)

    threading.Thread(target=watch, daemon=True).start()
    # 起動している間は Windows が自動でスリープしないようにする (このプロセスが終われば元に戻る)
    try:
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
    except Exception:  # noqa: BLE001
        pass
    import atexit
    atexit.register(proc.terminate)
    return True
