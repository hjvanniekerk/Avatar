#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(r"C:\Users\hjvan\OneDrive\_Codex\Avatar")
UNREAL_RL = ROOT / "unreal_realism_rl.py"
LOCAL_GPU_REWARD = ROOT / "local_gpu_reward_model.py"
RUN_ROOT = Path(r"D:\Avatar\UnrealTalkingHead\Saved\RealismRL")
STOP_FILE = RUN_ROOT / "continuous_unreal_rl.stop"
PID_FILE = RUN_ROOT / "continuous_unreal_rl.pid"
STATE_FILE = RUN_ROOT / "continuous_unreal_rl_state.json"
LOG_FILE = RUN_ROOT / "continuous_unreal_rl.log"

SITE_ROOT = Path(r"C:\Users\hjvan\OneDrive\_Codex\Avatar\public_html")
HM_FILES = ROOT / "deploy" / "hm_files.py"
SITE_REALISM = SITE_ROOT / "avatar" / "realism.json"
SITE_RL_FRAME = SITE_ROOT / "avatar" / "rl-frame.png"
SITE_RL_HISTORY = SITE_ROOT / "avatar" / "rl-history.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def append_log(message: str) -> None:
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    line = f"{utc_now()} {message}"
    print(line, flush=True)
    with LOG_FILE.open("a", encoding="utf-8") as out:
        out.write(line + "\n")


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def run_command(command: list[str], cwd: Path, timeout: int, env: dict[str, str] | None = None) -> int:
    append_log("run " + " ".join(f'"{part}"' if " " in part else part for part in command))
    proc = subprocess.Popen(
        command,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            append_log(line.rstrip())
        return proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        append_log(f"timeout after {timeout}s; terminating")
        proc.terminate()
        try:
            return proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            return proc.wait(timeout=30)


def deploy_file(local: Path, remote: str) -> int:
    if not local.exists():
        append_log(f"skip deploy; missing {local}")
        return 1
    if not HM_FILES.exists():
        append_log(f"skip deploy; missing {HM_FILES}")
        return 1
    env = os.environ.copy()
    if not env.get("HM_ADMIN_OP_PASSWORD"):
        append_log("skip deploy; HM_ADMIN_OP_PASSWORD is not set")
        return 1
    return run_command(
        [sys.executable, str(HM_FILES), "put", str(local), remote],
        SITE_ROOT,
        timeout=240,
        env=env,
    )


def deploy_realism() -> int:
    rc = deploy_file(SITE_REALISM, "public_html/avatar/realism.json")
    frame_rc = deploy_file(SITE_RL_FRAME, "public_html/avatar/rl-frame.png")
    history_rc = deploy_file(SITE_RL_HISTORY, "public_html/avatar/rl-history.json")
    return rc or frame_rc or history_rc


def train_local_gpu_reward(args: argparse.Namespace) -> int:
    if args.skip_gpu_train or not args.gpu_train:
        append_log("skip local GPU reward training; enable with --gpu-train after the render pipeline is validated")
        return 0
    if not LOCAL_GPU_REWARD.exists():
        append_log(f"skip local GPU reward training; missing {LOCAL_GPU_REWARD}")
        return 1
    command = [
        sys.executable,
        str(LOCAL_GPU_REWARD),
        "--epochs",
        str(args.gpu_train_epochs),
        "--batches-per-epoch",
        str(args.gpu_train_batches),
        "--batch-size",
        str(args.gpu_train_batch_size),
        "--device",
        "cuda",
    ]
    return run_command(command, ROOT, timeout=args.gpu_train_timeout)


def run_cycle(args: argparse.Namespace, cycle: int) -> dict[str, object]:
    command = [
        sys.executable,
        str(UNREAL_RL),
        "--render",
        "--render-mode",
        "python",
        "--start-frame",
        str(args.start_frame),
        "--end-frame",
        str(args.end_frame),
        "--step",
        str(args.step),
        "--max-frames",
        str(args.max_frames),
        "--limit",
        str(args.limit),
        "--fov",
        str(args.fov),
        "--copy-site-file",
        "--unreal-timeout",
        str(args.unreal_timeout),
        "--endpoint",
        args.endpoint,
        "--model",
        args.model,
    ]
    started = utc_now()
    rc = run_command(command, ROOT, timeout=args.cycle_timeout)
    gpu_train_rc = train_local_gpu_reward(args)
    deploy_rc = deploy_realism() if args.deploy else 0
    state = {
        "pid": os.getpid(),
        "cycle": cycle,
        "started": started,
        "finished": utc_now(),
        "render_eval_rc": rc,
        "gpu_train_rc": gpu_train_rc,
        "deploy_rc": deploy_rc,
        "stop_file": str(STOP_FILE),
    }
    write_json(STATE_FILE, state)
    return state


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Continuously render Unreal avatar frames and score them with Nemotron.")
    parser.add_argument("--start-frame", type=int, default=0)
    parser.add_argument("--end-frame", type=int, default=1)
    parser.add_argument("--step", type=int, default=1)
    parser.add_argument("--max-frames", type=int, default=1)
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--fov", type=float, default=42.0)
    parser.add_argument("--sleep", type=int, default=30)
    parser.add_argument("--unreal-timeout", type=int, default=900)
    parser.add_argument("--cycle-timeout", type=int, default=1200)
    parser.add_argument("--endpoint", default="http://100.106.75.76:1234/v1")
    parser.add_argument("--model", default="nvidia/nemotron-3-nano-omni")
    parser.add_argument("--deploy", action="store_true")
    parser.add_argument("--gpu-train", action="store_true")
    parser.add_argument("--skip-gpu-train", action="store_true")
    parser.add_argument("--gpu-train-epochs", type=int, default=2)
    parser.add_argument("--gpu-train-batches", type=int, default=64)
    parser.add_argument("--gpu-train-batch-size", type=int, default=32)
    parser.add_argument("--gpu-train-timeout", type=int, default=900)
    parser.add_argument("--once", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()) + "\n", encoding="utf-8")
    append_log(f"continuous Unreal/Nemotron RL started pid={os.getpid()}")
    cycle = 0
    try:
        while not STOP_FILE.exists():
            cycle += 1
            try:
                state = run_cycle(args, cycle)
                append_log(
                    "cycle "
                    f"{cycle} complete render_eval_rc={state['render_eval_rc']} "
                    f"gpu_train_rc={state['gpu_train_rc']} deploy_rc={state['deploy_rc']}"
                )
            except Exception as exc:  # noqa: BLE001
                append_log(f"cycle {cycle} failed: {exc.__class__.__name__}: {exc}")
                write_json(
                    STATE_FILE,
                    {
                        "pid": os.getpid(),
                        "cycle": cycle,
                        "failed": utc_now(),
                        "error": f"{exc.__class__.__name__}: {exc}",
                    },
                )
            if args.once:
                break
            for _ in range(max(1, args.sleep)):
                if STOP_FILE.exists():
                    break
                time.sleep(1)
    finally:
        append_log("continuous Unreal/Nemotron RL stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
