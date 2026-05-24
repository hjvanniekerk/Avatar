#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib import error as urlerror
from urllib import request as urlrequest


RUN_ROOT = Path(r"D:\Avatar\UnrealTalkingHead\Saved\RealismRL")
SITE_ROOT = Path(r"C:\Users\hjvan\OneDrive\_Codex\Avatar\public_html\avatar")
LOCAL_STATUS = RUN_ROOT / "nt_workload_pump_status.json"
EVENTS = RUN_ROOT / "nt_workload_pump_events.jsonl"
STOP_FILE = RUN_ROOT / "nt_workload_pump.stop"
PUBLIC_STATUS = SITE_ROOT / "nt-workload-status.json"
PUBLIC_DIRECTIVES = SITE_ROOT / "nt-optimizer-directives.json"
LOCAL_DIRECTIVES = RUN_ROOT / "nt_optimizer_directives.json"
HM_FILES = Path(r"C:\Users\hjvan\OneDrive\_Codex\Avatar\deploy\hm_files.py")

DEFAULT_ENDPOINT = "http://100.106.75.76:1234/v1/chat/completions"
DEFAULT_MODEL = "nvidia/nemotron-3-nano-omni"
REMOTE_STATUS = "public_html/avatar/nt-workload-status.json"
REMOTE_DIRECTIVES = "public_html/avatar/nt-optimizer-directives.json"

CONTEXT_FILES = {
    "browser": RUN_ROOT / "continuous_browser_rl_status.json",
    "gpu": RUN_ROOT / "local_gpu_reward_model_status.json",
    "optimizer": RUN_ROOT / "avatar_visual_optimizer_status.json",
    "supervisor": RUN_ROOT / "nemotron_supervisor_status.json",
    "glnk": RUN_ROOT / "gibberlink_multiconnect_status.json",
    "gate": RUN_ROOT / "latest_summary.json",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def append_event(event: dict[str, object]) -> None:
    EVENTS.parent.mkdir(parents=True, exist_ok=True)
    with EVENTS.open("a", encoding="utf-8") as out:
        out.write(json.dumps({"ts": utc_now(), **event}, ensure_ascii=False) + "\n")


def read_json(path: Path) -> object | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def compact(value: object, depth: int = 0) -> object:
    if depth > 4:
        return str(value)[:400]
    if isinstance(value, dict):
        out: dict[str, object] = {}
        for key, item in value.items():
            if key in {"raw_content", "content", "stdout", "stderr", "history_entries"}:
                continue
            out[key] = compact(item, depth + 1)
        return out
    if isinstance(value, list):
        return [compact(item, depth + 1) for item in value[-12:]]
    if isinstance(value, str) and len(value) > 700:
        return value[:700] + "..."
    return value


def context_snapshot() -> dict[str, object]:
    snapshot: dict[str, object] = {"sampled_at": utc_now()}
    for name, path in CONTEXT_FILES.items():
        data = read_json(path)
        if data is not None:
            snapshot[name] = compact(data)
    return snapshot


def call_nt(endpoint: str, model: str, lane: int, seq: int, task: str, snapshot: dict[str, object], timeout: int, max_tokens: int) -> dict[str, object]:
    system = (
        "You are Nemotron acting as the active RL teacher for a web avatar. "
        "Return compact JSON only. Never say idle. Give one concrete next action."
    )
    prompt = {
        "protocol": "NT-WORK/1",
        "lane": lane,
        "seq": seq,
        "task": task,
        "requirements": [
            "keep NT busy with useful RL work",
            "detect stale learners or weak candidate publishing",
            "prefer actions that improve score toward 90 and 240 verified frames",
            "do not unlock motion unless gate is truly met",
        ],
        "context": snapshot,
        "schema": {
            "ok": True,
            "task": task,
            "priority": "high|watch|low",
            "action": "next concrete action",
            "critic": "short critique",
            "optimizer_directive": {
                "publish": False,
                "min_delta": 3,
                "focus": "lighting|hands|borders|motion|hair|face",
                "reason": "why",
            },
        },
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
        "temperature": 0,
        "max_tokens": max_tokens,
        "stream": False,
        "response_format": {"type": "json_object"},
    }
    started = time.time()
    def post(body_payload: dict[str, object]) -> dict[str, object]:
        body = json.dumps(body_payload).encode("utf-8")
        req = urlrequest.Request(endpoint, data=body, headers={"Content-Type": "application/json"}, method="POST")
        with urlrequest.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))

    try:
        data = post(payload)
    except urlerror.HTTPError as exc:
        if exc.code not in (400, 422):
            detail = exc.read().decode("utf-8", errors="replace")[:700]
            raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
        payload.pop("response_format", None)
        try:
            data = post(payload)
        except urlerror.HTTPError as exc2:
            detail = exc2.read().decode("utf-8", errors="replace")[:700]
            raise RuntimeError(f"HTTP {exc2.code}: {detail}") from exc2
    elapsed = round(time.time() - started, 3)
    message = data.get("choices", [{}])[0].get("message", {})
    content = str(message.get("content") or "").strip()
    parsed: dict[str, object]
    try:
        parsed = json.loads(content)
    except Exception:
        parsed = {"ok": False, "critic": content[:500], "action": "retry with compact JSON"}
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    return {
        "ok": bool(parsed.get("ok", True)),
        "lane": lane,
        "seq": seq,
        "task": task,
        "latency_seconds": elapsed,
        "updated_at": utc_now(),
        "parsed": parsed,
        "usage": usage,
        "reasoning_content_present": bool(message.get("reasoning_content")),
    }


class Shared:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.lock = threading.Lock()
        self.lanes: dict[str, dict[str, object]] = {}
        self.started_at = utc_now()
        self.total_cycles = 0
        self.total_ok = 0
        self.last_directive: dict[str, object] = {}

    def update(self, lane: int, result: dict[str, object]) -> dict[str, object]:
        with self.lock:
            self.total_cycles += 1
            if result.get("ok"):
                self.total_ok += 1
            self.lanes[str(lane)] = result
            parsed = result.get("parsed")
            if isinstance(parsed, dict) and isinstance(parsed.get("optimizer_directive"), dict):
                self.last_directive = {
                    "updated_at": result.get("updated_at") or utc_now(),
                    "lane": lane,
                    "task": result.get("task"),
                    **parsed["optimizer_directive"],
                }
                write_json(LOCAL_DIRECTIVES, self.last_directive)
                write_json(PUBLIC_DIRECTIVES, self.last_directive)
            status = self.status_locked()
            write_json(LOCAL_STATUS, status)
            write_json(PUBLIC_STATUS, public_status(status))
            return status

    def status_locked(self) -> dict[str, object]:
        now = time.time()
        connected = 0
        for lane in self.lanes.values():
            ts = str(lane.get("updated_at") or "")
            try:
                age = now - datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
            except Exception:
                age = 999999
            if lane.get("ok") and age <= max(240, self.args.interval * 8):
                connected += 1
        return {
            "ok": True,
            "active": True,
            "running": True,
            "mode": "nt-workload-pump",
            "protocol": "NT-WORK/1",
            "pid": os.getpid(),
            "started_at": self.started_at,
            "updated_at": utc_now(),
            "endpoint": self.args.endpoint,
            "model": self.args.model,
            "lanes_configured": self.args.lanes,
            "lanes_connected": connected,
            "total_cycles": self.total_cycles,
            "total_ok": self.total_ok,
            "tasks": self.args.tasks,
            "last_directive": self.last_directive,
            "lanes": self.lanes,
            "stop_file": str(STOP_FILE),
        }

    def status(self) -> dict[str, object]:
        with self.lock:
            return self.status_locked()


def public_status(status: dict[str, object]) -> dict[str, object]:
    clean = dict(status)
    lanes = {}
    for lane, item in (clean.get("lanes") or {}).items():
        if isinstance(item, dict):
            parsed = item.get("parsed") if isinstance(item.get("parsed"), dict) else {}
            lanes[lane] = {
                "ok": item.get("ok"),
                "updated_at": item.get("updated_at"),
                "task": item.get("task"),
                "seq": item.get("seq"),
                "latency_seconds": item.get("latency_seconds"),
                "action": parsed.get("action"),
                "critic": parsed.get("critic"),
                "priority": parsed.get("priority"),
                "usage": item.get("usage"),
            }
    clean["lanes"] = lanes
    return clean


def deploy_file(local: Path, remote: str) -> dict[str, object]:
    if not os.environ.get("HM_ADMIN_OP_PASSWORD"):
        return {"ok": False, "skipped": "HM_ADMIN_OP_PASSWORD is not set", "remote": remote}
    completed = subprocess.run(
        [sys.executable, str(HM_FILES), "put", str(local), remote],
        cwd=str(HM_FILES.parent),
        text=True,
        capture_output=True,
        timeout=120,
    )
    return {
        "ok": completed.returncode == 0,
        "remote": remote,
        "returncode": completed.returncode,
        "stdout": completed.stdout[-500:],
        "stderr": completed.stderr[-500:],
    }


def deploy_public() -> dict[str, object]:
    result = {"requested": True, "at": utc_now()}
    result["status"] = deploy_file(PUBLIC_STATUS, REMOTE_STATUS)
    if PUBLIC_DIRECTIVES.is_file():
        result["directives"] = deploy_file(PUBLIC_DIRECTIVES, REMOTE_DIRECTIVES)
    return result


def worker(shared: Shared, lane: int) -> None:
    seq = 0
    if shared.args.stagger:
        time.sleep(min(shared.args.interval, lane * shared.args.stagger))
    while not STOP_FILE.exists():
        seq += 1
        task = shared.args.tasks[(lane + seq) % len(shared.args.tasks)]
        try:
            result = call_nt(
                shared.args.endpoint,
                shared.args.model,
                lane,
                seq,
                task,
                context_snapshot(),
                shared.args.timeout,
                shared.args.max_tokens,
            )
        except Exception as exc:  # noqa: BLE001
            result = {
                "ok": False,
                "lane": lane,
                "seq": seq,
                "task": task,
                "updated_at": utc_now(),
                "error": f"{exc.__class__.__name__}: {exc}",
            }
        status = shared.update(lane, result)
        append_event({"event": "nt_work", "lane": lane, "seq": seq, "task": task, "ok": result.get("ok")})
        if shared.args.deploy:
            deploy = deploy_public()
            status["deploy"] = deploy
            write_json(LOCAL_STATUS, status)
            write_json(PUBLIC_STATUS, public_status(status))
        if shared.args.once:
            break
        deadline = time.time() + max(1.0, shared.args.interval)
        while time.time() < deadline and not STOP_FILE.exists():
            time.sleep(0.5)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Keep NT loaded with useful avatar RL critique work.")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--lanes", type=int, default=4)
    parser.add_argument("--interval", type=float, default=10.0)
    parser.add_argument("--stagger", type=float, default=1.5)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--max-tokens", type=int, default=900)
    parser.add_argument("--tasks", nargs="+", default=["frame_gate", "optimizer_policy", "gpu_training", "glnk_backlog"])
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
    SITE_ROOT.mkdir(parents=True, exist_ok=True)
    shared = Shared(args)
    write_json(LOCAL_STATUS, shared.status())
    write_json(PUBLIC_STATUS, public_status(shared.status()))
    append_event({"event": "started", "lanes": args.lanes, "endpoint": args.endpoint})
    threads = [threading.Thread(target=worker, args=(shared, lane), daemon=True) for lane in range(1, args.lanes + 1)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    status = shared.status()
    status["running"] = False
    status["active"] = False
    status["stopped_at"] = utc_now()
    write_json(LOCAL_STATUS, status)
    write_json(PUBLIC_STATUS, public_status(status))
    append_event({"event": "stopped"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
