"""音源の取得と WAV への変換。

- ローカルファイル: そのまま ffmpeg で 44.1kHz ステレオ WAV に変換
- YouTube などの URL: yt-dlp で音声のみ取得
- Spotify のトラック URL: Spotify は DRM 保護のため音声そのものは取得しない。
  ページのメタデータ (曲名・アーティスト) だけを読み取り、YouTube 検索で同じ曲を探して取得する。
"""
from __future__ import annotations

import html
import re
import subprocess
from pathlib import Path

import imageio_ffmpeg
import requests

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
SPOTIFY_RE = re.compile(r"https?://open\.spotify\.com/(?:intl-[a-z]+/)?track/([A-Za-z0-9]+)")


class SourceError(Exception):
    pass


def to_wav(src: Path, dst: Path, start: float | None = None, end: float | None = None) -> None:
    """任意の音声/動画ファイルを 44.1kHz / 16bit / ステレオ WAV に変換 (区間指定可)。"""
    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error"]
    if start:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", str(src)]
    if end and end > (start or 0):
        cmd += ["-t", f"{end - (start or 0):.3f}"]
    cmd += ["-vn", "-ac", "2", "-ar", "44100", "-sample_fmt", "s16", str(dst)]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0 or not dst.exists():
        raise SourceError(f"音声の変換に失敗しました: {proc.stderr.strip()[-500:]}")


def spotify_query(url: str) -> tuple[str, str]:
    """Spotify トラックページから (検索クエリ, 表示タイトル) を得る。"""
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    r.raise_for_status()
    page = r.text

    def meta(prop: str) -> str | None:
        m = re.search(rf'<meta[^>]+(?:property|name)="{prop}"[^>]+content="([^"]*)"', page)
        return html.unescape(m.group(1)) if m else None

    title = meta("og:title")
    desc = meta("og:description") or ""  # 例: "Artist · Album · Song · 2020"
    artist = desc.split("·")[0].strip() if "·" in desc else ""
    if not title:
        try:
            title = requests.get("https://open.spotify.com/oembed", params={"url": url}, timeout=15).json()["title"]
        except Exception as e:  # noqa: BLE001
            raise SourceError("Spotify から曲情報を取得できませんでした") from e
    display = f"{artist} - {title}" if artist else title
    return f"{display} audio", display


DRIVE_RE = re.compile(
    r"https?://(?:drive|docs)\.google\.com/(?:file/d/|open\?id=|uc\?(?:[^#]*&)?id=)([\w-]{20,})")


def download_drive(url: str, workdir: Path, log) -> tuple[Path, str]:
    """Google Drive の共有リンク (リンクを知っている全員が閲覧可) からファイルを取得する。

    YouTube に接続できないネットワークでも、スマホで録画/保存したファイルを Drive 経由で受け取れる。
    """
    file_id = DRIVE_RE.match(url).group(1)
    log(f"Google Drive からダウンロード (ID: {file_id})")
    dest, name = drive_fetch(file_id, workdir, log, stem="download")
    return dest, Path(name).stem


def drive_fetch(file_id: str, workdir: Path, log, stem: str | None = None) -> tuple[Path, str]:
    """Drive のファイルを workdir に保存し (パス, 元のファイル名) を返す。"""
    r = requests.get("https://drive.usercontent.google.com/download",
                     params={"id": file_id, "export": "download", "confirm": "t"},
                     stream=True, timeout=30)
    ctype = r.headers.get("Content-Type", "")
    if r.status_code != 200 or ctype.startswith("text/html"):
        r.close()
        raise SourceError("Google Drive のファイルを取得できませんでした。Drive でファイルの共有設定を"
                          "「リンクを知っている全員」(閲覧者) にしてから、もう一度試してください。")
    name = "drive_file"
    m = re.search(r"filename\*=UTF-8''([^;]+)|filename=\"([^\"]+)\"", r.headers.get("Content-Disposition", ""))
    if m:
        from urllib.parse import unquote
        name = unquote(m.group(1) or m.group(2))
    ext = Path(name).suffix.lower() or ".bin"
    dest = workdir / (f"{stem}{ext}" if stem else re.sub(r"[^\w.-]", "_", name))
    total = int(r.headers.get("Content-Length") or 0)
    done, next_log = 0, 50 << 20
    with open(dest, "wb") as f:
        for chunk in r.iter_content(1 << 20):
            f.write(chunk)
            done += len(chunk)
            if done >= next_log:
                log(f"  {done >> 20} MB" + (f" / {total >> 20} MB" if total else ""))
                next_log += 50 << 20
    log(f"ダウンロード完了: {name} ({done >> 20} MB)")
    return dest, name


def download_url(url: str, workdir: Path, log) -> tuple[Path, str]:
    """URL から音声を取得し (ファイルパス, タイトル) を返す。"""
    if DRIVE_RE.match(url):
        return download_drive(url, workdir, log)
    import yt_dlp

    target = url
    title_hint = None
    if SPOTIFY_RE.match(url):
        query, title_hint = spotify_query(url)
        log(f"Spotify の曲情報: {title_hint} → YouTube で検索します")
        target = f"ytsearch1:{query}"

    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(workdir / "download.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "ffmpeg_location": FFMPEG,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(target, download=True)
    except Exception as e:  # noqa: BLE001
        raise SourceError(f"ダウンロードに失敗しました: {e}") from e
    if info and "entries" in info:
        entries = [e for e in info["entries"] if e]
        if not entries:
            raise SourceError("検索結果が見つかりませんでした")
        info = entries[0]
    files = [p for p in workdir.glob("download.*") if p.suffix not in (".part", ".ytdl")]
    if not files:
        raise SourceError("ダウンロードしたファイルが見つかりません")
    title = title_hint or info.get("title") or "untitled"
    if title_hint and info.get("title"):
        log(f"取得元: {info.get('title')} ({info.get('webpage_url', '')})")
    return files[0], title


def encode_aac(src: Path, dst: Path, bitrate: str = "160k") -> None:
    """再生用に AAC (.m4a) へ圧縮。"""
    tmp = dst.with_suffix(".tmp.m4a")
    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-vn",
           "-c:a", "aac", "-b:a", bitrate, "-movflags", "+faststart", str(tmp)]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise SourceError(f"AAC への変換に失敗しました: {proc.stderr.strip()[-300:]}")
    tmp.replace(dst)


def configure_proxy(log=print) -> None:
    """Windows のプロキシ設定 (自動構成スクリプト/PAC 利用時を含む) を Python の通信に反映する。

    Python は PAC を解釈できないため、環境変数が未設定で、レジストリのプロキシサーバーに
    実際に接続できる (= そのネットワーク内にいる) 場合だけ、それを HTTP(S)_PROXY として使う。
    自宅など別のネットワークでは接続できないので、従来どおり直接接続になる。
    """
    import os
    import socket
    import sys

    if sys.platform != "win32" or os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy"):
        return
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings") as k:
            server = winreg.QueryValueEx(k, "ProxyServer")[0]
    except OSError:
        return
    if not server:
        return
    # "http=host:port;https=host:port" 形式にも対応
    if "=" in server:
        parts = dict(p.split("=", 1) for p in server.split(";") if "=" in p)
        server = parts.get("https") or parts.get("http") or ""
    server = server.replace("http://", "").strip()
    host, _, port = server.partition(":")
    if not host:
        return
    try:
        socket.create_connection((host, int(port or 8080)), timeout=2).close()
    except OSError:
        return
    url = f"http://{host}:{port or 8080}"
    for k in ("HTTPS_PROXY", "HTTP_PROXY"):
        os.environ[k] = url
    os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1,::1,.local")
    log(f"  プロキシを使用します: {url}")
