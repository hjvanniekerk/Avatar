#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter


RUN_ROOT = Path(r"D:\Avatar\UnrealTalkingHead\Saved\RealismRL")
SITE_ROOT = Path(r"C:\Users\hjvan\OneDrive\_Codex\Avatar\public_html\avatar")
PRESENTERS_DIR = SITE_ROOT / "presenters"
STATUS_LOCAL = RUN_ROOT / "avatar_visual_optimizer_status.json"
STATUS_PUBLIC = SITE_ROOT / "rl-improvement-status.json"
STOP_FILE = RUN_ROOT / "avatar_visual_optimizer.stop"
HM_FILES = Path(r"C:\Users\hjvan\OneDrive\_Codex\Avatar\deploy\hm_files.py")
REMOTE_STATUS = "public_html/avatar/rl-improvement-status.json"

PRESENTERS: dict[str, dict[str, object]] = {}

RL_BEST = {
    "stage": PRESENTERS_DIR / "rl-best-stage.png",
    "source_stage": PRESENTERS_DIR / "rl-best-source.png",
    "thumb": PRESENTERS_DIR / "rl-best-thumb.png",
    "stage_remote": "public_html/avatar/presenters/rl-best-stage.png",
    "thumb_remote": "public_html/avatar/presenters/rl-best-thumb.png",
    "source": Path(r"D:\Avatar\BestGrid\best_g582_s085.png"),
    "source_score": 85,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def version_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


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
        "stdout": completed.stdout[-600:],
        "stderr": completed.stderr[-600:],
    }


def subject_mask(img: Image.Image) -> Image.Image:
    rgb = img.convert("RGB")
    arr = np.asarray(rgb).astype(np.int16)
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    blue_bg = (b > r + 25) & (b > g + 15) & (b > 70)
    cyan_white_bg = (b > 185) & (g > 170) & (r > 145)
    nearly_white = (r > 215) & (g > 225) & (b > 225)
    mask = ~(blue_bg | cyan_white_bg | nearly_white)
    mask_img = Image.fromarray((mask.astype(np.uint8) * 255), "L")
    # Contract first to remove the blue/cyan fringe from the authored preview,
    # then apply only a small feather. The previous dilation kept background
    # pixels and made a visible blue halo around the presenter.
    mask_img = mask_img.filter(ImageFilter.MinFilter(5))
    mask_img = mask_img.filter(ImageFilter.GaussianBlur(1.2))
    return mask_img


def studio_background(size: tuple[int, int], style: str) -> Image.Image:
    w, h = size
    y = np.linspace(0, 1, h, dtype=np.float32)[:, None]
    x = np.linspace(0, 1, w, dtype=np.float32)[None, :]
    cx = 0.46 if style != "warm" else 0.54
    radial = np.sqrt((x - cx) ** 2 + (y - 0.34) ** 2)
    radial = np.clip(1.0 - radial * 1.7, 0, 1)
    if style == "warm":
        top = np.array([78, 82, 86], dtype=np.float32)
        bottom = np.array([22, 24, 28], dtype=np.float32)
        key = np.array([134, 119, 101], dtype=np.float32)
    elif style == "neutral":
        top = np.array([54, 63, 74], dtype=np.float32)
        bottom = np.array([12, 17, 24], dtype=np.float32)
        key = np.array([108, 121, 136], dtype=np.float32)
    else:
        top = np.array([42, 55, 72], dtype=np.float32)
        bottom = np.array([8, 13, 23], dtype=np.float32)
        key = np.array([82, 116, 148], dtype=np.float32)
    base = bottom * y[..., None] + top * (1 - y[..., None])
    base = base + radial[..., None] * key * 0.55
    base = np.clip(base, 0, 255).astype(np.uint8)
    return Image.fromarray(base, "RGB").convert("RGBA")


def grade_subject(fg: Image.Image, mask: Image.Image, warmth: float, lift: float, contrast: float) -> Image.Image:
    rgba = fg.convert("RGBA")
    rgb = rgba.convert("RGB")
    rgb = ImageEnhance.Brightness(rgb).enhance(lift)
    rgb = ImageEnhance.Contrast(rgb).enhance(contrast)
    arr = np.asarray(rgb).astype(np.float32)
    m = np.asarray(mask).astype(np.float32) / 255.0
    skin_tint = np.array([1.0 + warmth * 0.10, 1.0 + warmth * 0.04, 1.0 - warmth * 0.06], dtype=np.float32)
    arr = arr * (1 - m[..., None] * 0.45) + (arr * skin_tint) * (m[..., None] * 0.45)
    arr = np.clip(arr, 0, 255).astype(np.uint8)
    out = Image.fromarray(arr, "RGB").convert("RGBA")
    out.putalpha(mask)
    return out


def crop_subject(img: Image.Image, mask: Image.Image) -> tuple[Image.Image, Image.Image]:
    bbox = mask.getbbox()
    if bbox is None:
        return img.convert("RGBA"), mask
    left, top, right, bottom = bbox
    pad_x = int((right - left) * 0.10)
    pad_y = int((bottom - top) * 0.06)
    box = (
        max(0, left - pad_x),
        max(0, top - pad_y),
        min(img.width, right + pad_x),
        min(img.height, bottom + pad_y),
    )
    return img.crop(box).convert("RGBA"), mask.crop(box)


def masked_overlay(base: Image.Image, overlay: Image.Image, alpha: Image.Image) -> None:
    clipped = overlay.copy()
    clipped.putalpha(ImageChops.multiply(clipped.getchannel("A"), alpha))
    base.alpha_composite(clipped)


def add_hair_and_clothing(canvas: Image.Image, mask: Image.Image, box: tuple[int, int, int, int], variant: dict[str, object]) -> None:
    x, y, w, h = box
    scale = h / 420.0
    draw_layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(draw_layer, "RGBA")

    hair_color = tuple(variant.get("hair_color", (30, 20, 15, 205)))  # type: ignore[arg-type]
    head_top = y + int(h * 0.07)
    head_cx = x + int(w * 0.52)
    head_cy = y + int(h * 0.22)
    # The subject faces left. Keep generated hair on the back/top of the skull
    # so it does not paint over the face and nose.
    d.ellipse(
        [
            head_cx - int(18 * scale),
            head_top + int(4 * scale),
            head_cx + int(82 * scale),
            head_cy + int(86 * scale),
        ],
        fill=hair_color,
    )
    if variant.get("ponytail"):
        d.ellipse(
            [
                head_cx + int(52 * scale),
                head_cy + int(66 * scale),
                head_cx + int(112 * scale),
                head_cy + int(164 * scale),
            ],
            fill=(18, 11, 9, 180),
        )
    for offset in range(0, 28, 7):
        d.line(
            [
                (head_cx + int(4 * scale) + offset, head_top + int(12 * scale)),
                (head_cx + int(52 * scale) + offset // 3, head_cy + int(94 * scale)),
            ],
            fill=(80, 58, 43, 58),
            width=max(1, int(1.6 * scale)),
        )

    suit = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(suit, "RGBA")
    shoulder_y = y + int(h * 0.48)
    waist_y = y + int(h * 0.86)
    sd.polygon(
        [
            (x + int(w * 0.43), shoulder_y),
            (x + int(w * 0.67), shoulder_y + int(h * 0.05)),
            (x + int(w * 0.73), waist_y),
            (x + int(w * 0.47), waist_y),
            (x + int(w * 0.36), shoulder_y + int(h * 0.17)),
        ],
        fill=(4, 5, 7, 175),
    )
    sd.line(
        [(x + int(w * 0.43), shoulder_y), (x + int(w * 0.36), y + int(h * 0.38))],
        fill=(10, 11, 14, 190),
        width=max(2, int(4 * scale)),
    )
    masked_overlay(canvas, suit, mask)
    masked_overlay(canvas, draw_layer, mask)

    detail = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    dd = ImageDraw.Draw(detail, "RGBA")
    dd.ellipse(
        [
            x + int(w * 0.26),
            y + int(h * 0.25),
            x + int(w * 0.29),
            y + int(h * 0.27),
        ],
        fill=(225, 214, 196, 130),
    )
    dd.line(
        [
            (x + int(w * 0.19), y + int(h * 0.36)),
            (x + int(w * 0.28), y + int(h * 0.36)),
        ],
        fill=(93, 38, 42, 145),
        width=max(1, int(2 * scale)),
    )
    masked_overlay(canvas, detail, mask)


def render_variant(source: Path, output_size: tuple[int, int], variant: dict[str, object]) -> Image.Image:
    base = Image.open(source).convert("RGBA")
    mask = subject_mask(base)
    cropped, cropped_mask = crop_subject(base, mask)
    fg = grade_subject(
        cropped,
        cropped_mask,
        warmth=float(variant["warmth"]),
        lift=float(variant["lift"]),
        contrast=float(variant["contrast"]),
    )
    bg = studio_background(output_size, str(variant["bg"]))
    target_h = int(output_size[1] * float(variant["scale"]))
    target_w = max(1, int(fg.width * (target_h / max(1, fg.height))))
    fg = fg.resize((target_w, target_h), Image.Resampling.LANCZOS)
    cropped_mask = cropped_mask.resize((target_w, target_h), Image.Resampling.LANCZOS)
    fg.putalpha(cropped_mask)
    x = int(output_size[0] * float(variant["x_anchor"]) - target_w * 0.5)
    y = output_size[1] - target_h + int(output_size[1] * float(variant["y_shift"]))
    shadow = Image.new("RGBA", output_size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow, "RGBA")
    sd.ellipse(
        [x + int(target_w * 0.12), output_size[1] - 42, x + int(target_w * 0.92), output_size[1] + 24],
        fill=(0, 0, 0, 80),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(18))
    bg.alpha_composite(shadow)
    bg.alpha_composite(fg, (x, y))
    full_mask = Image.new("L", output_size, 0)
    full_mask.paste(cropped_mask, (x, y))
    add_hair_and_clothing(bg, full_mask, (x, y, target_w, target_h), variant)
    rgb = bg.convert("RGB")
    rgb = ImageEnhance.Sharpness(rgb).enhance(float(variant["sharpness"]))
    rgb = ImageEnhance.Contrast(rgb).enhance(1.03)
    return rgb


def directional_blur(img: Image.Image, radius: int, direction: str) -> Image.Image:
    if radius <= 0:
        return img
    src = img.convert("RGBA")
    acc = Image.new("RGBA", src.size, (0, 0, 0, 0))
    steps = max(3, radius * 2 + 1)
    for i in range(steps):
        offset = i - steps // 2
        if direction == "vertical":
            shifted = ImageChops.offset(src, 0, offset)
        else:
            shifted = ImageChops.offset(src, offset, 0)
        acc = Image.blend(acc, shifted, 1.0 / (i + 1))
    return acc.convert("RGB")


def soft_ellipse_mask(size: tuple[int, int], box: tuple[int, int, int, int], blur: float) -> Image.Image:
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse(box, fill=255)
    return mask.filter(ImageFilter.GaussianBlur(blur))


def render_rl_best_variant(source: Path, output_size: tuple[int, int], variant: dict[str, object]) -> Image.Image:
    base = Image.open(source).convert("RGB")
    w, h = output_size
    base = base.resize(output_size, Image.Resampling.LANCZOS)
    zoom = float(variant.get("zoom", 1.0))
    crop_w = max(1, min(w, int(w / zoom)))
    crop_h = max(1, min(h, int(h / zoom)))
    cx = int(w * float(variant.get("x_center", 0.5)))
    cy = int(h * float(variant.get("y_center", 0.5)))
    left = max(0, min(w - crop_w, cx - crop_w // 2))
    top = max(0, min(h - crop_h, cy - crop_h // 2))
    frame = base.crop((left, top, left + crop_w, top + crop_h)).resize(output_size, Image.Resampling.LANCZOS)

    bg = base.filter(ImageFilter.GaussianBlur(float(variant.get("bg_blur", 12.0))))
    bg = ImageEnhance.Brightness(bg).enhance(float(variant.get("bg_lift", 0.72)))
    frame = Image.blend(bg, frame, float(variant.get("subject_mix", 0.86)))

    arr = np.asarray(frame).astype(np.float32)
    yy = np.linspace(0, 1, h, dtype=np.float32)[:, None]
    xx = np.linspace(0, 1, w, dtype=np.float32)[None, :]
    key_x = float(variant.get("key_x", 0.54))
    key_y = float(variant.get("key_y", 0.34))
    key = np.clip(1.0 - np.sqrt((xx - key_x) ** 2 + (yy - key_y) ** 2) * 1.65, 0, 1)
    arr *= 1.0 + key[..., None] * float(variant.get("key_strength", 0.16))
    vignette = np.clip(np.sqrt((xx - 0.5) ** 2 + (yy - 0.48) ** 2) * 1.2, 0, 1)
    arr *= 1.0 - vignette[..., None] * float(variant.get("vignette", 0.12))
    warmth = float(variant.get("warmth", 0.0))
    arr[..., 0] *= 1.0 + warmth * 0.05
    arr[..., 1] *= 1.0 + warmth * 0.015
    arr[..., 2] *= 1.0 - warmth * 0.035
    frame = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")

    blur_radius = int(variant.get("motion_blur", 0))
    if blur_radius:
        blurred = directional_blur(frame, blur_radius, str(variant.get("motion_dir", "horizontal")))
        # Keep the face sharp; apply motion cue mostly on the hands/torso.
        mask = Image.new("L", output_size, 0)
        mask = ImageChops.lighter(mask, soft_ellipse_mask(output_size, (int(w * 0.50), int(h * 0.47), int(w * 0.93), int(h * 0.92)), 34))
        mask = ImageChops.lighter(mask, soft_ellipse_mask(output_size, (int(w * 0.25), int(h * 0.36), int(w * 0.62), int(h * 0.98)), 42))
        frame = Image.composite(blurred, frame, mask.point(lambda p: int(p * float(variant.get("motion_alpha", 0.30)))))

    frame = ImageEnhance.Brightness(frame).enhance(float(variant.get("lift", 1.0)))
    frame = ImageEnhance.Contrast(frame).enhance(float(variant.get("contrast", 1.0)))
    frame = ImageEnhance.Sharpness(frame).enhance(float(variant.get("sharpness", 1.0)))
    return frame


def heuristic_score(img: Image.Image) -> dict[str, float]:
    arr = np.asarray(img.convert("RGB")).astype(np.float32)
    luma = arr.mean(axis=2)
    contrast = float(np.std(luma))
    dark = float((luma < 18).mean())
    bright = float((luma > 235).mean())
    sat = float(np.mean(np.max(arr, axis=2) - np.min(arr, axis=2)))
    artifact_penalty = (dark + bright) * 35
    lighting = max(0.0, min(100.0, contrast * 1.35 - artifact_penalty))
    hair = min(100.0, sat * 0.72 + 18.0)
    score = max(0.0, min(100.0, 0.55 * lighting + 0.25 * hair + 22.0))
    return {
        "score": round(score, 2),
        "face": round(min(100.0, score + 4), 2),
        "body": round(min(100.0, score + 2), 2),
        "hair": round(hair, 2),
        "lighting": round(lighting, 2),
        "artifacts": round(max(0.0, 100.0 - artifact_penalty), 2),
    }


def reward_model_scorer(device_name: str, image_size: int):
    try:
        import torch
        from torchvision import transforms

        import local_gpu_reward_model as reward

        device = torch.device("cuda" if device_name == "cuda" and torch.cuda.is_available() else "cpu")
        model = reward.build_model(device)
        optimizer = torch.optim.AdamW(model.parameters(), lr=1e-4)
        reward.load_checkpoint(model, optimizer, reward.CHECKPOINT, device)
        model.eval()
        tf = transforms.Compose(
            [
                transforms.Resize((image_size, image_size)),
                transforms.ToTensor(),
                transforms.Normalize(mean=(0.485, 0.456, 0.406), std=(0.229, 0.224, 0.225)),
            ]
        )

        def score(img: Image.Image) -> dict[str, float]:
            with torch.no_grad():
                x = tf(img.convert("RGB")).unsqueeze(0).to(device)
                pred = torch.sigmoid(model(x)).detach().cpu().numpy()[0] * 100.0
            return {key: round(float(pred[i]), 2) for i, key in enumerate(reward.SCORE_KEYS)}

        return score, {"ok": True, "device": str(device), "checkpoint": str(reward.CHECKPOINT)}
    except Exception as exc:  # noqa: BLE001
        return heuristic_score, {"ok": False, "fallback": "heuristic", "error": f"{exc.__class__.__name__}: {exc}"}


def variants_for(cycle: int, presenter_id: str, count: int) -> list[dict[str, object]]:
    rng = random.Random(f"{presenter_id}:{cycle}:visual-optimizer")
    variants: list[dict[str, object]] = []
    bases = [
        {"bg": "neutral", "scale": 0.94, "x_anchor": 0.50, "y_shift": 0.00, "warmth": 0.65, "lift": 1.12, "contrast": 1.20, "sharpness": 1.16, "ponytail": True},
        {"bg": "warm", "scale": 0.98, "x_anchor": 0.50, "y_shift": 0.01, "warmth": 0.82, "lift": 1.08, "contrast": 1.18, "sharpness": 1.18, "ponytail": False},
        {"bg": "cool", "scale": 0.91, "x_anchor": 0.49, "y_shift": 0.00, "warmth": 0.55, "lift": 1.15, "contrast": 1.22, "sharpness": 1.12, "ponytail": True},
    ]
    for base in bases:
        item = dict(base)
        item["hair_color"] = (24, 16, 13, 235)
        variants.append(item)
    while len(variants) < count:
        variants.append(
            {
                "bg": rng.choice(["neutral", "warm", "cool"]),
                "scale": rng.uniform(0.88, 1.02),
                "x_anchor": rng.uniform(0.47, 0.54),
                "y_shift": rng.uniform(-0.01, 0.03),
                "warmth": rng.uniform(0.45, 0.88),
                "lift": rng.uniform(1.05, 1.20),
                "contrast": rng.uniform(1.10, 1.28),
                "sharpness": rng.uniform(1.08, 1.28),
                "ponytail": rng.random() > 0.35,
                "hair_color": rng.choice([(24, 16, 13, 235), (34, 23, 16, 235), (18, 14, 12, 238)]),
            }
        )
    return variants


def rl_best_variants(cycle: int, count: int) -> list[dict[str, object]]:
    rng = random.Random(f"rl-best:{cycle}:visible-feedback")
    variants: list[dict[str, object]] = [
        {"zoom": 1.00, "x_center": 0.50, "y_center": 0.50, "bg_blur": 12, "bg_lift": 0.74, "subject_mix": 0.88, "key_strength": 0.14, "vignette": 0.10, "warmth": 0.10, "lift": 1.00, "contrast": 1.03, "sharpness": 1.06, "motion_blur": 0},
        {"zoom": 1.05, "x_center": 0.52, "y_center": 0.48, "bg_blur": 16, "bg_lift": 0.70, "subject_mix": 0.90, "key_strength": 0.18, "vignette": 0.12, "warmth": 0.14, "lift": 1.02, "contrast": 1.05, "sharpness": 1.08, "motion_blur": 1, "motion_alpha": 0.18},
        {"zoom": 1.08, "x_center": 0.53, "y_center": 0.47, "bg_blur": 20, "bg_lift": 0.68, "subject_mix": 0.91, "key_strength": 0.22, "vignette": 0.14, "warmth": 0.08, "lift": 1.04, "contrast": 1.06, "sharpness": 1.05, "motion_blur": 2, "motion_alpha": 0.22},
        {"zoom": 1.02, "x_center": 0.50, "y_center": 0.48, "bg_blur": 18, "bg_lift": 0.76, "subject_mix": 0.87, "key_strength": 0.20, "vignette": 0.08, "warmth": 0.18, "lift": 1.03, "contrast": 1.02, "sharpness": 1.10, "motion_blur": 1, "motion_alpha": 0.14, "motion_dir": "vertical"},
    ]
    while len(variants) < count:
        variants.append(
            {
                "zoom": rng.uniform(1.0, 1.12),
                "x_center": rng.uniform(0.49, 0.55),
                "y_center": rng.uniform(0.45, 0.52),
                "bg_blur": rng.uniform(12.0, 24.0),
                "bg_lift": rng.uniform(0.64, 0.80),
                "subject_mix": rng.uniform(0.84, 0.94),
                "key_x": rng.uniform(0.48, 0.58),
                "key_y": rng.uniform(0.26, 0.42),
                "key_strength": rng.uniform(0.10, 0.26),
                "vignette": rng.uniform(0.06, 0.18),
                "warmth": rng.uniform(0.02, 0.22),
                "lift": rng.uniform(0.98, 1.06),
                "contrast": rng.uniform(1.00, 1.09),
                "sharpness": rng.uniform(1.02, 1.14),
                "motion_blur": rng.choice([0, 1, 1, 2]),
                "motion_alpha": rng.uniform(0.10, 0.26),
                "motion_dir": rng.choice(["horizontal", "vertical"]),
            }
        )
    return variants


def save_thumb(img: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img.resize((240, 135), Image.Resampling.LANCZOS).save(path, optimize=True, quality=90)


def ensure_rl_best_source() -> Path:
    source = Path(RL_BEST["source_stage"])
    stage = Path(RL_BEST["stage"])
    if not source.is_file() and stage.is_file():
        shutil.copyfile(stage, source)
    return source if source.is_file() else stage


def optimize_once(args: argparse.Namespace, cycle: int) -> dict[str, object]:
    status: dict[str, object] = {
        "ok": True,
        "active": True,
        "running": bool(args.watch),
        "mode": "avatar-visual-optimizer",
        "cycle": cycle,
        "updated_at": utc_now(),
        "current_asset_version": version_id(),
        "presenters": {},
        "stop_file": str(STOP_FILE),
    }
    score_fn, scorer_info = reward_model_scorer(args.device, args.image_size)
    status["scorer"] = scorer_info
    out_dir = RUN_ROOT / "visual_optimizer" / f"cycle-{cycle:05d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    deploys: list[dict[str, object]] = []
    for presenter_id, spec in PRESENTERS.items():
        source = Path(spec["source"])
        best: dict[str, object] | None = None
        for index, variant in enumerate(variants_for(cycle, presenter_id, args.candidates)):
            img = render_variant(source, (960, 540), variant)
            scores = score_fn(img)
            candidate_path = out_dir / f"{presenter_id}-{index:02d}.png"
            img.save(candidate_path, optimize=True)
            score = float(scores.get("score", 0.0))
            if best is None or score > float(best["score"]):
                best = {
                    "score": score,
                    "scores": scores,
                    "variant": variant,
                    "path": str(candidate_path),
                    "index": index,
                }
        if best is None:
            continue
        best_img = Image.open(best["path"]).convert("RGB")
        output = Path(spec["output"])
        output.parent.mkdir(parents=True, exist_ok=True)
        best_img.save(output, optimize=True)
        status["presenters"][presenter_id] = {
            "source": str(source),
            "output": str(output),
            "best": best,
            "published": True,
        }
        if args.deploy:
            deploys.append(deploy_file(output, str(spec["remote"])))
    rl_source = ensure_rl_best_source()
    if rl_source.is_file():
        source_img = Image.open(rl_source).convert("RGB").resize((960, 540), Image.Resampling.LANCZOS)
        baseline_scores = score_fn(source_img)
        baseline = {
            "score": float(baseline_scores.get("score", 0.0)),
            "scores": baseline_scores,
            "variant": {"source": "rl_best_source", "published_if_no_candidate_beats_baseline": True},
            "path": str(rl_source),
            "index": -1,
        }
        candidate_best: dict[str, object] = dict(baseline)
        candidate_count = max(args.candidates, 8)
        for index, variant in enumerate(rl_best_variants(cycle, candidate_count)):
            img = render_rl_best_variant(rl_source, (960, 540), variant)
            scores = score_fn(img)
            candidate_path = out_dir / f"rl-best-{index:02d}.png"
            img.save(candidate_path, optimize=True, quality=94)
            score = float(scores.get("score", 0.0))
            if score >= float(candidate_best["score"]):
                candidate_best = {
                    "score": score,
                    "scores": scores,
                    "variant": variant,
                    "path": str(candidate_path),
                    "index": index,
                }
        min_delta = float(getattr(args, "rl_best_min_delta", 3.0))
        published_candidate = (
            int(candidate_best.get("index", -1)) >= 0
            and float(candidate_best["score"]) >= float(baseline["score"]) + min_delta
        )
        best = candidate_best if published_candidate else baseline
        best_img = Image.open(str(best["path"])).convert("RGB").resize((960, 540), Image.Resampling.LANCZOS)
        stage_path = Path(RL_BEST["stage"])
        stage_path.parent.mkdir(parents=True, exist_ok=True)
        best_img.save(stage_path, optimize=True, quality=94)
        save_thumb(best_img, Path(RL_BEST["thumb"]))
        status["presenters"]["rl-best"] = {
            "source": str(rl_source),
            "output": str(stage_path),
            "best": best,
            "baseline": baseline,
            "candidate_best": candidate_best,
            "candidate_count": candidate_count,
            "publish_min_delta": min_delta,
            "published": True,
            "published_candidate": published_candidate,
            "learning": "visible_rl_best_mutation_loop",
        }
        if args.deploy:
            deploys.append(deploy_file(stage_path, str(RL_BEST["stage_remote"])))
            deploys.append(deploy_file(Path(RL_BEST["thumb"]), str(RL_BEST["thumb_remote"])))
    status["deploy_assets"] = deploys
    write_json(STATUS_LOCAL, status)
    write_json(STATUS_PUBLIC, status)
    if args.deploy:
        status["deploy_status"] = deploy_file(STATUS_PUBLIC, REMOTE_STATUS)
        write_json(STATUS_LOCAL, status)
        write_json(STATUS_PUBLIC, status)
    return status


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate/publish improved avatar presenter renders from the local reward model.")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=float, default=120.0)
    parser.add_argument("--candidates", type=int, default=8)
    parser.add_argument("--image-size", type=int, default=160)
    parser.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    parser.add_argument("--rl-best-min-delta", type=float, default=3.0)
    parser.add_argument("--deploy", action="store_true")
    parser.add_argument("--once", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    cycle = 0
    while not STOP_FILE.exists():
        cycle += 1
        status = optimize_once(args, cycle)
        print(json.dumps(status, ensure_ascii=True), flush=True)
        if args.once or not args.watch:
            break
        deadline = time.time() + max(10.0, args.interval)
        while time.time() < deadline and not STOP_FILE.exists():
            time.sleep(1.0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
