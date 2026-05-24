#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import torch
from PIL import Image
from torch import nn
from torch.utils.data import DataLoader, Dataset
from torchvision import models, transforms


RUN_ROOT = Path(r"D:\Avatar\UnrealTalkingHead\Saved\RealismRL")
CHECKPOINT = RUN_ROOT / "local_gpu_reward_model.pt"
STATUS = RUN_ROOT / "local_gpu_reward_model_status.json"
STOP_FILE = RUN_ROOT / "local_gpu_reward_model.stop"
SCORE_KEYS = ("score", "face", "body", "hair", "motion", "lighting", "artifacts")
NEMOTRON_MODEL_FRAGMENT = "nemotron"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def normalize_score(value: object) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        score = 0.0
    return max(0.0, min(1.0, score / 100.0))


@dataclass(frozen=True)
class Sample:
    frame: Path
    target: tuple[float, ...]
    source: Path
    label_time: str | None
    model: str


@dataclass(frozen=True)
class LabelLoadResult:
    samples: list[Sample]
    label_rows_seen: int
    skipped_rows: int
    last_nemotron_label_time: str | None


def parse_label_datetime(row: dict[str, object], source: Path) -> datetime | None:
    for key in ("ts", "timestamp", "created_at", "updated_at"):
        value = row.get(key)
        if not value:
            continue
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    try:
        return datetime.fromtimestamp(source.stat().st_mtime, timezone.utc)
    except OSError:
        return None


def score_tuple(row: dict[str, object]) -> tuple[float, ...] | None:
    scores: list[float] = []
    for key in SCORE_KEYS:
        if key not in row:
            return None
        try:
            raw = float(row.get(key))
        except (TypeError, ValueError):
            return None
        if not math.isfinite(raw):
            return None
        scores.append(max(0.0, min(1.0, raw / 100.0)))
    return tuple(scores)


def is_nemotron_label(row: dict[str, object], allow_non_nemotron_labels: bool) -> bool:
    if row.get("ok") is not True:
        return False
    if allow_non_nemotron_labels:
        return True
    model = str(row.get("model") or "").lower()
    return NEMOTRON_MODEL_FRAGMENT in model


def load_samples(root: Path, allow_non_nemotron_labels: bool = False) -> LabelLoadResult:
    samples: list[Sample] = []
    label_rows_seen = 0
    skipped_rows = 0
    latest_label_time: datetime | None = None
    for jsonl in sorted(root.rglob("*scores.jsonl")):
        try:
            lines = jsonl.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                skipped_rows += 1
                continue
            if not isinstance(row, dict):
                skipped_rows += 1
                continue
            if not is_nemotron_label(row, allow_non_nemotron_labels):
                skipped_rows += 1
                continue
            frame = Path(str(row.get("frame") or ""))
            if not frame.is_file():
                skipped_rows += 1
                continue
            target = score_tuple(row)
            if target is None:
                skipped_rows += 1
                continue
            label_time = parse_label_datetime(row, jsonl)
            if label_time is not None and (latest_label_time is None or label_time > latest_label_time):
                latest_label_time = label_time
            label_rows_seen += 1
            samples.append(
                Sample(
                    frame=frame,
                    target=target,
                    source=jsonl,
                    label_time=label_time.isoformat(timespec="seconds") if label_time else None,
                    model=str(row.get("model") or ""),
                )
            )
    deduped: dict[Path, Sample] = {}
    for sample in samples:
        deduped[sample.frame] = sample
    last_label_time = latest_label_time.isoformat(timespec="seconds") if latest_label_time else None
    return LabelLoadResult(
        samples=list(deduped.values()),
        label_rows_seen=label_rows_seen,
        skipped_rows=skipped_rows,
        last_nemotron_label_time=last_label_time,
    )


class RewardFrameDataset(Dataset):
    def __init__(self, samples: list[Sample], image_size: int, preload: bool = True) -> None:
        self.samples = samples
        self.preload = preload
        self.base_tf = transforms.Compose(
            [
                transforms.Resize((image_size, image_size)),
                transforms.ToTensor(),
                transforms.Normalize(mean=(0.485, 0.456, 0.406), std=(0.229, 0.224, 0.225)),
            ]
        )
        self.cached: list[tuple[torch.Tensor, torch.Tensor]] | None = None
        if preload:
            cached = []
            for sample in self.samples:
                cached.append((self.load_image(sample), torch.tensor(sample.target, dtype=torch.float32)))
            self.cached = cached

    def __len__(self) -> int:
        return len(self.samples)

    def load_image(self, sample: Sample) -> torch.Tensor:
        with Image.open(sample.frame) as img:
            image = img.convert("RGB")
        return self.base_tf(image)

    def __getitem__(self, index: int) -> tuple[torch.Tensor, torch.Tensor]:
        if self.cached is not None:
            return self.cached[index]
        sample = self.samples[index]
        return self.load_image(sample), torch.tensor(sample.target, dtype=torch.float32)


class RepeatingLoader:
    def __init__(self, loader: DataLoader) -> None:
        self.loader = loader
        self.iterator = iter(loader)

    def next(self) -> tuple[torch.Tensor, torch.Tensor]:
        try:
            return next(self.iterator)
        except StopIteration:
            self.iterator = iter(self.loader)
            return next(self.iterator)


def augment_on_device(images: torch.Tensor) -> torch.Tensor:
    if images.shape[0] <= 0:
        return images
    result = images
    flip_mask = torch.rand(result.shape[0], device=result.device) < 0.35
    if bool(flip_mask.any()):
        result = result.clone()
        result[flip_mask] = torch.flip(result[flip_mask], dims=(-1,))
    brightness = torch.empty((result.shape[0], 1, 1, 1), device=result.device).uniform_(0.92, 1.08)
    contrast = torch.empty((result.shape[0], 1, 1, 1), device=result.device).uniform_(0.90, 1.10)
    mean = result.mean(dim=(2, 3), keepdim=True)
    result = (result - mean) * contrast + mean
    result = result * brightness
    noise = torch.randn_like(result) * 0.015
    return result + noise


def build_model(device: torch.device) -> nn.Module:
    weights = None
    try:
        weights = models.MobileNet_V3_Small_Weights.DEFAULT
    except Exception:
        weights = None
    try:
        model = models.mobilenet_v3_small(weights=weights)
    except Exception:
        model = models.mobilenet_v3_small(weights=None)
    in_features = model.classifier[-1].in_features
    model.classifier[-1] = nn.Linear(in_features, len(SCORE_KEYS))
    return model.to(device)


def load_checkpoint(model: nn.Module, optimizer: torch.optim.Optimizer, path: Path, device: torch.device) -> int:
    if not path.is_file():
        return 0
    try:
        payload = torch.load(path, map_location=device, weights_only=False)
        model.load_state_dict(payload["model"])
        optimizer.load_state_dict(payload["optimizer"])
        return int(payload.get("steps", 0))
    except Exception:
        return 0


def save_checkpoint(model: nn.Module, optimizer: torch.optim.Optimizer, steps: int, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "model": model.state_dict(),
            "optimizer": optimizer.state_dict(),
            "score_keys": SCORE_KEYS,
            "steps": steps,
            "saved_at": utc_now(),
        },
        path,
    )


def gpu_snapshot(device: torch.device) -> dict[str, object]:
    if device.type != "cuda":
        return {"device": str(device), "cuda": False}
    index = device.index or 0
    return {
        "device": torch.cuda.get_device_name(index),
        "cuda": True,
        "memory_allocated_mb": round(torch.cuda.memory_allocated(index) / 1024 / 1024, 1),
        "memory_reserved_mb": round(torch.cuda.memory_reserved(index) / 1024 / 1024, 1),
        "max_memory_reserved_mb": round(torch.cuda.max_memory_reserved(index) / 1024 / 1024, 1),
    }


def cuda_available() -> bool:
    return bool(torch.cuda.is_available())


def cuda_gpu_name() -> str | None:
    if not cuda_available():
        return None
    try:
        return torch.cuda.get_device_name(0)
    except Exception:
        return None


def read_motion_gate(root: Path) -> dict[str, object]:
    summary_path = root / "latest_summary.json"
    gate: dict[str, object] = {
        "summary": str(summary_path),
        "latest_motion_approved": False,
        "motion_resume_allowed": False,
        "reason": "latest_summary_missing",
    }
    if not summary_path.is_file():
        return gate
    try:
        data = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        gate["reason"] = f"latest_summary_unreadable:{exc.__class__.__name__}"
        return gate
    if not isinstance(data, dict):
        gate["reason"] = "latest_summary_not_object"
        return gate

    def int_value(key: str, default: int = 0) -> int:
        try:
            return int(data.get(key) or default)
        except (TypeError, ValueError):
            return default

    failures = data.get("approval_failures")
    has_failures = isinstance(failures, list) and len(failures) > 0
    frames_verified = int_value("frames_verified", int_value("iter"))
    approval_min_frames = int_value("approval_min_frames", 240)
    approval_min_score = int_value("approval_min_score", 90)
    score = int_value("score")
    latest_motion_approved = data.get("motion_approved") is True
    resume_allowed = (
        data.get("mode") == "unreal-nemotron-frame-rl"
        and latest_motion_approved
        and score >= approval_min_score
        and frames_verified >= approval_min_frames
        and not has_failures
    )
    gate.update(
        {
            "latest_motion_approved": latest_motion_approved,
            "motion_resume_allowed": resume_allowed,
            "frames_verified": frames_verified,
            "approval_min_frames": approval_min_frames,
            "approval_min_score": approval_min_score,
            "score": score,
            "approval_failures": failures if isinstance(failures, list) else [],
            "reason": "approved" if resume_allowed else "blocked_by_latest_nemotron_summary",
        }
    )
    return gate


def status_payload(
    args: argparse.Namespace,
    device: torch.device,
    labels: LabelLoadResult,
    *,
    training_active: bool,
    steps: int,
    last_loss: float | None = None,
    ok: bool = True,
    reason: str | None = None,
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    motion_gate = read_motion_gate(args.root)
    payload: dict[str, object] = {
        "ok": ok,
        "mode": "local-gpu-nemotron-reward-training",
        "pid": os.getpid(),
        "updated_at": utc_now(),
        "cuda_available": cuda_available(),
        "gpu_name": cuda_gpu_name(),
        "selected_device": str(device),
        "training_active": training_active,
        "running": training_active or bool(args.watch),
        "samples_seen": len(labels.samples),
        "samples": len(labels.samples),
        "label_rows_seen": labels.label_rows_seen,
        "skipped_rows": labels.skipped_rows,
        "last_loss": last_loss,
        "last_nemotron_label_time": labels.last_nemotron_label_time,
        "checkpoint": str(args.checkpoint),
        "stop_file": str(STOP_FILE),
        "score_keys": list(SCORE_KEYS),
        "require_nemotron_model": not args.allow_non_nemotron_labels,
        "motion_resume_allowed": bool(motion_gate.get("motion_resume_allowed")),
        "motion_gate": motion_gate,
        "gpu": gpu_snapshot(device),
        "steps": steps,
    }
    if reason:
        payload["reason"] = reason
    if extra:
        payload.update(extra)
    return payload


def train_cycle(
    args: argparse.Namespace,
    model: nn.Module,
    optimizer: torch.optim.Optimizer,
    device: torch.device,
    steps: int,
) -> tuple[int, dict[str, object]]:
    labels = load_samples(args.root, allow_non_nemotron_labels=args.allow_non_nemotron_labels)
    samples = labels.samples
    if len(samples) < args.min_samples:
        summary = status_payload(
            args,
            device,
            labels,
            training_active=False,
            steps=steps,
            ok=False,
            reason=f"need at least {args.min_samples} valid Nemotron-labeled frames",
        )
        write_json(STATUS, summary)
        return steps, summary

    random.shuffle(samples)
    dataset = RewardFrameDataset(samples, args.image_size, preload=not args.no_preload)
    cached = dataset.cached or [dataset[index] for index in range(len(dataset))]
    all_images = torch.stack([item[0] for item in cached]).to(device, non_blocking=True)
    all_targets = torch.stack([item[1] for item in cached]).to(device, non_blocking=True)
    sample_count = int(all_images.shape[0])
    loss_fn = nn.SmoothL1Loss(beta=0.08)
    amp_enabled = device.type == "cuda" and args.amp
    scaler = torch.amp.GradScaler("cuda", enabled=amp_enabled)
    model.train()
    losses: list[float] = []
    started = time.time()
    total_batches = max(1, args.batches_per_epoch) * max(1, args.epochs)
    for _ in range(total_batches):
        indices = torch.randint(0, sample_count, (args.batch_size,), device=device)
        images = all_images.index_select(0, indices)
        targets = all_targets.index_select(0, indices)
        images = augment_on_device(images)
        optimizer.zero_grad(set_to_none=True)
        with torch.amp.autocast("cuda", enabled=amp_enabled):
            preds = torch.sigmoid(model(images))
            loss = loss_fn(preds, targets)
        if amp_enabled:
            scaler.scale(loss).backward()
            scaler.step(optimizer)
            scaler.update()
        else:
            loss.backward()
            optimizer.step()
        steps += 1
        losses.append(float(loss.detach().cpu()))
    elapsed = max(0.001, time.time() - started)
    if not args.no_save:
        save_checkpoint(model, optimizer, steps, args.checkpoint)
    avg_loss = sum(losses) / max(1, len(losses))
    last_loss = losses[-1] if losses else None
    summary = status_payload(
        args,
        device,
        labels,
        training_active=bool(args.watch and not STOP_FILE.exists()),
        steps=steps,
        last_loss=last_loss,
        extra={
            "trained_this_cycle": True,
            "epochs": args.epochs,
            "batches": total_batches,
            "batch_size": args.batch_size,
            "image_size": args.image_size,
            "checkpoint_saved": not args.no_save,
            "avg_loss": avg_loss,
            "batches_per_second": total_batches / elapsed,
        },
    )
    write_json(STATUS, summary)
    return steps, summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train a local CUDA reward surrogate from Nemotron-labeled avatar frames.")
    parser.add_argument("--root", type=Path, default=RUN_ROOT)
    parser.add_argument("--checkpoint", type=Path, default=CHECKPOINT)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batches-per-epoch", type=int, default=64)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--image-size", type=int, default=224)
    parser.add_argument("--workers", type=int, default=0)
    parser.add_argument("--no-preload", action="store_true")
    parser.add_argument("--amp", action="store_true", help="Use mixed precision. Disabled by default because it is slower on this WDDM setup.")
    parser.add_argument("--min-samples", type=int, default=4)
    parser.add_argument("--lr", type=float, default=2e-4)
    parser.add_argument("--device", default="cuda", choices=("cuda", "cpu"))
    parser.add_argument("--no-save", action="store_true", help="Run training without updating the checkpoint.")
    parser.add_argument(
        "--allow-non-nemotron-labels",
        action="store_true",
        help="Permit ok:true score rows whose model name does not include 'nemotron'.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    if args.device == "cuda" and not torch.cuda.is_available():
        device = torch.device("cpu")
    else:
        device = torch.device(args.device)
    if device.type == "cuda":
        torch.backends.cudnn.benchmark = True
        torch.set_float32_matmul_precision("high")

    model = build_model(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    steps = load_checkpoint(model, optimizer, args.checkpoint, device)
    labels = load_samples(args.root, allow_non_nemotron_labels=args.allow_non_nemotron_labels)
    write_json(
        STATUS,
        status_payload(
            args,
            device,
            labels,
            training_active=True,
            steps=steps,
            extra={"started_at": utc_now()},
        ),
    )
    if args.watch and STOP_FILE.exists():
        summary = status_payload(
            args,
            device,
            labels,
            training_active=False,
            steps=steps,
            ok=False,
            reason="stop file exists before watch start",
        )
        write_json(STATUS, summary)
        print(json.dumps(summary, ensure_ascii=True), flush=True)
        return 0
    summary: dict[str, object] | None = None
    while True:
        steps, summary = train_cycle(args, model, optimizer, device, steps)
        print(json.dumps(summary, ensure_ascii=True), flush=True)
        if not args.watch or STOP_FILE.exists():
            break
        deadline = time.time() + max(0.1, args.interval)
        while time.time() < deadline:
            if STOP_FILE.exists():
                break
            time.sleep(0.25)
    if summary is not None and summary.get("training_active") is True:
        summary = dict(summary)
        summary["training_active"] = False
        summary["running"] = False
        summary["stopped_at"] = utc_now()
        write_json(STATUS, summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
