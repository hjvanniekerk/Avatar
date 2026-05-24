#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib import error as urlerror
from urllib import request as urlrequest


RUN_ROOT = Path(r"D:\Avatar\UnrealTalkingHead\Saved\RealismRL")
LOCAL_STATUS_FILE = RUN_ROOT / "nemotron_supervisor_status.json"
EVENTS_FILE = RUN_ROOT / "nemotron_supervisor_events.jsonl"
STOP_FILE = RUN_ROOT / "nemotron_supervisor.stop"

PUBLIC_STATUS_FILE = Path(
    r"C:\Users\hjvan\OneDrive\_Codex\Avatar\public_html\avatar\nemotron-status.json"
)
HM_FILES = Path(r"C:\Users\hjvan\OneDrive\_Codex\Avatar\deploy\hm_files.py")
REMOTE_STATUS_PATH = "public_html/avatar/nemotron-status.json"

DEFAULT_ENDPOINT = "http://100.106.75.76:1234/v1/chat/completions"
FALLBACK_ENDPOINT = "http://127.0.0.1:8766/v1/chat/completions"
DEFAULT_MODEL = "nvidia/nemotron-3-nano-omni"

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
CONTEXT_NAME_TOKENS = ("status", "state", "summary", "history", "request", "report")
SELF_STATUS_NAMES = {LOCAL_STATUS_FILE.name, EVENTS_FILE.name}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_endpoint(endpoint: str) -> str:
    endpoint = endpoint.strip().rstrip("/")
    if endpoint.endswith("/chat/completions"):
        return endpoint
    return endpoint + "/chat/completions"


def endpoint_chain(primary: str) -> list[str]:
    endpoints = [normalize_endpoint(primary), normalize_endpoint(FALLBACK_ENDPOINT)]
    unique: list[str] = []
    for endpoint in endpoints:
        if endpoint not in unique:
            unique.append(endpoint)
    return unique


def file_mtime_utc(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def append_event(event: dict[str, object]) -> None:
    event = {"ts": utc_now(), **event}
    EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with EVENTS_FILE.open("a", encoding="utf-8") as out:
        out.write(json.dumps(event, ensure_ascii=False) + "\n")


def compact_json(value: object, depth: int = 0) -> object:
    if depth >= 6:
        return str(value)[:500]
    if isinstance(value, dict):
        compacted: dict[str, object] = {}
        for key, child in value.items():
            if key in {"raw_content", "raw_response", "image_b64"}:
                text = str(child)
                compacted[key] = f"<omitted {len(text)} chars>"
            else:
                compacted[str(key)] = compact_json(child, depth + 1)
        return compacted
    if isinstance(value, list):
        if len(value) <= 12:
            return [compact_json(item, depth + 1) for item in value]
        return {
            "_count": len(value),
            "_tail": [compact_json(item, depth + 1) for item in value[-12:]],
        }
    if isinstance(value, str):
        return value if len(value) <= 1600 else value[:1600] + "...<truncated>"
    return value


def read_context_file(path: Path) -> dict[str, object]:
    stat = path.stat()
    item: dict[str, object] = {
        "name": path.name,
        "path": str(path),
        "mtime": file_mtime_utc(path),
        "size_bytes": stat.st_size,
    }
    if stat.st_size > 512_000:
        item["skipped"] = "file larger than 512000 bytes"
        return item
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        item["json"] = compact_json(data)
    except Exception as exc:  # noqa: BLE001
        item["read_error"] = f"{exc.__class__.__name__}: {exc}"
    return item


def context_json_files(root: Path, limit: int) -> list[Path]:
    if not root.is_dir():
        return []
    candidates: list[Path] = []
    for path in root.glob("*.json"):
        lower = path.name.lower()
        if path.name in SELF_STATUS_NAMES:
            continue
        if any(token in lower for token in CONTEXT_NAME_TOKENS):
            candidates.append(path)
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[:limit]


def latest_run_dirs(root: Path, limit: int = 8) -> list[dict[str, object]]:
    if not root.is_dir():
        return []
    dirs = [path for path in root.iterdir() if path.is_dir()]
    dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [
        {
            "name": path.name,
            "path": str(path),
            "mtime": file_mtime_utc(path),
        }
        for path in dirs[:limit]
    ]


def latest_image(root: Path) -> Path | None:
    if not root.is_dir():
        return None
    latest: Path | None = None
    latest_mtime = -1.0
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in IMAGE_EXTS:
            continue
        mtime = path.stat().st_mtime
        if mtime > latest_mtime:
            latest = path
            latest_mtime = mtime
    return latest


def image_mime(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    return "image/png"


def image_payload(path: Path, max_bytes: int) -> dict[str, object] | None:
    data = path.read_bytes()
    if len(data) > max_bytes:
        try:
            from PIL import Image  # type: ignore

            with Image.open(io.BytesIO(data)) as img:
                img = img.convert("RGB")
                img.thumbnail((512, 512))
                out = io.BytesIO()
                img.save(out, format="JPEG", quality=64, optimize=True)
                data = out.getvalue()
        except Exception:  # noqa: BLE001
            return None
    if len(data) > max_bytes:
        return None
    mime = "image/jpeg" if data[:2] == b"\xff\xd8" else image_mime(path)
    b64 = base64.b64encode(data).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{mime};base64,{b64}"},
    }


def collect_context(args: argparse.Namespace) -> tuple[dict[str, object], Path | None, bool]:
    files = [read_context_file(path) for path in context_json_files(RUN_ROOT, args.max_context_files)]
    frame = latest_image(RUN_ROOT)
    frame_attached = False
    frame_info: dict[str, object] | None = None
    if frame is not None:
        frame_info = {
            "path": str(frame),
            "name": frame.name,
            "mtime": file_mtime_utc(frame),
            "size_bytes": frame.stat().st_size,
            "attached": False,
        }
        if not args.no_image:
            frame_attached = True
            frame_info["attached"] = True
        else:
            frame_info["attach_skipped"] = "--no-image"

    context = {
        "rl_root": str(RUN_ROOT),
        "rl_root_exists": RUN_ROOT.is_dir(),
        "sampled_at": utc_now(),
        "json_files": files,
        "latest_run_dirs": latest_run_dirs(RUN_ROOT),
        "latest_frame": frame_info,
    }
    return context, frame, frame_attached


def limit_context_text(context: dict[str, object], max_chars: int) -> str:
    text = json.dumps(context, indent=2, ensure_ascii=False)
    if len(text) <= max_chars:
        return text
    trimmed = dict(context)
    trimmed["json_files"] = trimmed.get("json_files", [])[:4]
    text = json.dumps(trimmed, indent=2, ensure_ascii=False)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...<context truncated>"


def build_messages(context: dict[str, object], frame: Path | None, frame_attached: bool, args: argparse.Namespace) -> list[dict[str, object]]:
    context_text = limit_context_text(context, args.context_chars)
    prompt = (
        "Inspect the current Avatar realism RL/status context and produce one compact supervisor update. "
        "Keep Nemotron active, but do not invent measurements that are not in the files. "
        "If a latest frame image is attached, factor it into visual observations. "
        "Return only JSON with this schema: "
        '{"ok":true,"summary":"...","priority":"idle|watch|action","observations":["..."],'
        '"next_action":"...","risk":"...","stale_inputs":[]}. '
        "Use short strings. Context JSON follows:\n"
        f"{context_text}"
    )
    content: list[dict[str, object]] = [{"type": "text", "text": prompt}]
    if frame is not None and frame_attached:
        payload = image_payload(frame, args.max_image_bytes)
        if payload is not None:
            content.append(payload)
    return [
        {
            "role": "system",
            "content": (
                "You are Nemotron supervising an Unreal avatar realism RL loop. "
                "You return strict JSON only and focus on operational status, risks, and next actions."
            ),
        },
        {"role": "user", "content": content},
    ]


def post_json(url: str, payload: dict[str, object], timeout: int) -> dict[str, object]:
    body = json.dumps(payload).encode("utf-8")
    req = urlrequest.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urlrequest.urlopen(req, timeout=timeout) as resp:
        text = resp.read().decode("utf-8", errors="replace")
    return json.loads(text)


def message_text(message: object) -> str:
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts).strip()
    return ""


def extract_json(text: str) -> dict[str, object]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            raise
        data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("response JSON is not an object")
    return data


def response_content(response: dict[str, object]) -> tuple[str, dict[str, object]]:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        return "", {}
    choice = choices[0]
    if not isinstance(choice, dict):
        return "", {}
    message = choice.get("message")
    if not isinstance(message, dict):
        return "", {}
    return message_text(message), message


def remove_image_parts(messages: list[dict[str, object]]) -> list[dict[str, object]]:
    cleaned: list[dict[str, object]] = []
    for message in messages:
        cloned = dict(message)
        content = cloned.get("content")
        if isinstance(content, list):
            cloned["content"] = [item for item in content if not (isinstance(item, dict) and item.get("type") == "image_url")]
        cleaned.append(cloned)
    return cleaned


def text_only_messages(messages: list[dict[str, object]]) -> list[dict[str, object]]:
    cleaned: list[dict[str, object]] = []
    for message in messages:
        cloned = dict(message)
        content = cloned.get("content")
        if isinstance(content, list):
            texts = [
                item.get("text", "")
                for item in content
                if isinstance(item, dict) and isinstance(item.get("text"), str)
            ]
            cloned["content"] = "\n".join(text for text in texts if text).strip()
        cleaned.append(cloned)
    return cleaned


def call_nemotron(args: argparse.Namespace, messages: list[dict[str, object]], cycle: int) -> dict[str, object]:
    failures: list[dict[str, object]] = []
    image_included = any(
        isinstance(item, dict) and item.get("type") == "image_url"
        for message in messages
        for item in (message.get("content") if isinstance(message.get("content"), list) else [])
    )
    text_messages = text_only_messages(remove_image_parts(messages))
    variants: list[tuple[str, list[dict[str, object]], bool]] = [
        ("text_json_prompt", text_messages, False),
        ("plain_with_image" if image_included else "plain_text", messages, False),
    ]
    if image_included:
        variants.append(("plain_text_no_image", text_messages, False))

    for endpoint in endpoint_chain(args.endpoint):
        for variant_name, variant_messages, use_response_format in variants:
            payload: dict[str, object] = {
                "model": args.model,
                "messages": variant_messages,
                "temperature": 0.2,
                "max_tokens": args.max_tokens,
                "stream": False,
            }
            if use_response_format:
                payload["response_format"] = {"type": "text"}
            started = time.time()
            try:
                response = post_json(endpoint, payload, args.timeout)
                content, message = response_content(response)
                if not content:
                    payload["max_tokens"] = max(args.max_tokens, 4096)
                    response = post_json(endpoint, payload, args.timeout)
                    content, message = response_content(response)
                if not content:
                    raise RuntimeError("empty assistant message content")
                try:
                    parsed = extract_json(content)
                    parsed_ok = True
                except Exception as exc:  # noqa: BLE001
                    parsed = {
                        "ok": False,
                        "summary": "Nemotron returned non-JSON supervisor text.",
                        "priority": "watch",
                        "observations": [content[:600]],
                        "next_action": "Keep polling and repair response formatting.",
                        "risk": f"{exc.__class__.__name__}: {exc}",
                        "stale_inputs": [],
                    }
                    parsed_ok = False
                return {
                    "ok": True,
                    "endpoint": endpoint,
                    "variant": variant_name,
                    "latency_seconds": round(time.time() - started, 3),
                    "content": content,
                    "parsed": compact_json(parsed),
                    "parsed_ok": parsed_ok,
                    "usage": response.get("usage", {}),
                    "reasoning_content_present": bool(message.get("reasoning_content")),
                    "failures_before_success": failures,
                }
            except urlerror.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:600]
                failure = {
                    "endpoint": endpoint,
                    "variant": variant_name,
                    "error": f"HTTPError {exc.code}: {detail}",
                }
                failures.append(failure)
                append_event({"event": "endpoint_failure", "cycle": cycle, **failure})
            except Exception as exc:  # noqa: BLE001
                failure = {
                    "endpoint": endpoint,
                    "variant": variant_name,
                    "error": f"{exc.__class__.__name__}: {exc}",
                }
                failures.append(failure)
                append_event({"event": "endpoint_failure", "cycle": cycle, **failure})
    return {
        "ok": False,
        "endpoint": None,
        "variant": None,
        "latency_seconds": 0,
        "content": "",
        "parsed": {
            "ok": False,
            "summary": "All Nemotron endpoint attempts failed.",
            "priority": "action",
            "observations": [],
            "next_action": "Check the primary and fallback OpenAI-compatible servers.",
            "risk": "No successful supervisor completion.",
            "stale_inputs": [],
        },
        "parsed_ok": False,
        "usage": {},
        "failures_before_success": failures,
    }


def public_status(status: dict[str, object]) -> dict[str, object]:
    clean = dict(status)
    nemotron = clean.get("nemotron")
    if isinstance(nemotron, dict):
        clean["nemotron"] = {
            key: value
            for key, value in nemotron.items()
            if key not in {"content", "failures_before_success"}
        }
        if nemotron.get("failures_before_success"):
            clean["nemotron"]["endpoint_failures"] = len(nemotron["failures_before_success"])
    return clean


def write_status_pair(status: dict[str, object]) -> None:
    write_json(LOCAL_STATUS_FILE, status)
    write_json(PUBLIC_STATUS_FILE, public_status(status))


def deploy_public_status() -> dict[str, object]:
    result: dict[str, object] = {
        "requested": True,
        "remote": REMOTE_STATUS_PATH,
        "hm_files": str(HM_FILES),
        "attempted_at": utc_now(),
    }
    if not os.environ.get("HM_ADMIN_OP_PASSWORD"):
        result.update({"ok": False, "skipped": "HM_ADMIN_OP_PASSWORD is not set"})
        return result
    if not HM_FILES.is_file():
        result.update({"ok": False, "skipped": f"missing {HM_FILES}"})
        return result
    try:
        completed = subprocess.run(
            [sys.executable, str(HM_FILES), "put", str(PUBLIC_STATUS_FILE), REMOTE_STATUS_PATH],
            cwd=str(HM_FILES.parent),
            text=True,
            capture_output=True,
            timeout=120,
        )
    except Exception as exc:  # noqa: BLE001
        result.update({"ok": False, "error": f"{exc.__class__.__name__}: {exc}", "finished_at": utc_now()})
        return result
    result.update(
        {
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "stdout": completed.stdout[-1200:],
            "stderr": completed.stderr[-1200:],
            "finished_at": utc_now(),
        }
    )
    return result


def gpu_status() -> dict[str, object]:
    try:
        completed = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,utilization.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            capture_output=True,
            timeout=5,
        )
    except Exception as exc:  # noqa: BLE001
        return {"cuda_available": False, "error": f"{exc.__class__.__name__}: {exc}"}
    if completed.returncode != 0:
        return {"cuda_available": False, "error": completed.stderr.strip()[:300]}
    line = completed.stdout.strip().splitlines()[0] if completed.stdout.strip() else ""
    parts = [part.strip() for part in line.split(",")]
    status: dict[str, object] = {"cuda_available": bool(parts and parts[0])}
    if len(parts) >= 4:
        status.update(
            {
                "gpu_name": parts[0],
                "gpu_utilization_pct": parts[1],
                "gpu_memory_used_mib": parts[2],
                "gpu_memory_total_mib": parts[3],
            }
        )
    return status


def supervisor_feedback(nemotron: dict[str, object]) -> tuple[str, list[object], str | None]:
    parsed = nemotron.get("parsed")
    if not isinstance(parsed, dict):
        return "Nemotron has not returned parsed supervisor feedback.", [], None
    summary = str(parsed.get("summary") or "").strip()
    next_action = str(parsed.get("next_action") or "").strip()
    observations = parsed.get("observations")
    lessons = observations if isinstance(observations, list) else []
    parts = [part for part in [summary, f"Next: {next_action}" if next_action else ""] if part]
    return " ".join(parts) or "Nemotron returned an empty supervisor summary.", lessons, next_action or None


def run_cycle(args: argparse.Namespace, cycle: int) -> dict[str, object]:
    started_at = utc_now()
    context, frame, frame_attached = collect_context(args)
    messages = build_messages(context, frame, frame_attached, args)
    append_event(
        {
            "event": "cycle_started",
            "cycle": cycle,
            "model": args.model,
            "endpoint": args.endpoint,
            "latest_frame": str(frame) if frame else None,
            "frame_attached": frame_attached,
        }
    )
    nemotron = call_nemotron(args, messages, cycle)
    updated_at = utc_now()
    feedback, lessons, next_action = supervisor_feedback(nemotron)
    gpu = gpu_status()
    status: dict[str, object] = {
        "ok": bool(nemotron.get("ok")),
        "running": not args.once,
        "active": not args.once,
        "mode": "nemotron-supervisor",
        "pid": os.getpid(),
        "cycle": cycle,
        "loop_count": cycle,
        "samples_seen": cycle,
        "started_at": started_at,
        "updated_at": updated_at,
        "last_tick": updated_at,
        "last_nemotron_label_time": updated_at if nemotron.get("ok") else None,
        "interval_seconds": args.interval,
        "model": args.model,
        "endpoint": args.endpoint,
        "fallback_endpoint": FALLBACK_ENDPOINT,
        "active_endpoint": nemotron.get("endpoint"),
        "rl_root": str(RUN_ROOT),
        "local_status": str(LOCAL_STATUS_FILE),
        "public_status": str(PUBLIC_STATUS_FILE),
        "events_jsonl": str(EVENTS_FILE),
        "stop_file": str(STOP_FILE),
        "latest_frame": context.get("latest_frame"),
        "context_files": [
            {
                "name": item.get("name"),
                "mtime": item.get("mtime"),
                "size_bytes": item.get("size_bytes"),
            }
            for item in context.get("json_files", [])
            if isinstance(item, dict)
        ],
        "cuda_available": bool(gpu.get("cuda_available")),
        "gpu": gpu,
        "feedback": feedback,
        "lessons": lessons,
        "next_action": next_action,
        "nemotron": nemotron,
        "deploy": {"requested": bool(args.deploy), "ok": None},
    }
    write_status_pair(status)
    if args.deploy:
        deploy_result = deploy_public_status()
        status["deploy"] = deploy_result
        write_status_pair(status)
        append_event({"event": "deploy_completed", "cycle": cycle, "ok": deploy_result.get("ok")})
    append_event(
        {
            "event": "cycle_completed",
            "cycle": cycle,
            "ok": status["ok"],
            "active_endpoint": status["active_endpoint"],
            "deploy_ok": status.get("deploy", {}).get("ok") if isinstance(status.get("deploy"), dict) else None,
        }
    )
    return status


def sleep_with_stop(interval: float) -> None:
    deadline = time.time() + max(0.0, interval)
    while time.time() < deadline:
        if STOP_FILE.exists():
            return
        time.sleep(min(1.0, deadline - time.time()))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Standalone continuous Nemotron supervisor for Avatar RL/status files."
    )
    parser.add_argument("--interval", type=float, default=30.0, help="Seconds between supervisor calls.")
    parser.add_argument("--deploy", action="store_true", help="Upload public nemotron-status.json with hm_files.py.")
    parser.add_argument("--once", action="store_true", help="Run one supervisor cycle and exit.")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT, help="OpenAI-compatible chat/completions endpoint.")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--timeout", type=int, default=180, help="HTTP timeout per endpoint attempt.")
    parser.add_argument("--max-tokens", type=int, default=1400)
    parser.add_argument("--context-chars", type=int, default=6_000)
    parser.add_argument("--max-context-files", type=int, default=12)
    parser.add_argument("--max-image-bytes", type=int, default=180_000)
    parser.add_argument("--no-image", action="store_true", help="Do not attach the latest frame image.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    cycle = 0
    last_status: dict[str, object] | None = None
    failed_once = False
    append_event(
        {
            "event": "supervisor_started",
            "pid": os.getpid(),
            "model": args.model,
            "endpoint": args.endpoint,
            "interval_seconds": args.interval,
            "deploy": bool(args.deploy),
        }
    )
    try:
        while True:
            if STOP_FILE.exists():
                break
            cycle += 1
            try:
                last_status = run_cycle(args, cycle)
                failed_once = bool(last_status is not None and not last_status.get("ok"))
            except Exception as exc:  # noqa: BLE001
                failed_once = True
                last_status = {
                    "ok": False,
                    "running": not args.once,
                    "mode": "nemotron-supervisor",
                    "pid": os.getpid(),
                    "cycle": cycle,
                    "updated_at": utc_now(),
                    "model": args.model,
                    "endpoint": args.endpoint,
                    "fallback_endpoint": FALLBACK_ENDPOINT,
                    "rl_root": str(RUN_ROOT),
                    "local_status": str(LOCAL_STATUS_FILE),
                    "public_status": str(PUBLIC_STATUS_FILE),
                    "events_jsonl": str(EVENTS_FILE),
                    "stop_file": str(STOP_FILE),
                    "error": f"{exc.__class__.__name__}: {exc}",
                }
                write_status_pair(last_status)
                append_event({"event": "cycle_failed", "cycle": cycle, "error": last_status["error"]})
            if args.once:
                break
            sleep_with_stop(args.interval)
    finally:
        if last_status is not None and (STOP_FILE.exists() or not args.once):
            last_status = dict(last_status)
            last_status["running"] = False
            last_status["stopped_at"] = utc_now()
            last_status["stop_reason"] = "stop_file" if STOP_FILE.exists() else "process_exit"
            write_status_pair(last_status)
        append_event({"event": "supervisor_stopped", "pid": os.getpid(), "cycle": cycle})
    if args.once and failed_once:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
