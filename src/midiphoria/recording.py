from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from queue import SimpleQueue
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

import mido


@dataclass(frozen=True)
class RecordingMeta:
    schema: str = "midiphoria.midi_recording.v1"
    created_unix_s: float = 0.0
    app: str = "midiphoria"
    app_version: str = "0.1.0"
    state: Dict[str, Any] = None  # populated by caller


@dataclass(frozen=True)
class RecordedMidiEvent:
    t: float
    data: List[int]


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default


def _msg_to_bytes(msg: mido.Message) -> List[int]:
    try:
        return list(msg.bytes())
    except Exception:
        return []


def _msg_from_bytes(data: Iterable[int]) -> mido.Message:
    return mido.Message.from_bytes(list(data))


class MidiRecorder:
    """
    Writes a JSONL recording:
      - first line: {"type":"meta", ...}
      - next lines: {"type":"midi","t":...,"data":[...]}
    """

    def __init__(self, path: str | Path, meta_state: Dict[str, Any]):
        self.path = Path(path)
        self._meta_state = meta_state
        self._t0 = 0.0
        self._queue: SimpleQueue[Optional[str]] = SimpleQueue()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def start(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._t0 = time.perf_counter()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_writer, daemon=True)
        self._thread.start()

        meta = RecordingMeta(
            created_unix_s=time.time(),
            state=self._meta_state,
        )
        self._queue.put(json.dumps({"type": "meta", **asdict(meta)}, separators=(",", ":")))

    def stop(self, timeout_s: float = 2.0) -> None:
        self._stop.set()
        self._queue.put(None)
        if self._thread:
            self._thread.join(timeout=timeout_s)
        self._thread = None

    def record(self, msg: mido.Message, t: Optional[float] = None) -> None:
        if self._thread is None:
            return
        t_rel = _safe_float(t, default=time.perf_counter() - self._t0)
        payload = {
            "type": "midi",
            "t": t_rel,
            "data": _msg_to_bytes(msg),
        }
        self._queue.put(json.dumps(payload, separators=(",", ":")))

    def _run_writer(self) -> None:
        with self.path.open("w", encoding="utf-8") as f:
            while not self._stop.is_set():
                item = self._queue.get()
                if item is None:
                    break
                f.write(item)
                f.write("\n")
                f.flush()


def read_recording(path: str | Path) -> Tuple[Dict[str, Any], List[RecordedMidiEvent]]:
    meta: Dict[str, Any] = {}
    events: List[RecordedMidiEvent] = []
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if obj.get("type") == "meta" and not meta:
                meta = obj
                continue
            if obj.get("type") == "midi":
                events.append(RecordedMidiEvent(t=float(obj["t"]), data=list(obj.get("data") or [])))
    events.sort(key=lambda e: e.t)
    return meta, events


def iter_messages(events: Iterable[RecordedMidiEvent]) -> Iterator[Tuple[float, mido.Message]]:
    for e in events:
        yield e.t, _msg_from_bytes(e.data)

