# Midiphoria

GPU‑accelerated MIDI‑driven visualizer (MVP).

## What this is

Midiphoria opens a resizable OpenGL window and listens for MIDI input. A mapped MIDI note (or CC) drives a global brightness visual:

- No input → black screen.
- Note‑on → white screen while held.
- Velocity sensitivity optional: brightness follows velocity/value.
- Global ADSR envelope optional (default is instantaneous on/off).

This is the minimal viable project scaffold; we’ll iterate from here.

## Requirements

- Python 3.10+
- NVIDIA GPU + CUDA (optional; app falls back to CPU)
- MIDI backend: `python-rtmidi`

Torch install varies by GPU/CUDA. Install the right wheel for your system:
https://pytorch.org/get-started/locally/

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

List available MIDI input ports:

```bash
python -m midiphoria.app --list-ports
```

Start visualizer (auto‑opens first port if any):

```bash
python -m midiphoria.app
```

Some useful startup flags:

```bash
# Any note triggers, binary on/off
python -m midiphoria.app --trigger-mode all_notes

# Start in color mode, velocity-sensitive, with a snappy envelope
python -m midiphoria.app --color-mode --velocity-sensitive --attack 0.05 --decay 0.1 --sustain 0.7 --release 0.2

# Preconfigure a drum note-set
python -m midiphoria.app --note-set "36,38,42,46" --trigger-mode note_set
```

Choose a specific port (substring match):

```bash
python -m midiphoria.app --port "My MIDI Device"
```

Open all ports:

```bash
python -m midiphoria.app --all-ports
```

Generate test MIDI internally:

```bash
python -m midiphoria.app --generate
```

## Controls

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

## Notes

- MVP uses torch to compute the envelope and target brightness, then presents via OpenGL clear color. Later versions can upgrade to full tensor‑to‑texture rendering.
- Trigger modes:
  - `mapped`: respond to one learned note or CC.
  - `all_notes`: any note_on/off drives the gate; overlaps keep the screen on.
  - `note_set`: only notes in the learned set drive the gate.
- MIDI file playback and richer UI are planned next.
