"""Opt-in, observational progress for the single-PC launcher; no heartbeat thread."""
from __future__ import annotations

import json
import os
from pathlib import Path
import time


class Progress:
    def __init__(self, path: Path, run_id: str, clock=time.time):
        self.path = path
        self.run_id = run_id
        self.clock = clock
        self.phase = "starting"
        self.deadline = None
        self.last_success = None
        self.write()

    def write(self):
        # A failed write must become stale, not take down the existing runner.
        temp = self.path.with_name(self.path.name + f".{os.getpid()}.tmp")
        try:
            temp.write_text(json.dumps({
                "version": 1, "run_id": self.run_id, "phase": self.phase,
                "observed_at": self.clock(), "deadline_at": self.deadline,
                "last_success_at": self.last_success,
            }, allow_nan=False))
            temp.replace(self.path)
        except OSError:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass

    def begin(self, phase, timeout=None):
        self.phase = phase
        self.deadline = self.clock() + timeout if timeout is not None else None
        self.write()

    def success(self):
        self.last_success = self.clock()
        if self.phase == "error":
            self.phase, self.deadline = "polling", None
        self.write()  # Never extend the phase deadline on API observations.

    def error(self):
        self.begin("error")

    def idle(self):
        if self.phase != "error":
            self.begin("idle")


def from_args(parser, args):
    if bool(args.progress_file) != bool(args.run_id):
        parser.error("--progress-file과 --run-id는 함께 지정해야 합니다")
    if not args.progress_file:
        return None
    path = Path(args.progress_file)
    if not path.is_absolute():
        parser.error("--progress-file은 절대 경로여야 합니다")
    return Progress(path, args.run_id)
