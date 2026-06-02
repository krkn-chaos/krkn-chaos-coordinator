"""Pipeline status line — shows current phase and progress."""

from __future__ import annotations

import sys


PHASES = ["DISCOVER", "FILTER", "MAP", "ANALYZE", "ACT", "REMEMBER"]


def _bar(done: int, total: int, width: int = 12) -> str:
    if total == 0:
        return "█" * width
    filled = int(width * done / total)
    return "█" * filled + "░" * (width - filled)


def status(agent: str, phase: str, message: str, done: int = 0, total: int = 0) -> None:
    """Print a status line showing agent, phase, progress bar, and message."""
    phase_idx = PHASES.index(phase) if phase in PHASES else 0
    phase_map = "".join("●" if i <= phase_idx else "○" for i in range(len(PHASES)))

    if total > 0:
        bar = _bar(done, total)
        progress = f"{bar} {done}/{total}"
    else:
        bar = _bar(1, 1)
        progress = bar

    line = f"\r[{agent}] {phase_map} {phase:8s} {progress} {message}"
    sys.stderr.write(f"\033[2K{line}")
    sys.stderr.flush()


def status_done(agent: str, phase: str, message: str) -> None:
    """Print a completed status line (with newline)."""
    phase_idx = PHASES.index(phase) if phase in PHASES else 0
    phase_map = "".join("●" if i <= phase_idx else "○" for i in range(len(PHASES)))
    bar = _bar(1, 1)
    line = f"[{agent}] {phase_map} {phase:8s} {bar} {message}"
    sys.stderr.write(f"\033[2K\r{line}\n")
    sys.stderr.flush()
