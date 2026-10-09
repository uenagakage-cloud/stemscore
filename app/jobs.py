"""ジョブ管理と処理パイプライン (取得 → 解析 → 分離 → 採譜 → 楽譜化)。"""
from __future__ import annotations

import json
import queue
import shutil
import threading
import time
import traceback
import uuid
from pathlib import Path

from . import config

DATA = config.DATA / "jobs"
DATA.mkdir(parents=True, exist_ok=True)

_lock = threading.Lock()
_queue: "queue.Queue[str]" = queue.Queue()
_cancel: set[str] = set()
_order: list[str] = []   # 順番待ちの並び (表示用)


class Cancelled(Exception):
    pass


def job_dir(jid: str) -> Path:
    d = DATA / jid
    if not d.resolve().is_relative_to(DATA.resolve()):
        raise ValueError("invalid job id")
    return d


def load(jid: str) -> dict | None:
    f = job_dir(jid) / "job.json"
    if not f.exists():
        return None
    with _lock:
        return json.loads(f.read_text(encoding="utf-8"))


def save(job: dict) -> None:
    d = job_dir(job["id"])
    d.mkdir(parents=True, exist_ok=True)
    job["updated"] = time.time()
    tmp = d / "job.json.tmp"
    with _lock:
        tmp.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        tmp.replace(d / "job.json")


def update(jid: str, **kw) -> dict:
    job = load(jid)
    job.update(kw)
    save(job)
    return job


def log(jid: str, msg: str) -> None:
    job = load(jid)
    job.setdefault("log", []).append(f"[{time.strftime('%H:%M:%S')}] {msg}")
    save(job)


def queue_position(jid: str) -> int | None:
    """順番待ちの何番目か (1 始まり)。処理中・待ちでない場合は None。"""
    with _lock:
        return _order.index(jid) + 1 if jid in _order else None


def owner_of(job: dict) -> str:
    return job.get("owner") or "admin"   # 共有機能より前に作った曲は管理者のもの


def active_count(owner: str) -> int:
    return sum(1 for j in list_jobs() if j["owner"] == owner and j["status"] in ("queued", "running"))


def list_jobs(owner: str | None = None) -> list[dict]:
    """owner を指定すると、その人の曲だけを返す。"""
    out = []
    for f in DATA.glob("*/job.json"):
        try:
            j = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if owner is not None and owner_of(j) != owner:
            continue
        out.append({k: j.get(k) for k in ("id", "title", "status", "progress", "stage", "created",
                                          "options", "owner_name")}
                   | {"owner": owner_of(j), "queue_pos": queue_position(j["id"])}
                   | {"key": (j.get("analysis") or {}).get("key", {}).get("name"),
                      "bpm": (j.get("analysis") or {}).get("bpm")})
    return sorted(out, key=lambda j: j["created"] or 0, reverse=True)


def create(source: dict, options: dict, title: str, enqueue: bool = True,
           owner: dict | None = None) -> dict:
    jid = uuid.uuid4().hex[:12]
    owner = owner or {"id": "admin", "name": "管理者"}
    job = {"id": jid, "title": title, "source": source, "options": options, "status": "queued",
           "stage": "待機中", "progress": 0.0, "created": time.time(), "log": [], "stems": {},
           "parts": {}, "analysis": None, "error": None,
           "owner": owner["id"], "owner_name": owner["name"]}
    save(job)
    if enqueue:
        globals()["enqueue"](jid)
    return job


def enqueue(jid: str) -> None:
    with _lock:
        if jid not in _order:
            _order.append(jid)
    _queue.put(jid)


def delete(jid: str) -> None:
    _cancel.add(jid)
    shutil.rmtree(job_dir(jid), ignore_errors=True)


def cancel(jid: str) -> None:
    _cancel.add(jid)


def _check(jid: str):
    if jid in _cancel:
        raise Cancelled()


def _progress(jid: str, lo: float, hi: float, stage: str | None = None):
    last = {"t": 0.0}

    def f(frac: float):
        _check(jid)
        now = time.time()
        if now - last["t"] < 0.7 and frac < 1:
            return
        last["t"] = now
        kw = {"progress": round(lo + (hi - lo) * max(0.0, min(1.0, frac)), 4)}
        if stage:
            kw["stage"] = stage
        update(jid, **kw)
    return f


def transcribe_part(jid: str, part: str, sensitivity: float) -> dict:
    """1 パートを採譜して notes / MIDI / 統計を保存。"""
    from . import analysis as an
    from . import notation, transcribe

    job = load(jid)
    d = job_dir(jid)
    wav = d / "stems" / f"{part}.wav"
    if part == "drums":
        notes = transcribe.transcribe_drums(str(wav), sensitivity)
        pkey = None
    else:
        notes = transcribe.transcribe_pitched(str(wav), part, sensitivity)
        pkey = an.stem_key(str(wav), job["analysis"].get("key"))
    (d / "parts").mkdir(exist_ok=True)
    (d / "parts" / f"{part}.json").write_text(json.dumps(notes), encoding="utf-8")
    notation.write_midi([(part, notes)], job["analysis"]["bpm"], d / "parts" / f"{part}.mid")
    for old in (d / "scores").glob(f"*{part}*") if (d / "scores").exists() else []:
        old.unlink(missing_ok=True)
    info = {"stats": notation.part_stats(notes), "key": pkey["name"] if pkey else None,
            "sensitivity": sensitivity}
    job = load(jid)
    job["parts"][part] = info
    save(job)
    return info


def run(jid: str) -> None:
    from . import analysis as an
    from . import separate, source

    job = load(jid)
    if not job:
        return
    d = job_dir(jid)
    opts = job["options"]
    update(jid, status="running", error=None)
    try:
        # 1. 音源取得
        update(jid, stage="音源を取得中", progress=0.01)
        src = job["source"]
        wav = d / "source.wav"
        raw = None
        if src["type"] == "url":
            if not wav.exists():
                log(jid, f"URL から取得: {src['url']}")
                raw, title = source.download_url(src["url"], d, lambda m: log(jid, m))
                update(jid, title=title)
        elif src["type"] == "inbox":
            raw = config.INBOX / src["name"] if config.INBOX else None
            if raw is None or not raw.exists():
                raise RuntimeError(f"フォルダにファイルが見つかりません: {src['name']}")
            log(jid, f"フォルダから読み込み: {src['name']}")
        else:
            raw = d / src["file"]
        _check(jid)
        if raw is not None and raw.exists():
            source.to_wav(raw, wav, opts.get("start"), opts.get("end"))
            # 元ファイル (大きな動画など) は変換後に削除し、再実行時は source.wav を使う
            if src["type"] != "inbox":
                raw.unlink(missing_ok=True)
            log(jid, "WAV に変換しました")
        elif wav.exists():
            log(jid, "変換済みの音声を再利用します")
        else:
            raise RuntimeError("元の音声ファイルが見つかりません")

        # 無音のファイル (音声なしの画面収録など) は分離しても意味がないので早めに止める
        import numpy as np
        import soundfile as sf
        peak = 0.0
        with sf.SoundFile(str(wav)) as fh:
            for block in fh.blocks(blocksize=44100 * 10, dtype="float32"):
                peak = max(peak, float(np.abs(block).max()) if block.size else 0.0)
                if peak > 1e-3:
                    break
        if peak <= 1e-3:
            raise RuntimeError("音声が入っていません（全体が無音です）。画面収録の場合は、音が録音されているか確認してください。"
                               "音楽配信アプリの再生音は、著作権保護のため画面収録では無音になることがあります。")

        # 2. 楽曲解析
        update(jid, stage="テンポ・キー・コードを解析中", progress=0.08)
        result = an.analyze(str(d / "source.wav"))
        update(jid, analysis=result)
        log(jid, f"BPM {result['bpm']} / キー {result['key']['name']}")
        _check(jid)

        # 3. 音源分離
        model = opts.get("model", "htdemucs")
        from .separate import model_status
        stt = model_status().get(model, {})
        if not stt.get("available", True):
            try:
                import requests
                requests.head(separate.MODEL_URL, timeout=8)
            except Exception:  # noqa: BLE001
                raise RuntimeError(
                    f"分離モデル {model} がこの PC になく、配布元にも接続できません。"
                    "「モデルの追加」から Google Drive 経由で取り込んでください。") from None
        update(jid, stage=f"音源を分離中 ({model})", progress=0.12)
        log(jid, f"Demucs ({model}) で分離開始 ※初回はモデルをダウンロードします")
        t0 = time.time()
        stems = separate.separate(d / "source.wav", d / "stems", model,
                                  _progress(jid, 0.12, 0.7), shifts=int(opts.get("shifts", 1)))
        # スマホ再生用に軽量な AAC も作成
        for name in stems:
            try:
                source.encode_aac(d / "stems" / f"{name}.wav", d / "stems" / f"{name}.m4a")
            except Exception as e:  # noqa: BLE001
                log(jid, f"{name}: AAC 変換に失敗 ({e})")
        update(jid, stems=stems, progress=0.7)
        log(jid, f"分離完了 ({time.time() - t0:.0f} 秒): {', '.join(stems)}")

        # 4. 採譜
        if opts.get("transcribe", True):
            targets = [s for s in stems if stems[s]["rms"] > 0.002]
            for s in stems:
                if s not in targets:
                    log(jid, f"{s}: ほぼ無音のため採譜をスキップ")
            for i, part in enumerate(targets):
                _check(jid)
                update(jid, stage=f"採譜中: {part}", progress=0.7 + 0.3 * i / max(1, len(targets)))
                info = transcribe_part(jid, part, float(opts.get("sensitivity", 0.5)))
                st = info["stats"]
                rng = f" 音域 {st['low']}〜{st['high']}" if st.get("count") and part != "drums" else ""
                log(jid, f"{part}: {st['count']} ノート{rng}")
        update(jid, status="done", stage="完了", progress=1.0)
        log(jid, "すべての処理が完了しました")
    except Cancelled:
        if load(jid):
            update(jid, status="cancelled", stage="キャンセルされました")
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        msg = str(e)
        if any(k in msg for k in ("urlopen error", "SSLError", "SSLCertVerificationError", "CERTIFICATE", "WinError 10060")):
            msg = ("ネットワークに接続できませんでした (YouTube の取得や分離モデルのダウンロード)。"
                   "プロキシ/フィルタで遮断されていないか確認してください: " + msg)
        if load(jid):
            update(jid, status="error", stage="エラー", error=msg)
            log(jid, f"エラー: {msg}")
    finally:
        _cancel.discard(jid)


def _worker():
    while True:
        jid = _queue.get()
        with _lock:
            if jid in _order:
                _order.remove(jid)
        try:
            if job_dir(jid).exists() and jid not in _cancel:
                run(jid)
        finally:
            _cancel.discard(jid)
            _queue.task_done()


def start_worker() -> None:
    # 前回終了時に途中だったジョブを再投入
    for f in sorted(DATA.glob("*/job.json"), key=lambda p: p.stat().st_mtime):
        try:
            j = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if j.get("status") in ("queued", "running"):
            j["status"] = "queued"
            j["stage"] = "待機中 (再開)"
            save(j)
            enqueue(j["id"])
    threading.Thread(target=_worker, daemon=True).start()
    if config.KEEP_DAYS:
        threading.Thread(target=_cleanup_loop, daemon=True).start()


def _cleanup_loop():
    """KEEP_DAYS より古い曲を定期的に削除する。"""
    while True:
        limit = time.time() - config.KEEP_DAYS * 86400
        for j in list_jobs():
            if (j["created"] or 0) < limit and j["status"] not in ("queued", "running"):
                delete(j["id"])
        time.sleep(6 * 3600)
