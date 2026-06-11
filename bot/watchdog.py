from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from bot.config import BotConfig
from bot.telemetry import build_telemetry


def _paper_command(cycles: int, sleep_seconds: int) -> list[str]:
    return [
        sys.executable,
        "main.py",
        "paper",
        "--cycles",
        str(cycles),
        "--sleep-seconds",
        str(sleep_seconds),
    ]


def _record(telemetry, event: str, payload: dict[str, Any]) -> None:
    telemetry.record("watchdog", telemetry.cfg.symbol, event, payload)


def run_watchdog(
    cfg: BotConfig,
    cycles: int = 0,
    sleep_seconds: int = 300,
    restart_delay: int = 30,
    max_restarts: int = 0,
) -> None:
    telemetry = build_telemetry(cfg)
    workdir = Path.cwd()
    restarts = 0
    _record(
        telemetry,
        "watchdog_start",
        {
            "cycles": cycles,
            "sleep_seconds": sleep_seconds,
            "restart_delay": restart_delay,
            "max_restarts": max_restarts,
        },
    )

    while True:
        command = _paper_command(cycles, sleep_seconds)
        _record(telemetry, "paper_process_start", {"command": command})
        process = subprocess.Popen(command, cwd=workdir)

        try:
            return_code = process.wait()
        except KeyboardInterrupt:
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
            _record(telemetry, "watchdog_stop", {"reason": "keyboard_interrupt"})
            return

        _record(
            telemetry,
            "paper_process_exit",
            {"return_code": return_code, "restart_count": restarts},
        )

        if return_code == 0 and cycles > 0:
            _record(telemetry, "watchdog_stop", {"reason": "finite_cycles_completed"})
            return

        restarts += 1
        if max_restarts > 0 and restarts > max_restarts:
            _record(
                telemetry,
                "watchdog_stop",
                {
                    "reason": "max_restarts_reached",
                    "max_restarts": max_restarts,
                },
            )
            return

        _record(
            telemetry,
            "paper_process_restart_scheduled",
            {"restart_count": restarts, "restart_delay": restart_delay},
        )
        time.sleep(restart_delay)

