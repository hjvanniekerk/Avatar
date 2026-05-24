#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from urllib import parse as urlparse
from urllib import request as urlrequest

import websocket

from unreal_realism_rl import (
    APPROVAL_MIN_FRAMES,
    APPROVAL_MIN_SCORE,
    LMSTUDIO_BASE,
    MODEL,
    RUN_ROOT,
    SITE_AVATAR_REALISM,
    SITE_REALISM,
    judge_frame,
    summarize,
    utc_now,
    write_json,
)


MODE = "browser-nemotron-continuous-rl"
STATUS_FILE = RUN_ROOT / "continuous_browser_rl_status.json"
STOP_FILE = RUN_ROOT / "continuous_browser_rl.stop"
DEFAULT_URL = "https://www.aieng.co.za/avatar/?rl_eval=1"
CHROME_CANDIDATES = [
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
]
HM_FILES = Path(r"C:\Users\hjvan\OneDrive\_Codex\Avatar\deploy\hm_files.py")
GPU_CHROME_FLAGS = [
    "--enable-gpu",
    "--ignore-gpu-blocklist",
    "--disable-software-rasterizer",
]


def ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


class BrowserProcess:
    def __init__(self, pid: int, profile: Path, fallback: subprocess.Popen | None = None) -> None:
        self.pid = pid
        self.profile = profile
        self.fallback = fallback

    def terminate(self) -> None:
        if self.fallback is not None:
            self.fallback.terminate()
            return
        script = (
            "Get-CimInstance Win32_Process -Filter \"name = 'chrome.exe'\" | "
            f"Where-Object {{ $_.CommandLine -like '*{str(self.profile)}*' }} | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; "
            f"Stop-Process -Id {self.pid} -Force -ErrorAction SilentlyContinue"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True, timeout=20)

    def wait(self, timeout: int) -> int:
        if self.fallback is not None:
            return self.fallback.wait(timeout=timeout)
        deadline = time.time() + timeout
        while time.time() < deadline:
            alive = subprocess.run(
                ["powershell", "-NoProfile", "-Command", f"Get-Process -Id {self.pid} -ErrorAction SilentlyContinue"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if not alive.stdout.strip():
                return 0
            time.sleep(0.25)
        raise subprocess.TimeoutExpired(str(self.pid), timeout)

    def kill(self) -> None:
        self.terminate()


class Cdp:
    def __init__(self, ws_url: str) -> None:
        self.ws = websocket.create_connection(ws_url, timeout=45)
        self.next_id = 0

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:
            pass

    def send(self, method: str, params: dict[str, object] | None = None) -> dict[str, object]:
        self.next_id += 1
        msg_id = self.next_id
        self.ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
        while True:
            raw = self.ws.recv()
            msg = json.loads(raw)
            if msg.get("id") != msg_id:
                continue
            if "error" in msg:
                raise RuntimeError(f"CDP {method} failed: {msg['error']}")
            return dict(msg.get("result") or {})


def now_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def find_chrome() -> Path:
    for candidate in CHROME_CANDIDATES:
        if candidate.is_file():
            return candidate
    found = shutil.which("chrome") or shutil.which("chrome.exe")
    if found:
        return Path(found)
    raise FileNotFoundError("Chrome was not found.")


def read_json_url(url: str, timeout: int = 10) -> dict[str, object]:
    with urlrequest.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def wait_for_debugger(port: int, timeout: int = 30) -> None:
    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{port}/json/version"
    while time.time() < deadline:
        try:
            read_json_url(url, timeout=2)
            return
        except Exception:
            time.sleep(0.25)
    raise TimeoutError(f"Chrome debugger did not open on port {port}.")


def open_target(port: int, url: str) -> Cdp:
    quoted = urlparse.quote(url, safe="")
    req = urlrequest.Request(f"http://127.0.0.1:{port}/json/new?{quoted}", method="PUT")
    with urlrequest.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8", errors="replace"))
    ws_url = str(data["webSocketDebuggerUrl"])
    cdp = Cdp(ws_url)
    cdp.send("Page.enable")
    cdp.send("Runtime.enable")
    return cdp


def launch_chrome(command: list[str], profile: Path) -> BrowserProcess:
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )
    return BrowserProcess(proc.pid, profile, proc)


def wait_ready(cdp: Cdp, timeout: int = 20) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = cdp.send(
            "Runtime.evaluate",
            {"expression": "document.readyState", "returnByValue": True},
        )
        value = result.get("result", {}).get("value")
        if value == "complete":
            return
        time.sleep(0.25)


def enable_rl_eval(cdp: Cdp, url: str, warmup: float) -> None:
    cdp.send("Page.navigate", {"url": url})
    wait_ready(cdp)
    cdp.send(
        "Runtime.evaluate",
        {
            "expression": "localStorage.setItem('nemotron-avatar-rl-eval','1');",
            "returnByValue": True,
        },
    )
    sep = "&" if "?" in url else "?"
    cdp.send("Page.navigate", {"url": f"{url}{sep}cb={now_id()}"})
    wait_ready(cdp)
    time.sleep(warmup)


def capture_frame(cdp: Cdp, path: Path) -> None:
    result = cdp.send(
        "Page.captureScreenshot",
        {"format": "png", "fromSurface": True, "captureBeyondViewport": False},
    )
    data = str(result["data"])
    path.write_bytes(base64.b64decode(data))


def browser_state(cdp: Cdp) -> dict[str, object]:
    result = cdp.send(
        "Runtime.evaluate",
        {
            "expression": "JSON.stringify(window.__nva && window.__nva.state ? window.__nva.state() : {})",
            "returnByValue": True,
        },
    )
    raw = result.get("result", {}).get("value") or "{}"
    try:
        data = json.loads(str(raw))
    except Exception:
        data = {}
    return data if isinstance(data, dict) else {}


def safe_bool_reason(name: str, condition: bool, reason: str) -> str | None:
    return None if condition else f"{name}: {reason}"


def explain_policy_gates(score: dict[str, object]) -> list[str]:
    failures: list[str] = []

    def add(name: str, condition: bool, reason: str) -> None:
        item = safe_bool_reason(name, condition, reason)
        if item:
            failures.append(item)

    render_source = str(score.get("render_source") or score.get("renderSource") or "")
    deformation = score.get("targetMeshDeformation") if isinstance(score.get("targetMeshDeformation"), dict) else {}
    deformation_method = str(score.get("target_mesh_deformation_method") or deformation.get("method") or "")
    warnings = score.get("diagnostics_warnings") or score.get("diagnosticsWarnings") or []
    errors = score.get("diagnostics_errors") or score.get("diagnosticsErrors") or []
    reason = "; ".join([str(x) for x in (errors or warnings)]) or render_source or "unknown"
    add("true_3d_evidence", score.get("true_3d_evidence") is True or score.get("true3dRender") is True, reason)
    add("target_real_glb_visible", score.get("target_real_glb_visible") is True or score.get("realMeshBrowserVerified") is True, "real GLB/model-viewer visibility not proven")
    add("target_mesh_deformation_visible", score.get("target_mesh_deformation_visible") is True, f"method={deformation_method or 'unknown'}")
    add("target_mesh_proxy_canvas_visible", score.get("target_mesh_proxy_canvas_visible") is not True, "proxy/canvas mesh is visible")
    add("target_joint_driver_visible", score.get("target_joint_driver_visible") is not True, "target joint driver/proxy is visible")
    add("avatar_joint_fallback_count", float(score.get("avatar_joint_fallback_count") or score.get("avatarJointFallbackCount") or 0) <= 0, "fallback avatar joints present")
    add("avatar_joint_source", not str(score.get("avatar_joint_source") or score.get("avatarJointSource") or "").startswith("target_video_"), "avatar joints sourced from target video")
    add("target_mesh_deformation_method", deformation_method != "proxy_overlay_only_blocked", "proxy overlay cannot count as deformation")
    return failures


def first_reason(items: list[str], fallback: str) -> str:
    return items[0] if items else fallback


def policy_reason_fields(
    *,
    reward: int,
    best_policy_reward: int,
    policy_learnable: bool,
    policy_publishable: bool,
    policy_learned: bool,
    gate_failures: list[str],
) -> dict[str, str]:
    learnable_reason = "all policy gates passed" if policy_learnable else first_reason(
        gate_failures,
        "policy gates failed",
    )
    publishable_reason = "policy is publishable" if policy_publishable else (
        first_reason(gate_failures, "CDP telemetry loop does not publish policies")
        if policy_learnable
        else first_reason(gate_failures, "policy is not learnable")
    )
    if policy_learned:
        learned_reason = "reward improved best policy reward"
    elif not policy_learnable:
        learned_reason = first_reason(gate_failures, "policy is not learnable")
    elif reward <= best_policy_reward:
        learned_reason = f"reward {reward} did not improve best {best_policy_reward}"
    else:
        learned_reason = "learning disabled for CDP telemetry loop"
    return {
        "policy_learnable_reason": learnable_reason,
        "policy_publishable_reason": publishable_reason,
        "policy_learned_reason": learned_reason,
    }


def log_iteration_summary(
    index: int,
    score: dict[str, object],
    reward: int,
    best_policy_reward: int,
    policy_learnable: bool,
    policy_publishable: bool,
    policy_learned: bool,
    candidate_policy: dict[str, object],
) -> dict[str, object]:
    delta = reward - best_policy_reward
    gate_failures = list(score.get("policy_gate_failures") or explain_policy_gates(score))
    reason_fields = policy_reason_fields(
        reward=reward,
        best_policy_reward=best_policy_reward,
        policy_learnable=policy_learnable,
        policy_publishable=policy_publishable,
        policy_learned=policy_learned,
        gate_failures=gate_failures,
    )
    summary = {
        "iter": index,
        "candidate_policy_id": str(candidate_policy.get("candidate_id") or "cdp-telemetry"),
        "reward": reward,
        "best_policy_reward_before": best_policy_reward,
        "best_policy_reward_after": reward if policy_learned else best_policy_reward,
        "reward_delta": delta,
        "policy_learnable": policy_learnable,
        "policy_publishable": policy_publishable,
        "policy_learned": policy_learned,
        "policy_gate_failures": gate_failures,
        **reason_fields,
    }
    print("[browser-rl-cdp-iteration] " + json.dumps(summary, ensure_ascii=False), flush=True)
    return summary


def telemetry_score(state: dict[str, object], reason: str) -> dict[str, object]:
    movement = state.get("movement") if isinstance(state.get("movement"), dict) else {}
    deformation = state.get("targetMeshDeformation") if isinstance(state.get("targetMeshDeformation"), dict) else {}
    true_3d = state.get("true3dRender") is True
    score = 25 if true_3d else 0
    return {
        "ok": True,
        "score": score,
        "face": int(movement.get("eye_score") or 0),
        "body": score,
        "hair": score,
        "motion": int(movement.get("realism") or 0),
        "lighting": score,
        "artifacts": 0,
        "comment": reason,
        "true_3d_evidence": true_3d,
        "render_source": state.get("renderSource"),
        "target_mesh_deformation_visible": deformation.get("visible") is True,
        "target_mesh_deformation_method": deformation.get("method") or state.get("target_mesh_deformation_method"),
        "target_mesh_proxy_canvas_visible": state.get("target_mesh_proxy_canvas_visible"),
        "target_joint_driver_visible": state.get("target_joint_driver_visible"),
        "avatar_joint_fallback_count": state.get("avatarJointFallbackCount"),
        "avatar_joint_source": state.get("avatarJointSource"),
        "diagnostics_warnings": state.get("diagnosticsWarnings") or [],
        "diagnostics_errors": state.get("diagnosticsErrors") or [],
    }


def mark_mode(summary: dict[str, object]) -> dict[str, object]:
    summary["mode"] = MODE
    summary["source"] = "chrome-cdp-screenshot"
    return summary


def deploy(path: Path, remote: str, hm_files: Path) -> bool:
    if not hm_files.is_file() or not os.environ.get("HM_ADMIN_OP_PASSWORD"):
        return False
    result = subprocess.run(
        [sys.executable, str(hm_files), "put", str(path), remote],
        cwd=str(hm_files.parent),
        text=True,
        capture_output=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "hm_files deploy failed")
    return True


def write_status(data: dict[str, object]) -> None:
    write_json(STATUS_FILE, data)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Continuously capture /avatar/ browser frames and use Nemotron as the RL reward model."
    )
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--endpoint", default=LMSTUDIO_BASE)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--interval", type=float, default=1.0, help="Delay after each judged frame. Nemotron latency dominates.")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=900)
    parser.add_argument("--warmup", type=float, default=3.0)
    parser.add_argument("--approval-min-frames", type=int, default=APPROVAL_MIN_FRAMES)
    parser.add_argument("--approval-min-score", type=int, default=APPROVAL_MIN_SCORE)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--chrome", type=Path, default=None)
    parser.add_argument("--hm-files", type=Path, default=HM_FILES)
    parser.add_argument("--deploy", action="store_true")
    parser.add_argument("--disable-browser-gpu", action="store_true")
    parser.add_argument("--telemetry-only", action="store_true", help="Skip Nemotron judge and emit browser state diagnostics only.")
    parser.add_argument("--max-frames", type=int, default=0, help="Stop after this many frames; 0 means continuous.")
    parser.add_argument("--once", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_id = args.run_id or f"browser-rl-{now_id()}"
    run_dir = RUN_ROOT / run_id
    frame_dir = run_dir / "frames"
    frame_dir.mkdir(parents=True, exist_ok=True)
    jsonl = run_dir / "browser_frame_scores.jsonl"
    chrome = args.chrome or find_chrome()
    profile = Path(tempfile.mkdtemp(prefix="avatar-browser-rl-"))
    port = free_port()
    command = [
        str(chrome),
        "--headless=new",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "--disable-extensions",
        "--no-sandbox",
        "--remote-allow-origins=*",
        "--remote-debugging-address=127.0.0.1",
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
        f"--window-size={args.width},{args.height}",
        "about:blank",
    ]
    if not args.disable_browser_gpu:
        command[2:2] = GPU_CHROME_FLAGS
    else:
        command.insert(2, "--disable-gpu")
    proc = launch_chrome(command, profile)
    scores: list[dict[str, object]] = []
    cdp: Cdp | None = None
    try:
        wait_for_debugger(port)
        cdp = open_target(port, "about:blank")
        enable_rl_eval(cdp, args.url, args.warmup)
        write_status(
            {
                "ok": True,
                "running": True,
                "mode": MODE,
                "run_id": run_id,
                "url": args.url,
                "model": args.model,
                "endpoint": args.endpoint,
                "started_at": utc_now(),
                "stop_file": str(STOP_FILE),
            }
        )
        index = 0
        while True:
            if STOP_FILE.exists():
                break
            frame = frame_dir / f"browser_frame_{index:06d}.png"
            capture_frame(cdp, frame)
            state = browser_state(cdp)
            try:
                if args.telemetry_only:
                    score = telemetry_score(state, "telemetry-only mode")
                else:
                    score = judge_frame(frame, args.endpoint, args.model)
                score.update(
                    {
                        "ok": True,
                        "frame_index": index,
                        "frame": str(frame),
                        "ts": utc_now(),
                        "model": args.model,
                        "source": "chrome-cdp-screenshot",
                        "browser_state": state,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                score = telemetry_score(state, f"Nemotron evaluator failure: {exc}")
                score.update({
                    "ok": False,
                    "frame_index": index,
                    "frame": str(frame),
                    "ts": utc_now(),
                    "model": args.model,
                    "source": "chrome-cdp-screenshot",
                    "score": 0,
                    "face": 0,
                    "body": 0,
                    "hair": 0,
                    "motion": 0,
                    "lighting": 0,
                    "artifacts": 0,
                    "comment": f"Nemotron evaluator failure: {exc}",
                    "fix": "Keep avatar locked and restore the evaluator path.",
                    "browser_state": state,
                })
            reward = int(score.get("score") or 0)
            gate_failures = explain_policy_gates(score)
            proxy_or_overlay_blocked = any(
                key in failure
                for failure in gate_failures
                for key in (
                    "true_3d_evidence",
                    "target_mesh_proxy_canvas_visible",
                    "target_joint_driver_visible",
                    "target_mesh_deformation_method",
                    "avatar_joint_source",
                )
            )
            if proxy_or_overlay_blocked:
                reward = 0
                score["score"] = 0
                score["proxy_cap_applied"] = True
                score["proxy_cap_score"] = 0
                score["proxy_cap_score_scale"] = 1000
                score["proxy_cap_reason"] = first_reason(gate_failures, "render is proxy/overlay-only")
            if reward <= 0:
                gate_failures.append("reward: reward must be positive before policy can learn")
            policy_learnable = len(gate_failures) == 0
            policy_publishable = False
            policy_learned = False
            score["policy_gate_failures"] = gate_failures
            reason_fields = policy_reason_fields(
                reward=reward,
                best_policy_reward=-1,
                policy_learnable=policy_learnable,
                policy_publishable=policy_publishable,
                policy_learned=policy_learned,
                gate_failures=gate_failures,
            )
            iteration_summary = log_iteration_summary(
                index,
                score,
                reward,
                -1,
                policy_learnable,
                policy_publishable,
                policy_learned,
                {"candidate_id": "cdp-telemetry"},
            )
            score.update(
                {
                    "learning_reward": reward,
                    "best_policy_reward": -1,
                    "policy_learnable": policy_learnable,
                    "policy_publishable": policy_publishable,
                    "policy_learned": policy_learned,
                    "policy_gate_failures": gate_failures,
                    **reason_fields,
                    "iteration_summary": iteration_summary,
                }
            )
            scores.append(score)
            with jsonl.open("a", encoding="utf-8") as out:
                out.write(json.dumps(score, ensure_ascii=False) + "\n")

            summary = mark_mode(
                summarize(scores, run_id, frame_dir, args.approval_min_frames, args.approval_min_score)
            )
            write_json(run_dir / "summary.json", summary)
            write_json(RUN_ROOT / "latest_browser_summary.json", summary)
            write_json(SITE_REALISM, summary)
            if SITE_AVATAR_REALISM.parent.is_dir():
                write_json(SITE_AVATAR_REALISM, summary)
            deployed = False
            if args.deploy and SITE_AVATAR_REALISM.is_file():
                deployed = deploy(SITE_AVATAR_REALISM, "public_html/avatar/realism.json", args.hm_files)
            write_status(
                {
                    "ok": True,
                    "running": True,
                    "mode": MODE,
                    "run_id": run_id,
                    "frames_verified": len(scores),
                    "latest_score": score.get("score", 0),
                    "latest_score_json": score,
                    "motion_approved": summary.get("motion_approved", False),
                    "approval_failures": summary.get("approval_failures", []),
                    "latest_frame": str(frame),
                    "latest_summary": str(run_dir / "summary.json"),
                    "deployed": deployed,
                    "updated_at": utc_now(),
                    "stop_file": str(STOP_FILE),
                }
            )
            index += 1
            if args.once or (args.max_frames and index >= args.max_frames):
                break
            time.sleep(max(0.0, args.interval))
    except Exception as exc:  # noqa: BLE001
        write_status(
            {
                "ok": False,
                "running": False,
                "mode": MODE,
                "run_id": run_id,
                "error": str(exc),
                "updated_at": utc_now(),
                "stop_file": str(STOP_FILE),
            }
        )
        raise
    finally:
        if cdp:
            cdp.close()
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(profile, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
