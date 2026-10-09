"""Demucs による楽器別の音源分離。"""
from __future__ import annotations

import os
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


class StemWriter:
    """分離結果をパートごとの WAV に少しずつ書き出す (曲全体をメモリに持たない)。

    波形表示用のピークと音量 (RMS) も書きながら集計する。
    """

    def __init__(self, out_dir: Path, names: list[str], sr: int, total: int, n_peaks: int = 1200):
        out_dir.mkdir(parents=True, exist_ok=True)
        self.out_dir, self.names, self.sr = out_dir, names, sr
        self.step = max(1, total // n_peaks)
        self.n_peaks = n_peaks
        self.files = {n: sf.SoundFile(str(out_dir / f"{n}.wav"), "w", sr, 2, "PCM_16") for n in names}
        self.inst = sf.SoundFile(str(out_dir / "instrumental.wav"), "w", sr, 2, "PCM_16")
        self.peaks = {n: np.zeros(n_peaks, dtype=np.float32) for n in names}
        self.sq = {n: 0.0 for n in names}
        self.pos = 0

    def write(self, parts: dict[str, np.ndarray]):
        """parts: {名前: (samples, 2)} の同じ長さのブロック。"""
        n = len(next(iter(parts.values())))
        idx = np.minimum((self.pos + np.arange(n)) // self.step, self.n_peaks - 1)
        inst = np.zeros((n, 2), dtype=np.float32)
        for name, blk in parts.items():
            blk = np.clip(blk, -1, 1).astype(np.float32)
            self.files[name].write(blk)
            np.maximum.at(self.peaks[name], idx, np.abs(blk).max(axis=1))
            self.sq[name] += float((blk.astype(np.float64) ** 2).sum())
            if name != "vocals":
                inst += blk
        self.inst.write(np.clip(inst, -1, 1))
        self.pos += n

    def close(self) -> dict:
        for f in [*self.files.values(), self.inst]:
            f.close()
        total = max(1, self.pos * 2)
        used = min(self.n_peaks, -(-self.pos // self.step))
        return {n: {"file": f"{n}.wav", "peaks": [round(float(v), 3) for v in self.peaks[n][:used]],
                    "rms": round(float(np.sqrt(self.sq[n] / total)), 5)} for n in self.names}


def _blocks(total: int, size: int, ctx: int):
    """(書き出す区間の開始, 終了, 前後の余白込みの読み込み開始, 終了) を順に返す。"""
    for s0 in range(0, total, size):
        s1 = min(total, s0 + size)
        yield s0, s1, max(0, s0 - ctx), min(total, s1 + ctx)


def separate_simple(wav_path: Path, out_dir: Path, on_progress) -> dict:
    """AI モデルを使わない信号処理ベースの簡易分離。

    - ドラム: 調波/打撃音分離 (HPSS) の打撃成分
    - ベース: 調波成分の低域 (〜250Hz)
    - ボーカル: 調波成分の中高域のうち、左右中央に定位している成分
    - その他: 残りの調波成分
    """
    import librosa

    info = sf.info(str(wav_path))
    sr, total = info.samplerate, info.frames
    n_fft, hop = 4096, 1024
    freqs = librosa.fft_frequencies(sr=sr, n_fft=n_fft)
    bass_w = np.clip((300 - freqs) / 100, 0, 1)[:, None]          # 200〜300Hz でなめらかに切替
    voc_band = (np.clip((freqs - 150) / 100, 0, 1) * np.clip((9000 - freqs) / 2000, 0, 1))[:, None]
    names = ["vocals", "drums", "bass", "other"]
    writer = StemWriter(out_dir, names, sr, total)
    blocks = list(_blocks(total, 30 * sr, sr))   # 30 秒ずつ、前後 1 秒の余白つき
    with sf.SoundFile(str(wav_path)) as fh:
        for bi, (s0, s1, a, b) in enumerate(blocks):
            fh.seek(a)
            seg = fh.read(b - a, dtype="float32", always_2d=True)
            if seg.shape[1] == 1:
                seg = np.repeat(seg, 2, axis=1)
            L = librosa.stft(seg[:, 0], n_fft=n_fft, hop_length=hop)
            R = librosa.stft(seg[:, 1], n_fft=n_fft, hop_length=hop)
            mag = (np.abs(L) + np.abs(R)) / 2
            H, P = librosa.decompose.hpss(mag, kernel_size=(17, 31), power=2.0, mask=True, margin=1.0)
            # 左右の似ている度合い (1 = 中央定位)
            center = (1 - np.abs(np.abs(L) - np.abs(R)) / (np.abs(L) + np.abs(R) + 1e-9)) ** 4
            masks = {"drums": P, "bass": H * bass_w, "vocals": H * (1 - bass_w) * voc_band * center}
            masks["other"] = np.clip(H - masks["bass"] - masks["vocals"], 0, 1)
            out = {}
            for name in names:
                y = np.stack([librosa.istft(X * masks[name], hop_length=hop, length=len(seg)) for X in (L, R)], 1)
                out[name] = y[s0 - a:s1 - a]
            writer.write(out)
            on_progress((bi + 1) / len(blocks))
    return writer.close()


def separate(wav_path: Path, out_dir: Path, model_name: str, on_progress, shifts: int = 1) -> dict:
    """AI (Demucs) による分離。曲を 30 秒ずつ (前後に余白をつけて) 処理し、順に書き出すので
    曲の長さに関係なくメモリ使用量が一定 (無料のクラウドなどメモリが少ない環境でも動く)。"""
    if model_name == "simple":
        return separate_simple(wav_path, out_dir, on_progress)
    import torch
    from demucs.apply import apply_model

    model = _get_model(model_name)
    info = sf.info(str(wav_path))
    sr, total = info.samplerate, info.frames
    if sr != model.samplerate:
        raise ValueError(f"サンプリングレートが {model.samplerate}Hz ではありません")
    threads = int(os.environ.get("STEMSCORE_THREADS") or 0) or max(1, torch.get_num_threads() or 4)
    torch.set_num_threads(threads)
    device = "cuda" if torch.cuda.is_available() else "cpu"

    # 曲全体の平均と標準偏差 (Demucs の入力の正規化に使う) を、少しずつ読みながら求める
    s, s2, n = 0.0, 0.0, 0
    with sf.SoundFile(str(wav_path)) as fh:
        for blk in fh.blocks(blocksize=sr * 30, dtype="float32", always_2d=True):
            m = blk.mean(axis=1).astype(np.float64)
            s, s2, n = s + m.sum(), s2 + (m ** 2).sum(), n + len(m)
    mean = s / max(1, n)
    std = float(np.sqrt(max(s2 / max(1, n) - mean ** 2, 0))) + 1e-8

    writer = StemWriter(out_dir, list(model.sources), sr, total)
    blocks = list(_blocks(total, 30 * sr, 3 * sr))   # 30 秒ずつ、前後 3 秒の余白 (継ぎ目を自然にする)
    with sf.SoundFile(str(wav_path)) as fh, torch.no_grad():
        for bi, (s0, s1, a, b) in enumerate(blocks):
            fh.seek(a)
            seg = fh.read(b - a, dtype="float32", always_2d=True)
            if seg.shape[1] == 1:
                seg = np.repeat(seg, 2, axis=1)
            x = (torch.from_numpy(seg.T.copy()) - mean) / std
            out = apply_model(model, x[None], device=device, shifts=shifts, split=True,
                              overlap=0.25, progress=False)[0]
            out = (out * std + mean).cpu().numpy()          # (stems, 2, samples)
            writer.write({name: out[k].T[s0 - a:s1 - a] for k, name in enumerate(model.sources)})
            del x, out
            on_progress((bi + 1) / len(blocks))
    return writer.close()
