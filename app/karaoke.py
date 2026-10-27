"""カラオケ用の書き出し: キー変更と、歌いだしの前のハイハットのカウント。

カウントは、メインボーカルが歌い始める直前の 4 拍に、曲のテンポ (実際の拍の位置) に合わせて入れる。
歌いだしが曲の頭すぐで 4 拍分の余裕がない場合は、曲の前に無音を足してからカウントを入れる。
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import soundfile as sf

COUNT_BEATS = 4


def vocal_start(path: Path, frame: float = 0.05, hold: float = 0.4) -> float | None:
    """ボーカルが歌い始める時刻 (秒)。一定以上の音量が hold 秒続いた最初の位置。"""
    y, sr = sf.read(str(path), dtype="float32", always_2d=True)
    mono = y.mean(axis=1)
    n = int(sr * frame)
    if len(mono) < n:
        return None
    frames = mono[: len(mono) // n * n].reshape(-1, n)
    rms = np.sqrt((frames ** 2).mean(axis=1))
    if rms.max() < 1e-3:
        return None
    th = max(0.08 * np.percentile(rms, 95), 2e-3)
    need = max(1, int(hold / frame))
    run = 0
    for i, on in enumerate(rms > th):
        run = run + 1 if on else 0
        if run >= need:
            return (i - need + 1) * frame
    return None


def count_in_plan(analysis: dict, v0: float) -> tuple[float, list[float], float]:
    """(曲の前に足す無音の秒数, カウントを鳴らす時刻 [元の曲の時間軸], 1 拍の秒数)。

    歌いだし付近で実際に検出した拍の並びに合わせて、歌いだしの直前で終わる 4 拍を数える
    (「1・2・3・4」のあとに歌が入る)。
    """
    beat = 60.0 / (analysis.get("bpm") or 120)
    beats = np.array(analysis.get("beats") or [], dtype=float)
    # 歌いだし付近の拍を基準にする (歌いだしの前は無音や前奏で拍が取れていないことがあるため)
    near = beats[(beats >= v0 - 2 * beat) & (beats <= v0 + 8 * beat)]
    if len(near) >= 3:
        beat = float(np.median(np.diff(near)))
        anchor = float(near[np.argmin(np.abs(near - v0))])
    else:
        anchor = float(beats[np.argmin(np.abs(beats - v0))]) if len(beats) else v0
    # 歌いだしより前にある、拍の位置の中で一番遅いもの = カウントの最後 (4 拍目)
    last = anchor + math.floor((v0 - 0.08 - anchor) / beat) * beat
    hits = [last - (COUNT_BEATS - 1 - i) * beat for i in range(COUNT_BEATS)]
    pre = 0.0
    if hits[0] < 0.05:   # 曲の頭に余裕がなければ、拍単位で無音を足す
        pre = math.ceil((0.05 - hits[0]) / beat) * beat
    return pre, hits, beat


def _hihat(sr: int, accent: bool) -> np.ndarray:
    """クローズド・ハイハットの音 (高域を強調したノイズを短く減衰させる)。"""
    n = int(sr * 0.06)
    rng = np.random.default_rng(1 if accent else 2)
    x = rng.standard_normal(n).astype(np.float32)
    for _ in range(3):                      # 簡易ハイパス (差分) で「チッ」という高い音に
        x = np.diff(x, prepend=0.0)
    x /= np.abs(x).max()
    env = np.exp(-np.arange(n) / (sr * (0.018 if not accent else 0.024)))
    return (x * env * (0.55 if accent else 0.4)).astype(np.float32)


def render(src: Path, dst: Path, pre: float, hits: list[float]) -> None:
    """src の前に pre 秒の無音を足し、hits (元の曲の時間軸) にハイハットを重ねて dst に書き出す。"""
    y, sr = sf.read(str(src), dtype="float32", always_2d=True)
    if y.shape[1] == 1:
        y = np.repeat(y, 2, axis=1)
    pad = int(round(pre * sr))
    out = np.concatenate([np.zeros((pad, 2), dtype=np.float32), y])
    for i, t in enumerate(hits):
        s = int(round((t + pre) * sr))
        h = _hihat(sr, accent=(i == 0))
        e = min(len(out), s + len(h))
        if s >= 0 and e > s:
            out[s:e] += h[: e - s, None]
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.stem + ".tmp.wav")
    sf.write(str(tmp), np.clip(out, -1, 1), sr, subtype="PCM_16")
    tmp.replace(dst)
