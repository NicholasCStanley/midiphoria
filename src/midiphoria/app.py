from __future__ import annotations

import argparse
import collections
import colorsys
import threading
import time
from dataclasses import dataclass
from dataclasses import field
from typing import Deque, Dict, Optional, Set, Tuple

import pyglet
from pyglet import gl

import mido
import torch

from .envelope import ADSR, GlobalEnvelope
from .midi import MidiInput, MidiMapping, TestMidiGenerator


@dataclass
class AppState:
    mapping: MidiMapping = field(default_factory=MidiMapping)
    learn_mode: bool = False
    debug_overlay: bool = True
    velocity_sensitive: bool = False
    adsr: ADSR = field(default_factory=ADSR)

    # Input/visual modes
    trigger_mode: str = "mapped"  # "mapped", "all_notes", "note_set"
    note_set: Set[int] = field(default_factory=set)
    color_mode: bool = False
    learn_add_to_set: bool = False

    # Active notes for overlap handling.
    active_notes: Set[int] = field(default_factory=set)
    note_levels: Dict[int, float] = field(default_factory=dict)

    gate_active: bool = False
    last_velocity: float = 1.0
    last_event: Optional[mido.Message] = None


class VisualizerWindow(pyglet.window.Window):
    def __init__(
        self,
        state: AppState,
        envelope: GlobalEnvelope,
        event_log: Deque[str],
        lock: threading.Lock,
    ):
        super().__init__(caption="Midiphoria", resizable=True, width=960, height=540)
        self.state = state
        self.envelope = envelope
        self.event_log = event_log
        self.lock = lock
        self._last_time = time.perf_counter()

        self.label = pyglet.text.Label(
            "",
            x=10,
            y=self.height - 10,
            anchor_x="left",
            anchor_y="top",
            multiline=True,
            width=600,
        )

        pyglet.clock.schedule_interval(self._update, 1 / 120.0)

    def on_draw(self):
        with self.lock:
            level = float(self.envelope.level.detach().clamp(0, 1).cpu().item())
            active_any = bool(self.state.active_notes)
            color_mode = self.state.color_mode
            if color_mode and active_any:
                base_r, base_g, base_b = note_color(self.state)
                r, g, b = base_r * level, base_g * level, base_b * level
            else:
                r = g = b = level
        gl.glClearColor(r, g, b, 1.0)
        gl.glClear(gl.GL_COLOR_BUFFER_BIT)

        if self.state.debug_overlay:
            self._draw_overlay(level)

    def _draw_overlay(self, level: float):
        with self.lock:
            recent = "\n".join(list(self.event_log)[-12:])
            mapping = self.state.mapping
            trigger_mode = self.state.trigger_mode
            color_mode = self.state.color_mode
            learn_mode = self.state.learn_mode
            learn_add_to_set = self.state.learn_add_to_set
            velocity_sensitive = self.state.velocity_sensitive
            adsr = self.state.adsr
            active_notes = sorted(self.state.active_notes)
            note_set = sorted(self.state.note_set)

        active_preview = ", ".join(str(n) for n in active_notes[:8])
        if len(active_notes) > 8:
            active_preview += " …"
        note_set_preview = ", ".join(str(n) for n in note_set[:8])
        if len(note_set) > 8:
            note_set_preview += " …"
        self.label.text = (
            f"Mapping: {mapping.kind} ch={mapping.channel+1} num={mapping.number}\n"
            f"Trigger mode: {trigger_mode}\n"
            f"Note set: [{note_set_preview}] ({len(note_set)})\n"
            f"Color mode: {'ON' if color_mode else 'off'}\n"
            f"Learn mode: {'ON' if learn_mode else 'off'}\n"
            f"Add-to-set learn: {'ON' if learn_add_to_set else 'off'}\n"
            f"Velocity sensitive: {'ON' if velocity_sensitive else 'off'}\n"
            f"ADSR A={adsr.attack:.2f} D={adsr.decay:.2f} "
            f"S={adsr.sustain:.2f} R={adsr.release:.2f}\n"
            f"Level: {level:.3f}  Active notes: [{active_preview}] ({len(active_notes)})\n"
            "Keys: F fullscreen, D debug, L learn, N mode, A add note, C clear, K color, V vel\n"
            f"Recent MIDI:\n{recent}"
        )
        self.label.y = self.height - 10
        self.label.draw()

    def on_resize(self, width, height):
        super().on_resize(width, height)
        self.label.width = min(800, max(200, width - 20))

    def _update(self, _dt):
        now = time.perf_counter()
        dt = now - self._last_time
        self._last_time = now
        with self.lock:
            self.envelope.step(dt)

    def on_key_press(self, symbol, modifiers):
        from pyglet.window import key

        with self.lock:
            if symbol == key.ESCAPE:
                self.close()
                return

            if symbol == key.F:
                self.set_fullscreen(not self.fullscreen)
                return

            if symbol == key.D:
                self.state.debug_overlay = not self.state.debug_overlay
                return

            if symbol == key.L:
                self.state.learn_mode = not self.state.learn_mode
                self.event_log.appendleft(f"Learn mode {'ON' if self.state.learn_mode else 'off'}")
                return

            if symbol == key.N:
                modes = ["mapped", "all_notes", "note_set"]
                try:
                    idx = modes.index(self.state.trigger_mode)
                except ValueError:
                    idx = 0
                self.state.trigger_mode = modes[(idx + 1) % len(modes)]
                self.state.active_notes.clear()
                self.state.note_levels.clear()
                self.envelope.gate_off()
                self.state.gate_active = False
                self.event_log.appendleft(f"Trigger mode: {self.state.trigger_mode}")
                return

            if symbol == key.K:
                self.state.color_mode = not self.state.color_mode
                self.event_log.appendleft(
                    f"Color mode {'ON' if self.state.color_mode else 'off'}"
                )
                return

            if symbol == key.A:
                self.state.learn_add_to_set = not self.state.learn_add_to_set
                if self.state.learn_add_to_set:
                    self.state.trigger_mode = "note_set"
                    self.event_log.appendleft("Add-to-set learn ON (next note adds)")
                else:
                    self.event_log.appendleft("Add-to-set learn off")
                return

            if symbol == key.C:
                self.state.note_set.clear()
                self.event_log.appendleft("Note set cleared")
                return

            if symbol == key.V:
                self.state.velocity_sensitive = not self.state.velocity_sensitive
                self.event_log.appendleft(
                    f"Velocity sensitive {'ON' if self.state.velocity_sensitive else 'off'}"
                )
                return

            if symbol == key.R:
                self.state.adsr = ADSR()
                self.envelope.reset(self.state.adsr)
                self.event_log.appendleft("ADSR reset")
                return

            # ADSR tweaks
            step_small = 0.05
            if symbol == key._1:
                self.state.adsr.attack = max(0.0, self.state.adsr.attack - step_small)
            elif symbol == key._2:
                self.state.adsr.attack += step_small
            elif symbol == key._3:
                self.state.adsr.decay = max(0.0, self.state.adsr.decay - step_small)
            elif symbol == key._4:
                self.state.adsr.decay += step_small
            elif symbol == key._5:
                self.state.adsr.sustain = max(0.0, self.state.adsr.sustain - step_small)
            elif symbol == key._6:
                self.state.adsr.sustain = min(1.0, self.state.adsr.sustain + step_small)
            elif symbol == key._7:
                self.state.adsr.release = max(0.0, self.state.adsr.release - step_small)
            elif symbol == key._8:
                self.state.adsr.release += step_small
            else:
                return

            self.state.adsr.clamp()
            self.envelope.adsr = self.state.adsr
            self.event_log.appendleft(
                f"ADSR A={self.state.adsr.attack:.2f} D={self.state.adsr.decay:.2f} "
                f"S={self.state.adsr.sustain:.2f} R={self.state.adsr.release:.2f}"
            )


def _format_msg(msg: mido.Message) -> str:
    if msg.type in ("note_on", "note_off"):
        return f"{msg.type} ch={msg.channel+1} note={msg.note} vel={msg.velocity}"
    if msg.type == "control_change":
        return f"cc ch={msg.channel+1} cc={msg.control} val={msg.value}"
    return str(msg)


def note_color(state: AppState) -> Tuple[float, float, float]:
    """
    Deterministic note->color mapping.
    If multiple notes are active, returns a weighted average of their hues.
    """
    if not state.active_notes:
        return 0.0, 0.0, 0.0

    total_weight = 0.0
    r_acc = g_acc = b_acc = 0.0
    for note in state.active_notes:
        hue = (note % 128) / 128.0
        r, g, b = colorsys.hsv_to_rgb(hue, 1.0, 1.0)
        weight = state.note_levels.get(note, 1.0) if state.velocity_sensitive else 1.0
        r_acc += r * weight
        g_acc += g * weight
        b_acc += b * weight
        total_weight += weight

    if total_weight <= 0:
        return 0.0, 0.0, 0.0
    return r_acc / total_weight, g_acc / total_weight, b_acc / total_weight


def main(argv=None):
    parser = argparse.ArgumentParser(description="Midiphoria visualizer (MVP).")
    parser.add_argument("--list-ports", action="store_true", help="List MIDI input ports and exit.")
    parser.add_argument("--port", type=str, default=None, help="Substring to match a MIDI input port.")
    parser.add_argument("--all-ports", action="store_true", help="Open all MIDI input ports.")
    parser.add_argument("--generate", action="store_true", help="Enable internal test MIDI generator.")
    parser.add_argument(
        "--trigger-mode",
        choices=["mapped", "all_notes", "note_set"],
        default="mapped",
        help="Initial trigger mode.",
    )
    parser.add_argument("--color-mode", action="store_true", help="Start with color-per-note mode on.")
    parser.add_argument("--velocity-sensitive", action="store_true", help="Start with velocity sensitivity on.")
    parser.add_argument("--attack", type=float, default=None, help="Initial ADSR attack seconds.")
    parser.add_argument("--decay", type=float, default=None, help="Initial ADSR decay seconds.")
    parser.add_argument("--sustain", type=float, default=None, help="Initial ADSR sustain level 0-1.")
    parser.add_argument("--release", type=float, default=None, help="Initial ADSR release seconds.")
    parser.add_argument("--map-channel", type=int, default=None, help="Initial mapping channel (1-16).")
    parser.add_argument("--map-note", type=int, default=None, help="Initial mapping note (0-127).")
    parser.add_argument(
        "--note-set",
        type=str,
        default=None,
        help="Comma-separated initial note set, e.g. '36,38,42'.",
    )
    args = parser.parse_args(argv)

    if args.list_ports:
        for name in MidiInput.list_ports():
            print(name)
        return

    device = torch.device("cuda") if torch.cuda.is_available() else torch.device("cpu")
    state = AppState()
    state.trigger_mode = args.trigger_mode
    state.color_mode = bool(args.color_mode)
    state.velocity_sensitive = bool(args.velocity_sensitive)

    if args.attack is not None:
        state.adsr.attack = args.attack
    if args.decay is not None:
        state.adsr.decay = args.decay
    if args.sustain is not None:
        state.adsr.sustain = args.sustain
    if args.release is not None:
        state.adsr.release = args.release
    state.adsr.clamp()

    if args.map_channel is not None:
        state.mapping.channel = max(0, min(15, args.map_channel - 1))
    if args.map_note is not None:
        state.mapping.kind = "note"
        state.mapping.number = max(0, min(127, args.map_note))

    if args.note_set:
        for part in args.note_set.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                n = int(part)
            except ValueError:
                continue
            if 0 <= n <= 127:
                state.note_set.add(n)
        if state.note_set:
            state.trigger_mode = "note_set"

    envelope = GlobalEnvelope(device=device, adsr=state.adsr)
    event_log: Deque[str] = collections.deque(maxlen=200)
    lock = threading.Lock()

    def note_allowed(channel: int, note: int) -> bool:
        if state.trigger_mode == "all_notes":
            return True
        if state.trigger_mode == "note_set":
            return note in state.note_set
        return (
            state.mapping.kind == "note"
            and state.mapping.channel == channel
            and state.mapping.number == note
        )

    def update_gate_target() -> None:
        if state.active_notes:
            target_level = (
                max(state.note_levels.values()) if state.velocity_sensitive else 1.0
            )
            if not state.gate_active:
                envelope.gate_on(target_level)
                state.gate_active = True
            else:
                envelope.set_target(target_level)
        else:
            if state.gate_active:
                envelope.gate_off()
                state.gate_active = False

    def on_midi(msg: mido.Message):
        with lock:
            state.last_event = msg
            event_log.appendleft(_format_msg(msg))

            if state.learn_add_to_set:
                if msg.type == "note_on" and msg.velocity > 0:
                    state.note_set.add(msg.note)
                    state.learn_add_to_set = False
                    state.trigger_mode = "note_set"
                    event_log.appendleft(f"Added NOTE {msg.note} to set")

            # Learn mode: map next note_on or CC.
            if state.learn_mode:
                if msg.type == "note_on" and msg.velocity > 0:
                    state.mapping = MidiMapping(kind="note", channel=msg.channel, number=msg.note)
                    state.learn_mode = False
                    state.trigger_mode = "mapped"
                    event_log.appendleft(f"Mapped NOTE ch={msg.channel+1} note={msg.note}")
                if msg.type == "control_change":
                    state.mapping = MidiMapping(kind="cc", channel=msg.channel, number=msg.control)
                    state.learn_mode = False
                    state.trigger_mode = "mapped"
                    event_log.appendleft(f"Mapped CC ch={msg.channel+1} cc={msg.control}")

            if msg.type in ("note_on", "note_off"):
                if not note_allowed(getattr(msg, "channel", 0), getattr(msg, "note", -1)) and (
                    getattr(msg, "note", -1) not in state.active_notes
                ):
                    return

                note = msg.note
                if msg.type == "note_on" and msg.velocity > 0:
                    level = (msg.velocity / 127.0) if state.velocity_sensitive else 1.0
                    state.active_notes.add(note)
                    state.note_levels[note] = level
                    state.last_velocity = level
                    update_gate_target()
                    return

                # note_off or note_on velocity 0
                state.active_notes.discard(note)
                state.note_levels.pop(note, None)
                update_gate_target()
                return

            if msg.type == "control_change":
                if state.trigger_mode != "mapped" or state.mapping.kind != "cc":
                    return
                if not state.mapping.matches(msg):
                    return

                level = (
                    (msg.value / 127.0)
                    if state.velocity_sensitive
                    else (1.0 if msg.value >= 64 else 0.0)
                )
                if level > 0:
                    envelope.gate_on(level)
                    state.gate_active = True
                else:
                    envelope.gate_off()
                    state.gate_active = False
                return

    ports = MidiInput.list_ports()
    selected_ports = []
    if args.all_ports:
        selected_ports = ports
    elif args.port:
        selected_ports = [p for p in ports if args.port.lower() in p.lower()]
    elif ports:
        selected_ports = [ports[0]]

    midi_in = MidiInput(selected_ports, on_midi) if selected_ports else None
    if midi_in:
        midi_in.open()
        event_log.appendleft(f"Opened ports: {', '.join(selected_ports)}")
    else:
        event_log.appendleft("No MIDI input ports opened")

    generator = None
    if args.generate:
        generator = TestMidiGenerator(on_midi)
        generator.start()
        event_log.appendleft("Internal MIDI generator ON")

    window = VisualizerWindow(state, envelope, event_log, lock)
    try:
        pyglet.app.run()
    finally:
        if generator:
            generator.stop()
        if midi_in:
            midi_in.close()


if __name__ == "__main__":
    main()
