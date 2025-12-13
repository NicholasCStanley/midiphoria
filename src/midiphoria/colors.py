from __future__ import annotations

import colorsys
from typing import Mapping, Sequence, Tuple


def note_color(
    active_notes: Sequence[int],
    note_levels: Mapping[int, float],
    velocity_sensitive: bool,
) -> Tuple[float, float, float]:
    """
    Deterministic note->color mapping.
    If multiple notes are active, returns a weighted average of their hues.
    """
    if not active_notes:
        return 0.0, 0.0, 0.0

    total_weight = 0.0
    r_acc = g_acc = b_acc = 0.0
    for note in active_notes:
        hue = (note % 128) / 128.0
        r, g, b = colorsys.hsv_to_rgb(hue, 1.0, 1.0)
        weight = note_levels.get(note, 1.0) if velocity_sensitive else 1.0
        r_acc += r * weight
        g_acc += g * weight
        b_acc += b * weight
        total_weight += weight

    if total_weight <= 0:
        return 0.0, 0.0, 0.0
    return r_acc / total_weight, g_acc / total_weight, b_acc / total_weight

