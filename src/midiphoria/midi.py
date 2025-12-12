from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional

import mido


MidiCallback = Callable[[mido.Message], None]


@dataclass
class MidiMapping:
    kind: str = "note"  # "note" or "cc"
    channel: int = 0
    number: int = 60  # note or cc number

    def matches(self, msg: mido.Message) -> bool:
        if msg.type in ("note_on", "note_off") and self.kind == "note":
            return getattr(msg, "channel", 0) == self.channel and getattr(msg, "note", -1) == self.number
        if msg.type == "control_change" and self.kind == "cc":
            return getattr(msg, "channel", 0) == self.channel and getattr(msg, "control", -1) == self.number
        return False


class MidiInput:
    def __init__(self, ports: Iterable[str], on_message: MidiCallback):
        self.ports = list(ports)
        self.on_message = on_message
        self._inputs: List[mido.ports.BaseInput] = []

    @staticmethod
    def list_ports() -> List[str]:
        return mido.get_input_names()

    def open(self) -> None:
        for name in self.ports:
            try:
                inp = mido.open_input(name, callback=self.on_message)
                self._inputs.append(inp)
            except Exception:
                continue

    def close(self) -> None:
        for inp in self._inputs:
            try:
                inp.close()
            except Exception:
                pass
        self._inputs.clear()


class TestMidiGenerator:
    """
    Simple internal MIDI generator for debugging.
    Sends a note-on/off pulse every second on a background thread.
    """

    def __init__(self, on_message: MidiCallback, channel: int = 0, note: int = 60, velocity: int = 127):
        self.on_message = on_message
        self.channel = channel
        self.note = note
        self.velocity = velocity
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            self.on_message(mido.Message("note_on", channel=self.channel, note=self.note, velocity=self.velocity))
            time.sleep(0.2)
            self.on_message(mido.Message("note_off", channel=self.channel, note=self.note, velocity=0))
            time.sleep(0.8)

