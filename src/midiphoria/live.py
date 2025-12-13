from __future__ import annotations

import argparse
import collections
import threading
import time
from dataclasses import dataclass, field
from typing import Deque, Dict, Optional, Set, Tuple

import mido
import torch

from .envelope import ADSR
from .midi import MidiMapping


@dataclass
class AppState:
    mapping: MidiMapping = field(default_factory=MidiMapping)
    learn_mode: bool = False
    debug_overlay: bool = True
    velocity_sensitive: bool = False
    adsr: ADSR = field(default_factory=ADSR)

    trigger_mode: str = "mapped"  # "mapped", "all_notes", "note_set"
    note_set: Set[int] = field(default_factory=set)
    color_mode: bool = False
    learn_add_to_set: bool = False

    active_notes: Set[int] = field(default_factory=set)
    note_levels: Dict[int, float] = field(default_factory=dict)

    gate_active: bool = False
    last_velocity: float = 1.0
    last_event: Optional[mido.Message] = None


def run_live(args: argparse.Namespace) -> None:
    import pyglet  # local import to keep export mode headless-friendly
    from pyglet import gl

    from .colors import note_color
    from .controller import MidiController
    from .envelope import ADSR, GlobalEnvelope
    from .midi import MidiInput, MidiMapping, TestMidiGenerator
    from .recording import MidiRecorder

    state = AppState()
    state.trigger_mode = args.trigger_mode or "mapped"
    state.color_mode = bool(args.color_mode) if args.color_mode is not None else False
    state.velocity_sensitive = (
        bool(args.velocity_sensitive) if args.velocity_sensitive is not None else False
    )

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

    device = torch.device("cuda") if torch.cuda.is_available() else torch.device("cpu")
    envelope = GlobalEnvelope(device=device, adsr=state.adsr)
    event_log: Deque[str] = collections.deque(maxlen=200)
    lock = threading.Lock()
    controller = MidiController(state, envelope, event_log=event_log)

    recorder: Optional[MidiRecorder] = None
    if args.record:
        recorder = MidiRecorder(
            args.record,
            meta_state={
                "mapping": {"kind": state.mapping.kind, "channel": state.mapping.channel, "number": state.mapping.number},
                "trigger_mode": state.trigger_mode,
                "note_set": sorted(state.note_set),
                "color_mode": state.color_mode,
                "velocity_sensitive": state.velocity_sensitive,
                "adsr": {"attack": state.adsr.attack, "decay": state.adsr.decay, "sustain": state.adsr.sustain, "release": state.adsr.release},
            },
        )
        recorder.start()
        event_log.appendleft(f"Recording MIDI: {args.record}")

    class VisualizerWindow(pyglet.window.Window):
        def __init__(self):
            super().__init__(caption="Midiphoria", resizable=True, width=960, height=540)
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
            with lock:
                level = float(envelope.level.detach().clamp(0, 1).cpu().item())
                active_any = bool(state.active_notes)
                if state.color_mode and active_any:
                    base_r, base_g, base_b = note_color(
                        active_notes=sorted(state.active_notes),
                        note_levels=state.note_levels,
                        velocity_sensitive=state.velocity_sensitive,
                    )
                    r, g, b = base_r * level, base_g * level, base_b * level
                else:
                    r = g = b = level
            gl.glClearColor(r, g, b, 1.0)
            gl.glClear(gl.GL_COLOR_BUFFER_BIT)
            if state.debug_overlay:
                self._draw_overlay(level)

        def _draw_overlay(self, level: float):
            with lock:
                recent = "\n".join(list(event_log)[-12:])
                mapping = state.mapping
                trigger_mode = state.trigger_mode
                color_mode = state.color_mode
                learn_mode = state.learn_mode
                learn_add_to_set = state.learn_add_to_set
                velocity_sensitive = state.velocity_sensitive
                adsr = state.adsr
                active_notes = sorted(state.active_notes)
                note_set = sorted(state.note_set)

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
            with lock:
                envelope.step(dt)

        def on_key_press(self, symbol, modifiers):
            from pyglet.window import key

            with lock:
                if symbol == key.ESCAPE:
                    self.close()
                    return

                if symbol == key.F:
                    self.set_fullscreen(not self.fullscreen)
                    return

                if symbol == key.D:
                    state.debug_overlay = not state.debug_overlay
                    return

                if symbol == key.L:
                    state.learn_mode = not state.learn_mode
                    event_log.appendleft(f"Learn mode {'ON' if state.learn_mode else 'off'}")
                    return

                if symbol == key.N:
                    modes = ["mapped", "all_notes", "note_set"]
                    try:
                        idx = modes.index(state.trigger_mode)
                    except ValueError:
                        idx = 0
                    state.trigger_mode = modes[(idx + 1) % len(modes)]
                    state.active_notes.clear()
                    state.note_levels.clear()
                    envelope.gate_off()
                    state.gate_active = False
                    event_log.appendleft(f"Trigger mode: {state.trigger_mode}")
                    return

                if symbol == key.K:
                    state.color_mode = not state.color_mode
                    event_log.appendleft(f"Color mode {'ON' if state.color_mode else 'off'}")
                    return

                if symbol == key.A:
                    state.learn_add_to_set = not state.learn_add_to_set
                    if state.learn_add_to_set:
                        state.trigger_mode = "note_set"
                        event_log.appendleft("Add-to-set learn ON (next note adds)")
                    else:
                        event_log.appendleft("Add-to-set learn off")
                    return

                if symbol == key.C:
                    state.note_set.clear()
                    event_log.appendleft("Note set cleared")
                    return

                if symbol == key.V:
                    state.velocity_sensitive = not state.velocity_sensitive
                    event_log.appendleft(f"Velocity sensitive {'ON' if state.velocity_sensitive else 'off'}")
                    return

                if symbol == key.R:
                    state.adsr = ADSR()
                    envelope.reset(state.adsr)
                    event_log.appendleft("ADSR reset")
                    return

                step_small = 0.05
                if symbol == key._1:
                    state.adsr.attack = max(0.0, state.adsr.attack - step_small)
                elif symbol == key._2:
                    state.adsr.attack += step_small
                elif symbol == key._3:
                    state.adsr.decay = max(0.0, state.adsr.decay - step_small)
                elif symbol == key._4:
                    state.adsr.decay += step_small
                elif symbol == key._5:
                    state.adsr.sustain = max(0.0, state.adsr.sustain - step_small)
                elif symbol == key._6:
                    state.adsr.sustain = min(1.0, state.adsr.sustain + step_small)
                elif symbol == key._7:
                    state.adsr.release = max(0.0, state.adsr.release - step_small)
                elif symbol == key._8:
                    state.adsr.release += step_small
                else:
                    return

                state.adsr.clamp()
                envelope.adsr = state.adsr
                event_log.appendleft(
                    f"ADSR A={state.adsr.attack:.2f} D={state.adsr.decay:.2f} "
                    f"S={state.adsr.sustain:.2f} R={state.adsr.release:.2f}"
                )

    def on_midi(msg: mido.Message):
        with lock:
            if recorder is not None:
                recorder.record(msg)
            controller.on_midi(msg)

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

    window = VisualizerWindow()
    try:
        pyglet.app.run()
    finally:
        if generator:
            generator.stop()
        if midi_in:
            midi_in.close()
        if recorder:
            recorder.stop()
