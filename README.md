<div align="center">
  <img src="src/midiphoria/assets/midiphoria-logo.gif" alt="Midiphoria Logo" width="600">
  <br>
</div>

# Midiphoria

MIDI‑reactive mask generator for generative AI workflows.

Midiphoria has two primary modes:

- **Live preview**: OpenGL window reacts to incoming MIDI in real time.
- **Deterministic export**: render fixed‑resolution frames or an MP4 at a chosen FPS from:
  - a recorded MIDI session (`.jsonl`), or
  - a MIDI file (`.mid`, tempo‑aware).

## Requirements

- Python 3.10+
- MIDI backend: `python-rtmidi` (for live input)

Optional tools depending on features:

- MP4 export: `ffmpeg`
- PNG frames: `Pillow` (`pip install Pillow`)
- “Audio from MIDI” in MP4: `fluidsynth` + a SoundFont (`.sf2`)

## Install

Create and activate a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Install Midiphoria into the environment (recommended):

```bash
pip install -e .
```

If you don’t want editable install, run from the repo root with:

```bash
PYTHONPATH=src python -m midiphoria.app --help
```

## Live preview

List MIDI input ports:

```bash
python -m midiphoria.app --list-ports
```

Start the visualizer (auto‑opens the first port if any):

```bash
python -m midiphoria.app
```

Useful flags:

```bash
# Any note triggers, binary on/off
python -m midiphoria.app --trigger-mode all_notes

# Color-per-note + velocity sensitivity + ADSR
python -m midiphoria.app --color-mode --velocity-sensitive --attack 0.05 --decay 0.1 --sustain 0.7 --release 0.2

# Preconfigure a drum note-set
python -m midiphoria.app --trigger-mode note_set --note-set "36,38,42,46"
```

## Deterministic export (fixed resolution)

### Record then export

Record incoming MIDI to a JSONL file (includes a snapshot of the startup state: mapping/mode/ADSR/color/velocity):

```bash
python -m midiphoria.app --record recordings/take01.jsonl
```

Export frames:

```bash
# PPM frames (no extra deps)
python -m midiphoria.app --export-recording recordings/take01.jsonl --fps 24 --width 512 --height 512 --out-dir frames --frame-format ppm

# PNG frames (requires Pillow)
python -m midiphoria.app --export-recording recordings/take01.jsonl --fps 24 --width 512 --height 512 --out-dir frames --frame-format png
```

Export MP4 (requires `ffmpeg`):

```bash
python -m midiphoria.app --export-recording recordings/take01.jsonl --fps 24 --width 512 --height 512 --mp4 out.mp4
```

State snapshot behavior:

- By default, `--export-recording` applies the recorded state snapshot.
- Use `--ignore-recording-state` to ignore the snapshot and use only CLI flags.
- You can also override specific fields on export (e.g. `--color-mode`, ADSR flags).

### Export directly from a .mid file

Basic:

```bash
python -m midiphoria.app --export-midi-file song.mid --fps 24 --width 512 --height 512 --out-dir frames --frame-format ppm
```

With visual parameters:

```bash
python -m midiphoria.app --export-midi-file song.mid --fps 24 --width 512 --height 512 --mp4 out.mp4 \
  --color-mode --velocity-sensitive --attack 0.03 --decay 0.08 --sustain 0.7 --release 0.12
```

Duration control:

- `--midi-duration events` (default): end time is the last note/CC event (+ `--tail`)
- `--midi-duration file`: end time is the full MIDI file length (+ `--tail`)

```bash
python -m midiphoria.app --export-midi-file song.mid --midi-duration file --fps 24 --width 512 --height 512 --mp4 out.mp4
```

Channel filter (repeatable, 1–16):

```bash
python -m midiphoria.app --export-midi-file song.mid --midi-channel 10 --fps 24 --width 512 --height 512 --out-dir frames
```

## MP4 with audio

MP4 containers can’t “play” a raw `.mid`; MIDI must be synthesized to audio first.

### Mux an existing audio file

```bash
python -m midiphoria.app --export-midi-file song.mid --fps 24 --width 512 --height 512 --mp4 out.mp4 --audio song.wav
```

### Render MIDI to audio and mux it

Requires `fluidsynth` + a SoundFont `.sf2`:

```bash
python -m midiphoria.app --export-midi-file song.mid --fps 24 --width 512 --height 512 --mp4 out.mp4 \
  --audio-from-midi --soundfont /path/to/soundfont.sf2
```

Ubuntu/Debian install:

```bash
sudo apt update
sudo apt install fluidsynth fluid-soundfont-gm
```

## SoundFonts

Common locations on Ubuntu:

- `/usr/share/sounds/sf2/`
- `/usr/share/soundfonts/`
- `~/.local/share/soundfonts/`

Find SoundFonts:

```bash
find /usr/share ~/.local/share -type f \( -iname "*.sf2" -o -iname "*.sf3" \) 2>/dev/null
```

## Frame timing (“shutter”)

When MIDI events fall between frames, choose how to sample:

- `--shutter sample`: sample envelope at `--sample-at start|center|end` (default `end`)
- `--shutter max`: preserves short hits better at low FPS (`--subsamples N`)
- `--shutter avg`: exposure-like averaging (`--subsamples N`), may create intermediate grays for binary gates

## Controls (live preview)

- `F` toggle fullscreen
- `D` toggle debug overlay (shows events/params)
- `L` toggle learn mode (next note/CC maps to trigger)
- `N` cycle trigger mode: mapped → all notes → note set
- `A` toggle add-to-set learn (next note adds to note set)
- `C` clear note set
- `K` toggle color mode (notes map to hues)
- `V` toggle velocity sensitivity
- `R` reset ADSR to defaults
- `1/2` attack −/+
- `3/4` decay −/+
- `5/6` sustain −/+
- `7/8` release −/+
- `Esc` quit

## Troubleshooting

- `ModuleNotFoundError: No module named 'midiphoria'`: run from repo root after `pip install -e .`, or use `PYTHONPATH=src`.
- `RuntimeError: fluidsynth not found in PATH`: install FluidSynth (`sudo apt install fluidsynth`) or provide audio via `--audio`.
- MP4 export fails: confirm `ffmpeg` is installed (`command -v ffmpeg`).
