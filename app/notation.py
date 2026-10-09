"""ノート列から MIDI / MusicXML (楽譜) を生成する。"""
from __future__ import annotations

import math
from pathlib import Path

import pretty_midi
from music21 import (chord, clef, harmony, instrument, key, layout, meter, note, percussion,
                     pitch, stream, tempo)
from music21.musicxml.m21ToXml import GeneralObjectExporter

from .analysis import PITCH_NAMES_FLAT, PITCH_NAMES_SHARP, key_uses_flats

PART_LABELS = {
    "vocals": "Vocal", "drums": "Drums", "bass": "Bass", "guitar": "Guitar",
    "piano": "Piano", "other": "Other",
}
GM_PROGRAM = {"vocals": 52, "bass": 33, "guitar": 25, "piano": 0, "other": 48}
# ドラム譜での表示位置 (五線上の位置) と符頭
DRUM_DISPLAY = {36: ("F", 4, "normal"), 38: ("C", 5, "normal"), 42: ("G", 5, "x"),
                49: ("A", 5, "x"), 45: ("E", 5, "normal")}


def midi_name(n: int) -> str:
    return f"{PITCH_NAMES_SHARP[n % 12]}{n // 12 - 1}"


def part_stats(notes: list[dict]) -> dict:
    if not notes:
        return {"count": 0}
    ps = [n["pitch"] for n in notes]
    return {"count": len(notes), "low": midi_name(min(ps)), "high": midi_name(max(ps)),
            "low_midi": min(ps), "high_midi": max(ps)}


def write_midi(parts: list[tuple[str, list[dict]]], bpm: float, path: Path, transpose: int = 0) -> None:
    """元音源とタイミングが一致する MIDI を書き出す (DAW で分離音源と並べて使える)。"""
    pm = pretty_midi.PrettyMIDI(initial_tempo=bpm or 120)
    for part, notes in parts:
        is_drum = part == "drums"
        inst = pretty_midi.Instrument(program=0 if is_drum else GM_PROGRAM.get(part, 0),
                                      is_drum=is_drum, name=PART_LABELS.get(part, part))
        shift = 0 if is_drum else transpose
        for n in notes:
            inst.notes.append(pretty_midi.Note(
                velocity=n["velocity"], pitch=min(127, max(0, n["pitch"] + shift)),
                start=n["start"], end=max(n["end"], n["start"] + 0.05)))
        pm.instruments.append(inst)
    pm.write(str(path))


class Grid:
    """秒 → 拍 (quarterLength) への変換と量子化。"""

    def __init__(self, bpm: float, downbeat: float, division: int = 4):
        self.bpm = bpm or 120
        self.beat = 60.0 / self.bpm
        bar = 4 * self.beat
        # 小節頭から遡り、曲の先頭 (0 秒) を含む小節の頭を基準点にする
        n = math.ceil((downbeat - 0.5 * self.beat) / bar)
        self.anchor = downbeat - max(0, n) * bar
        self.step = 1.0 / division

    def q(self, t: float) -> float:
        ql = (t - self.anchor) / self.beat
        return max(0.0, round(ql / self.step) * self.step)


def _spell(midi: int, flats: bool) -> pitch.Pitch:
    p = pitch.Pitch(midi=midi)
    if flats and p.accidental is not None and p.accidental.name == "sharp":
        p = p.getEnharmonic()
    return p


def _events(notes: list[dict], grid: Grid) -> list[tuple[float, float, list[dict]]]:
    """同時発音をまとめ、重なりを切り詰めて (offset, duration, notes) の列にする。"""
    groups: dict[float, list[dict]] = {}
    ends: dict[float, float] = {}
    for n in notes:
        s = grid.q(n["start"])
        e = max(grid.q(n["end"]), s + grid.step)
        groups.setdefault(s, []).append(n)
        ends[s] = max(ends.get(s, 0), e)
    offs = sorted(groups)
    out = []
    for i, s in enumerate(offs):
        e = ends[s]
        if i + 1 < len(offs):
            nxt = offs[i + 1]
            # 次の音までの短い隙間 (8分音符以下) は埋めて読みやすくする
            e = nxt if nxt - e <= 0.5 else min(e, nxt)
        out.append((s, e - s, groups[s]))
    return out


def _chord_figure(name: str, semis: int, flats: bool) -> str | None:
    if name == "N":
        return None
    root = name[:2] if len(name) > 1 and name[1] in "#b" else name[:1]
    qual = name[len(root):]
    idx = (PITCH_NAMES_SHARP.index(root) if root in PITCH_NAMES_SHARP else PITCH_NAMES_FLAT.index(root))
    idx = (idx + semis) % 12
    r = (PITCH_NAMES_FLAT if flats else PITCH_NAMES_SHARP)[idx]
    return r.replace("b", "-") + qual


DRUM_LABEL = {36: "K", 38: "S", 42: "H", 49: "C", 45: "T"}  # キック/スネア/ハイハット/シンバル/タム


def note_label(p: pitch.Pitch, mode: str) -> str:
    """音名ラベル。mode: letter (C D E) / letter_oct (C4)。"""
    acc = ""
    if p.accidental is not None:
        acc = {"sharp": "♯", "flat": "♭", "double-sharp": "𝄪", "double-flat": "𝄫"}.get(p.accidental.name, "")
    if mode == "letter_oct":
        return f"{p.step}{acc}{p.octave}"
    return p.step + acc


def _add_labels(el, pitches, mode):
    """音符の下に音名を歌詞として付ける (和音は上の音から順に縦に並べる)。"""
    if not mode:
        return
    for i, pp in enumerate(sorted(pitches, key=lambda x: -x.ps)):
        el.addLyric(note_label(pp, mode), lyricNumber=i + 1)


def _build_pitched(notes, grid, flats, label, clef_obj, part_cls=stream.Part, names=None):
    p = part_cls()
    p.partName = label
    p.insert(0, clef_obj)
    for off, dur, grp in _events(notes, grid):
        if len(grp) == 1:
            el = note.Note(_spell(grp[0]["pitch"], flats))
            _add_labels(el, [el.pitch], names)
        else:
            el = chord.Chord([_spell(m, flats) for m in sorted({g["pitch"] for g in grp})])
            _add_labels(el, el.pitches, names)
        el.quarterLength = dur
        el.volume.velocity = max(g["velocity"] for g in grp)
        p.insert(off, el)
    return p


def _build_drums(notes, grid, names=None):
    p = stream.Part()
    p.partName = "Drums"
    p.insert(0, instrument.UnpitchedPercussion())
    p.insert(0, clef.PercussionClef())
    for off, dur, grp in _events(notes, grid):
        els = []
        for n in grp:
            step, octv, head = DRUM_DISPLAY.get(n["pitch"], ("C", 5, "normal"))
            u = note.Unpitched(displayName=f"{step}{octv}")
            u.notehead = head
            u.stemDirection = "up"
            els.append(u)
        el = els[0] if len(els) == 1 else percussion.PercussionChord(els)
        if names:
            for i, n in enumerate(sorted(grp, key=lambda n: -DRUM_DISPLAY.get(n["pitch"], ("C", 5))[1] * 10
                                         - "CDEFGAB".index(DRUM_DISPLAY.get(n["pitch"], ("C", 5))[0]))):
                el.addLyric(DRUM_LABEL.get(n["pitch"], "?"), lyricNumber=i + 1)
        el.quarterLength = dur
        p.insert(off, el)
    return p


def build_score(parts: list[tuple[str, list[dict]]], analysis: dict, title: str,
                transpose: int = 0, division: int = 4, chords: bool = True, label_mode: str | None = None) -> str:
    """parts: [(パート名, ノート列)] → MusicXML 文字列。"""
    bpm = analysis.get("bpm") or 120
    grid = Grid(bpm, analysis.get("downbeat", analysis.get("first_beat", 0.0)), division)
    k = analysis.get("key") or {"tonic": 0, "mode": "major"}
    tonic = (k["tonic"] + transpose) % 12
    flats = key_uses_flats(tonic, k["mode"])
    names = PITCH_NAMES_FLAT if flats else PITCH_NAMES_SHARP
    ksig = key.Key(names[tonic].replace("b", "-"), k["mode"])

    score = stream.Score()
    from music21 import metadata
    score.metadata = metadata.Metadata()
    score.metadata.movementName = title

    chord_added = False
    for name, notes in parts:
        label = PART_LABELS.get(name, name)
        if name == "drums":
            built = [_build_drums(notes, grid, label_mode)]
        else:
            tn = [dict(n, pitch=min(127, max(0, n["pitch"] + transpose))) for n in notes]
            if name == "piano":
                rh = [n for n in tn if n["pitch"] >= 60]
                lh = [n for n in tn if n["pitch"] < 60]
                built = [_build_pitched(rh, grid, flats, "Piano", clef.TrebleClef(), stream.PartStaff, label_mode),
                         _build_pitched(lh, grid, flats, "Piano", clef.BassClef(), stream.PartStaff, label_mode)]
            else:
                med = sorted(n["pitch"] for n in tn)[len(tn) // 2] if tn else 60
                c = clef.BassClef() if (name == "bass" or med < 55) else clef.TrebleClef()
                if name == "bass" and med >= 40:
                    c = clef.BassClef()
                built = [_build_pitched(tn, grid, flats, label, c, names=label_mode)]
            for b in built:
                b.insert(0, ksig.__deepcopy__())
        for i, b in enumerate(built):
            b.insert(0, meter.TimeSignature("4/4"))
            if not score.parts:
                b.insert(0, tempo.MetronomeMark(number=round(bpm)))
            # コード記号は最初の音程パートにだけ付ける (リードシート風)
            if chords and not chord_added and name != "drums" and i == 0:
                for c in analysis.get("chords", []):
                    fig = _chord_figure(c["chord"], transpose, flats)
                    if not fig:
                        continue
                    try:
                        cs = harmony.ChordSymbol(fig)
                    except Exception:  # noqa: BLE001
                        continue
                    cs.writeAsChord = False
                    b.insert(grid.q(c["start"]), cs)
                chord_added = True
            score.insert(0, b)
        if name == "piano" and len(built) == 2:
            score.insert(0, layout.StaffGroup(built, name="Piano", abbreviation="Pno.", symbol="brace"))

    end = max((p.highestTime for p in score.parts), default=4.0)
    for p in score.parts:
        p.makeRests(fillGaps=True, inPlace=True, timeRangeFromBarDuration=True)
        # 全パートの長さを揃える
        if p.highestTime < end:
            r = note.Rest(quarterLength=end - p.highestTime)
            p.append(r)
    made = score.makeNotation()
    return GeneralObjectExporter(made).parse().decode("utf-8")
