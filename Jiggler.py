#!/usr/bin/env python3
from __future__ import annotations

import argparse
import random
import sys
import time


DEFAULT_DELAY = 10.0
DEFAULT_JIGGLE_AMOUNT = 5
DEFAULT_KEYS = ["space", "enter", "num0", "num1", "num2", "num3", "num4", "num5", "num6", "num7", "num8", "num9"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Move the mouse slightly and press a random key at a fixed interval."
    )
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY, help="Seconds between actions.")
    parser.add_argument("--jiggle-amount", type=int, default=DEFAULT_JIGGLE_AMOUNT, help="Pixels to move right and back.")
    parser.add_argument("--iterations", type=int, default=0, help="How many cycles to run. Use 0 for infinite.")
    parser.add_argument("--dry-run", action="store_true", help="Print the planned actions without moving the mouse or typing.")
    return parser.parse_args()


def load_pyautogui():
    try:
        import pyautogui  # type: ignore
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "pyautogui is not installed in the active Python environment. "
            "Install it with: pip install pyautogui"
        ) from exc

    return pyautogui


def main() -> int:
    args = parse_args()
    delay = max(0.0, float(args.delay))
    jiggle_amount = max(0, int(args.jiggle_amount))
    iterations = max(0, int(args.iterations))
    dry_run = bool(args.dry_run)

    pyautogui = None
    if not dry_run:
        try:
            pyautogui = load_pyautogui()
        except RuntimeError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

    total_cycles = "infinite" if iterations == 0 else str(iterations)
    mode = "dry-run" if dry_run else "live"
    print(f"Mouse jiggler started in {mode} mode. Cycles: {total_cycles}. Press Ctrl+C to stop.")

    completed = 0
    try:
        while iterations == 0 or completed < iterations:
            key = random.choice(DEFAULT_KEYS)

            if dry_run:
                print(f"Cycle {completed + 1}: move +{jiggle_amount}/-{jiggle_amount}, press {key}, sleep {delay}s")
            else:
                pyautogui.moveRel(jiggle_amount, 0, duration=0.2)
                pyautogui.moveRel(-jiggle_amount, 0, duration=0.2)
                pyautogui.press(key)
                print(f"Cycle {completed + 1}: pressed {key}")

            completed += 1
            if iterations != 0 and completed >= iterations:
                break
            time.sleep(delay)
    except KeyboardInterrupt:
        print("Stopped by user.")
        return 130

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
