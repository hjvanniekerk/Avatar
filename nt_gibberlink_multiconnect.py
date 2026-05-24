#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib import error as urlerror
from urllib import request as urlrequest


RUN_ROOT = Path(r"D:\Avatar\UnrealTalkingHead\Saved\RealismRL")
LOCAL_STATUS_FILE = RUN_ROOT / "gibberlink_multiconnect_status.json"
EVENTS_FILE = RUN_ROOT / "gibberlink_multiconnect_events.jsonl"
STOP_FILE = RUN_ROOT / "gibberlink_multiconnect.stop"
PUBLIC_STATUS_FILE = Path(
    r"C:\Users\hjvan\OneDrive\_Codex\Avatar\public_html\avatar\gibberlink-status.json"
)
HM_FILES = Path(r"C:\Users\hjvan\OneDrive\_Codex\Avatar\deploy\hm_files.py")
REMOTE_STATUS_PATH = "public_html/avatar/gibberlink-status.json"

DEFAULT_ENDPOINT = "http://100.106.75.76:1234/v1/chat/completions"
DEFAULT_MODEL = "nvidia/nemotron-3-nano-omni"
PROTOCOL = "GLNK/1"

STATUS_SOURCES = {
    "browser_rl": RUN_ROOT / "continuous_browser_rl_status.json",
    "gpu_reward": RUN_ROOT / "local_gpu_reward_model_status.json",
    "nt_supervisor": RUN_ROOT / "nemotron_supervisor_status.json",
    "unreal_gate": RUN_ROOT / "latest_summary.json",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def compact(value: object, depth: int = 0) -> object:
    if depth >= 5:
        return str(value)[:300]
    if isinstance(value, dict):
        out: dict[str, object] = {}
        keep = {
            "ok",
            "running",
            "active",
            "mode",
            "score",
            "latest_score",
            "frames_verified",
            "samples_seen",
            "training_active",
            "cuda_available",
            "gpu_name",
            "last_loss",
            "last_nemotron_label_time",
            "motion_resume_allowed",
            "approval_failures",
            "comment",
            "fix",
            "updated_at",
            "last_tick",
            "error",
            "risk",
            "next_action",
            "lessons",
        }
        for key, child in value.items():
            if key in {"raw_content", "content", "stdout", "stderr", "image_b64"}:
                continue
            if depth == 0 and key not in keep:
                continue
            out[str(key)] = compact(child, depth + 1)
        return out
    if isinstance(value, list):
        return [compact(item, depth + 1) for item in value[-8:]]
    if isinstance(value, str):
        return value if len(value) <= 700 else value[:697] + "..."
    return value


def read_json(path: Path) -> object | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def short_text(value: object, limit: int = 180) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 3] + "..."


def compact_lessons(value: object) -> list[str]:
    if isinstance(value, list):
        return [text for text in (short_text(item, 110) for item in value[-3:]) if text]
    text = short_text(value, 140)
    return [text] if text else []


def summarize_source(name: str, data: object | None) -> dict[str, object]:
    if not isinstance(data, dict):
        return {"ok": False, "missing": True}
    if name == "browser_rl":
        latest = data.get("latest_score") if isinstance(data.get("latest_score"), dict) else {}
        return {
            "ok": data.get("ok"),
            "running": data.get("running"),
            "frames": data.get("frames_verified"),
            "score": latest.get("score") if isinstance(latest, dict) else data.get("score"),
            "motion_allowed": data.get("motion_resume_allowed"),
            "error": short_text(latest.get("error") if isinstance(latest, dict) else data.get("error"), 120),
            "updated": data.get("updated_at"),
        }
    if name == "gpu_reward":
        gate = data.get("motion_gate") if isinstance(data.get("motion_gate"), dict) else {}
        return {
            "ok": data.get("ok"),
            "running": data.get("running"),
            "training": data.get("training_active"),
            "cuda": data.get("cuda_available"),
            "gpu": data.get("gpu_name"),
            "samples": data.get("samples_seen") or data.get("samples"),
            "loss": data.get("last_loss"),
            "steps": data.get("steps"),
            "motion_allowed": data.get("motion_resume_allowed"),
            "gate_score": gate.get("score") if isinstance(gate, dict) else None,
            "gate_frames": gate.get("frames_verified") if isinstance(gate, dict) else None,
            "gate_failures": gate.get("approval_failures") if isinstance(gate, dict) else None,
            "updated": data.get("updated_at"),
        }
    if name == "nt_supervisor":
        return {
            "ok": data.get("ok"),
            "active": data.get("active"),
            "loop": data.get("loop_count") or data.get("cycle"),
            "last_label": data.get("last_nemotron_label_time"),
            "endpoint": data.get("active_endpoint") or data.get("endpoint"),
            "cuda": data.get("cuda_available"),
            "lessons": compact_lessons(data.get("lessons") or data.get("feedback")),
            "next": short_text(data.get("next_action"), 120),
            "updated": data.get("updated_at"),
        }
    if name == "unreal_gate":
        return {
            "score": data.get("score"),
            "best": data.get("best_score"),
            "worst": data.get("worst_score"),
            "frames": data.get("frames_verified"),
            "approved": data.get("motion_approved"),
            "min_frames": data.get("approval_min_frames"),
            "min_score": data.get("approval_min_score"),
            "face": data.get("face"),
            "body": data.get("body"),
            "hair": data.get("hair"),
            "motion": data.get("motion"),
            "lighting": data.get("lighting"),
            "artifacts": data.get("artifacts"),
            "failures": data.get("approval_failures"),
            "comment": short_text(data.get("comment"), 180),
            "updated": data.get("ts"),
        }
    return compact(data) if isinstance(compact(data), dict) else {"value": compact(data)}


def collect_rl_state() -> dict[str, object]:
    state: dict[str, object] = {"sampled_at": utc_now()}
    for name, path in STATUS_SOURCES.items():
        if not path.is_file():
            state[name] = {"ok": False, "missing": True}
            continue
        state[name] = summarize_source(name, read_json(path))
    return state


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def append_event(event: dict[str, object]) -> None:
    EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with EVENTS_FILE.open("a", encoding="utf-8") as out:
        out.write(json.dumps({"ts": utc_now(), **event}, ensure_ascii=False) + "\n")


def post_json(url: str, payload: dict[str, object], timeout: int) -> dict[str, object]:
    body = json.dumps(payload).encode("utf-8")
    req = urlrequest.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urlrequest.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def message_text(message: object) -> str:
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n".join(
            str(item.get("text", ""))
            for item in content
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        ).strip()
    reasoning = message.get("reasoning_content")
    return str(reasoning or "").strip()


def extract_json(text: str) -> dict[str, object]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            raise
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("NT response JSON is not an object")
    return parsed


def build_payload(args: argparse.Namespace, lane_id: int, seq: int, rl_state: dict[str, object]) -> dict[str, object]:
    frame = {
        "protocol": PROTOCOL,
        "transport": "text-over-http",
        "lane": lane_id,
        "seq": seq,
        "ts": utc_now(),
        "intent": "rl-teacher-multiconnect",
        "payload": rl_state,
        "request": {
            "ack": True,
            "teach_rl": True,
            "keep_nt_busy": True,
            "return_json_only": True,
        },
    }
    prompt = (
        "You are Nemotron (NT), the teacher model for an avatar RL learner. "
        "A machine peer is switching to GibberLink-style compact frames. "
        "This transport is text-over-HTTP, not acoustic GGWave. "
        "Acknowledge the frame and teach the RL loop using only observed state. "
        "Return strict JSON only, under 700 characters. Use max three short lessons. "
        "Do not invent scores and do not claim motion approval unless the frame gate says approved. "
        "Required keys: ok, protocol, ack, lane, seq, priority, lessons, reward_hint, next_action, keepalive. "
        "Frame follows:\n"
        + json.dumps(frame, ensure_ascii=False, separators=(",", ":"))
    )
    return {
        "model": args.model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are NT in a compact agent-to-agent RL teaching protocol. "
                    "Reply with one compact JSON object only. Do not add markdown. "
                    "Do not invent scores or claim motion approval."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.15,
        "max_tokens": args.max_tokens,
        "stream": False,
    }


class SharedState:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.lock = threading.Lock()
        self.write_lock = threading.Lock()
        self.started_at = utc_now()
        self.lanes: dict[str, dict[str, object]] = {}
        self.total_cycles = 0
        self.total_ok = 0
        self.last_ack: dict[str, object] | None = None
        self.last_deploy_at = 0.0

    def status(self) -> dict[str, object]:
        with self.lock:
            lane_values = list(self.lanes.values())
            now = time.time()
            connected = sum(
                1
                for lane in lane_values
                if lane.get("ok") and now - float(lane.get("last_ok_epoch") or 0) <= max(60.0, self.args.interval * 4)
            )
            return {
                "ok": connected > 0,
                "active": True,
                "running": True,
                "mode": "gibberlink-nt-multiconnect",
                "protocol": PROTOCOL,
                "transport": "text-over-http",
                "acoustic_ggwave": False,
                "acoustic_note": "GGWave/GibberLink is acoustic; NT here is LM Studio HTTP, so GLNK/1 uses compact text frames.",
                "pid": os.getpid(),
                "started_at": self.started_at,
                "updated_at": utc_now(),
                "endpoint": self.args.endpoint,
                "model": self.args.model,
                "lanes_configured": self.args.lanes,
                "lanes_connected": connected,
                "total_cycles": self.total_cycles,
                "total_ok": self.total_ok,
                "last_nt_label_time": self.last_ack.get("ts") if self.last_ack else None,
                "last_ack": self.last_ack,
                "lanes": self.lanes,
                "stop_file": str(STOP_FILE),
                "public_status": str(PUBLIC_STATUS_FILE),
                "events_jsonl": str(EVENTS_FILE),
            }

    def update_lane(self, lane_id: int, update: dict[str, object]) -> None:
        with self.lock:
            self.total_cycles += 1
            if update.get("ok"):
                self.total_ok += 1
                update["last_ok_epoch"] = time.time()
                self.last_ack = {
                    "ts": utc_now(),
                    "lane": lane_id,
                    "seq": update.get("seq"),
                    "ack": update.get("ack"),
                    "lessons": update.get("lessons", []),
                    "next_action": update.get("next_action"),
                }
            self.lanes[str(lane_id)] = {"updated_at": utc_now(), **update}


def public_status(status: dict[str, object]) -> dict[str, object]:
    clean = dict(status)
    lanes = clean.get("lanes")
    if isinstance(lanes, dict):
        clean["lanes"] = {
            lane: {
                key: value
                for key, value in data.items()
                if key not in {"raw_content", "error_detail"}
            }
            for lane, data in lanes.items()
            if isinstance(data, dict)
        }
    return clean


def deploy_public_status() -> dict[str, object]:
    if not os.environ.get("HM_ADMIN_OP_PASSWORD"):
        return {"requested": True, "ok": False, "skipped": "HM_ADMIN_OP_PASSWORD is not set"}
    try:
        completed = subprocess.run(
            [sys.executable, str(HM_FILES), "put", str(PUBLIC_STATUS_FILE), REMOTE_STATUS_PATH],
            cwd=str(HM_FILES.parent),
            text=True,
            capture_output=True,
            timeout=120,
        )
        return {
            "requested": True,
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "stdout": completed.stdout[-800:],
            "stderr": completed.stderr[-800:],
            "finished_at": utc_now(),
        }
    except Exception as exc:  # noqa: BLE001
        return {"requested": True, "ok": False, "error": f"{exc.__class__.__name__}: {exc}"}


def write_status(shared: SharedState) -> None:
    with shared.write_lock:
        status = shared.status()
        write_json(LOCAL_STATUS_FILE, status)
        write_json(PUBLIC_STATUS_FILE, public_status(status))
        if shared.args.deploy:
            with shared.lock:
                if time.time() - shared.last_deploy_at < 10:
                    return
                shared.last_deploy_at = time.time()
            deploy = deploy_public_status()
            status["deploy"] = deploy
            write_json(LOCAL_STATUS_FILE, status)
            write_json(PUBLIC_STATUS_FILE, public_status(status))


def call_lane(args: argparse.Namespace, lane_id: int, seq: int) -> dict[str, object]:
    payload = build_payload(args, lane_id, seq, collect_rl_state())
    started = time.time()
    try:
        response = post_json(args.endpoint, payload, args.timeout)
        message = response.get("choices", [{}])[0].get("message", {})
        content = message_text(message)
        if not content:
            raise RuntimeError("empty NT response")
        parsed = extract_json(content)
        return {
            "ok": True,
            "seq": seq,
            "latency_seconds": round(time.time() - started, 3),
            "ack": parsed.get("ack"),
            "priority": parsed.get("priority"),
            "lessons": parsed.get("lessons", []),
            "reward_hint": parsed.get("reward_hint"),
            "next_action": parsed.get("next_action"),
            "usage": response.get("usage", {}),
            "reasoning_content_present": bool(isinstance(message, dict) and message.get("reasoning_content")),
            "raw_content": content[:1200],
        }
    except urlerror.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:600]
        return {"ok": False, "seq": seq, "error": f"HTTPError {exc.code}", "error_detail": detail}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "seq": seq, "error": f"{exc.__class__.__name__}: {exc}"}


def lane_loop(shared: SharedState, lane_id: int) -> None:
    seq = 0
    if shared.args.stagger:
        time.sleep(min(shared.args.interval, lane_id * shared.args.stagger))
    while not STOP_FILE.exists():
        seq += 1
        update = call_lane(shared.args, lane_id, seq)
        shared.update_lane(lane_id, update)
        append_event({"event": "lane_cycle", "lane": lane_id, **{k: v for k, v in update.items() if k != "raw_content"}})
        write_status(shared)
        if shared.args.once:
            break
        deadline = time.time() + max(1.0, shared.args.interval)
        while time.time() < deadline and not STOP_FILE.exists():
            time.sleep(0.5)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="GLNK/1 GibberLink-style multiconnect bridge between RL and NT.")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--lanes", type=int, default=3)
    parser.add_argument("--interval", type=float, default=12.0)
    parser.add_argument("--stagger", type=float, default=2.0)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--max-tokens", type=int, default=1200)
    parser.add_argument("--deploy", action="store_true")
    parser.add_argument("--once", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.endpoint = args.endpoint.rstrip("/")
    if not args.endpoint.endswith("/chat/completions"):
        args.endpoint += "/chat/completions"
    args.lanes = max(1, min(8, args.lanes))
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    shared = SharedState(args)
    append_event({"event": "gibberlink_started", "pid": os.getpid(), "lanes": args.lanes, "endpoint": args.endpoint})
    write_status(shared)
    threads = [threading.Thread(target=lane_loop, args=(shared, lane), daemon=True) for lane in range(1, args.lanes + 1)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    status = shared.status()
    status["running"] = False
    status["active"] = False
    status["stopped_at"] = utc_now()
    write_json(LOCAL_STATUS_FILE, status)
    write_json(PUBLIC_STATUS_FILE, public_status(status))
    append_event({"event": "gibberlink_stopped", "pid": os.getpid()})
    return 0 if status.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
