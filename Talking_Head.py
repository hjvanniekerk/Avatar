#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib import error as urlerror
from urllib import request as urlrequest


PROJECT_PATH = Path(r"D:\Avatar\UnrealTalkingHead\TalkingHead.uproject")
OPEN_SCRIPT = Path(r"D:\Avatar\UnrealTalkingHead\Open-TalkingHead.ps1")
SETUP_SCRIPT = Path(r"D:\Avatar\UnrealTalkingHead\Scripts\setup_talking_head.py")
SOURCE_AUDIO = Path(r"D:\Avatar\last_audio.wav")
LEVEL_ASSET = Path(r"D:\Avatar\UnrealTalkingHead\Content\TalkingHead\Maps\LVL_TalkingHead.umap")
SEQUENCE_ASSET = Path(r"D:\Avatar\UnrealTalkingHead\Content\TalkingHead\Sequences\LS_TalkingHead.uasset")
IMPORTED_AUDIO_ASSET = Path(r"D:\Avatar\UnrealTalkingHead\Content\TalkingHead\Audio\last_audio.uasset")
EPIC_MANIFEST_DIR = Path(r"C:\ProgramData\Epic\EpicGamesLauncher\Data\Manifests")
NIA_PROJECT = Path(r"D:\Avatar\CharacterCreatorNia\CharacterCreatorNia.uproject")
NIA_PREVIEW = Path(r"D:\Avatar\CharacterCreatorNia\CharacterCreatorNia.png")
NIA_MAP = Path(r"D:\Avatar\CharacterCreatorNia\Content\CC_ControlRig_Nia\Maps\Nia.umap")
NIA_SEQUENCE = Path(r"D:\Avatar\CharacterCreatorNia\Content\CC_ControlRig_Nia\Cinematic\Nia.uasset")
NIA_SKELETAL_MESH = Path(r"D:\Avatar\CharacterCreatorNia\Content\CC_ControlRig_Nia\RLContent\Nia\SK_Nia.uasset")
RL_SCRIPT = Path(__file__).with_name("unreal_realism_rl.py")
REALISM_JSON = Path(r"D:\Avatar\realism.json")
REALISM_RUN_ROOT = Path(r"D:\Avatar\UnrealTalkingHead\Saved\RealismRL")
MOTION_APPROVAL_MIN_SCORE = 90
MOTION_APPROVAL_MIN_FRAMES = 240

EDITOR_CANDIDATES = [
    Path(r"D:\Program Files\Epic Games\UE_5.7\Engine\Binaries\Win64\UnrealEditor.exe"),
    Path(r"D:\Avatar\Epic\UE_5.7\Engine\Binaries\Win64\UnrealEditor.exe"),
    Path(r"D:\Avatar\Epic Games\UE_5.7\Engine\Binaries\Win64\UnrealEditor.exe"),
    Path(r"D:\Epic Games\UE_5.7\Engine\Binaries\Win64\UnrealEditor.exe"),
    Path(r"D:\UE_5.7\Engine\Binaries\Win64\UnrealEditor.exe"),
    Path(r"C:\Program Files\Epic Games\UE_5.7\Engine\Binaries\Win64\UnrealEditor.exe"),
    Path(r"C:\Program Files\Epic Games\UE_5.6\Engine\Binaries\Win64\UnrealEditor.exe"),
]
LAUNCHER_CANDIDATES = [
    Path(r"D:\Avatar\Epic\Epic Games\Launcher\Portal\Binaries\Win64\EpicGamesLauncher.exe"),
    Path(r"C:\Program Files\Epic Games\Launcher\Portal\Binaries\Win64\EpicGamesLauncher.exe"),
]

LAST_ACTION: dict[str, str] = {
    "message": "Last button pressed: none",
    "kind": "neutral",
    "updated_at": "",
}

CHAT_MESSAGES: list[dict[str, str]] = [
    {
        "role": "assistant",
        "content": "Hi, I am Nia. I can answer here and speak the reply through the avatar.",
        "provider": "local",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
]


def h(value: object) -> str:
    return html.escape(str(value), quote=True)


def first_existing(paths: list[Path]) -> Path | None:
    for path in paths:
        if path.is_file():
            return path
    return None


def load_epic_manifest_items() -> list[dict[str, object]]:
    if not EPIC_MANIFEST_DIR.is_dir():
        return []

    items: list[dict[str, object]] = []
    for path in EPIC_MANIFEST_DIR.glob("*.item"):
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            items.append(data)
    return items


def manifest_editor_candidates() -> list[Path]:
    candidates: list[Path] = []
    for item in load_epic_manifest_items():
        app_name = str(item.get("AppName", ""))
        display_name = str(item.get("DisplayName", ""))
        launch_executable = str(item.get("LaunchExecutable", ""))
        install_location = str(item.get("InstallLocation", ""))
        if app_name != "UE_5.7" and display_name != "Unreal Engine":
            continue
        if not install_location or not launch_executable:
            continue
        candidates.append(Path(install_location) / launch_executable)
    return candidates


def editor_candidates() -> list[Path]:
    seen: set[str] = set()
    candidates: list[Path] = []
    for path in [*manifest_editor_candidates(), *EDITOR_CANDIDATES]:
        key = str(path).casefold()
        if key in seen:
            continue
        seen.add(key)
        candidates.append(path)
    return candidates


def epic_staging_dirs() -> list[Path]:
    dirs: list[Path] = []
    seen: set[str] = set()
    for item in load_epic_manifest_items():
        app_name = str(item.get("AppName", ""))
        if app_name != "UE_5.7":
            continue
        for key_name in ("StagingLocation",):
            raw_path = str(item.get(key_name, ""))
            if not raw_path:
                continue
            path = Path(raw_path)
            normalized = str(path).casefold()
            if normalized in seen:
                continue
            seen.add(normalized)
            dirs.append(path)
    return dirs


def ensure_epic_staging_dirs() -> list[Path]:
    created_or_ready: list[Path] = []
    for path in epic_staging_dirs():
        path.mkdir(parents=True, exist_ok=True)
        created_or_ready.append(path)
    return created_or_ready


def file_status(path: Path) -> str:
    if not path.exists():
        return "missing"
    try:
        stat = path.stat()
    except OSError as exc:
        return f"unavailable ({exc.__class__.__name__})"
    size = stat.st_size
    modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    return f"present | {size:,} bytes | modified {modified}"


def directory_status(path: Path) -> str:
    if not path.exists():
        return "missing"
    if not path.is_dir():
        return "not a directory"
    try:
        stat = path.stat()
    except OSError as exc:
        return f"unavailable ({exc.__class__.__name__})"
    modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    return f"ready | modified {modified}"


def first_env(names: tuple[str, ...]) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def nemotron_api_key() -> str:
    return first_env(
        (
            "NEMOTRON_API_KEY",
            "NVIDIA_API_KEY",
            "NVIDIA_NIM_API_KEY",
            "NIM_API_KEY",
            "NGC_API_KEY",
        )
    )


def nemotron_base_url() -> str:
    return first_env(("NEMOTRON_BASE_URL", "NVIDIA_NIM_BASE_URL", "NIM_BASE_URL")) or "http://100.106.75.76:1234/v1"


def nemotron_model() -> str:
    return first_env(("NEMOTRON_MODEL", "NVIDIA_NEMOTRON_MODEL", "NIM_MODEL")) or "nvidia/nemotron-3-nano-omni"


def nemotron_endpoint() -> str:
    base_url = nemotron_base_url().rstrip("/")
    if base_url.endswith("/chat/completions"):
        return base_url
    if base_url.endswith("/v1"):
        return f"{base_url}/chat/completions"
    return f"{base_url}/v1/chat/completions"


def nemotron_status() -> str:
    key = nemotron_api_key()
    base_url = nemotron_base_url()
    configured = bool(key) or base_url.startswith("http://100.") or "localhost" in base_url or "127.0.0.1" in base_url
    if configured:
        return f"Nemotron ready | model {nemotron_model()} | {base_url}"
    return f"Nemotron waiting for NVIDIA_API_KEY | model {nemotron_model()}"


def visible_chat_messages() -> list[dict[str, str]]:
    return CHAT_MESSAGES[-20:]


def model_chat_messages(user_message: str) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": (
                "You are Nia, the AiEng Unreal female avatar. "
                "Answer as a concise, practical assistant. "
                "Return only the final answer; do not include reasoning. "
                "Keep replies speakable in under 80 words unless the user asks for detail."
            ),
        }
    ]
    for message in CHAT_MESSAGES[-12:]:
        role = message.get("role", "")
        if role not in {"user", "assistant"}:
            continue
        messages.append({"role": role, "content": message.get("content", "")})
    messages.append({"role": "user", "content": user_message})
    return messages


def fallback_chat_reply(user_message: str) -> str:
    text = user_message.strip()
    if not text:
        return "I am here."
    if any(word in text.lower() for word in ("hello", "hi", "hey")):
        return "Hello. I am Nia, running from the local Avatar controller. I can speak replies while the Nemotron key is being configured."
    if "nemotron" in text.lower() or "nemtron" in text.lower():
        return "The chat box is wired to LM Studio on Tailscale at http://100.106.75.76:1234. Confirm LM Studio is serving an OpenAI-compatible model if replies fail."
    return (
        "I heard you. The avatar motion and speech are active locally. "
        "Nemotron will handle this reply through LM Studio on Tailscale when the endpoint is reachable."
    )


def call_nemotron(user_message: str) -> tuple[str, str]:
    base_url = nemotron_base_url()
    key = nemotron_api_key()
    local_nim = base_url.startswith("http://100.") or "localhost" in base_url or "127.0.0.1" in base_url
    if not key and not local_nim:
        return fallback_chat_reply(user_message), "local fallback"

    payload = {
        "model": nemotron_model(),
        "messages": model_chat_messages(user_message),
        "temperature": 0.6,
        "top_p": 0.9,
        "max_tokens": 1024,
    }
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = urlrequest.Request(nemotron_endpoint(), data=data, headers=headers, method="POST")
    try:
        with urlrequest.urlopen(request, timeout=45) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except (urlerror.URLError, TimeoutError, OSError) as exc:
        return f"{fallback_chat_reply(user_message)} Nemotron call failed locally: {exc.__class__.__name__}.", "local fallback"

    try:
        result = json.loads(raw)
        choices = result.get("choices") or []
        message = choices[0].get("message", {}) if choices else {}
        content = str(message.get("content", "")).strip()
    except (AttributeError, IndexError, json.JSONDecodeError):
        content = ""
    if not content:
        return f"{fallback_chat_reply(user_message)} Nemotron returned an empty response.", "local fallback"
    return content, "Nemotron"


def append_chat_message(role: str, content: str, provider: str = "") -> dict[str, str]:
    message = {
        "role": role,
        "content": content.strip(),
        "provider": provider,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    CHAT_MESSAGES.append(message)
    if len(CHAT_MESSAGES) > 40:
        del CHAT_MESSAGES[:-40]
    return message


def handle_chat(user_message: str) -> dict[str, object]:
    clean = " ".join(user_message.strip().split())
    if not clean:
        raise ValueError("message is empty")
    append_chat_message("user", clean)
    reply, provider = call_nemotron(clean)
    append_chat_message("assistant", reply, provider)
    return {
        "ok": True,
        "reply": reply,
        "provider": provider,
        "status": nemotron_status(),
        "messages": visible_chat_messages(),
    }


def latest_user_message(messages: object) -> str:
    if not isinstance(messages, list):
        return ""
    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        if str(message.get("role", "")).lower() == "user":
            return str(message.get("content", "") or "")
    return ""


def render_chat_messages() -> str:
    pieces: list[str] = []
    for message in visible_chat_messages():
        role = message.get("role", "assistant")
        content = message.get("content", "")
        provider = message.get("provider", "")
        created_at = message.get("created_at", "")
        meta = f"{provider} | {created_at}" if provider else created_at
        pieces.append(
            f'<article class="chat-msg chat-{h(role)}">'
            f'<div class="chat-role">{h("Nia" if role == "assistant" else "You")}</div>'
            f'<div class="chat-text">{h(content)}</div>'
            f'<div class="chat-meta">{h(meta)}</div>'
            "</article>"
        )
    return "\n".join(pieces)


def render_talking_head(audio_ready: bool) -> str:
    motion_unlocked = motion_is_approved()
    motion_note = (
        f"Motion approved by Unreal/Nemotron RL."
        if motion_unlocked
        else f"Motion locked until Unreal/Nemotron approves {MOTION_APPROVAL_MIN_FRAMES} frames at {MOTION_APPROVAL_MIN_SCORE}+."
    )
    audio_src = "http://127.0.0.1:8766/audio/last_audio.wav"
    audio = (
        f'<audio id="avatar-audio" class="th-audio" controls preload="metadata" src="{h(audio_src)}"></audio>'
        if audio_ready
        else '<div class="th-audio th-muted">No voice audio loaded</div>'
    )
    nia_ready = NIA_PREVIEW.is_file()
    if nia_ready:
        try:
            image_version = int(NIA_PREVIEW.stat().st_mtime)
        except OSError:
            image_version = 0
        avatar_markup = (
            '<div class="th-head-wrap th-nia-wrap">'
            '<div class="th-nia-avatar">'
            f'<img class="th-nia-img" src="http://127.0.0.1:8766/image/nia.png?v={image_version}" '
            'alt="Unreal female avatar Nia">'
            '<span class="th-nia-mouth" aria-hidden="true"></span>'
            '</div>'
            '<div class="th-speaking" aria-hidden="true"><span></span><span></span><span></span><span></span></div>'
            '</div>'
        )
        avatar_source = "Unreal female avatar source: CharacterCreatorNia / Nia."
    else:
        avatar_markup = """
  <div class="th-head-wrap">
    <svg class="th-svg" width="260" height="340" viewBox="0 0 260 340" role="img" aria-label="Animated AiEng avatar">
      <rect x="0" y="0" width="260" height="340" rx="12" fill="#111923"></rect>
      <ellipse cx="130" cy="322" rx="92" ry="54" fill="#29415c"></ellipse>
      <rect x="102" y="236" width="56" height="70" rx="19" fill="#b77d65"></rect>
      <ellipse cx="58" cy="155" rx="22" ry="35" fill="#bd846b"></ellipse>
      <ellipse cx="202" cy="155" rx="22" ry="35" fill="#bd846b"></ellipse>
      <ellipse cx="130" cy="151" rx="86" ry="118" fill="#c78f72">
        <animate attributeName="cy" values="151;155;151" dur="3.8s" repeatCount="indefinite"></animate>
      </ellipse>
      <path d="M55 101 C72 29 191 31 207 104 C180 76 91 76 55 101Z" fill="#222a31"></path>
      <rect x="72" y="127" width="45" height="7" rx="4" fill="#2b2020" transform="rotate(-6 94 130)"></rect>
      <rect x="143" y="127" width="45" height="7" rx="4" fill="#2b2020" transform="rotate(6 166 130)"></rect>
      <ellipse cx="93" cy="151" rx="13" ry="8" fill="#101820">
        <animate attributeName="ry" values="8;8;1;8;8" keyTimes="0;0.92;0.94;0.96;1" dur="5.4s" repeatCount="indefinite"></animate>
      </ellipse>
      <ellipse cx="167" cy="151" rx="13" ry="8" fill="#101820">
        <animate attributeName="ry" values="8;8;1;8;8" keyTimes="0;0.92;0.94;0.96;1" dur="5.4s" repeatCount="indefinite"></animate>
      </ellipse>
      <path d="M132 160 Q126 187 141 194" fill="none" stroke="#744234" stroke-width="4" stroke-linecap="round"></path>
      <ellipse cx="130" cy="226" rx="28" ry="9" fill="#3b1418">
        <animate attributeName="ry" values="6;20;8;17;6" dur=".42s" repeatCount="indefinite"></animate>
        <animate attributeName="rx" values="24;30;25;31;24" dur=".42s" repeatCount="indefinite"></animate>
      </ellipse>
      <path d="M111 213 Q130 224 149 213" fill="none" stroke="#2c0f13" stroke-width="5" stroke-linecap="round"></path>
    </svg>
  </div>"""
        avatar_source = "Fallback browser avatar. Nia preview image is missing."
    return f"""
<style>
  .th-stage {{
    display: grid;
    grid-template-columns: minmax(220px, 360px) minmax(220px, 1fr);
    gap: 18px;
    align-items: center;
    margin: 12px 0;
    padding: 18px;
    background: #111923;
    border: 1px solid #263447;
  }}
  .th-head-wrap {{
    display: grid;
    place-items: center;
    min-height: 320px;
    background: linear-gradient(180deg, #182331 0%, #0f151e 100%);
    border: 1px solid #27364a;
    overflow: hidden;
  }}
  .th-nia-wrap {{
    min-height: 360px;
    padding: 12px;
    background: radial-gradient(circle at 50% 34%, #26384d 0%, #111923 64%);
  }}
  .th-nia-avatar {{
    position: relative;
    width: min(100%, 340px);
    animation: th-nod 3.8s ease-in-out infinite;
    transform-origin: 50% 62%;
  }}
  .th-nia-img {{
    display: block;
    width: 100%;
    max-height: 360px;
    object-fit: contain;
    image-rendering: auto;
    filter: drop-shadow(0 24px 42px rgba(0, 0, 0, .42));
  }}
  .th-nia-mouth {{
    position: absolute;
    left: 36.5%;
    top: 61.5%;
    width: 14%;
    height: 3.2%;
    background: rgba(43, 12, 18, .78);
    border: 1px solid rgba(255, 184, 184, .18);
    border-radius: 0 0 999px 999px;
    box-shadow: inset 0 -3px 0 rgba(0, 0, 0, .22);
    transform-origin: center top;
    opacity: .38;
    transform: scaleY(.45) scaleX(.9);
  }}
  .th-speaking {{
    display: flex;
    gap: 5px;
    align-items: end;
    height: 24px;
    margin-top: -16px;
    opacity: .38;
    transition: opacity .16s ease;
  }}
  .th-speaking span {{
    display: block;
    width: 5px;
    height: 8px;
    background: #3ce6cf;
    border-radius: 999px;
    animation: th-meter .48s ease-in-out infinite alternate;
  }}
  .th-speaking span:nth-child(2) {{ animation-delay: .08s; }}
  .th-speaking span:nth-child(3) {{ animation-delay: .16s; }}
  .th-speaking span:nth-child(4) {{ animation-delay: .24s; }}
  .avatar-app.is-speaking .th-nia-avatar,
  .th-stage.is-speaking .th-nia-avatar {{
    animation: th-speak-head .72s ease-in-out infinite;
  }}
  .avatar-app.is-thinking .th-nia-avatar,
  .th-stage.is-thinking .th-nia-avatar {{
    animation: th-listen 1.25s ease-in-out infinite;
  }}
  .avatar-app.is-speaking .th-nia-mouth,
  .th-stage.is-speaking .th-nia-mouth {{
    opacity: .95;
    animation: th-nia-talk .13s ease-in-out infinite alternate;
  }}
  .avatar-app.is-speaking .th-speaking,
  .th-stage.is-speaking .th-speaking {{
    opacity: .95;
  }}
  .th-stage.motion-locked *,
  .avatar-app.motion-locked * {{
    animation: none !important;
    transition: none !important;
  }}
  .th-stage.motion-locked .th-nia-avatar,
  .avatar-app.motion-locked .th-nia-avatar,
  .th-stage.motion-locked .th-head,
  .avatar-app.motion-locked .th-head {{
    transform: none !important;
  }}
  .th-stage.motion-locked .th-nia-mouth,
  .avatar-app.motion-locked .th-nia-mouth {{
    opacity: 0 !important;
  }}
  .th-stage.motion-locked .th-speaking,
  .avatar-app.motion-locked .th-speaking {{
    opacity: .16 !important;
  }}
  .th-head {{
    position: relative;
    width: min(76%, 240px);
    aspect-ratio: 0.78;
    background: #c78f72;
    border-radius: 44% 44% 42% 42% / 34% 34% 54% 54%;
    box-shadow: inset 18px -28px 0 rgba(96, 49, 38, .18), 0 28px 60px rgba(0, 0, 0, .35);
    animation: th-nod 3.8s ease-in-out infinite;
  }}
  .th-hair {{
    position: absolute;
    inset: -9% 7% 69% 7%;
    background: #222a31;
    border-radius: 52% 48% 22% 24%;
  }}
  .th-ear {{
    position: absolute;
    top: 39%;
    width: 11%;
    height: 18%;
    background: #bd846b;
    border-radius: 50%;
  }}
  .th-ear.left {{ left: -7%; }}
  .th-ear.right {{ right: -7%; }}
  .th-eye {{
    position: absolute;
    top: 39%;
    width: 14%;
    height: 7%;
    background: #101820;
    border-radius: 50%;
    animation: th-blink 5.4s infinite;
  }}
  .th-eye.left {{ left: 27%; }}
  .th-eye.right {{ right: 27%; }}
  .th-brow {{
    position: absolute;
    top: 32%;
    width: 18%;
    height: 3%;
    background: #2b2020;
    border-radius: 999px;
  }}
  .th-brow.left {{ left: 24%; transform: rotate(-6deg); }}
  .th-brow.right {{ right: 24%; transform: rotate(6deg); }}
  .th-nose {{
    position: absolute;
    top: 45%;
    left: 48%;
    width: 6%;
    height: 18%;
    border-right: 3px solid rgba(92, 47, 38, .4);
    border-bottom: 3px solid rgba(92, 47, 38, .32);
    border-radius: 0 0 12px 0;
  }}
  .th-mouth {{
    position: absolute;
    left: 37%;
    top: 67%;
    width: 26%;
    height: 7%;
    background: #3b1418;
    border: 3px solid rgba(40, 12, 16, .55);
    border-radius: 0 0 999px 999px;
    transform-origin: center top;
    animation: th-talk .18s ease-in-out infinite alternate;
  }}
  .th-neck {{
    position: absolute;
    left: 38%;
    bottom: -18%;
    width: 24%;
    height: 21%;
    background: #b77d65;
    z-index: -1;
  }}
  .th-body {{
    position: absolute;
    left: 10%;
    bottom: -42%;
    width: 80%;
    height: 34%;
    background: #29415c;
    border-radius: 44% 44% 0 0;
    z-index: -2;
  }}
  .th-panel {{
    display: grid;
    gap: 12px;
    align-content: center;
    min-width: 0;
  }}
  .th-title {{
    font-size: 1.25rem;
    font-weight: 700;
    color: #f3f7fb;
  }}
  .th-status {{
    color: #b8c7d8;
    line-height: 1.45;
  }}
  .th-audio {{
    width: min(100%, 520px);
  }}
  .th-muted {{
    color: #f0b86b;
    border: 1px solid #654b20;
    padding: 10px;
    background: #21180d;
  }}
  @keyframes th-talk {{
    from {{ height: 4%; transform: scaleX(.92); }}
    to {{ height: 15%; transform: scaleX(1.06); }}
  }}
  @keyframes th-nod {{
    0%, 100% {{ transform: translateY(0) rotate(-1deg); }}
    50% {{ transform: translateY(4px) rotate(1deg); }}
  }}
  @keyframes th-speak-head {{
    0%, 100% {{ transform: translateY(0) rotate(-1.2deg) scale(1); }}
    35% {{ transform: translateY(3px) rotate(.8deg) scale(1.008); }}
    70% {{ transform: translateY(1px) rotate(-.4deg) scale(1.002); }}
  }}
  @keyframes th-listen {{
    0%, 100% {{ transform: translateY(0) rotate(-.8deg); }}
    50% {{ transform: translateY(2px) rotate(.8deg); }}
  }}
  @keyframes th-blink {{
    0%, 92%, 100% {{ transform: scaleY(1); }}
    94%, 96% {{ transform: scaleY(.08); }}
  }}
  @keyframes th-meter {{
    from {{ height: 7px; opacity: .6; }}
    to {{ height: 24px; opacity: 1; }}
  }}
  @keyframes th-nia-talk {{
    from {{ height: 2.5%; transform: scaleX(.86); opacity: .72; }}
    to {{ height: 6.8%; transform: scaleX(1.05); opacity: .95; }}
  }}
  @media (max-width: 760px) {{
    .th-stage {{ grid-template-columns: 1fr; padding: 12px; }}
    .th-head-wrap {{ min-height: 260px; }}
    .th-title {{ font-size: 1rem; }}
  }}
</style>
<section id="avatar-stage" class="th-stage{' ' if motion_unlocked else ' motion-locked'}" aria-label="Talking head avatar">
  {avatar_markup}
  <div class="th-panel">
    <div class="th-title">AiEng Avatar</div>
    <div class="th-status">{h(avatar_source)} Local voice source: {h("ready" if audio_ready else "missing")}. {h(motion_note)}</div>
    {audio}
  </div>
</section>"""


def set_action(message: str, kind: str = "neutral") -> None:
    LAST_ACTION["message"] = str(message or "")
    LAST_ACTION["kind"] = str(kind or "neutral")
    LAST_ACTION["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def launch_open_script(run_setup: bool = False) -> str:
    ensure_epic_staging_dirs()
    if not OPEN_SCRIPT.is_file():
        raise FileNotFoundError(f"Open script not found: {OPEN_SCRIPT}")
    command = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(OPEN_SCRIPT),
    ]
    if run_setup:
        command.append("-RunSetup")
    subprocess.Popen(
        command,
        cwd=str(OPEN_SCRIPT.parent),
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    if run_setup:
        return "Running Unreal talking-head setup script."
    return "Opening Unreal talking-head project."


def launch_epic() -> str:
    ensure_epic_staging_dirs()
    launcher = first_existing(LAUNCHER_CANDIDATES)
    if launcher is None:
        raise FileNotFoundError("Epic Games Launcher was not found.")
    subprocess.Popen([str(launcher)], cwd=str(launcher.parent))
    return "Opening Epic Games Launcher."


def launch_nia_project() -> str:
    if not NIA_PROJECT.is_file():
        raise FileNotFoundError(f"Nia project not found: {NIA_PROJECT}")
    editor = first_existing(editor_candidates())
    if editor is None:
        raise FileNotFoundError("UnrealEditor.exe was not found.")
    subprocess.Popen(
        [str(editor), str(NIA_PROJECT)],
        cwd=str(NIA_PROJECT.parent),
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    return "Opening Nia female avatar Unreal project."


def launch_realism_rl() -> str:
    if not RL_SCRIPT.is_file():
        raise FileNotFoundError(f"Realism RL script not found: {RL_SCRIPT}")
    command = [
        sys.executable,
        str(RL_SCRIPT),
        "--render",
        "--width",
        "640",
        "--height",
        "360",
        "--start-frame",
        "0",
        "--end-frame",
        "240",
        "--step",
        "1",
    ]
    subprocess.Popen(
        command,
        cwd=str(RL_SCRIPT.parent),
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    return "Started Unreal frame render + Nemotron realism RL pass for every frame."


def open_project_folder() -> str:
    if not PROJECT_PATH.parent.is_dir():
        raise FileNotFoundError(f"Project folder not found: {PROJECT_PATH.parent}")
    subprocess.Popen(["explorer.exe", str(PROJECT_PATH.parent)])
    return "Opening Unreal project folder."


def realism_status() -> str:
    if not REALISM_JSON.is_file():
        return "no realism score yet"
    try:
        data = json.loads(REALISM_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return f"unreadable realism.json: {exc.__class__.__name__}"
    raw_score = data.get("score", "?")
    try:
        score_value = float(raw_score)
        score = f"{round(score_value if score_value > 100 else score_value * 10)}/1000"
    except (TypeError, ValueError):
        score = f"{raw_score}/1000"
    mode = data.get("mode", "?")
    ts = data.get("ts", "?")
    comment = str(data.get("comment", "")).strip()
    if len(comment) > 160:
        comment = comment[:157] + "..."
    approved = "approved" if data.get("motion_approved") is True else "locked"
    frames = data.get("frames_verified", data.get("iter", "?"))
    return f"score {score} | frames {frames} | motion {approved} | {mode} | {ts} | {comment}"


def motion_is_approved() -> bool:
    if not REALISM_JSON.is_file():
        return False
    try:
        data = json.loads(REALISM_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    failures = data.get("approval_failures")
    return (
        data.get("mode") == "unreal-nemotron-frame-rl"
        and data.get("motion_approved") is True
        and int(data.get("score") or 0) >= MOTION_APPROVAL_MIN_SCORE
        and int(data.get("frames_verified") or data.get("iter") or 0) >= MOTION_APPROVAL_MIN_FRAMES
        and (not isinstance(failures, list) or len(failures) == 0)
    )


def current_status_rows() -> list[tuple[str, object, str]]:
    editor = first_existing(editor_candidates())
    launcher = first_existing(LAUNCHER_CANDIDATES)
    staging_dirs = epic_staging_dirs()
    editor_ready = editor is not None
    rows = [
        ("Project", PROJECT_PATH, file_status(PROJECT_PATH)),
        ("Setup script", SETUP_SCRIPT, file_status(SETUP_SCRIPT)),
        ("Source audio", SOURCE_AUDIO, file_status(SOURCE_AUDIO)),
        ("Female avatar project", NIA_PROJECT, file_status(NIA_PROJECT)),
        ("Female avatar preview", NIA_PREVIEW, file_status(NIA_PREVIEW)),
        ("Female avatar map", NIA_MAP, file_status(NIA_MAP)),
        ("Female avatar sequence", NIA_SEQUENCE, file_status(NIA_SEQUENCE)),
        ("Female avatar mesh", NIA_SKELETAL_MESH, file_status(NIA_SKELETAL_MESH)),
        ("Prepared level", LEVEL_ASSET, file_status(LEVEL_ASSET)),
        ("Level sequence", SEQUENCE_ASSET, file_status(SEQUENCE_ASSET)),
        ("Imported audio", IMPORTED_AUDIO_ASSET, file_status(IMPORTED_AUDIO_ASSET)),
        ("Realism RL script", RL_SCRIPT, file_status(RL_SCRIPT)),
        ("Latest realism", REALISM_JSON, realism_status()),
        ("Unreal Editor", editor or "not found", "ready" if editor_ready else "missing"),
        ("Epic Games Launcher", launcher or "not found", "ready" if launcher else "missing"),
    ]
    for path in staging_dirs:
        rows.append(("Epic staging", path, directory_status(path)))
    rows.append(("Nemotron agent", nemotron_model(), nemotron_status()))
    return rows


def render_status_table() -> str:
    pieces = ['<div class="table-wrap"><table><thead><tr><th>Item</th><th>Path</th><th>Status</th></tr></thead><tbody>']
    for name, path, status in current_status_rows():
        pieces.append(f"<tr><td>{h(name)}</td><td>{h(path)}</td><td>{h(status)}</td></tr>")
    pieces.append("</tbody></table></div>")
    return "\n".join(pieces)


def render_report() -> str:
    src = "http://127.0.0.1:8766/live"
    return f"""
<style>
  .avatar-live-frame {{
    display: block;
    width: 100%;
    min-height: 920px;
    border: 0;
    background: #0b0f14;
  }}
  .avatar-live-fallback {{
    margin-top: 8px;
    color: #b8c7d8;
    font: 13px Arial, Helvetica, sans-serif;
  }}
</style>
<iframe class="avatar-live-frame" src="{h(src)}" title="AiEng Avatar" allow="autoplay; microphone"></iframe>
<div class="avatar-live-fallback">Avatar controller: <a href="http://127.0.0.1:8766/live" target="_blank" rel="noopener">open local live view</a></div>
"""


def render_live_page(public: bool = False) -> str:
    editor = first_existing(editor_candidates())
    staging_dirs = epic_staging_dirs()
    staging_ready = all(path.is_dir() for path in staging_dirs)
    project_ready = PROJECT_PATH.is_file()
    setup_ready = SETUP_SCRIPT.is_file()
    audio_ready = SOURCE_AUDIO.is_file()
    editor_ready = editor is not None

    readiness = "Ready to open in Unreal Engine" if project_ready and setup_ready else "Project files incomplete"
    if not editor_ready:
        readiness = "Install Unreal Engine 5.7 to open the talking head"

    action_kind = h(LAST_ACTION.get("kind") or "neutral")
    action_message = h(LAST_ACTION.get("message") or "")
    action_updated = h(LAST_ACTION.get("updated_at") or "")

    pieces: list[str] = []
    if not public:
        pieces.append(
            '<div class="topline">'
            f'<div>Unreal MetaHuman Talking Head | {h(readiness)}</div>'
            '<div class="toolbar">'
            '<button class="tool" onclick="refreshNow()">Refresh</button>'
            '<button class="tool primary" onclick="runAction(0, \'open_unreal\')">Open Unreal</button>'
            '<button class="tool" onclick="runAction(0, \'open_nia\')">Open Female Avatar</button>'
            '<button class="tool primary" onclick="runAction(0, \'run_realism_rl\')">Run Unreal RL</button>'
            '<button class="tool" onclick="runAction(0, \'run_setup\')">Run Setup</button>'
            '<button class="tool" onclick="runAction(0, \'open_folder\')">Project Folder</button>'
            '<button class="tool" onclick="runAction(0, \'open_launcher\')">Epic Launcher</button>'
            '</div></div>'
        )
        pieces.append(
            f'<div class="action action-{action_kind}">{action_message}'
            f'{(" | " + action_updated) if action_updated else ""}</div>'
        )
    pieces.append(render_talking_head(audio_ready))
    pieces.append(
        '<section class="chat-shell">'
        '<div class="chat-head">'
        '<div><strong>Nia Chat</strong></div>'
        f'<div id="agent-status">{h(nemotron_status())}</div>'
        '</div>'
        f'<div id="chat-log" class="chat-log">{render_chat_messages()}</div>'
        '<form id="chat-form" class="chat-form" autocomplete="off">'
        '<input id="chat-input" name="message" type="text" maxlength="900" placeholder="Message Nia" required>'
        '<button type="submit">Send</button>'
        '<button type="button" id="speak-last">Speak</button>'
        '</form>'
        '</section>'
    )
    if not public:
        pieces.append('<section class="meta">')
        pieces.append("<div>Prepared Unreal project: MetaHuman talking-head scene, portrait lighting, camera, source audio import, and level sequence setup.</div>")
        pieces.append("<div>The Avatar tab now renders the local Nia female Unreal character preview directly from this controller, with voice audio served from D:\\Avatar\\last_audio.wav.</div>")
        pieces.append("</section>")
        pieces.append(render_status_table())
        if staging_dirs and not staging_ready:
            pieces.append('<section class="footer"><div>Epic prerequisite staging was missing. Press Open Unreal or Epic Launcher once to recreate it automatically.</div></section>')
        if not editor_ready:
            pieces.append('<section class="footer"><div>UnrealEditor.exe was not found. Use Epic Launcher to install Unreal Engine 5.7, then refresh this controller.</div></section>')
        elif not audio_ready:
            pieces.append('<section class="footer"><div>No D:\\Avatar\\last_audio.wav found. Add or generate audio there before processing facial animation.</div></section>')
    content = "\n".join(pieces)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AiEng Avatar</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: #0b0f14;
      color: #e6edf3;
      font-family: Arial, Helvetica, sans-serif;
    }}
    .avatar-app {{
      min-height: 100vh;
      padding: 14px;
      background:
        linear-gradient(90deg, rgba(60,230,207,.08), transparent 28%),
        linear-gradient(180deg, #0b0f14 0%, #101821 100%);
    }}
    .avatar-app.public-live {{
      display: grid;
      grid-template-columns: minmax(260px, 340px) minmax(280px, 1fr);
      gap: 12px;
      align-items: stretch;
      padding: 12px;
    }}
    .public-live .th-stage {{
      grid-template-columns: 1fr;
      align-content: start;
      gap: 12px;
      margin: 0;
      min-height: calc(100vh - 24px);
      padding: 12px;
    }}
    .public-live .th-head-wrap {{
      min-height: 330px;
    }}
    .public-live .th-nia-wrap {{
      min-height: 380px;
    }}
    .public-live .chat-shell {{
      display: flex;
      flex-direction: column;
      min-height: calc(100vh - 24px);
      margin: 0;
    }}
    .public-live .chat-log {{
      flex: 1;
      max-height: none;
      min-height: 0;
    }}
    .topline {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      margin-bottom: 6px;
      color: #f3f7fb;
      font-weight: 700;
    }}
    .toolbar {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }}
    button, .tool {{
      border: 1px solid #304259;
      background: #182535;
      color: #f3f7fb;
      min-height: 32px;
      padding: 6px 10px;
      cursor: pointer;
      border-radius: 4px;
      font: inherit;
    }}
    button:hover, .tool:hover {{ background: #22344a; }}
    button:disabled {{ opacity: .55; cursor: wait; }}
    .primary {{
      border-color: #28cab7;
      background: #123c42;
    }}
    .action {{
      margin: 6px 0 12px;
      color: #b8c7d8;
      min-height: 20px;
    }}
    .action-error {{ color: #ffb1a8; }}
    .action-ok {{ color: #91f2d7; }}
    .action-start {{ color: #b8d7ff; }}
    .chat-shell {{
      margin: 12px 0;
      padding: 14px;
      background: #111923;
      border: 1px solid #263447;
    }}
    .chat-head {{
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      gap: 10px;
      margin-bottom: 10px;
      color: #f3f7fb;
    }}
    #agent-status {{
      color: #9fb0c4;
      font-size: .92rem;
    }}
    .chat-log {{
      display: grid;
      gap: 8px;
      max-height: 300px;
      overflow-y: auto;
      padding-right: 4px;
    }}
    .chat-msg {{
      max-width: min(760px, 92%);
      padding: 10px 12px;
      border: 1px solid #263447;
      background: #0e151e;
      border-radius: 6px;
    }}
    .chat-user {{
      justify-self: end;
      background: #123c42;
      border-color: #1e766f;
    }}
    .chat-assistant {{
      justify-self: start;
    }}
    .chat-role {{
      margin-bottom: 4px;
      font-size: .78rem;
      color: #95a8bd;
      text-transform: uppercase;
      letter-spacing: 0;
    }}
    .chat-text {{
      line-height: 1.45;
      color: #f3f7fb;
      overflow-wrap: anywhere;
    }}
    .chat-meta {{
      margin-top: 6px;
      font-size: .75rem;
      color: #7e91a8;
    }}
    .chat-form {{
      display: grid;
      grid-template-columns: minmax(180px, 1fr) auto auto;
      gap: 8px;
      margin-top: 12px;
    }}
    .chat-form input {{
      min-height: 36px;
      border: 1px solid #304259;
      background: #0b1119;
      color: #f3f7fb;
      padding: 7px 10px;
      border-radius: 4px;
      font: inherit;
    }}
    .meta, .footer {{
      margin: 12px 0;
      color: #c7d3e1;
      line-height: 1.45;
    }}
    .table-wrap {{
      overflow-x: auto;
      border: 1px solid #263447;
      background: #0e151e;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      min-width: 760px;
    }}
    th, td {{
      padding: 8px 10px;
      border-bottom: 1px solid #1f2b3a;
      text-align: left;
      vertical-align: top;
      overflow-wrap: anywhere;
    }}
    th {{
      background: #121d2a;
      color: #f3f7fb;
    }}
    @media (max-width: 760px) {{
      .avatar-app {{ padding: 10px; }}
      .avatar-app.public-live {{
        grid-template-columns: minmax(240px, 310px) minmax(250px, 1fr);
        padding: 10px;
      }}
      .chat-form {{ grid-template-columns: 1fr; }}
      .toolbar button {{ flex: 1 1 auto; }}
    }}
    @media (max-width: 640px) {{
      .avatar-app.public-live {{ grid-template-columns: 1fr; }}
      .public-live .th-stage,
      .public-live .chat-shell {{ min-height: auto; }}
      .public-live .chat-log {{ min-height: 260px; }}
    }}
  </style>
</head>
<body>
  <main id="avatar-app" class="avatar-app{' public-live' if public else ''}{'' if motion_is_approved() else ' motion-locked'}">
    {content}
  </main>
  <script>
    const app = document.getElementById('avatar-app');
    const stage = document.getElementById('avatar-stage');
    const chatLog = document.getElementById('chat-log');
    const chatForm = document.getElementById('chat-form');
    const chatInput = document.getElementById('chat-input');
    const agentStatus = document.getElementById('agent-status');
    const speakLastButton = document.getElementById('speak-last');
    const motionUnlocked = {'true' if motion_is_approved() else 'false'};
    let lastAssistantText = '';
    let speakingTimer = 0;

    function escapeHtml(value) {{
      return String(value).replace(/[&<>"']/g, (char) => ({{
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;'
      }}[char]));
    }}

    function setAvatarState(name, active) {{
      app.classList.toggle(name, active);
      if (stage) stage.classList.toggle(name, active);
    }}

    function setSpeaking(active) {{
      active = motionUnlocked && active;
      setAvatarState('is-speaking', active);
      if (!active && speakingTimer) {{
        clearTimeout(speakingTimer);
        speakingTimer = 0;
      }}
    }}

    function setThinking(active) {{
      setAvatarState('is-thinking', motionUnlocked && active);
    }}

    function chooseVoice() {{
      const voices = window.speechSynthesis ? window.speechSynthesis.getVoices() : [];
      return voices.find((voice) => /Jenny|Aria|Zira|Natasha|Samantha|Female|English/i.test(voice.name))
        || voices.find((voice) => /^en[-_]/i.test(voice.lang))
        || voices[0]
        || null;
    }}

    function estimateSpeechMs(text) {{
      return Math.min(16000, Math.max(1800, String(text).split(/\\s+/).length * 360));
    }}

    function speakText(text) {{
      lastAssistantText = text || lastAssistantText;
      if (!lastAssistantText) return;
      if (!('speechSynthesis' in window)) {{
        setSpeaking(true);
        speakingTimer = setTimeout(() => setSpeaking(false), estimateSpeechMs(lastAssistantText));
        return;
      }}
      window.speechSynthesis.cancel();
      const utterance = new SpeechSynthesisUtterance(lastAssistantText);
      const voice = chooseVoice();
      if (voice) utterance.voice = voice;
      utterance.rate = 1;
      utterance.pitch = 1.05;
      utterance.volume = 1;
      utterance.onstart = () => setSpeaking(true);
      utterance.onend = () => setSpeaking(false);
      utterance.onerror = () => setSpeaking(false);
      setTimeout(() => window.speechSynthesis.speak(utterance), 60);
    }}

    function renderMessages(messages) {{
      chatLog.innerHTML = messages.map((message) => {{
        const role = message.role === 'user' ? 'user' : 'assistant';
        const label = role === 'user' ? 'You' : 'Nia';
        const meta = message.provider ? `${{message.provider}} | ${{message.created_at || ''}}` : (message.created_at || '');
        if (role === 'assistant' && message.content) lastAssistantText = message.content;
        return `<article class="chat-msg chat-${{role}}">
          <div class="chat-role">${{label}}</div>
          <div class="chat-text">${{escapeHtml(message.content || '')}}</div>
          <div class="chat-meta">${{escapeHtml(meta)}}</div>
        </article>`;
      }}).join('');
      chatLog.scrollTop = chatLog.scrollHeight;
    }}

    async function loadChatHistory() {{
      const response = await fetch('/api/chat/history', {{ cache: 'no-store' }});
      if (!response.ok) return;
      const payload = await response.json();
      if (payload.messages) renderMessages(payload.messages);
      if (payload.status) agentStatus.textContent = payload.status;
    }}

    async function sendChat(event) {{
      event.preventDefault();
      const message = chatInput.value.trim();
      if (!message) return;
      chatInput.value = '';
      setThinking(true);
      const submitButton = chatForm.querySelector('button[type="submit"]');
      submitButton.disabled = true;
      try {{
        const response = await fetch('/api/chat', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ message }})
        }});
        const payload = await response.json();
        if (!response.ok || !payload.ok) throw new Error(payload.error || response.statusText);
        renderMessages(payload.messages || []);
        if (payload.status) agentStatus.textContent = payload.status;
        speakText(payload.reply || '');
      }} catch (error) {{
        renderMessages([
          ...Array.from(chatLog.querySelectorAll('.chat-msg')).map((node) => ({{
            role: node.classList.contains('chat-user') ? 'user' : 'assistant',
            content: node.querySelector('.chat-text')?.textContent || '',
            created_at: node.querySelector('.chat-meta')?.textContent || ''
          }})),
          {{ role: 'assistant', content: `Chat error: ${{error.message}}`, provider: 'local', created_at: new Date().toLocaleString() }}
        ]);
      }} finally {{
        setThinking(false);
        submitButton.disabled = false;
        chatInput.focus();
      }}
    }}

    async function runAction(store, action) {{
      const body = new URLSearchParams({{ store: String(store), action }});
      const response = await fetch('/action', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/x-www-form-urlencoded' }},
        body
      }});
      if (!response.ok) return;
      location.reload();
    }}

    function refreshNow() {{
      location.reload();
    }}

    const localAudio = document.getElementById('avatar-audio');
    if (localAudio) {{
      localAudio.addEventListener('play', () => setSpeaking(true));
      localAudio.addEventListener('pause', () => setSpeaking(false));
      localAudio.addEventListener('ended', () => setSpeaking(false));
    }}
    if ('speechSynthesis' in window) {{
      window.speechSynthesis.onvoiceschanged = chooseVoice;
    }}
    chatForm.addEventListener('submit', sendChat);
    speakLastButton.addEventListener('click', () => speakText(lastAssistantText));
    loadChatHistory();
  </script>
</body>
</html>"""


def index_html(interval: int, public: bool = False) -> str:
    return render_live_page(public=public)


class AvatarHandler(BaseHTTPRequestHandler):
    server_version = "AvatarTalkingHead/1.0"

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return

    def _send_cors_headers(self) -> None:
        origin = str(self.headers.get("Origin", "") or "").strip()
        allowed = {
            "https://www.aieng.co.za",
            "https://aieng.co.za",
        }
        if (
            origin in allowed
            or origin.startswith("http://localhost:")
            or origin.startswith("http://127.0.0.1:")
        ):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Private-Network", "true")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _send_openai_chat(self, payload: dict[str, object], reply: str) -> None:
        model = str(payload.get("model") or nemotron_model())
        created = int(time.time())
        if payload.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self._send_cors_headers()
            self.end_headers()
            words = reply.split(" ")
            if not words:
                words = [reply]
            for index, word in enumerate(words):
                text = word if index == len(words) - 1 else f"{word} "
                chunk = {
                    "id": f"chatcmpl-nia-{created}",
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": text},
                            "finish_reason": None,
                        }
                    ],
                }
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode("utf-8"))
                self.wfile.flush()
                time.sleep(0.025)
            done = {
                "id": f"chatcmpl-nia-{created}",
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
            self.wfile.write(f"data: {json.dumps(done)}\n\n".encode("utf-8"))
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            return

        response = {
            "id": f"chatcmpl-nia-{created}",
            "object": "chat.completion",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": reply},
                    "finish_reason": "stop",
                }
            ],
        }
        self._send(200, json.dumps(response).encode("utf-8"), "application/json; charset=utf-8")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path in {"", "/"}:
            self._send(200, index_html(15).encode("utf-8"), "text/html; charset=utf-8")
            return
        if parsed.path == "/live":
            self._send(200, index_html(15, public=True).encode("utf-8"), "text/html; charset=utf-8")
            return
        if parsed.path == "/control":
            self._send(200, index_html(15).encode("utf-8"), "text/html; charset=utf-8")
            return
        if parsed.path == "/api/report":
            payload = {
                "html": render_report(),
                "status": f"Avatar updated {datetime.now():%Y-%m-%d %H:%M:%S}",
            }
            self._send(200, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")
            return
        if parsed.path == "/api/chat/history":
            payload = {
                "ok": True,
                "messages": visible_chat_messages(),
                "status": nemotron_status(),
            }
            self._send(200, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")
            return
        if parsed.path == "/v1/models":
            payload = {
                "object": "list",
                "data": [
                    {
                        "id": nemotron_model(),
                        "object": "model",
                        "owned_by": "local-nia-proxy",
                    }
                ],
            }
            self._send(200, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")
            return
        if parsed.path == "/audio/last_audio.wav":
            if not SOURCE_AUDIO.is_file():
                self._send(404, b"Audio not found", "text/plain; charset=utf-8")
                return
            self._send(200, SOURCE_AUDIO.read_bytes(), "audio/wav")
            return
        if parsed.path == "/image/nia.png":
            if not NIA_PREVIEW.is_file():
                self._send(404, b"Nia preview not found", "text/plain; charset=utf-8")
                return
            self._send(200, NIA_PREVIEW.read_bytes(), "image/png")
            return
        self._send(404, b"Not found", "text/plain; charset=utf-8")

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/v1/chat/completions":
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length).decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw or "{}")
                message = latest_user_message(payload.get("messages"))
                result = handle_chat(message)
                self._send_openai_chat(payload, str(result.get("reply", "")))
            except Exception as exc:  # noqa: BLE001
                body = json.dumps({"error": {"message": f"{exc.__class__.__name__}: {exc}"}}).encode("utf-8")
                self._send(500, body, "application/json; charset=utf-8")
            return

        if parsed.path == "/api/chat":
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length).decode("utf-8", errors="replace")
            try:
                if "application/json" in str(self.headers.get("Content-Type", "")).lower():
                    data = json.loads(raw or "{}")
                    message = str(data.get("message", ""))
                else:
                    data = parse_qs(raw)
                    message = str((data.get("message") or [""])[0])
                payload = handle_chat(message)
                body = json.dumps(payload).encode("utf-8")
                self._send(200, body, "application/json; charset=utf-8")
            except Exception as exc:  # noqa: BLE001
                body = json.dumps({"ok": False, "error": f"{exc.__class__.__name__}: {exc}"}).encode("utf-8")
                self._send(500, body, "application/json; charset=utf-8")
            return

        if parsed.path != "/action":
            self._send(404, b"Not found", "text/plain; charset=utf-8")
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        data = parse_qs(raw)
        action = str((data.get("action") or [""])[0]).strip().lower()
        try:
            if action == "open_unreal":
                message = launch_open_script(run_setup=False)
                set_action(message, "start")
            elif action == "run_setup":
                message = launch_open_script(run_setup=True)
                set_action(message, "start")
            elif action == "open_launcher":
                message = launch_epic()
                set_action(message, "start")
            elif action == "open_nia":
                message = launch_nia_project()
                set_action(message, "start")
            elif action == "run_realism_rl":
                message = launch_realism_rl()
                set_action(message, "start")
            elif action == "open_folder":
                message = open_project_folder()
                set_action(message, "ok")
            else:
                raise ValueError(f"unknown action: {action}")
            body = json.dumps({"ok": True, "message": message}).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
        except Exception as exc:  # noqa: BLE001
            message = f"{exc.__class__.__name__}: {exc}"
            set_action(message, "error")
            self._send(500, message.encode("utf-8", errors="replace"), "text/plain; charset=utf-8")


def run_server(host: str, port: int) -> int:
    server = ThreadingHTTPServer((host, port), AvatarHandler)
    url_host = "localhost" if host in {"0.0.0.0", "::"} else host
    print(f"Avatar talking-head controller running at http://{url_host}:{port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
        return 0
    finally:
        server.server_close()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local Avatar talking-head controller.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host.")
    parser.add_argument("--port", type=int, default=8766, help="Bind port.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return run_server(str(args.host), int(args.port))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
