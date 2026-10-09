"""楽曲全体の解析: テンポ / ビート / キー / コード進行。"""
from __future__ import annotations

import numpy as np
import librosa

PITCH_NAMES_SHARP = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
PITCH_NAMES_FLAT = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]

# Krumhansl-Kessler のキープロファイル
MAJOR_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

# フラット系で表記する方が自然なキー (主音のピッチクラス)
FLAT_MAJOR = {5, 10, 3, 8, 1}   # F Bb Eb Ab Db
FLAT_MINOR = {2, 7, 0, 5, 10}   # Dm Gm Cm Fm Bbm


def key_name(tonic: int, mode: str) -> str:
    use_flat = tonic in (FLAT_MAJOR if mode == "major" else FLAT_MINOR)
    names = PITCH_NAMES_FLAT if use_flat else PITCH_NAMES_SHARP
    return f"{names[tonic]} {'major' if mode == 'major' else 'minor'}"


def key_uses_flats(tonic: int, mode: str) -> bool:
    return tonic in (FLAT_MAJOR if mode == "major" else FLAT_MINOR)


def estimate_key(chroma_mean: np.ndarray) -> list[dict]:
    """平均クロマからキー候補を相関の高い順に返す。"""
    scores = []
    c = (chroma_mean - chroma_mean.mean()) / (chroma_mean.std() + 1e-9)
    for mode, prof in (("major", MAJOR_PROFILE), ("minor", MINOR_PROFILE)):
        p = (prof - prof.mean()) / prof.std()
        for tonic in range(12):
            r = float(np.dot(c, np.roll(p, tonic)) / 12)
            scores.append({"tonic": tonic, "mode": mode, "name": key_name(tonic, mode), "score": round(r, 3)})
    scores.sort(key=lambda s: s["score"], reverse=True)
    return scores


def _chord_templates():
    labels, temps = [], []
    for root in range(12):
        for qual, ivs in (("", (0, 4, 7)), ("m", (0, 3, 7))):
            t = np.zeros(12)
            for iv in ivs:
                t[(root + iv) % 12] = 1
            # ルート音をわずかに強調
            t[root] += 0.3
            labels.append((root, qual))
            temps.append(t / np.linalg.norm(t))
    return labels, np.array(temps)


def estimate_chords(y: np.ndarray, sr: int, beat_times: np.ndarray, use_flats: bool, duration: float) -> list[dict]:
    """ビート単位でクロマとテンプレートを照合し、コード進行を推定する。"""
    hop = 2048
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=hop)
    frame_times = librosa.frames_to_time(np.arange(chroma.shape[1]), sr=sr, hop_length=hop)
    energy = chroma.sum(axis=0)
    labels, temps = _chord_templates()
    names = PITCH_NAMES_FLAT if use_flats else PITCH_NAMES_SHARP

    bounds = list(beat_times)
    if not bounds or bounds[0] > 0.05:
        bounds = [0.0] + bounds
    bounds.append(duration)

    raw = []
    for a, b in zip(bounds[:-1], bounds[1:]):
        idx = np.where((frame_times >= a) & (frame_times < b))[0]
        if len(idx) == 0:
            raw.append("N")
            continue
        v = chroma[:, idx].mean(axis=1)
        if energy[idx].mean() < 0.5 or v.max() < 0.2:
            raw.append("N")
            continue
        v = v / (np.linalg.norm(v) + 1e-9)
        sims = temps @ v
        best = int(np.argmax(sims))
        raw.append("N" if sims[best] < 0.55 else f"{names[labels[best][0]]}{labels[best][1]}")

    # 1拍だけ別のコードになる揺れを除去 (メディアンフィルタ的に)
    smooth = raw[:]
    for i in range(1, len(raw) - 1):
        if raw[i - 1] == raw[i + 1] and raw[i] != raw[i - 1]:
            smooth[i] = raw[i - 1]

    out: list[dict] = []
    for (a, b), name in zip(zip(bounds[:-1], bounds[1:]), smooth):
        if out and out[-1]["chord"] == name:
            out[-1]["end"] = round(float(b), 3)
        else:
            out.append({"start": round(float(a), 3), "end": round(float(b), 3), "chord": name})
    return out


def estimate_downbeat(beat_times: np.ndarray, chords: list[dict]) -> float:
    """コードが変わる拍は小節頭になりやすい、という前提で 4 拍のうちの小節頭の位相を推定。"""
    if len(beat_times) < 4:
        return float(beat_times[0]) if len(beat_times) else 0.0
    votes = np.zeros(4)
    for c in chords[1:]:
        if c["chord"] == "N":
            continue
        i = int(np.argmin(np.abs(beat_times - c["start"])))
        if abs(beat_times[i] - c["start"]) < 0.08:
            votes[i % 4] += 1
    return float(beat_times[int(np.argmax(votes))])


def analyze(path: str) -> dict:
    y, sr = librosa.load(path, sr=22050, mono=True)
    duration = len(y) / sr
    y_harm, y_perc = librosa.effects.hpss(y)

    tempo, beat_frames = librosa.beat.beat_track(y=y_perc, sr=sr, units="frames")
    tempo = float(np.atleast_1d(tempo)[0])
    beat_times = librosa.frames_to_time(beat_frames, sr=sr)
    # 極端なテンポは 2 倍/半分に補正 (70〜180 BPM に収める)
    while tempo and tempo < 70:
        tempo *= 2
    while tempo > 180:
        tempo /= 2
    if len(beat_times) > 8:
        # ビート位置の線形回帰の傾きから BPM を再計算 (フレーム分解能より精密)
        idx = np.arange(len(beat_times))
        slope = float(np.polyfit(idx, beat_times, 1)[0])
        med = float(np.median(np.diff(beat_times)))
        bt = 60.0 / (slope if 0.5 * med < slope < 1.5 * med else med)
        while bt < 70:
            bt *= 2
        while bt > 180:
            bt /= 2
        tempo = bt

    chroma = librosa.feature.chroma_cqt(y=y_harm, sr=sr)
    keys = estimate_key(chroma.mean(axis=1))
    best = keys[0]
    chords = estimate_chords(y_harm, sr, beat_times, key_uses_flats(best["tonic"], best["mode"]), duration)

    downbeat = estimate_downbeat(beat_times, chords)

    return {
        "duration": round(duration, 3),
        "downbeat": round(downbeat, 3),
        "bpm": round(tempo, 1),
        "beats": [round(float(t), 3) for t in beat_times],
        "first_beat": round(float(beat_times[0]), 3) if len(beat_times) else 0.0,
        "key": best,
        "key_candidates": keys[:5],
        "chords": chords,
    }


def stem_key(path: str, song_key: dict | None = None) -> dict | None:
    """パート単体のキー推定。曲全体のキーとほぼ同点なら曲のキーを優先 (平行調の取り違え防止)。"""
    y, sr = librosa.load(path, sr=22050, mono=True)
    if np.abs(y).max() < 1e-3:
        return None
    chroma = librosa.feature.chroma_cqt(y=librosa.effects.harmonic(y), sr=sr)
    keys = estimate_key(chroma.mean(axis=1))
    if song_key:
        same = next(k for k in keys if k["tonic"] == song_key["tonic"] and k["mode"] == song_key["mode"])
        # 平行調 (同じ調号) と判定された場合や、ほぼ同点の場合は曲のキーに合わせる
        rel = (song_key["tonic"] + (9 if song_key["mode"] == "major" else 3)) % 12
        is_relative = keys[0]["tonic"] == rel and keys[0]["mode"] != song_key["mode"]
        if is_relative or keys[0]["score"] - same["score"] < 0.08:
            return same
    return keys[0]
