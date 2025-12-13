from __future__ import annotations

import argparse

from .export import export_midi_file, export_recording
from .midi import MidiInput


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Midiphoria visualizer (MVP).")

    parser.add_argument("--list-ports", action="store_true", help="List MIDI input ports and exit.")
    parser.add_argument("--port", type=str, default=None, help="Substring to match a MIDI input port.")
    parser.add_argument("--all-ports", action="store_true", help="Open all MIDI input ports.")
    parser.add_argument("--generate", action="store_true", help="Enable internal test MIDI generator.")
    parser.add_argument(
        "--trigger-mode",
        choices=["mapped", "all_notes", "note_set"],
        default=None,
        help="Initial trigger mode.",
    )
    parser.add_argument(
        "--color-mode",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Start with color-per-note mode on/off.",
    )
    parser.add_argument(
        "--velocity-sensitive",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Start with velocity sensitivity on/off.",
    )
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

    parser.add_argument(
        "--record",
        type=str,
        default=None,
        help="Record incoming MIDI to a JSONL file (for later deterministic export).",
    )

    parser.add_argument(
        "--export-recording",
        type=str,
        default=None,
        help="Export frames/video from a previously recorded JSONL recording, then exit.",
    )
    parser.add_argument(
        "--ignore-recording-state",
        action="store_true",
        help="For --export-recording: ignore recorded state snapshot and use CLI flags instead.",
    )
    parser.add_argument(
        "--export-midi-file",
        type=str,
        default=None,
        help="Export frames/video from a .mid file (tempo-aware), then exit.",
    )
    parser.add_argument(
        "--midi-channel",
        type=int,
        action="append",
        default=None,
        help="Filter MIDI channels for --export-midi-file (1-16); repeatable.",
    )
    parser.add_argument(
        "--midi-duration",
        choices=["events", "file"],
        default="events",
        help="For --export-midi-file: use duration of last note/CC event, or full file duration.",
    )
    parser.add_argument("--fps", type=float, default=24.0, help="Export FPS.")
    parser.add_argument("--width", type=int, default=512, help="Export width (pixels).")
    parser.add_argument("--height", type=int, default=512, help="Export height (pixels).")
    parser.add_argument("--out-dir", type=str, default=None, help="Output directory for frame export.")
    parser.add_argument("--frame-format", choices=["ppm", "png"], default="ppm", help="Frame format for --out-dir.")
    parser.add_argument("--mp4", type=str, default=None, help="Optional MP4 output path (requires ffmpeg).")
    parser.add_argument(
        "--audio",
        type=str,
        default=None,
        help="Optional audio file to mux into --mp4 (e.g. wav/mp3).",
    )
    parser.add_argument(
        "--audio-from-midi",
        action="store_true",
        help="For --export-midi-file: render MIDI to audio via FluidSynth and mux into --mp4 (requires --soundfont).",
    )
    parser.add_argument(
        "--soundfont",
        type=str,
        default=None,
        help="SoundFont .sf2 path for --audio-from-midi.",
    )
    parser.add_argument("--shutter", choices=["sample", "max", "avg"], default="sample", help="Frame shutter mode.")
    parser.add_argument("--subsamples", type=int, default=8, help="Subsamples per frame for avg/max.")
    parser.add_argument("--sample-at", choices=["start", "center", "end"], default="end", help="Sample time for shutter=sample.")
    parser.add_argument("--start-time", type=float, default=0.0, help="Export start time (seconds).")
    parser.add_argument("--end-time", type=float, default=None, help="Export end time (seconds).")
    parser.add_argument("--tail", type=float, default=1.0, help="Extra tail seconds past last event (default 1.0).")
    return parser


def main(argv=None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_ports:
        for name in MidiInput.list_ports():
            print(name)
        return

    if args.export_recording:
        from .live import AppState

        overrides = {}
        if args.trigger_mode is not None:
            overrides["trigger_mode"] = args.trigger_mode
        if args.color_mode is not None:
            overrides["color_mode"] = bool(args.color_mode)
        if args.velocity_sensitive is not None:
            overrides["velocity_sensitive"] = bool(args.velocity_sensitive)
        if args.map_channel is not None or args.map_note is not None:
            overrides["mapping"] = {}
            if args.map_channel is not None:
                overrides["mapping"]["channel"] = max(0, min(15, args.map_channel - 1))
            if args.map_note is not None:
                overrides["mapping"]["kind"] = "note"
                overrides["mapping"]["number"] = max(0, min(127, args.map_note))
        if args.attack is not None or args.decay is not None or args.sustain is not None or args.release is not None:
            overrides["adsr"] = {}
            if args.attack is not None:
                overrides["adsr"]["attack"] = args.attack
            if args.decay is not None:
                overrides["adsr"]["decay"] = args.decay
            if args.sustain is not None:
                overrides["adsr"]["sustain"] = args.sustain
            if args.release is not None:
                overrides["adsr"]["release"] = args.release

        state = AppState()
        if args.note_set:
            note_list = []
            for p in args.note_set.split(","):
                p = p.strip()
                if not p:
                    continue
                try:
                    n = int(p)
                except ValueError:
                    continue
                if 0 <= n <= 127:
                    note_list.append(n)
            overrides["note_set"] = note_list

        result = export_recording(
            recording_path=args.export_recording,
            fps=args.fps,
            width=args.width,
            height=args.height,
            out_dir=args.out_dir,
            frame_format=args.frame_format,
            mp4_path=args.mp4,
            audio_path=args.audio,
            shutter=args.shutter,
            subsamples=args.subsamples,
            sample_at=args.sample_at,
            start_time_s=args.start_time,
            end_time_s=args.end_time,
            tail_s=args.tail,
            use_recording_meta=not args.ignore_recording_state,
            state=state,
            overrides_state=overrides or None,
        )
        print(f"Exported {result['frames']} frames @ {result['fps']}fps ({result['width']}x{result['height']})")
        if result.get("out_dir"):
            print(f"Frames: {result['out_dir']}")
        if result.get("mp4"):
            print(f"MP4: {result['mp4']}")
        return

    if args.export_midi_file:
        # Sensible default for MIDI file export unless user explicitly configured mapping/note_set.
        if (
            (args.trigger_mode or "mapped") == "mapped"
            and args.map_note is None
            and args.map_channel is None
            and not args.note_set
        ):
            args.trigger_mode = "all_notes"

        from .live import AppState

        state = AppState()
        state.trigger_mode = args.trigger_mode or "mapped"
        state.color_mode = bool(args.color_mode) if args.color_mode is not None else False
        state.velocity_sensitive = bool(args.velocity_sensitive) if args.velocity_sensitive is not None else False
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

        channels = None
        if args.midi_channel:
            channels = []
            for ch in args.midi_channel:
                try:
                    ch_i = int(ch)
                except Exception:
                    continue
                if 1 <= ch_i <= 16:
                    channels.append(ch_i - 1)

        if args.audio_from_midi and not args.mp4:
            raise SystemExit("--audio-from-midi requires --mp4 (it muxes audio into the MP4).")
        if args.audio_from_midi and not args.soundfont:
            raise SystemExit("--audio-from-midi requires --soundfont /path/to.sf2")
        if args.audio and args.audio_from_midi:
            raise SystemExit("Use either --audio or --audio-from-midi (not both).")

        result = export_midi_file(
            midi_path=args.export_midi_file,
            fps=args.fps,
            width=args.width,
            height=args.height,
            out_dir=args.out_dir,
            frame_format=args.frame_format,
            mp4_path=args.mp4,
            audio_path=args.audio,
            audio_from_midi=bool(args.audio_from_midi),
            soundfont_path=args.soundfont,
            shutter=args.shutter,
            subsamples=args.subsamples,
            sample_at=args.sample_at,
            start_time_s=args.start_time,
            end_time_s=args.end_time,
            tail_s=args.tail,
            state=state,
            channels=channels,
            midi_duration=args.midi_duration,
        )
        print(f"Exported {result['frames']} frames @ {result['fps']}fps ({result['width']}x{result['height']})")
        if result.get("out_dir"):
            print(f"Frames: {result['out_dir']}")
        if result.get("mp4"):
            print(f"MP4: {result['mp4']}")
        return

    from .live import run_live

    run_live(args)


if __name__ == "__main__":
    main()
