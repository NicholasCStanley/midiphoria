from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

import mido


@dataclass(frozen=True)
class MidiFileMeta:
    ticks_per_beat: int
    duration_s: float


def read_midi_file_events(
    path: str | Path,
    *,
    channels: Optional[Sequence[int]] = None,  # 0-15
    include_cc: bool = True,
    include_notes: bool = True,
) -> Tuple[MidiFileMeta, List[Tuple[float, mido.Message]]]:
    """
    Parse a .mid into absolute-time (seconds) messages using tempo meta events.

    Notes:
    - Merges all tracks; ordering between simultaneous events is preserved by merge order.
    - Tempo defaults to 500000 us/beat (120 BPM) until a set_tempo is encountered.
    """
    p = Path(path)
    midi = mido.MidiFile(p)
    ticks_per_beat = int(midi.ticks_per_beat)

    wanted_channels = set(int(c) for c in channels) if channels else None

    tempo = 500_000  # default 120bpm
    abs_s = 0.0
    duration_s = 0.0
    events: List[Tuple[float, mido.Message]] = []

    for msg in mido.merge_tracks(midi.tracks):
        delta_ticks = int(getattr(msg, "time", 0) or 0)
        if delta_ticks:
            abs_s += float(mido.tick2second(delta_ticks, ticks_per_beat, tempo))
            if abs_s > duration_s:
                duration_s = abs_s

        if msg.is_meta:
            if msg.type == "set_tempo":
                tempo = int(msg.tempo)
            continue

        if wanted_channels is not None and hasattr(msg, "channel"):
            if int(msg.channel) not in wanted_channels:
                continue

        if msg.type in ("note_on", "note_off"):
            if not include_notes:
                continue
            events.append((abs_s, msg))
            continue

        if msg.type == "control_change":
            if not include_cc:
                continue
            events.append((abs_s, msg))
            continue

    meta = MidiFileMeta(ticks_per_beat=ticks_per_beat, duration_s=duration_s)
    return meta, events

