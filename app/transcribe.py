"""パートごとの採譜 (音声 → ノート列)。

- 音程のある楽器: Spotify 製 Basic Pitch (ONNX) で多声採譜
- ボーカル/ベース: 単音化してメロディ・ベースラインを抽出
- ドラム: 帯域ごとのオンセット検出でキック/スネア/ハイハットに分類
"""
from __future__ import annotations

import numpy as np
import librosa

# パートごとの Basic Pitch パラメータと音域 (MIDI ノート番号)
PRESETS = {
    "vocals": dict(onset=0.55, frame=0.35, min_ms=110, lo=45, hi=84, mono="top"),
    "bass":   dict(onset=0.5,  frame=0.3,  min_ms=90,  lo=28, hi=60, mono="bottom"),
    "guitar": dict(onset=0.5,  frame=0.3,  min_ms=80,  lo=40, hi=88, mono=None),
    "piano":  dict(onset=0.5,  frame=0.3,  min_ms=70,  lo=21, hi=108, mono=None),
    "other":  dict(onset=0.55, frame=0.35, min_ms=90,  lo=36, hi=96, mono=None),
}

_model = None


def _bp_model():
    global _model
    if _model is None:
        from pathlib import Path

        import basic_pitch
        from basic_pitch.inference import Model

        # TensorFlow が入っている環境 (Colab など) でも同じ結果になるよう ONNX 版を明示的に使う
        _model = Model(Path(basic_pitch.__file__).parent / "saved_models" / "icassp_2022" / "nmp.onnx")
    return _model


def _monophonic(notes: list[dict], mode: str) -> list[dict]:
    """同時に鳴っている音から 1 音だけ残す (top=最高音 / bottom=最低音、音量も考慮)。"""
    notes = sorted(notes, key=lambda n: n["start"])
    out: list[dict] = []
    for n in notes:
        if out and n["start"] < out[-1]["end"] - 0.03:
            prev = out[-1]
            better = (n["pitch"] > prev["pitch"]) if mode == "top" else (n["pitch"] < prev["pitch"])
            # 明らかに弱い音は採用しない
            if better and n["velocity"] >= prev["velocity"] * 0.6:
                prev["end"] = n["start"]
                if prev["end"] - prev["start"] < 0.06:
                    out.pop()
                out.append(n)
            elif n["end"] > prev["end"] and n["start"] < prev["end"]:
                continue
            continue
        out.append(n)
    return out


def transcribe_pitched(path: str, part: str, sensitivity: float = 0.5) -> list[dict]:
    from basic_pitch.inference import predict

    p = PRESETS.get(part, PRESETS["other"])
    # sensitivity: 0 (控えめ) 〜 1 (多めに拾う)
    shift = (0.5 - sensitivity) * 0.3
    _, _, events = predict(
        path,
        _bp_model(),
        onset_threshold=float(np.clip(p["onset"] + shift, 0.1, 0.9)),
        frame_threshold=float(np.clip(p["frame"] + shift, 0.1, 0.9)),
        minimum_note_length=p["min_ms"],
        minimum_frequency=float(librosa.midi_to_hz(p["lo"])),
        maximum_frequency=float(librosa.midi_to_hz(p["hi"])),
        multiple_pitch_bends=False,
        melodia_trick=True,
    )
    notes = [
        {"start": round(float(s), 3), "end": round(float(e), 3), "pitch": int(pitch),
         "velocity": int(np.clip(amp * 127, 20, 127))}
        for s, e, pitch, amp, _ in events
    ]
    if p["mono"]:
        notes = _monophonic(notes, p["mono"])
    return sorted(notes, key=lambda n: (n["start"], n["pitch"]))


# General MIDI ドラムのノート番号
KICK, SNARE, HIHAT, CRASH, TOM = 36, 38, 42, 49, 45


def transcribe_drums(path: str, sensitivity: float = 0.5) -> list[dict]:
    y, sr = librosa.load(path, sr=22050, mono=True)
    if np.abs(y).max() < 1e-3:
        return []
    hop = 256
    S = np.abs(librosa.stft(y, n_fft=1024, hop_length=hop))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=1024)
    bands = {
        KICK: (20, 150),
        SNARE: (150, 2500),
        HIHAT: (6000, 11000),
    }
    delta = 0.12 - sensitivity * 0.1
    notes = []
    for pitch, (lo, hi) in bands.items():
        mask = (freqs >= lo) & (freqs < hi)
        band = S[mask].sum(axis=0)
        env = librosa.onset.onset_strength(S=librosa.amplitude_to_db(S[mask], ref=np.max), sr=sr)
        env = env / (env.max() + 1e-9)
        onsets = librosa.onset.onset_detect(onset_envelope=env, sr=sr, hop_length=hop,
                                            delta=delta, wait=int(0.07 * sr / hop), units="frames")
        bmax = band.max() + 1e-9
        for f in onsets:
            level = band[min(f + 2, len(band) - 1)] / bmax
            if level < 0.12:
                continue
            t = float(f * hop / sr)
            notes.append({"start": round(t, 3), "end": round(t + 0.1, 3), "pitch": pitch,
                          "velocity": int(np.clip(40 + level * 87, 1, 127))})

    # スネア帯域はキックの倍音も拾うので、ほぼ同時のキックがあり弱いスネアは除外
    kicks = [n["start"] for n in notes if n["pitch"] == KICK]
    filtered = []
    for n in notes:
        if n["pitch"] == SNARE and any(abs(n["start"] - k) < 0.03 for k in kicks) and n["velocity"] < 80:
            continue
        filtered.append(n)
    return sorted(filtered, key=lambda n: (n["start"], n["pitch"]))
