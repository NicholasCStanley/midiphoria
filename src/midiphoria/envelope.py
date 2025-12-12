from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import torch


@dataclass
class ADSR:
    attack: float = 0.0
    decay: float = 0.0
    sustain: float = 1.0
    release: float = 0.0

    def clamp(self) -> None:
        self.attack = max(0.0, float(self.attack))
        self.decay = max(0.0, float(self.decay))
        self.sustain = min(1.0, max(0.0, float(self.sustain)))
        self.release = max(0.0, float(self.release))


class GlobalEnvelope:
    """
    Global ADSR envelope driven by a single gate and target level.
    Computed in torch, on CUDA if available.
    """

    def __init__(self, device: Optional[torch.device] = None, adsr: Optional[ADSR] = None):
        self.device = device or (torch.device("cuda") if torch.cuda.is_available() else torch.device("cpu"))
        self.adsr = adsr or ADSR()
        self.adsr.clamp()

        self._level = torch.tensor(0.0, device=self.device)
        self._gate = False
        self._target = torch.tensor(0.0, device=self.device)
        self._phase = "idle"  # idle, attack, decay, sustain, release

    @property
    def level(self) -> torch.Tensor:
        return self._level

    def reset(self, adsr: Optional[ADSR] = None) -> None:
        if adsr is not None:
            self.adsr = adsr
            self.adsr.clamp()
        self._level = torch.tensor(0.0, device=self.device)
        self._target = torch.tensor(0.0, device=self.device)
        self._gate = False
        self._phase = "idle"

    def gate_on(self, target_level: float) -> None:
        target = torch.tensor(float(target_level), device=self.device).clamp(0.0, 1.0)

        # If already gated and not releasing, update target without retriggering.
        if self._gate and self._phase not in ("idle", "release"):
            self._target = target
            if self._phase == "sustain":
                self._level = self._target * self.adsr.sustain
            return

        self._gate = True
        self._target = target
        if self.adsr.attack <= 0:
            self._level = self._target.clone()
            self._phase = "decay" if self.adsr.decay > 0 else "sustain"
        else:
            self._phase = "attack"

    def set_target(self, target_level: float) -> None:
        """Update target while keeping current gate/phase."""
        self._target = torch.tensor(float(target_level), device=self.device).clamp(0.0, 1.0)
        if self._phase == "sustain":
            self._level = self._target * self.adsr.sustain

    def gate_off(self) -> None:
        self._gate = False
        if self.adsr.release <= 0:
            self._level = torch.tensor(0.0, device=self.device)
            self._phase = "idle"
        else:
            self._phase = "release"

    def step(self, dt: float) -> None:
        dt = max(0.0, float(dt))
        a, d, s, r = self.adsr.attack, self.adsr.decay, self.adsr.sustain, self.adsr.release

        if self._phase == "idle":
            return

        if self._phase == "attack":
            if a <= 0:
                self._level = self._target.clone()
                self._phase = "decay" if d > 0 else "sustain"
                return
            inc = dt / a
            self._level = torch.minimum(self._target, self._level + inc)
            if bool(self._level >= self._target - 1e-6):
                self._phase = "decay" if d > 0 else "sustain"
            return

        if self._phase == "decay":
            if d <= 0:
                self._level = self._target * s
                self._phase = "sustain"
                return
            dec = dt / d
            sustain_level = self._target * s
            self._level = torch.maximum(sustain_level, self._level - dec)
            if bool(self._level <= sustain_level + 1e-6):
                self._phase = "sustain"
            return

        if self._phase == "sustain":
            self._level = self._target * s if self._gate else self._level
            return

        if self._phase == "release":
            if r <= 0:
                self._level = torch.tensor(0.0, device=self.device)
                self._phase = "idle"
                return
            dec = dt / r
            self._level = torch.maximum(torch.tensor(0.0, device=self.device), self._level - dec)
            if bool(self._level <= 1e-6):
                self._phase = "idle"
            return
