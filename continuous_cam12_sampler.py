#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import time
from datetime import datetime
from pathlib import Path
from urllib.error import URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


ROOT = Path(r"C:\Users\hjvan\OneDrive\_Codex\Avatar")
BUILD_FILE = ROOT / "public_html" / "avatar" / "build.txt"
DEFAULT_OUTPUT_DIR = ROOT / "Sample"
DEFAULT_BUILD = BUILD_FILE.read_text(encoding="utf-8").strip() if BUILD_FILE.exists() else "latest"
DEFAULT_URL = f"https://www.aieng.co.za/avatar/?capture=1&cb={DEFAULT_BUILD}"
DEFAULT_CONTROL_URL = "https://www.aieng.co.za/avatar/sample-export-control.php?action=status"


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def write_status(output_dir: Path, payload: dict[str, object]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "cam12_sampler_status.json").write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )


def control_status_url(control_url: str) -> str:
    parts = urlsplit(control_url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    if not any(key == "action" for key, _ in query):
        query.append(("action", "status"))
    query = [(key, value) for key, value in query if key != "cb"]
    query.append(("cb", str(int(time.time() * 1000))))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def read_export_control(control_url: str | None) -> dict[str, object]:
    if not control_url:
        return {"enabled": True, "reachable": False, "error": "control disabled"}
    try:
        request = Request(
            control_status_url(control_url),
            headers={
                "Accept": "application/json",
                "Cache-Control": "no-store",
                "Pragma": "no-cache",
            },
        )
        with urlopen(request, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
        return {
            "enabled": bool(payload.get("enabled", True)),
            "interval_seconds": int(payload.get("interval_seconds", 10) or 10),
            "reachable": True,
            "updated_at": str(payload.get("updated_at", "")),
            "error": "",
        }
    except (OSError, URLError, ValueError, json.JSONDecodeError) as exc:
        return {
            "enabled": True,
            "reachable": False,
            "error": f"{exc.__class__.__name__}: {exc}",
        }


def interval_from_control(control: dict[str, object], fallback: float) -> float:
    try:
        seconds = float(control.get("interval_seconds", fallback))
    except (TypeError, ValueError):
        seconds = fallback
    return max(1.0, min(3600.0, seconds))


def wait_for_next_interval(control_url: str | None, interval: float) -> None:
    started = time.monotonic()
    current_interval = max(1.0, min(3600.0, interval))
    while True:
        elapsed = time.monotonic() - started
        remaining = current_interval - elapsed
        if remaining <= 0:
            return
        time.sleep(min(10.0, max(1.0, remaining)))
        control = read_export_control(control_url)
        if not bool(control.get("enabled", True)):
            return
        current_interval = interval_from_control(control, current_interval)


def wait_for_capture_ready(page) -> None:
    page.wait_for_selector("#capture-view-board", state="attached", timeout=60000)
    page.wait_for_selector(".capture-view--body-front", state="attached", timeout=30000)
    page.wait_for_selector(".capture-view--body-side", state="attached", timeout=30000)
    try:
        page.wait_for_function(
            "() => window.__nva && window.__nva.state && "
            "window.__nva.state().referenceVideoActive === true && "
            "window.__nva.state().referenceJointOverlayActive === true && "
            "window.__nva.state().avatarJointOverlayActive === true",
            timeout=70000,
        )
    except PlaywrightTimeoutError:
        # Still capture the cameras if a readiness telemetry field lags.
        pass


def cam12_clip(page) -> dict[str, float]:
    clip = page.evaluate(
        """
        () => {
          const a = document.querySelector('.capture-view--body-front');
          const b = document.querySelector('.capture-view--body-side');
          if (!a || !b) throw new Error('Cam 01 or Cam 02 panel missing');
          const ra = a.getBoundingClientRect();
          const rb = b.getBoundingClientRect();
          const pad = 8;
          const left = Math.max(0, Math.min(ra.left, rb.left) - pad);
          const top = Math.max(0, Math.min(ra.top, rb.top) - pad);
          const right = Math.min(window.innerWidth, Math.max(ra.right, rb.right) + pad);
          const bottom = Math.min(window.innerHeight, Math.max(ra.bottom, rb.bottom) + pad);
          return { x: left, y: top, width: right - left, height: bottom - top };
        }
        """
    )
    return {
        "x": max(0.0, float(clip["x"])),
        "y": max(0.0, float(clip["y"])),
        "width": max(1.0, float(clip["width"])),
        "height": max(1.0, float(clip["height"])),
    }


def sample_loop(
    url: str,
    output_dir: Path,
    interval: float,
    width: int,
    height: int,
    once: bool,
    control_url: str | None,
) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    latest_path = output_dir / "latest_cam01_cam02.png"
    count = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=1)
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        wait_for_capture_ready(page)
        while True:
            control = read_export_control(control_url)
            current_interval = interval_from_control(control, interval)
            if not bool(control.get("enabled", True)):
                write_status(
                    output_dir,
                    {
                        "ok": True,
                        "running": not once,
                        "export_enabled": False,
                        "control_url": control_url,
                        "control_reachable": bool(control.get("reachable", False)),
                        "control_updated_at": control.get("updated_at", ""),
                        "url": url,
                        "interval_seconds": current_interval,
                        "samples_written": count,
                        "latest": str(latest_path) if latest_path.exists() else None,
                        "last_sample": None,
                        "last_clip": None,
                        "updated_at": datetime.now().isoformat(timespec="seconds"),
                    },
                )
                if once:
                    break
                time.sleep(min(10.0, max(1.0, current_interval)))
                continue
            try:
                page.evaluate("window.__nva && window.__nva.feed && window.__nva.feed(0.82)")
            except Exception:
                pass
            page.wait_for_timeout(450)
            out_path = output_dir / f"cam01_cam02_{timestamp()}.png"
            clip = cam12_clip(page)
            page.screenshot(path=str(out_path), clip=clip)
            shutil.copyfile(out_path, latest_path)
            count += 1
            write_status(
                output_dir,
                {
                    "ok": True,
                    "running": not once,
                    "export_enabled": True,
                    "control_url": control_url,
                    "control_reachable": bool(control.get("reachable", False)),
                    "control_error": str(control.get("error", "")),
                    "control_updated_at": control.get("updated_at", ""),
                    "url": url,
                    "interval_seconds": current_interval,
                    "samples_written": count,
                    "latest": str(latest_path),
                    "last_sample": str(out_path),
                    "last_clip": clip,
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                },
            )
            if once:
                break
            wait_for_next_interval(control_url, current_interval)
        browser.close()
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Write Cam 01 + Cam 02 side-by-side samples.")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--interval", type=float, default=10.0)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=900)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--control-url", default=DEFAULT_CONTROL_URL)
    parser.add_argument("--ignore-control", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return sample_loop(
        url=args.url,
        output_dir=Path(args.output_dir),
        interval=args.interval,
        width=args.width,
        height=args.height,
        once=args.once,
        control_url=None if args.ignore_control else args.control_url,
    )


if __name__ == "__main__":
    raise SystemExit(main())
