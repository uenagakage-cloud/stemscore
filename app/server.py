"""Web サーバー (FastAPI)。 起動: python -m app.server"""
from __future__ import annotations

import io
import json
import re
import threading
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import jobs, remote
from .separate import MODELS

STATIC = Path(__file__).resolve().parent / "static"
from .config import MEDIA_EXT as AUDIO_EXT  # noqa: E402

@asynccontextmanager
async def lifespan(_app):
    from .source import configure_proxy
    configure_proxy()
    remote.access_key()  # アクセスキーのファイルを用意 (クラウド起動用ノートブックが読み取る)
    try:
        # 社内/学内プロキシの証明書を Windows の証明書ストアから使う
        import truststore
        truststore.inject_into_ssl()
    except Exception:  # noqa: BLE001
        pass
    jobs.start_worker()
    yield


app = FastAPI(title="StemScore", lifespan=lifespan)
app.middleware("http")(remote.auth_middleware)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
PORT = 8765
_locks: dict[str, "threading.Lock"] = {}
_locks_guard = threading.Lock()


def _file_lock(key: str):
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())





@app.get("/", response_class=HTMLResponse)
def index():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    # 更新後に古い JS/CSS がキャッシュから使われないよう、更新時刻をバージョンとして付ける
    for name in ("app.js", "style.css"):
        v = int((STATIC / name).stat().st_mtime)
        html = html.replace(f'"/static/{name}"', f'"/static/{name}?v={v}"')
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


@app.get("/manifest.webmanifest")
def manifest():
    data = {
        "name": "StemScore", "short_name": "StemScore", "start_url": "/", "scope": "/",
        "display": "standalone", "background_color": "#121219", "theme_color": "#6c5ce7",
        "lang": "ja", "description": "楽器別の音源分離と自動採譜",
        "icons": [
            {"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
            {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }
    return Response(json.dumps(data, ensure_ascii=False), media_type="application/manifest+json")


@app.get("/sw.js")
def service_worker():
    # ホーム画面への追加 (PWA) を有効にするための最小限の Service Worker。処理はすべてサーバー側。
    js = "self.addEventListener('install',e=>self.skipWaiting());"          "self.addEventListener('activate',e=>self.clients.claim());"          "self.addEventListener('fetch',()=>{});"
    return Response(js, media_type="text/javascript", headers={"Cache-Control": "no-cache"})


@app.get("/api/connect")
def connect_info(request: Request):
    """この PC の画面にだけ、スマホ接続用の URL と QR コードを返す。"""
    if not remote.is_local(request):
        raise HTTPException(403)
    key = remote.access_key()
    urls = [f"http://{ip}:{PORT}/?key={key}" for ip in remote.lan_ips()]
    return {"key": key, "urls": urls, "qr": remote.qr_svg(urls[0]) if urls else None,
            "lan": getattr(app.state, "lan", False)}


@app.post("/api/connect/reset")
def connect_reset(request: Request):
    if not remote.is_local(request):
        raise HTTPException(403)
    remote.reset_key()
    return connect_info(request)


@app.get("/api/config")
def get_config():
    from . import config
    return {"cloud": config.CLOUD, "inbox": bool(config.INBOX), "upload_limit_mb": config.UPLOAD_LIMIT_MB}


@app.get("/api/inbox")
def inbox_list():
    """曲フォルダ (Google ドライブの「曲」など) にある音声・動画ファイルの一覧。"""
    from . import config
    if not config.INBOX:
        raise HTTPException(404)
    if not config.INBOX.exists():
        return []
    items = [{"name": f.name, "size": f.stat().st_size, "mtime": f.stat().st_mtime}
             for f in config.INBOX.iterdir() if f.is_file() and f.suffix.lower() in AUDIO_EXT]
    return sorted(items, key=lambda x: -x["mtime"])


@app.get("/api/models")
def models():
    from .separate import model_status
    return model_status()


_import_state = {"running": False, "log": [], "error": None}


def _import_worker(items: list[tuple[str, Path | None, str | None]]):
    """items: (種類, 一時ファイル, Drive ID)。モデルの重みを取り込む。"""
    import tempfile
    from .separate import install_weight
    from .source import drive_fetch

    st = _import_state
    log = lambda m: st["log"].append(m)  # noqa: E731
    try:
        with tempfile.TemporaryDirectory(dir=jobs.DATA.parent) as td:
            for kind, path, ref in items:
                if kind == "drive":
                    path, name = drive_fetch(ref, Path(td), log)
                else:
                    name = ref
                installed = install_weight(path, name)
                log(f"✔ {installed} を取り込みました")
        log("完了しました")
    except Exception as e:  # noqa: BLE001
        st["error"] = str(e)
        log(f"エラー: {e}")
    finally:
        st["running"] = False


@app.post("/api/models/import")
async def import_models(links: str = Form(""), files: list[UploadFile] | None = File(None)):
    """Drive の共有リンク (複数可) または .th ファイルのアップロードでモデルを取り込む。"""
    from .source import DRIVE_RE

    if _import_state["running"]:
        raise HTTPException(409, "取り込み中です")
    items = []
    for m in re.finditer(r"https?://\S+", links):
        dm = DRIVE_RE.match(m.group(0))
        if not dm:
            raise HTTPException(400, f"Google Drive のリンクではありません: {m.group(0)[:80]}")
        items.append(("drive", None, dm.group(1)))
    tmpdir = jobs.DATA.parent / "upload_tmp"
    tmpdir.mkdir(exist_ok=True)
    for f in files or []:
        if not f.filename:
            continue
        dest = tmpdir / f"{len(items)}_{Path(f.filename).name}"
        with open(dest, "wb") as out:
            while chunk := await f.read(1 << 20):
                out.write(chunk)
        items.append(("file", dest, Path(f.filename).name))
    if not items:
        raise HTTPException(400, "リンクかファイルを指定してください")
    _import_state.update(running=True, log=[f"{len(items)} 個のファイルを取り込みます"], error=None)
    threading.Thread(target=_import_worker, args=(items,), daemon=True).start()
    return {"ok": True, "count": len(items)}


@app.get("/api/models/import")
def import_status():
    from .separate import model_status
    return {**_import_state, "models": model_status()}


def _job_or_404(jid: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{12}", jid):
        raise HTTPException(404)
    job = jobs.load(jid)
    if not job:
        raise HTTPException(404, "ジョブが見つかりません")
    return job


@app.post("/api/jobs")
async def create_job(
    url: str = Form(""),
    inbox: str = Form(""),
    file: UploadFile | None = File(None),
    model: str = Form("htdemucs"),
    transcribe: bool = Form(True),
    sensitivity: float = Form(0.5),
    shifts: int = Form(1),
    start: float | None = Form(None),
    end: float | None = Form(None),
):
    if model not in MODELS:
        raise HTTPException(400, "不明なモデルです")
    options = {"model": model, "transcribe": transcribe, "sensitivity": min(1, max(0, sensitivity)),
               "shifts": min(5, max(1, shifts)), "start": start or None, "end": end or None}
    if file is not None and file.filename:
        ext = Path(file.filename).suffix.lower()
        if ext not in AUDIO_EXT:
            raise HTTPException(400, f"対応していない形式です: {ext}")
        job = jobs.create({"type": "file", "file": "upload" + ext, "name": file.filename}, options,
                          Path(file.filename).stem, enqueue=False)
        # 書き込みが終わってからキューに入れる
        await _write_upload(job, file)
        jobs.enqueue(job["id"])
        return job
    if inbox:
        from . import config
        f = (config.INBOX / inbox) if config.INBOX else None
        if f is None or f.parent != config.INBOX or not f.is_file() or f.suffix.lower() not in AUDIO_EXT:
            raise HTTPException(404, "フォルダにそのファイルがありません")
        return jobs.create({"type": "inbox", "name": f.name}, options, f.stem)
    url = url.strip()
    if not re.match(r"^https?://", url):
        raise HTTPException(400, "URL かファイルを指定してください")
    return jobs.create({"type": "url", "url": url}, options, url)


async def _write_upload(job: dict, file: UploadFile) -> dict:
    dest = jobs.job_dir(job["id"]) / job["source"]["file"]
    with open(dest, "wb") as f:
        while chunk := await file.read(1 << 20):
            f.write(chunk)
    return job


@app.get("/api/jobs")
def list_jobs():
    return jobs.list_jobs()


@app.get("/api/jobs/{jid}")
def get_job(jid: str):
    return _job_or_404(jid)


@app.delete("/api/jobs/{jid}")
def delete_job(jid: str):
    _job_or_404(jid)
    jobs.delete(jid)
    return {"ok": True}


@app.post("/api/jobs/{jid}/cancel")
def cancel_job(jid: str):
    _job_or_404(jid)
    jobs.cancel(jid)
    return {"ok": True}


@app.post("/api/jobs/{jid}/retry")
def retry_job(jid: str):
    job = _job_or_404(jid)
    if job["status"] in ("queued", "running"):
        raise HTTPException(409, "処理中です")
    jobs.update(jid, status="queued", stage="待機中", progress=0, error=None)
    jobs.enqueue(jid)
    return {"ok": True}


@app.get("/api/jobs/{jid}/notes/{part}")
def notes(jid: str, part: str):
    _job_or_404(jid)
    f = jobs.job_dir(jid) / "parts" / f"{part}.json"
    if not re.fullmatch(r"\w+", part) or not f.exists():
        raise HTTPException(404)
    return Response(f.read_text(encoding="utf-8"), media_type="application/json")


@app.post("/api/jobs/{jid}/retranscribe/{part}")
def retranscribe(jid: str, part: str, sensitivity: float = 0.5):
    job = _job_or_404(jid)
    if part not in job.get("stems", {}):
        raise HTTPException(404)
    if job["status"] in ("queued", "running"):
        raise HTTPException(409, "処理中です")
    return jobs.transcribe_part(jid, part, min(1, max(0, sensitivity)))


def _parts(job: dict, parts: str) -> list[tuple[str, list[dict]]]:
    d = jobs.job_dir(job["id"]) / "parts"
    names = [p for p in parts.split(",") if p] if parts else list(job.get("parts", {}))
    order = ["vocals", "guitar", "piano", "other", "bass", "drums"]
    names = sorted(names, key=lambda n: order.index(n) if n in order else 99)
    out = []
    for n in names:
        f = d / f"{n}.json"
        if re.fullmatch(r"\w+", n) and f.exists():
            out.append((n, json.loads(f.read_text(encoding="utf-8"))))
    if not out:
        raise HTTPException(404, "採譜データがありません")
    return out


@app.get("/api/jobs/{jid}/score")
def score(jid: str, parts: str = "", transpose: int = 0, division: int = 4, chords: bool = True,
          names: str = "",
          download: bool = False):
    from .notation import build_score

    job = _job_or_404(jid)
    sel = _parts(job, parts)
    division = division if division in (2, 4) else 4
    transpose = max(-12, min(12, transpose))
    names = names if names in ("letter", "letter_oct") else ""
    cache_dir = jobs.job_dir(jid) / "scores"
    cache_dir.mkdir(exist_ok=True)
    name = f"{'-'.join(n for n, _ in sel)}_t{transpose}_d{division}_c{int(chords)}_n{names}.musicxml"
    f = cache_dir / name
    if not f.exists():
        xml = build_score(sel, job["analysis"], job["title"], transpose, division, chords,
                          names or None)
        f.write_text(xml, encoding="utf-8")
    headers = {}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{_safe(job["title"])}_{name}"'
    return FileResponse(f, media_type="application/vnd.recordare.musicxml+xml", headers=headers)


@app.get("/api/jobs/{jid}/midi")
def midi(jid: str, parts: str = "", transpose: int = 0):
    from .notation import write_midi

    job = _job_or_404(jid)
    sel = _parts(job, parts)
    buf = io.BytesIO()
    tmp = jobs.job_dir(jid) / "scores"
    tmp.mkdir(exist_ok=True)
    f = tmp / f"tmp_{'-'.join(n for n, _ in sel)}_{transpose}.mid"
    write_midi(sel, job["analysis"]["bpm"], f, transpose)
    buf.write(f.read_bytes())
    fname = f"{_safe(job['title'])}_{'-'.join(n for n, _ in sel)}.mid"
    return Response(buf.getvalue(), media_type="audio/midi",
                    headers={"Content-Disposition": _disp(fname)})


def _safe(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "_", s or "untitled")[:60]


def _disp(fname: str) -> str:
    from urllib.parse import quote
    return f"attachment; filename=\"download\"; filename*=UTF-8''{quote(fname)}"


@app.get("/api/jobs/{jid}/file/{name}")
def job_file(jid: str, name: str, download: bool = False):
    job = _job_or_404(jid)
    if not re.fullmatch(r"[\w.]+", name):
        raise HTTPException(404)
    for sub in ("stems", "parts", ""):
        f = jobs.job_dir(jid) / sub / name
        if f.is_file():
            headers = {"Content-Disposition": _disp(f"{_safe(job['title'])}_{name}")} if download else {}
            return FileResponse(f, headers=headers)
    raise HTTPException(404)


@app.get("/api/jobs/{jid}/stream/{name}")
def stream(jid: str, name: str):
    """再生用の軽量音声 (AAC)。WAV の 1/8 程度のサイズでスマホでも読み込みが速い。"""
    _job_or_404(jid)
    if not re.fullmatch(r"\w+", name):
        raise HTTPException(404)
    d = jobs.job_dir(jid) / "stems"
    wav = d / f"{name}.wav"
    if not wav.exists():
        raise HTTPException(404)
    m4a = d / f"{name}.m4a"
    with _file_lock(str(m4a)):
        if not m4a.exists():
            from .source import encode_aac
            encode_aac(wav, m4a)
    return FileResponse(m4a, media_type="audio/mp4")


@app.get("/api/jobs/{jid}/zip")
def zip_all(jid: str):
    job = _job_or_404(jid)
    d = jobs.job_dir(jid)
    title = _safe(job["title"])

    def gen():
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted((d / "stems").glob("*.wav")):
                z.write(f, f"{title}/stems/{f.name}")
            for f in sorted((d / "parts").glob("*.mid")) if (d / "parts").exists() else []:
                z.write(f, f"{title}/midi/{f.name}")
            for f in sorted((d / "scores").glob("*.musicxml")) if (d / "scores").exists() else []:
                z.write(f, f"{title}/scores/{f.name}")
            if job.get("analysis"):
                a = job["analysis"]
                lines = [f"Title: {job['title']}", f"BPM: {a['bpm']}", f"Key: {a['key']['name']}", "",
                         "Chords:"]
                lines += [f"{c['start']:8.2f}s  {c['chord']}" for c in a["chords"] if c["chord"] != "N"]
                z.writestr(f"{title}/analysis.txt", "\n".join(lines))
        yield buf.getvalue()

    return StreamingResponse(gen(), media_type="application/zip",
                             headers={"Content-Disposition": _disp(f"{title}.zip")})


def main():
    import argparse
    import threading
    import webbrowser

    import uvicorn

    ap = argparse.ArgumentParser()
    ap.add_argument("--local", action="store_true", help="この PC からのみアクセス可能にする")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    import sys
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    app.state.lan = not args.local
    host = "127.0.0.1" if args.local else "0.0.0.0"

    print(f"\n  StemScore: http://127.0.0.1:{PORT}")
    if app.state.lan:
        key = remote.access_key()
        ips = remote.lan_ips()
        for ip in ips:
            print(f"  スマホから (同じ Wi-Fi): http://{ip}:{PORT}/?key={key}")
        if ips:
            print("\n  ↓ スマホのカメラで読み取ってください")
            try:
                print(remote.qr_terminal(f"http://{ips[0]}:{PORT}/?key={key}"))
            except Exception:  # noqa: BLE001
                print("  (QR コードは PC のブラウザ画面の「スマホで開く」から表示できます)")
    print("  終了するには Ctrl+C\n")
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}")).start()
    uvicorn.run(app, host=host, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
