from __future__ import annotations

from typing import Deque, Optional

import mido

from .midi import MidiMapping


class MidiController:
    """
    Applies MIDI messages to AppState + GlobalEnvelope.
    Shared by live preview and offline export (replay).
    """

    def __init__(self, state, envelope, event_log: Optional[Deque[str]] = None):
        self.state = state
        self.envelope = envelope
        self.event_log = event_log

    def _log(self, text: str) -> None:
        if self.event_log is not None:
            self.event_log.appendleft(text)

    @staticmethod
    def format_msg(msg: mido.Message) -> str:
        if msg.type in ("note_on", "note_off"):
            return f"{msg.type} ch={msg.channel+1} note={msg.note} vel={msg.velocity}"
        if msg.type == "control_change":
            return f"cc ch={msg.channel+1} cc={msg.control} val={msg.value}"
        return str(msg)

    def note_allowed(self, channel: int, note: int) -> bool:
        if self.state.trigger_mode == "all_notes":
            return True
        if self.state.trigger_mode == "note_set":
            return note in self.state.note_set
        return (
            self.state.mapping.kind == "note"
            and self.state.mapping.channel == channel
            and self.state.mapping.number == note
        )

    def update_gate_target(self) -> None:
        if self.state.active_notes:
            target_level = (
                max(self.state.note_levels.values()) if self.state.velocity_sensitive else 1.0
            )
            if not self.state.gate_active:
                self.envelope.gate_on(target_level)
                self.state.gate_active = True
            else:
                self.envelope.set_target(target_level)
        else:
            if self.state.gate_active:
                self.envelope.gate_off()
                self.state.gate_active = False

    def on_midi(self, msg: mido.Message) -> None:
        self.state.last_event = msg
        self._log(self.format_msg(msg))

        if self.state.learn_add_to_set:
            if msg.type == "note_on" and msg.velocity > 0:
                self.state.note_set.add(msg.note)
                self.state.learn_add_to_set = False
                self.state.trigger_mode = "note_set"
                self._log(f"Added NOTE {msg.note} to set")

        if self.state.learn_mode:
            if msg.type == "note_on" and msg.velocity > 0:
                self.state.mapping = MidiMapping(kind="note", channel=msg.channel, number=msg.note)
                self.state.learn_mode = False
                self.state.trigger_mode = "mapped"
                self._log(f"Mapped NOTE ch={msg.channel+1} note={msg.note}")
            if msg.type == "control_change":
                self.state.mapping = MidiMapping(kind="cc", channel=msg.channel, number=msg.control)
                self.state.learn_mode = False
                self.state.trigger_mode = "mapped"
                self._log(f"Mapped CC ch={msg.channel+1} cc={msg.control}")

        if msg.type in ("note_on", "note_off"):
            if not self.note_allowed(getattr(msg, "channel", 0), getattr(msg, "note", -1)) and (
                getattr(msg, "note", -1) not in self.state.active_notes
            ):
                return

            note = msg.note
            if msg.type == "note_on" and msg.velocity > 0:
                level = (msg.velocity / 127.0) if self.state.velocity_sensitive else 1.0
                self.state.active_notes.add(note)
                self.state.note_levels[note] = level
                self.state.last_velocity = level
                self.update_gate_target()
                return

            self.state.active_notes.discard(note)
            self.state.note_levels.pop(note, None)
            self.update_gate_target()
            return

        if msg.type == "control_change":
            if self.state.trigger_mode != "mapped" or self.state.mapping.kind != "cc":
                return
            if not self.state.mapping.matches(msg):
                return

            level = (
                (msg.value / 127.0)
                if self.state.velocity_sensitive
                else (1.0 if msg.value >= 64 else 0.0)
            )
            if level > 0:
                self.envelope.gate_on(level)
                self.state.gate_active = True
            else:
                self.envelope.gate_off()
                self.state.gate_active = False
            return
