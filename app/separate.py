"""Demucs による楽器別の音源分離。"""
from __future__ import annotations

import os
import types
from pathlib import Path

import numpy as np
import soundfile as sf

# torch は重い (メモリ数百MB) ので、実際に AI 分離をするときだけ読み込む

MODELS = {
    "htdemucs": "4パート (ボーカル/ドラム/ベース/その他) 標準",
    "htdemucs_6s": "6パート (+ギター/ピアノ)",
    "htdemucs_ft": "4パート 高品質 (処理時間 約4倍)",
    "simple": "簡易分離 (AIモデル不要・オフライン、品質は低め)",
}

# 各モデルの重みファイル (torch hub のキャッシュに置かれる)。ファイル名の後半は sha256 の先頭8桁
MODEL_FILES = {
    "htdemucs": ["955717e8-8726e21a.th"],
    "htdemucs_6s": ["5c90dfd2-34c22ccb.th"],
    "htdemucs_ft": ["f7e0c4bc-ba3fe64a.th", "d12395a8-e57c48e6.th",
                    "92cfc3b6-ef3bcb9c.th", "04573f0d-f3cf25b2.th"],
}
MODEL_URL = "https://dl.fbaipublicfiles.com/demucs/hybrid_transformer/"
ALL_FILES = {f for fs in MODEL_FILES.values() for f in fs}

_cache: dict[str, object] = {}


def checkpoint_dir() -> Path:
    # torch.hub.get_dir() と同じ規則 (TORCH_HOME → XDG_CACHE_HOME/torch → ~/.cache/torch)
    home = os.environ.get("TORCH_HOME") or os.path.join(
        os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache"), "torch")
    return Path(home) / "hub" / "checkpoints"


def model_status() -> dict:
    d = checkpoint_dir()
    out = {}
    for name, label in MODELS.items():
        missing = [f for f in MODEL_FILES.get(name, []) if not (d / f).exists()]
        out[name] = {"label": label, "available": not missing, "missing": missing}
    return out


def install_weight(src: Path, filename: str) -> str:
    """重みファイルを検証してキャッシュに配置する。filename は元のファイル名。"""
    import hashlib
    import shutil

    name = Path(filename).name
    if name not in ALL_FILES:
        # ファイル名が変わっていても中身のハッシュで判定する
        h = hashlib.sha256(src.read_bytes()).hexdigest()
        match = [f for f in ALL_FILES if f.split("-")[1].split(".")[0] == h[:8]]
        if not match:
            raise ValueError(f"{filename}: StemScore のモデルファイルではありません")
        name = match[0]
    else:
        h = hashlib.sha256(src.read_bytes()).hexdigest()
        if not h.startswith(name.split("-")[1].split(".")[0]):
            raise ValueError(f"{name}: ファイルが壊れています (ハッシュ不一致)。もう一度ダウンロードしてください")
    d = checkpoint_dir()
    d.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(d / name))
    _cache.clear()
    return name


def _get_model(name: str):
    from demucs.pretrained import get_model

    if name not in _cache:
        m = get_model(name)
        m.eval()
        _cache[name] = m
    return _cache[name]


def _install_progress_hook(on_progress, n_models: int):
    """demucs 内部の tqdm を差し替えてチャンク単位の進捗を受け取る。"""
    import demucs.apply as dapply

    state = {"model": 0}

    class _Bar:
        def __init__(self, iterable, *args, **kwargs):
            self.items = list(iterable)

        def __iter__(self):
            n = len(self.items)
            for i, it in enumerate(self.items):
                yield it
                on_progress((state["model"] + (i + 1) / n) / n_models)
            state["model"] = min(state["model"] + 1, n_models - 1)

    dapply.tqdm = types.SimpleNamespace(tqdm=_Bar)


def peaks(data: np.ndarray, n: int = 1200) -> list[float]:
    """波形表示用のピーク列 (0〜1)。data: (samples,) または (samples, ch)。"""
    mono = np.abs(data).max(axis=1) if data.ndim == 2 else np.abs(data)
    if len(mono) == 0:
        return []
    step = max(1, len(mono) // n)
    trimmed = mono[: step * (len(mono) // step)].reshape(-1, step).max(axis=1)
    return [round(float(v), 3) for v in trimmed[:n]]


def _write_stems(arrays: dict[str, np.ndarray], sr: int, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    stems = {}
    instrumental = None
    for name, arr in arrays.items():
        path = out_dir / f"{name}.wav"
        sf.write(str(path), np.clip(arr, -1, 1), sr, subtype="PCM_16")
        rms = float(np.sqrt(np.mean(arr ** 2)))
        stems[name] = {"file": path.name, "peaks": peaks(arr), "rms": round(rms, 5)}
        if name != "vocals":
            instrumental = arr if instrumental is None else instrumental + arr
    if instrumental is not None:
        sf.write(str(out_dir / "instrumental.wav"), np.clip(instrumental, -1, 1), sr, subtype="PCM_16")
    return stems


def separate_simple(wav_path: Path, out_dir: Path, on_progress) -> dict:
    """AI モデルを使わない信号処理ベースの簡易分離。

    - ドラム: 調波/打撃音分離 (HPSS) の打撃成分
    - ベース: 調波成分の低域 (〜250Hz)
    - ボーカル: 調波成分の中高域のうち、左右中央に定位している成分
    - その他: 残りの調波成分
    """
    import librosa

    data, sr = sf.read(str(wav_path), dtype="float32", always_2d=True)
    if data.shape[1] == 1:
        data = np.repeat(data, 2, axis=1)
    n_fft, hop = 4096, 1024
    out = {k: np.zeros_like(data) for k in ("vocals", "drums", "bass", "other")}
    # メモリ節約のため 60 秒ごとに処理 (前後 1 秒重ねてクロスフェード)
    block, pad = 60 * sr, sr
    starts = list(range(0, len(data), block))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=n_fft)
    bass_w = np.clip((300 - freqs) / 100, 0, 1)[:, None]          # 200〜300Hz でなめらかに切替
    voc_band = (np.clip((freqs - 150) / 100, 0, 1) * np.clip((9000 - freqs) / 2000, 0, 1))[:, None]
    for bi, s0 in enumerate(starts):
        a, b = max(0, s0 - pad), min(len(data), s0 + block + pad)
        seg = data[a:b]
        L = librosa.stft(seg[:, 0], n_fft=n_fft, hop_length=hop)
        R = librosa.stft(seg[:, 1], n_fft=n_fft, hop_length=hop)
        mag = (np.abs(L) + np.abs(R)) / 2
        H, P = librosa.decompose.hpss(mag, kernel_size=(17, 31), power=2.0, mask=True, margin=1.0)
        # 左右の似ている度合い (1 = 中央定位)
        center = 1 - np.abs(np.abs(L) - np.abs(R)) / (np.abs(L) + np.abs(R) + 1e-9)
        center = center ** 4
        masks = {
            "drums": P,
            "bass": H * bass_w,
            "vocals": H * (1 - bass_w) * voc_band * center,
        }
        masks["other"] = np.clip(H - masks["bass"] - masks["vocals"], 0, 1)
        lo, hi = s0 - a, s0 - a + min(block, len(data) - s0)
        for name, m in masks.items():
            for ch, X in enumerate((L, R)):
                y = librosa.istft(X * m, hop_length=hop, length=len(seg))
                out[name][s0:s0 + (hi - lo), ch] = y[lo:hi]
        on_progress((bi + 1) / len(starts))
    return _write_stems(out, sr, out_dir)


def separate(wav_path: Path, out_dir: Path, model_name: str, on_progress, shifts: int = 1) -> dict:
    if model_name == "simple":
        return separate_simple(wav_path, out_dir, on_progress)
    import torch
    from demucs.apply import apply_model

    model = _get_model(model_name)
    n_models = len(getattr(model, "models", [model]))
    _install_progress_hook(on_progress, n_models)

    data, sr = sf.read(str(wav_path), dtype="float32", always_2d=True)
    if sr != model.samplerate:
        import librosa

        data = librosa.resample(data.T, orig_sr=sr, target_sr=model.samplerate).T
        sr = model.samplerate
    wav = torch.from_numpy(data.T.copy())
    if wav.shape[0] == 1:
        wav = wav.repeat(2, 1)
    ref = wav.mean(0)
    mean, std = ref.mean(), ref.std() + 1e-8
    wav = (wav - mean) / std

    threads = max(1, (torch.get_num_threads() or 4))
    torch.set_num_threads(threads)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    with torch.no_grad():
        sources = apply_model(model, wav[None], device=device, shifts=shifts, split=True,
                              overlap=0.25, progress=True)[0]
    sources = sources * std + mean

    return _write_stems({name: src.cpu().numpy().T for name, src in zip(model.sources, sources)},
                        sr, out_dir)
