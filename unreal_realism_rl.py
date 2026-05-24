#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib import error as urlerror
from urllib import request as urlrequest


ROOT = Path(r"D:\Avatar")
TALKING_HEAD_PROJECT = ROOT / "UnrealTalkingHead" / "TalkingHead.uproject"
NIA_PROJECT = ROOT / "CharacterCreatorNia" / "CharacterCreatorNia.uproject"
UNREAL_SCRIPT = ROOT / "UnrealTalkingHead" / "Scripts" / "render_realism_frames.py"
RUN_ROOT = ROOT / "UnrealTalkingHead" / "Saved" / "RealismRL"
REQUEST_PATH = RUN_ROOT / "render_request.json"
SITE_REALISM = ROOT / "realism.json"
SITE_AVATAR_REALISM = Path(r"C:\Users\hjvan\OneDrive\_Codex\Avatar\public_html\avatar\realism.json")
SITE_AVATAR_FRAME = Path(r"C:\Users\hjvan\OneDrive\_Codex\Avatar\public_html\avatar\rl-frame.png")
RUN_HISTORY = RUN_ROOT / "rl-history.json"
SITE_AVATAR_HISTORY = Path(r"C:\Users\hjvan\OneDrive\_Codex\Avatar\public_html\avatar\rl-history.json")

LMSTUDIO_BASE = "http://100.106.75.76:1234/v1"
MODEL = "nvidia/nemotron-3-nano-omni"

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
APPROVAL_MIN_SCORE = 90
APPROVAL_MIN_FRAMES = 240
APPROVAL_KEYS = ("score", "face", "body", "hair", "motion", "lighting", "artifacts")
SCORE_KEYS = ("score", "face", "body", "hair", "motion", "lighting", "artifacts")
CAMERA_SCORE_KEYS = ("full_body_front", "full_body_side", "full_face_front", "full_face_side")


EDITOR_CANDIDATES = [
    Path(r"D:\Program Files\Epic Games\UE_5.7\Engine\Binaries\Win64\UnrealEditor.exe"),
    Path(r"D:\Avatar\Epic\UE_5.7\Engine\Binaries\Win64\UnrealEditor.exe"),
    Path(r"D:\Avatar\Epic Games\UE_5.7\Engine\Binaries\Win64\UnrealEditor.exe"),
    Path(r"D:\Epic Games\UE_5.7\Engine\Binaries\Win64\UnrealEditor.exe"),
    Path(r"D:\UE_5.7\Engine\Binaries\Win64\UnrealEditor.exe"),
    Path(r"C:\Program Files\Epic Games\UE_5.7\Engine\Binaries\Win64\UnrealEditor.exe"),
    Path(r"C:\Program Files\Epic Games\UE_5.6\Engine\Binaries\Win64\UnrealEditor.exe"),
]


def cmd_editor_for(editor: Path) -> Path:
    if editor.name.lower() == "unrealeditor-cmd.exe":
        return editor
    cmd = editor.with_name("UnrealEditor-Cmd.exe")
    return cmd if cmd.exists() else editor


def now_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def first_existing(paths: list[Path]) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


def default_project() -> Path:
    if NIA_PROJECT.exists():
        return NIA_PROJECT
    return TALKING_HEAD_PROJECT


def default_level(project: Path) -> str:
    if project == NIA_PROJECT:
        return "/Game/CC_ControlRig_Nia/Maps/Nia"
    return "/Game/TalkingHead/Maps/LVL_TalkingHead"


def default_sequence(project: Path) -> str:
    if project == NIA_PROJECT:
        return "/Game/CC_ControlRig_Nia/Cinematic/Nia"
    return "/Game/TalkingHead/Sequences/LS_TalkingHead"


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def copy_preview_frame(summary: dict[str, object]) -> None:
    frame = Path(str(summary.get("worst_frame") or summary.get("best_frame") or ""))
    if not frame.exists() or frame.suffix.lower() not in IMAGE_EXTS:
        return
    SITE_AVATAR_FRAME.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(frame, SITE_AVATAR_FRAME)


def read_history(path: Path) -> list[dict[str, object]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict) and isinstance(data.get("entries"), list):
        return [item for item in data["entries"] if isinstance(item, dict)]
    return []


def history_entry(summary: dict[str, object], index: int, previous: dict[str, object] | None) -> dict[str, object]:
    metrics = {key: normalize_score(summary.get(key)) for key in SCORE_KEYS}
    raw_camera_scores = summary.get("camera_scores") if isinstance(summary.get("camera_scores"), dict) else {}
    camera_scores = {
        key: normalize_score(raw_camera_scores.get(key))
        for key in CAMERA_SCORE_KEYS
        if raw_camera_scores.get(key) is not None
    } if isinstance(raw_camera_scores, dict) else {}
    metric_values = list(metrics.values())
    intelligence = int(round((normalize_score(summary.get("score")) + (sum(metric_values) / max(1, len(metric_values)))) / 2))
    previous_score = normalize_score(previous.get("score")) if previous else normalize_score(summary.get("score"))
    previous_best = normalize_score(previous.get("best_so_far")) if previous else 0
    score = normalize_score(summary.get("score"))
    return {
        "index": index,
        "ts": summary.get("ts") or utc_now(),
        "gen": summary.get("gen") or int(time.time()),
        "score": score,
        "best_so_far": max(previous_best, score),
        "delta": score - previous_score,
        "intelligence": intelligence,
        "motion_approved": bool(summary.get("motion_approved")),
        "evaluator_failures": int(summary.get("evaluator_failures") or 0),
        "frames_verified": int(summary.get("frames_verified") or summary.get("iter") or 0),
        "face": metrics["face"],
        "body": metrics["body"],
        "hair": metrics["hair"],
        "motion": metrics["motion"],
        "lighting": metrics["lighting"],
        "artifacts": metrics["artifacts"],
        "camera_scores": camera_scores,
        "comment": str(summary.get("comment") or "")[:420],
    }


def append_history(summary: dict[str, object], copy_site_file: bool) -> None:
    history = read_history(RUN_HISTORY)
    previous = history[-1] if history else None
    history.append(history_entry(summary, len(history) + 1, previous))
    history = history[-720:]
    payload = {
        "generated_at": utc_now(),
        "mode": "unreal-nemotron-frame-rl",
        "sample_count": len(history),
        "entries": history,
    }
    write_json(RUN_HISTORY, payload)
    if copy_site_file:
        SITE_AVATAR_HISTORY.parent.mkdir(parents=True, exist_ok=True)
        write_json(SITE_AVATAR_HISTORY, payload)


def image_files(path: Path) -> list[Path]:
    if path.is_file() and path.suffix.lower() in IMAGE_EXTS:
        return [path]
    if not path.is_dir():
        return []
    return sorted(
        (p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_EXTS),
        key=lambda p: p.name.lower(),
    )


def latest_frame_dir() -> Path | None:
    if not RUN_ROOT.exists():
        return None
    candidates = [p for p in RUN_ROOT.iterdir() if p.is_dir() and image_files(p)]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def call_json(url: str, payload: dict[str, object], timeout: int = 180) -> dict[str, object]:
    timeout_cap = os.environ.get("NEMOTRON_JUDGE_TIMEOUT_SECONDS")
    if timeout_cap:
        try:
            cap = int(float(timeout_cap))
            if cap > 0:
                timeout = min(timeout, cap)
        except (TypeError, ValueError):
            pass
    body = json.dumps(payload).encode("utf-8")
    req = urlrequest.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urlrequest.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def extract_json(text: str) -> dict[str, object]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            raise
        return json.loads(match.group(0))


def fallback_judgment_from_text(text: str, exc: Exception) -> dict[str, object]:
    data: dict[str, object] = {}
    for key in SCORE_KEYS:
        match = re.search(rf'"?{re.escape(key)}"?\s*[:=]\s*(\d+(?:\.\d+)?)', text, flags=re.I)
        data[key] = normalize_score(match.group(1) if match else 0)
    comment_match = re.search(r'"comment"\s*:\s*"([^"]{1,220})"', text, flags=re.I | re.S)
    fix_match = re.search(r'"fix"\s*:\s*"([^"]{1,220})"', text, flags=re.I | re.S)
    data["comment"] = (
        comment_match.group(1).strip()
        if comment_match
        else f"Nemotron returned malformed JSON: {exc.__class__.__name__}: {exc}"
    )
    data["fix"] = fix_match.group(1).strip() if fix_match else "Keep evaluating; repair JSON output or rerun the frame."
    data["json_repaired"] = False
    data["raw_content"] = text[:2000]
    return data


def normalize_score(value: object) -> int:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, int(round(score))))


def judge_frame(frame: Path, endpoint: str, model: str) -> dict[str, object]:
    mime = "image/png"
    if frame.suffix.lower() in {".jpg", ".jpeg"}:
        mime = "image/jpeg"
    b64 = base64.b64encode(frame.read_bytes()).decode("ascii")
    prompt = (
        "Rate this Unreal avatar render frame for TRUE 3D photorealism and RL training feedback. "
        "Do not reward 2D photo realism, a pretty source photo, or general image quality unless the avatar is a coherent 3D model render. "
        "If the image contains a 3D studio multi-camera layout, evaluate it as one linked model source seen through all four named cameras: "
        "Full Body Front View, Full Body Side View, Full Face Front View, and Full Face Side View. "
        "If one pane is explicitly labeled REFERENCE VIDEO or Sheena Parveen Reference Video, treat that pane as the source presenter target, not as an avatar render. "
        "Use that source to judge whether the Cam 01 target avatar matches the presenter: rust/orange sleeveless dress, warm brown long hair, presenter framing, body proportions, pose, and face/hair silhouette. "
        "If Cam 01 visibly has the wrong outfit color, black hair when the reference is brown, shorts instead of a dress, barefoot/game-preview styling, or a different presenter silhouette, the source-likeness gate fails and the overall score must stay below 20 out of 100. "
        "Do not award score for the reference video's realism; it is only the target to match. "
        "Penalize inconsistent geometry, lighting, scale, head/body alignment, or realism across views. "
        "If the avatar is a CSS-transformed 2D photo/billboard, flat cutout, warped plane, fake side view, "
        "or off-center non-3D proxy, the overall 3D photorealism score must be 0-5 out of 100 even if the underlying 2D photo looks realistic. "
        "Only a real 3D model render with coherent depth, side geometry, and matching face/body views may score above 5. "
        "Do not treat 'real 3D mesh' as photorealism. A real mesh with uncanny eyes, doll-like face, visible hair cards, jagged hairline, "
        "flat skin shader, plastic materials, broken mouth shape, or game-preview lighting must score below 30 out of 100. "
        "Scores above 70 require cinema/VFX-quality skin, eyes, groom hair, eyelids, mouth, anatomical proportions, and lighting that could pass as a real person. "
        "Return only final JSON, no markdown, no explanation outside JSON. "
        "Use integer 0-100 scores for: score, face, body, hair, motion, lighting, artifacts. "
        "Also score each camera individually in camera_scores using keys "
        "full_body_front, full_body_side, full_face_front, full_face_side, each integer 0-100. "
        "Punish web overlays, cutout edges, bad hands, bad mouth/lip sync, frozen expression, "
        "flat lighting, wrong scale, and anything that looks fake. "
        "Include comment and fix strings under 180 characters each."
        "Strict schema: {\"score\":0,\"face\":0,\"body\":0,\"hair\":0,"
        "\"motion\":0,\"lighting\":0,\"artifacts\":0,"
        "\"camera_scores\":{\"full_body_front\":0,\"full_body_side\":0,\"full_face_front\":0,\"full_face_side\":0},"
        "\"comment\":\"...\",\"fix\":\"...\"}."
    )
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "You are a strict true-3D photorealism critic for RL reward modeling. Ignore 2D photo realism. Return only compact JSON in final content.",
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                ],
            },
        ],
        "temperature": 0,
        "max_tokens": 2048,
        "stream": False,
        "response_format": {"type": "json_object"},
    }
    try:
        response = call_json(f"{endpoint.rstrip('/')}/chat/completions", payload)
    except urlerror.HTTPError as exc:
        if exc.code not in (400, 422):
            raise
        payload.pop("response_format", None)
        response = call_json(f"{endpoint.rstrip('/')}/chat/completions", payload)
    message = response.get("choices", [{}])[0].get("message", {})
    content = str(message.get("content") or "").strip()
    if not content:
        # Nemotron can spend tokens in reasoning_content before final content.
        payload["max_tokens"] = 4096
        response = call_json(f"{endpoint.rstrip('/')}/chat/completions", payload, timeout=240)
        message = response.get("choices", [{}])[0].get("message", {})
        content = str(message.get("content") or "").strip()
    if not content:
        raise RuntimeError("Nemotron returned no final content for image judgment")
    try:
        data = extract_json(content)
    except Exception as exc:  # noqa: BLE001
        data = fallback_judgment_from_text(content, exc)
    for key in SCORE_KEYS:
        data[key] = normalize_score(data.get(key))
    raw_camera_scores = data.get("camera_scores")
    camera_scores: dict[str, int] = {}
    if isinstance(raw_camera_scores, dict):
        for key in CAMERA_SCORE_KEYS:
            camera_scores[key] = normalize_score(raw_camera_scores.get(key))
    data["camera_scores"] = camera_scores
    data["raw_content"] = content
    data["reasoning_tokens"] = (
        response.get("usage", {})
        .get("completion_tokens_details", {})
        .get("reasoning_tokens", 0)
    )
    return data


def summarize(
    scores: list[dict[str, object]],
    run_id: str,
    frame_dir: Path,
    approval_min_frames: int = APPROVAL_MIN_FRAMES,
    approval_min_score: int = APPROVAL_MIN_SCORE,
) -> dict[str, object]:
    if not scores:
        return {
            "score": 0,
            "iter": 0,
            "frames_verified": 0,
            "best_score": 0,
            "worst_score": 0,
            "face": 0,
            "body": 0,
            "hair": 0,
            "motion": 0,
            "lighting": 0,
            "artifacts": 0,
            "audio": 0,
            "ts": utc_now(),
            "comment": "No frames were evaluated.",
            "mode": "unreal-nemotron-frame-rl",
            "motion_approved": False,
            "approval_min_frames": approval_min_frames,
            "approval_min_score": approval_min_score,
            "approval_failures": ["no_frames"],
        }

    def avg(key: str) -> int:
        vals = [normalize_score(s.get(key)) for s in scores]
        return int(round(sum(vals) / max(1, len(vals))))

    worst = min(scores, key=lambda item: normalize_score(item.get("score")))
    best = max(scores, key=lambda item: normalize_score(item.get("score")))
    overall = avg("score")
    frames_verified = len(scores)
    worst_score = normalize_score(worst.get("score"))
    failure_count = sum(1 for item in scores if not item.get("ok"))
    low_frame_count = sum(
        1
        for item in scores
        if any(normalize_score(item.get(key)) < approval_min_score for key in APPROVAL_KEYS)
    )
    approval_failures: list[str] = []
    if frames_verified < approval_min_frames:
        approval_failures.append(f"need_{approval_min_frames}_frames")
    if failure_count:
        approval_failures.append(f"{failure_count}_evaluator_failures")
    if worst_score < approval_min_score:
        approval_failures.append(f"worst_score_{worst_score}_below_{approval_min_score}")
    if low_frame_count:
        approval_failures.append(f"{low_frame_count}_frames_below_{approval_min_score}")
    motion_approved = not approval_failures
    comment = str(worst.get("comment") or "Worst frame needs realism work.")
    fix = str(worst.get("fix") or "Improve the lowest-scoring frame.")
    return {
        "score": overall,
        "iter": frames_verified,
        "frames_verified": frames_verified,
        "gen": int(time.time()),
        "best_score": normalize_score(best.get("score")),
        "worst_score": worst_score,
        "face": avg("face"),
        "body": avg("body"),
        "hair": avg("hair"),
        "motion": avg("motion"),
        "lighting": avg("lighting"),
        "artifacts": avg("artifacts"),
        "audio": 86,
        "ts": utc_now(),
        "comment": f"run {run_id} | frames {frames_verified} | approved {motion_approved} | worst {Path(str(worst.get('frame', ''))).name}: {comment} | fix: {fix}",
        "mode": "unreal-nemotron-frame-rl",
        "motion_approved": motion_approved,
        "approval_min_frames": approval_min_frames,
        "approval_min_score": approval_min_score,
        "approval_metric_keys": list(APPROVAL_KEYS),
        "approval_failures": approval_failures,
        "evaluator_failures": failure_count,
        "frames_below_approval": low_frame_count,
        "frame_dir": str(frame_dir),
        "worst_frame": str(worst.get("frame", "")),
        "best_frame": str(best.get("frame", "")),
    }


def render_with_unreal_legacy_capture(args: argparse.Namespace, run_dir: Path, editor: Path, project: Path, level: str, sequence: str) -> Path:
    cmd_editor = cmd_editor_for(editor)
    start = max(0, int(args.start_frame))
    end = int(args.end_frame) if int(args.end_frame) > start else 240
    movie_name = "frame_{frame}"
    command = [
        str(cmd_editor),
        str(project),
        level,
        "-game",
        "-NoLoadingScreen",
        "-NoSplash",
        "-Unattended",
        f"-ResX={args.width}",
        f"-ResY={args.height}",
        "-ForceRes",
        "-MovieSceneCaptureType=/Script/MovieSceneCapture.AutomatedLevelSequenceCapture",
        f"-LevelSequence={sequence}",
        f"-MovieFolder={run_dir}",
        f"-MovieName={movie_name}",
        "-MovieFormat=PNG",
        "-MovieFrameRate=30",
        f"-MovieStartFrame={start}",
        f"-MovieEndFrame={end}",
        "-NoTextureStreaming",
        "-MovieWarmUpFrames=0",
        "-MovieDelayBeforeWarmUp=0",
        "-MovieDelayBeforeShotWarmUp=0",
    ]
    print("Launching Unreal legacy LevelSequence capture:")
    print(" ".join(f'"{part}"' if " " in part else part for part in command))
    completed = subprocess.run(command, cwd=str(project.parent), timeout=args.unreal_timeout)
    if completed.returncode != 0:
        raise RuntimeError(f"Unreal capture failed with exit code {completed.returncode}")
    frames = image_files(run_dir)
    if not frames:
        raise RuntimeError(f"Unreal capture produced no frames in {run_dir}")
    return run_dir


def render_with_unreal_python(args: argparse.Namespace, run_dir: Path, editor: Path, project: Path, level: str, sequence: str) -> Path:
    editor = Path(args.editor) if args.editor else first_existing(EDITOR_CANDIDATES)
    if not editor or not editor.exists():
        raise FileNotFoundError("UnrealEditor.exe was not found")
    request = {
        "run_id": run_dir.name,
        "output_dir": str(run_dir),
        "level": level,
        "sequence": sequence,
        "width": args.width,
        "height": args.height,
        "fov": args.fov,
        "start_frame": args.start_frame,
        "end_frame": args.end_frame,
        "step": args.step,
        "max_frames": args.max_frames,
        "warmup_seconds": args.warmup_seconds,
    }
    write_json(REQUEST_PATH, request)
    command = [
        str(editor),
        str(project),
        f"-ExecutePythonScript={UNREAL_SCRIPT}",
        "-NoSplash",
        "-Unattended",
    ]
    print("Launching Unreal frame render:")
    print(" ".join(f'"{part}"' if " " in part else part for part in command))
    completed = subprocess.run(command, cwd=str(project.parent), timeout=args.unreal_timeout)
    if completed.returncode != 0:
        raise RuntimeError(f"Unreal render failed with exit code {completed.returncode}")
    frames = image_files(run_dir)
    if not frames:
        raise RuntimeError(f"Unreal produced no frames in {run_dir}")
    return run_dir


def render_with_unreal(args: argparse.Namespace, run_dir: Path) -> Path:
    editor = Path(args.editor) if args.editor else first_existing(EDITOR_CANDIDATES)
    if not editor or not editor.exists():
        raise FileNotFoundError("UnrealEditor.exe was not found")
    project = Path(args.project) if args.project else default_project()
    if not project.exists():
        raise FileNotFoundError(f"Unreal project not found: {project}")
    level = args.level or default_level(project)
    sequence = args.sequence or default_sequence(project)

    if args.render_mode == "python":
        return render_with_unreal_python(args, run_dir, editor, project, level, sequence)
    return render_with_unreal_legacy_capture(args, run_dir, editor, project, level, sequence)


def evaluate_frames(args: argparse.Namespace, frame_dir: Path, run_id: str) -> dict[str, object]:
    frames = image_files(frame_dir)
    if args.limit:
        frames = frames[: args.limit]
    if not frames:
        raise RuntimeError(f"No image frames found in {frame_dir}")

    run_dir = RUN_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    jsonl = run_dir / "frame_scores.jsonl"
    scores: list[dict[str, object]] = []
    with jsonl.open("a", encoding="utf-8") as out:
        for index, frame in enumerate(frames, start=1):
            print(f"[{index}/{len(frames)}] judging {frame.name}")
            try:
                score = judge_frame(frame, args.endpoint, args.model)
                score.update(
                    {
                        "ok": True,
                        "frame_index": index - 1,
                        "frame": str(frame),
                        "ts": utc_now(),
                        "model": args.model,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                score = {
                    "ok": False,
                    "frame_index": index - 1,
                    "frame": str(frame),
                    "ts": utc_now(),
                    "model": args.model,
                    "score": 0,
                    "face": 0,
                    "body": 0,
                    "hair": 0,
                    "motion": 0,
                    "lighting": 0,
                    "artifacts": 0,
                    "comment": f"{exc.__class__.__name__}: {exc}",
                    "fix": "Fix the evaluator or frame source, then rerun.",
                }
            scores.append(score)
            out.write(json.dumps(score, ensure_ascii=False) + "\n")
            out.flush()

    summary = summarize(scores, run_id, frame_dir, args.approval_min_frames, args.approval_min_score)
    write_json(run_dir / "summary.json", summary)
    write_json(RUN_ROOT / "latest_summary.json", summary)
    write_json(SITE_REALISM, summary)
    if args.copy_site_file:
        SITE_AVATAR_REALISM.parent.mkdir(parents=True, exist_ok=True)
        write_json(SITE_AVATAR_REALISM, summary)
        copy_preview_frame(summary)
    append_history(summary, args.copy_site_file)
    print(json.dumps(summary, indent=2, ensure_ascii=True))
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render Unreal frames and let Nemotron score every frame for realism RL.")
    parser.add_argument("--render", action="store_true", help="Launch Unreal and render frames before evaluation.")
    parser.add_argument("--render-mode", choices=("legacy", "python"), default="legacy", help="Unreal capture path. Legacy writes PNG frames reliably in unattended mode.")
    parser.add_argument("--frames-dir", type=Path, default=None, help="Directory or image file to evaluate. Defaults to latest Unreal RL render.")
    parser.add_argument("--editor", default="", help="UnrealEditor.exe path override.")
    parser.add_argument("--project", default="", help="Unreal .uproject path override.")
    parser.add_argument("--level", default="", help="Unreal level asset path override.")
    parser.add_argument("--sequence", default="", help="Unreal level sequence asset path override.")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--fov", type=float, default=42.0, help="SceneCapture field of view in degrees.")
    parser.add_argument("--start-frame", type=int, default=0)
    parser.add_argument("--end-frame", type=int, default=0, help="0 lets the Unreal script infer sequence end frame.")
    parser.add_argument("--step", type=int, default=1, help="Frame interval. 1 means every frame.")
    parser.add_argument("--max-frames", type=int, default=0, help="0 means no cap.")
    parser.add_argument("--limit", type=int, default=0, help="Evaluation cap after rendering/finding frames.")
    parser.add_argument("--approval-min-frames", type=int, default=APPROVAL_MIN_FRAMES, help="Minimum evaluated frames before website motion can unlock.")
    parser.add_argument("--approval-min-score", type=int, default=APPROVAL_MIN_SCORE, help="Minimum per-frame realism metric score before website motion can unlock.")
    parser.add_argument("--warmup-seconds", type=float, default=1.0)
    parser.add_argument("--unreal-timeout", type=int, default=1800)
    parser.add_argument("--endpoint", default=LMSTUDIO_BASE)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--copy-site-file", action="store_true", help="Also write local public_html/avatar/realism.json if present.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_id = f"run-{now_id()}"
    run_dir = RUN_ROOT / run_id

    if args.render:
        frame_dir = render_with_unreal(args, run_dir)
    elif args.frames_dir:
        frame_dir = args.frames_dir
    else:
        frame_dir = latest_frame_dir() or (ROOT / "CharacterCreatorNia" / "CharacterCreatorNia.png")

    evaluate_frames(args, frame_dir, run_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
