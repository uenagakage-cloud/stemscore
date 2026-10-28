"""ギター / ベースの TAB 譜。

採譜したノートを「何弦の何フレット」に割り当て、MusicXML の TAB 譜 (6 線 / 4 線) として出力する。
割り当ては、手の位置 (ポジション) の移動が少なく、和音が押さえられる形を優先する。
キー変更後の音で割り当てるので、移調すると押さえる場所も変わる。
"""
from __future__ import annotations

import itertools
import re
from xml.etree.ElementTree import Element

from music21 import articulations, chord, clef, note, stream
from music21.musicxml import m21ToXml

# 弦の開放音 (MIDI)。1 弦 (一番高い弦) から順
TUNINGS = {
    "guitar": [64, 59, 55, 50, 45, 40],   # E4 B3 G3 D3 A2 E2 (レギュラーチューニング)
    "bass": [43, 38, 33, 28],             # G2 D2 A1 E1
}
MAX_FRET = {"guitar": 20, "bass": 20}
TUNING_NAMES = {"guitar": ["E", "B", "G", "D", "A", "E"], "bass": ["G", "D", "A", "E"]}
TUNING_OCTAVES = {"guitar": [4, 3, 3, 3, 2, 2], "bass": [2, 2, 1, 1]}
# TAB 譜を作れるパート (「その他」は 4 パート分離でギターが入るところなので、ギターとして扱う)
TAB_PARTS = {"guitar": "guitar", "bass": "bass", "other": "guitar"}


def _fold(pitch: int, kind: str) -> int:
    """楽器の音域外の音は、オクターブ単位で音域内に移す。"""
    lo = min(TUNINGS[kind])
    hi = max(TUNINGS[kind]) + MAX_FRET[kind]
    while pitch < lo:
        pitch += 12
    while pitch > hi:
        pitch -= 12
    return pitch


def _candidates(pitch: int, kind: str) -> list[tuple[int, int]]:
    return [(s, pitch - open_) for s, open_ in enumerate(TUNINGS[kind], 1)
            if 0 <= pitch - open_ <= MAX_FRET[kind]]


def assign_frets(events: list[list[int]], kind: str) -> list[list[tuple[int, int, int]]]:
    """events: 同時に鳴る音 (MIDI) のリストの列 → [(音, 弦, フレット)] の列。

    手の位置からの移動距離・和音の指の広がり・高いフレットを避けるようにコストを付けて選ぶ。
    """
    n_strings = len(TUNINGS[kind])
    pos = 3.0   # 手の位置 (フレット)
    out = []
    for pitches in events:
        ps = sorted({_fold(p, kind) for p in pitches}, reverse=True)[:n_strings]
        cands = [_candidates(p, kind) for p in ps]
        best, best_cost = None, None
        for combo in itertools.product(*cands):
            strings = [s for s, _ in combo]
            if len(set(strings)) != len(strings):
                continue                       # 1 本の弦で 2 音は出せない
            frets = [f for _, f in combo if f > 0]
            span = (max(frets) - min(frets)) if frets else 0
            if span > 4:
                continue                       # 指が届かない
            center = sum(frets) / len(frets) if frets else pos
            cost = abs(center - pos) + 0.5 * span + 0.05 * (max(frets) if frets else 0)
            if best_cost is None or cost < best_cost:
                best, best_cost = combo, cost
        if best is None:                        # 押さえられない和音は、一番高い音から順に入るだけ
            used, best = set(), []
            for p, cs in zip(ps, cands):
                c = min((c for c in cs if c[0] not in used), key=lambda c: abs(c[1] - pos), default=None)
                if c:
                    used.add(c[0])
                    best.append(c)
            ps = ps[: len(best)]
        frets = [f for _, f in best if f > 0]
        if frets:
            pos = 0.7 * pos + 0.3 * (sum(frets) / len(frets))
        out.append([(p, s, f) for p, (s, f) in zip(ps, best)])
    return out


def build_tab_part(events: list[tuple[float, float, list[dict]]], kind: str, label: str) -> stream.Part:
    """events: notation._events の結果 (オフセット, 長さ, ノート群) → TAB 譜のパート。"""
    part = stream.Part()
    part.partName = label
    part.insert(0, clef.TabClef())
    assigned = assign_frets([[n["pitch"] for n in grp] for _, _, grp in events], kind)
    for (off, dur, _), notes in zip(events, assigned):
        if not notes:
            continue
        objs = []
        for p, s, f in notes:
            nn = note.Note(p)
            nn.articulations += [articulations.StringIndication(s), articulations.FretIndication(f)]
            objs.append(nn)
        el = objs[0] if len(objs) == 1 else chord.Chord(objs)
        el.quarterLength = dur
        part.insert(off, el)
    return part


# ── MusicXML 出力の補足 ──
# music21 は和音の 2 音目以降の弦・フレットを書き出さないので、各音の分も書き出すようにする
_orig_note_to_notations = m21ToXml.MeasureExporter.noteToNotations


def _note_to_notations(self, n, noteIndexInChord=0, chordParent=None):
    out = _orig_note_to_notations(self, n, noteIndexInChord, chordParent)
    if chordParent is not None:
        techs = [a for a in n.articulations
                 if isinstance(a, (articulations.StringIndication, articulations.FretIndication))]
        if techs:
            mx = next((e for e in out if e.tag == "technical"), None)
            if mx is None:
                mx = Element("technical")
                out.append(mx)
            for a in techs:
                mx.append(self.articulationToXmlTechnical(a))
    return out


m21ToXml.MeasureExporter.noteToNotations = _note_to_notations


def add_staff_details(xml: str) -> str:
    """TAB 譜のパートに、線の数と弦のチューニング (<staff-details>) を書き加える。"""
    names = dict(re.findall(r'<score-part id="([^"]+)">\s*<part-name>([^<]*)</part-name>', xml))

    def details(kind: str) -> str:
        steps, octs = TUNING_NAMES[kind], TUNING_OCTAVES[kind]
        lines = len(steps)
        # staff-tuning の line 1 は一番下の線 (= 一番低い弦)
        tun = "".join(
            f'<staff-tuning line="{i + 1}"><tuning-step>{st}</tuning-step><tuning-octave>{oc}</tuning-octave></staff-tuning>'
            for i, (st, oc) in enumerate(zip(reversed(steps), reversed(octs))))
        return f"<staff-details><staff-lines>{lines}</staff-lines>{tun}</staff-details>"

    def fix_part(m: re.Match) -> str:
        body = m.group(0)
        if "<sign>TAB</sign>" not in body:
            return body
        kind = "bass" if "Bass" in names.get(m.group(1), "") else "guitar"
        return re.sub(r"(<clef>\s*<sign>TAB</sign>.*?</clef>)", lambda c: c.group(1) + details(kind), body,
                      count=1, flags=re.S)

    return re.sub(r'<part id="([^"]+)">.*?</part>', fix_part, xml, flags=re.S)
