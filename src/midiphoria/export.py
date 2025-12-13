from __future__ import annotations

import math
import subprocess
import uuid
from dataclasses import asdict
from pathlib import Path
from shutil import which
from typing import Any, Dict, Iterable, List, Optional, Tuple

import torch

from .colors import note_color
from .controller import MidiController
from .envelope import ADSR, GlobalEnvelope
from .midifile import read_midi_file_events
from .recording import iter_messages, read_recording


def _state_to_meta_dict(state) -> Dict[str, Any]:
    return {
        "mapping": {"kind": state.mapping.kind, "channel": state.mapping.channel, "number": state.mapping.number},
        "trigger_mode": state.trigger_mode,
        "note_set": sorted(state.note_set),
        "color_mode": bool(state.color_mode),
        "velocity_sensitive": bool(state.velocity_sensitive),
        "adsr": asdict(state.adsr),
    }


def _apply_meta_to_state(state, meta_state: Dict[str, Any]) -> None:
    mapping = meta_state.get("mapping") or {}
    if isinstance(mapping, dict):
        state.mapping.kind = mapping.get("kind", state.mapping.kind)
        state.mapping.channel = int(mapping.get("channel", state.mapping.channel))
        state.mapping.number = int(mapping.get("number", state.mapping.number))
    state.trigger_mode = meta_state.get("trigger_mode", state.trigger_mode)
    state.color_mode = bool(meta_state.get("color_mode", state.color_mode))
    state.velocity_sensitive = bool(meta_state.get("velocity_sensitive", state.velocity_sensitive))

    note_set = meta_state.get("note_set") or []
    try:
        state.note_set = set(int(x) for x in note_set if 0 <= int(x) <= 127)
    except Exception:
        pass

    adsr = meta_state.get("adsr") or {}
    if isinstance(adsr, dict):
        state.adsr.attack = float(adsr.get("attack", state.adsr.attack))
        state.adsr.decay = float(adsr.get("decay", state.adsr.decay))
        state.adsr.sustain = float(adsr.get("sustain", state.adsr.sustain))
        state.adsr.release = float(adsr.get("release", state.adsr.release))
        state.adsr.clamp()


def _rgb_for_state(state, level: float) -> Tuple[int, int, int]:
    level = float(max(0.0, min(1.0, level)))
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
    return int(round(r * 255)), int(round(g * 255)), int(round(b * 255))


def _frame_bytes_rgb(width: int, height: int, rgb: Tuple[int, int, int]) -> bytes:
    r, g, b = rgb
    return bytes((r, g, b)) * (width * height)


def _write_ppm(path: Path, width: int, height: int, rgb_bytes: bytes) -> None:
    header = f"P6\n{width} {height}\n255\n".encode("ascii")
    with path.open("wb") as f:
        f.write(header)
        f.write(rgb_bytes)


def _run_ffmpeg_rawvideo(
    *,
    mp4_path: Path,
    fps: float,
    width: int,
    height: int,
    audio_path: Optional[Path] = None,
    audio_start_time_s: float = 0.0,
) -> subprocess.Popen[bytes]:
    cmd: List[str] = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{width}x{height}",
        "-r",
        str(fps),
        "-i",
        "-",
    ]
    if audio_path is not None:
        audio_start_time_s = float(max(0.0, audio_start_time_s))
        if audio_start_time_s > 0:
            cmd.extend(["-ss", str(audio_start_time_s)])
        cmd.extend(["-i", str(audio_path)])
        cmd.extend(["-map", "0:v:0", "-map", "1:a:0"])
    else:
        cmd.extend(["-an"])

    cmd.extend(["-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart"])
    if audio_path is not None:
        cmd.extend(["-c:a", "aac", "-shortest"])
    cmd.append(str(mp4_path))
    return subprocess.Popen(cmd, stdin=subprocess.PIPE)


def export_recording(
    *,
    recording_path: str | Path,
    fps: float,
    width: int,
    height: int,
    out_dir: Optional[str | Path] = None,
    frame_format: str = "ppm",
    mp4_path: Optional[str | Path] = None,
    audio_path: Optional[str | Path] = None,
    shutter: str = "sample",  # sample|max|avg
    subsamples: int = 8,
    sample_at: str = "end",  # start|center|end
    start_time_s: float = 0.0,
    end_time_s: Optional[float] = None,
    tail_s: float = 1.0,
    use_recording_meta: bool = True,
    state=None,
    overrides_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if fps <= 0:
        raise ValueError("fps must be > 0")
    if width <= 0 or height <= 0:
        raise ValueError("width/height must be > 0")
    shutter = shutter.lower()
    if shutter not in ("sample", "max", "avg"):
        raise ValueError("shutter must be one of: sample, max, avg")
    sample_at = sample_at.lower()
    if sample_at not in ("start", "center", "end"):
        raise ValueError("sample_at must be one of: start, center, end")
    subsamples = max(1, int(subsamples))

    meta, events = read_recording(recording_path)

    from .live import AppState  # noqa: WPS433

    state = state or AppState()
    if use_recording_meta:
        meta_state = (meta.get("state") if isinstance(meta, dict) else None) or {}
        if isinstance(meta_state, dict):
            _apply_meta_to_state(state, meta_state)
    if overrides_state:
        _apply_meta_to_state(state, overrides_state)

    last_event_t = (events[-1].t if events else 0.0)
    message_iter = iter_messages(events)

    result = _export_message_stream(
        message_iter=message_iter,
        last_event_t=float(last_event_t),
        fps=fps,
        width=width,
        height=height,
        out_dir=out_dir,
        frame_format=frame_format,
        mp4_path=mp4_path,
        audio_path=audio_path,
        shutter=shutter,
        subsamples=subsamples,
        sample_at=sample_at,
        start_time_s=start_time_s,
        end_time_s=end_time_s,
        tail_s=tail_s,
        state=state,
    )
    result["recording_meta_state"] = meta.get("state") if isinstance(meta, dict) else None
    result["effective_state"] = _state_to_meta_dict(state)
    return result


def export_midi_file(
    *,
    midi_path: str | Path,
    fps: float,
    width: int,
    height: int,
    out_dir: Optional[str | Path] = None,
    frame_format: str = "ppm",
    mp4_path: Optional[str | Path] = None,
    audio_path: Optional[str | Path] = None,
    audio_from_midi: bool = False,
    soundfont_path: Optional[str | Path] = None,
    shutter: str = "sample",  # sample|max|avg
    subsamples: int = 8,
    sample_at: str = "end",  # start|center|end
    start_time_s: float = 0.0,
    end_time_s: Optional[float] = None,
    tail_s: float = 1.0,
    state=None,
    channels: Optional[List[int]] = None,  # 0-15
    midi_duration: str = "events",  # events|file
) -> Dict[str, Any]:
    from .live import AppState  # noqa: WPS433

    state = state or AppState()
    midi_path = Path(midi_path)
    meta, events = read_midi_file_events(midi_path, channels=channels)

    midi_duration = (midi_duration or "events").lower()
    if midi_duration not in ("events", "file"):
        raise ValueError("midi_duration must be one of: events, file")

    last_event_t = (meta.duration_s if midi_duration == "file" else (events[-1][0] if events else 0.0))
    message_iter = iter(events)

    synthesized_audio: Optional[Path] = None
    try:
        if audio_from_midi:
            if mp4_path is None:
                raise ValueError("audio_from_midi requires mp4_path")
            if audio_path is not None:
                raise ValueError("Provide either audio_path or audio_from_midi, not both")
            if not soundfont_path:
                raise ValueError("audio_from_midi requires soundfont_path")
            if which("fluidsynth") is None:
                raise RuntimeError("fluidsynth not found in PATH (needed for --audio-from-midi)")

            out_base = Path(mp4_path).with_suffix("")
            synthesized_audio = out_base.parent / f".midiphoria_{out_base.name}_{uuid.uuid4().hex}.wav"
            cmd = [
                "fluidsynth",
                "-ni",
                str(Path(soundfont_path)),
                str(midi_path),
                "-F",
                str(synthesized_audio),
                "-r",
                "48000",
            ]
            try:
                subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            except subprocess.CalledProcessError as e:
                detail = ""
                if e.stderr:
                    try:
                        detail = e.stderr.decode("utf-8", errors="replace").strip()
                    except Exception:
                        detail = ""
                raise RuntimeError(f"fluidsynth failed: {detail or 'unknown error'}") from e
            audio_path = synthesized_audio

        result = _export_message_stream(
            message_iter=message_iter,
            last_event_t=float(last_event_t),
            fps=fps,
            width=width,
            height=height,
            out_dir=out_dir,
            frame_format=frame_format,
            mp4_path=mp4_path,
            audio_path=audio_path,
            shutter=shutter,
            subsamples=subsamples,
            sample_at=sample_at,
            start_time_s=start_time_s,
            end_time_s=end_time_s,
            tail_s=tail_s,
            state=state,
        )
    finally:
        if synthesized_audio is not None:
            try:
                synthesized_audio.unlink(missing_ok=True)
            except Exception:
                pass
    result["midi_meta"] = {"ticks_per_beat": meta.ticks_per_beat, "duration_s": meta.duration_s}
    result["midi_duration_mode"] = midi_duration
    result["effective_state"] = _state_to_meta_dict(state)
    return result


def _export_message_stream(
    *,
    message_iter,
    last_event_t: float,
    fps: float,
    width: int,
    height: int,
    out_dir: Optional[str | Path],
    frame_format: str,
    mp4_path: Optional[str | Path],
    audio_path: Optional[str | Path],
    shutter: str,
    subsamples: int,
    sample_at: str,
    start_time_s: float,
    end_time_s: Optional[float],
    tail_s: float,
    state,
) -> Dict[str, Any]:
    if fps <= 0:
        raise ValueError("fps must be > 0")
    if width <= 0 or height <= 0:
        raise ValueError("width/height must be > 0")
    shutter = shutter.lower()
    if shutter not in ("sample", "max", "avg"):
        raise ValueError("shutter must be one of: sample, max, avg")
    sample_at = sample_at.lower()
    if sample_at not in ("start", "center", "end"):
        raise ValueError("sample_at must be one of: start, center, end")
    subsamples = max(1, int(subsamples))

    device = torch.device("cpu")
    envelope = GlobalEnvelope(device=device, adsr=state.adsr)
    controller = MidiController(state, envelope, event_log=None)

    if end_time_s is None:
        end_time_s = max(0.0, float(last_event_t) + float(max(0.0, tail_s)))
    start_time_s = max(0.0, float(start_time_s))
    end_time_s = max(start_time_s, float(end_time_s))

    frame_dt = 1.0 / float(fps)
    num_frames = int(math.floor((end_time_s - start_time_s) * fps + 1e-9))
    if num_frames <= 0:
        return {
            "frames": 0,
            "fps": fps,
            "width": width,
            "height": height,
            "out_dir": str(out_dir) if out_dir else None,
            "mp4": str(mp4_path) if mp4_path else None,
        }

    out_path = Path(out_dir) if out_dir else None
    if out_path:
        out_path.mkdir(parents=True, exist_ok=True)

    mp4_proc: Optional[subprocess.Popen[bytes]] = None
    if mp4_path:
        audio_p = Path(audio_path) if audio_path else None
        mp4_proc = _run_ffmpeg_rawvideo(
            mp4_path=Path(mp4_path),
            fps=fps,
            width=width,
            height=height,
            audio_path=audio_p,
            audio_start_time_s=start_time_s,
        )
        if mp4_proc.stdin is None:
            raise RuntimeError("ffmpeg stdin unavailable")

    next_msg: Optional[Tuple[float, Any]] = None
    try:
        next_msg = next(message_iter)
    except StopIteration:
        next_msg = None

    sim_t = 0.0

    def advance_to(t_target: float) -> None:
        nonlocal sim_t, next_msg
        t_target = float(max(sim_t, t_target))
        while next_msg is not None and float(next_msg[0]) <= t_target + 1e-12:
            t_msg, msg = next_msg
            if t_msg > sim_t:
                envelope.step(t_msg - sim_t)
                sim_t = t_msg
            controller.on_midi(msg)
            try:
                next_msg = next(message_iter)
            except StopIteration:
                next_msg = None
        if t_target > sim_t:
            envelope.step(t_target - sim_t)
            sim_t = t_target

    def sample_level_at(t_sample: float) -> float:
        advance_to(t_sample)
        return float(envelope.level.detach().clamp(0, 1).cpu().item())

    def frame_value(t0: float) -> float:
        t1 = t0 + frame_dt
        if shutter == "sample":
            if sample_at == "start":
                ts = t0
            elif sample_at == "center":
                ts = t0 + 0.5 * frame_dt
            else:
                ts = t1
            return sample_level_at(ts)

        max_v = 0.0
        acc = 0.0
        for i in range(subsamples):
            ts = t0 + (i + 0.5) * frame_dt / subsamples
            v = sample_level_at(ts)
            if shutter == "max":
                if v > max_v:
                    max_v = v
            else:
                acc += v
        return max_v if shutter == "max" else (acc / subsamples)

    advance_to(start_time_s)

    for frame_idx in range(num_frames):
        t0 = start_time_s + frame_idx * frame_dt
        level = frame_value(t0)
        rgb = _rgb_for_state(state, level)
        rgb_bytes = _frame_bytes_rgb(width, height, rgb)

        if out_path:
            ff = frame_format.lower()
            if ff == "ppm":
                frame_file = out_path / f"frame_{frame_idx:06d}.ppm"
                _write_ppm(frame_file, width, height, rgb_bytes)
            elif ff == "png":
                try:
                    from PIL import Image  # type: ignore
                except Exception as e:
                    raise RuntimeError(
                        "frame_format=png requires Pillow (pip install Pillow) or use frame_format=ppm"
                    ) from e
                frame_file = out_path / f"frame_{frame_idx:06d}.png"
                Image.new("RGB", (width, height), rgb).save(frame_file)
            else:
                raise ValueError("frame_format must be one of: ppm, png")

        if mp4_proc is not None:
            mp4_proc.stdin.write(rgb_bytes)  # type: ignore[union-attr]

    if mp4_proc is not None:
        mp4_proc.stdin.close()  # type: ignore[union-attr]
        rc = mp4_proc.wait()
        if rc != 0:
            raise RuntimeError(f"ffmpeg failed with exit code {rc}")

    return {
        "frames": num_frames,
        "fps": fps,
        "width": width,
        "height": height,
        "out_dir": str(out_path) if out_path else None,
        "mp4": str(mp4_path) if mp4_path else None,
        "shutter": shutter,
        "subsamples": subsamples,
        "sample_at": sample_at,
    }
