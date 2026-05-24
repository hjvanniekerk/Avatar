#!/usr/bin/env python3
from __future__ import annotations

import json
import struct
from copy import deepcopy
from pathlib import Path


ASSET_DIR = Path("public_html/avatar/assets")
BASE_GLB = ASSET_DIR / "metahuman-sheena-match.glb"
OUTPUT_GLB = ASSET_DIR / "metahuman-sheena-match-morph.glb"
OUTPUT_GLTF = ASSET_DIR / "metahuman-sheena-match-morph.gltf"
OUTPUT_BIN = ASSET_DIR / "metahuman-sheena-match-morph.bin"
OUTPUT_TEXTURE_DIR = ASSET_DIR / "metahuman-sheena-match-morph-textures"
POSES = [
    ("look-left", ASSET_DIR / "metahuman-sheena-match-pose-look-left.glb"),
    ("look-right", ASSET_DIR / "metahuman-sheena-match-pose-look-right.glb"),
    ("look-up", ASSET_DIR / "metahuman-sheena-match-pose-look-up.glb"),
    ("look-down", ASSET_DIR / "metahuman-sheena-match-pose-look-down.glb"),
    ("arms-shoulder", ASSET_DIR / "metahuman-sheena-match-pose-arms-shoulder.glb"),
]

JSON_CHUNK = 0x4E4F534A
BIN_CHUNK = 0x004E4942
ARRAY_BUFFER = 34962
FLOAT = 5126


def align4(value: int) -> int:
    return (value + 3) & ~3


def read_glb(path: Path) -> tuple[dict, bytearray]:
    data = path.read_bytes()
    magic, version, length = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF" or version != 2 or length != len(data):
        raise ValueError(f"{path} is not a glTF 2.0 GLB")
    offset = 12
    gltf: dict | None = None
    binary = bytearray()
    while offset + 8 <= len(data):
        chunk_len, chunk_type = struct.unpack_from("<II", data, offset)
        offset += 8
        chunk = data[offset : offset + chunk_len]
        offset += chunk_len
        if chunk_type == JSON_CHUNK:
            gltf = json.loads(chunk.decode("utf-8"))
        elif chunk_type == BIN_CHUNK:
            binary = bytearray(chunk)
    if gltf is None:
        raise ValueError(f"{path} has no JSON chunk")
    return gltf, binary


def write_glb(path: Path, gltf: dict, binary: bytearray) -> None:
    json_bytes = json.dumps(gltf, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    json_pad = align4(len(json_bytes)) - len(json_bytes)
    bin_pad = align4(len(binary)) - len(binary)
    json_chunk = json_bytes + (b" " * json_pad)
    bin_chunk = bytes(binary) + (b"\x00" * bin_pad)
    total = 12 + 8 + len(json_chunk) + 8 + len(bin_chunk)
    out = bytearray()
    out += struct.pack("<4sII", b"glTF", 2, total)
    out += struct.pack("<II", len(json_chunk), JSON_CHUNK)
    out += json_chunk
    out += struct.pack("<II", len(bin_chunk), BIN_CHUNK)
    out += bin_chunk
    path.write_bytes(out)


def safe_asset_name(value: str, fallback: str) -> str:
    text = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in str(value or "")).strip("_")
    return text or fallback


def write_external_gltf(path: Path, bin_path: Path, texture_dir: Path, gltf: dict, binary: bytearray) -> list[str]:
    external = deepcopy(gltf)
    texture_dir.mkdir(parents=True, exist_ok=True)
    texture_paths: list[str] = []
    for index, image in enumerate(external.get("images", [])):
        view_index = image.get("bufferView")
        if view_index is None:
            continue
        view = external["bufferViews"][view_index]
        offset = int(view.get("byteOffset") or 0)
        length = int(view.get("byteLength") or 0)
        mime = str(image.get("mimeType") or "image/png")
        ext = ".jpg" if "jpeg" in mime.lower() or "jpg" in mime.lower() else ".png"
        filename = safe_asset_name(str(image.get("name") or f"texture_{index}"), f"texture_{index}") + ext
        target = texture_dir / filename
        target.write_bytes(bytes(binary[offset : offset + length]))
        image["uri"] = texture_dir.name + "/" + filename
        image.pop("bufferView", None)
        texture_paths.append(str(target))
    external["buffers"][0]["uri"] = bin_path.name
    external["buffers"][0]["byteLength"] = len(binary)
    bin_path.write_bytes(bytes(binary))
    path.write_text(json.dumps(external, indent=2, ensure_ascii=False), encoding="utf-8")
    return texture_paths


def accessor_components(accessor: dict) -> int:
    return {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}[accessor["type"]]


def read_float_accessor(gltf: dict, binary: bytes, accessor_index: int) -> list[tuple[float, ...]]:
    accessor = gltf["accessors"][accessor_index]
    if accessor.get("componentType") != FLOAT:
        raise ValueError(f"accessor {accessor_index} is not FLOAT")
    count = int(accessor["count"])
    comps = accessor_components(accessor)
    view = gltf["bufferViews"][accessor["bufferView"]]
    stride = int(view.get("byteStride") or comps * 4)
    start = int(view.get("byteOffset") or 0) + int(accessor.get("byteOffset") or 0)
    rows = []
    for i in range(count):
        at = start + i * stride
        rows.append(struct.unpack_from("<" + "f" * comps, binary, at))
    return rows


def append_float_accessor(
    gltf: dict,
    binary: bytearray,
    values: list[tuple[float, ...]] | list[float],
    accessor_type: str,
    target: int | None = ARRAY_BUFFER,
) -> int:
    if not values:
        raise ValueError("cannot append an empty accessor")
    if isinstance(values[0], tuple):
        flat = [float(component) for row in values for component in row]  # type: ignore[index]
        count = len(values)
        comps = len(values[0])  # type: ignore[arg-type]
    else:
        flat = [float(v) for v in values]  # type: ignore[arg-type]
        count = len(flat)
        comps = 1
    expected = accessor_components({"type": accessor_type})
    if comps != expected:
        raise ValueError(f"{accessor_type} expects {expected} components, got {comps}")
    while len(binary) % 4:
        binary.append(0)
    byte_offset = len(binary)
    packed = struct.pack("<" + "f" * len(flat), *flat)
    binary.extend(packed)
    buffer_view = {"buffer": 0, "byteOffset": byte_offset, "byteLength": len(packed)}
    if target is not None:
        buffer_view["target"] = target
    gltf.setdefault("bufferViews", []).append(buffer_view)
    accessor = {
        "bufferView": len(gltf["bufferViews"]) - 1,
        "componentType": FLOAT,
        "count": count,
        "type": accessor_type,
    }
    if accessor_type != "SCALAR":
        mins = []
        maxs = []
        for c in range(comps):
            vals = flat[c::comps]
            mins.append(min(vals))
            maxs.append(max(vals))
        accessor["min"] = mins
        accessor["max"] = maxs
    else:
        accessor["min"] = [min(flat)]
        accessor["max"] = [max(flat)]
    gltf.setdefault("accessors", []).append(accessor)
    return len(gltf["accessors"]) - 1


def primitive_position_accessor(gltf: dict, mesh_index: int, primitive_index: int) -> int:
    primitive = gltf["meshes"][mesh_index]["primitives"][primitive_index]
    return primitive["attributes"]["POSITION"]


def mesh_node_indices(gltf: dict) -> list[int]:
    return [i for i, node in enumerate(gltf.get("nodes", [])) if node.get("mesh") is not None]


def reparent_detached_dress_extension(gltf: dict) -> None:
    nodes = gltf.get("nodes", [])
    parent_index = next((i for i, n in enumerate(nodes) if n.get("name") == "BP_Lilly"), None)
    dress_index = next((i for i, n in enumerate(nodes) if "Dress Extension" in str(n.get("name", ""))), None)
    if parent_index is None or dress_index is None or parent_index == dress_index:
        return
    parent = nodes[parent_index]
    children = list(parent.get("children", []))
    if dress_index not in children:
        children.append(dress_index)
    parent["children"] = children
    for scene in gltf.get("scenes", []):
        scene_nodes = list(scene.get("nodes", []))
        scene["nodes"] = [node for node in scene_nodes if node != dress_index]


def main() -> int:
    base_gltf, base_bin = read_glb(BASE_GLB)
    pose_data = [(name, *read_glb(path)) for name, path in POSES]
    out_gltf = deepcopy(base_gltf)
    out_bin = bytearray(base_bin)
    target_names = [name for name, *_ in pose_data]
    pose_delta_max: dict[str, float] = {}

    for mesh_index, mesh in enumerate(out_gltf.get("meshes", [])):
        mesh.setdefault("weights", [0.0] * len(target_names))
        extras = mesh.setdefault("extras", {})
        if isinstance(extras, dict):
            extras["targetNames"] = target_names
        for primitive_index, primitive in enumerate(mesh.get("primitives", [])):
            targets = []
            base_pos_idx = primitive_position_accessor(base_gltf, mesh_index, primitive_index)
            base_positions = read_float_accessor(base_gltf, base_bin, base_pos_idx)
            for pose_name, pose_gltf, pose_bin in pose_data:
                pose_pos_idx = primitive_position_accessor(pose_gltf, mesh_index, primitive_index)
                pose_positions = read_float_accessor(pose_gltf, pose_bin, pose_pos_idx)
                if len(base_positions) != len(pose_positions):
                    raise ValueError(
                        f"{pose_name} mesh {mesh_index} primitive {primitive_index} vertex count changed"
                    )
                deltas = []
                max_delta = pose_delta_max.get(pose_name, 0.0)
                for base_row, pose_row in zip(base_positions, pose_positions):
                    delta = tuple(float(p) - float(b) for b, p in zip(base_row, pose_row))
                    deltas.append(delta)
                    max_delta = max(max_delta, max(abs(v) for v in delta))
                pose_delta_max[pose_name] = max_delta
                accessor_index = append_float_accessor(out_gltf, out_bin, deltas, "VEC3", ARRAY_BUFFER)
                targets.append({"POSITION": accessor_index})
            primitive["targets"] = targets

    key_times = append_float_accessor(out_gltf, out_bin, [0.0, 1.0], "SCALAR", None)
    animated_nodes = mesh_node_indices(out_gltf)
    animations = []
    all_pose_names = ["default"] + target_names
    for pose_name in all_pose_names:
        weights = [0.0] * len(target_names)
        if pose_name != "default":
            weights[target_names.index(pose_name)] = 1.0
        output_values = weights + weights
        output = append_float_accessor(out_gltf, out_bin, output_values, "SCALAR", None)
        animation = {
            "name": pose_name,
            "samplers": [{"input": key_times, "output": output, "interpolation": "STEP"}],
            "channels": [
                {"sampler": 0, "target": {"node": node_index, "path": "weights"}}
                for node_index in animated_nodes
            ],
        }
        animations.append(animation)
    out_gltf["animations"] = animations
    reparent_detached_dress_extension(out_gltf)
    out_gltf["asset"] = dict(out_gltf.get("asset", {}), generator="Codex real 3D MetaHuman morph target builder")
    out_gltf["buffers"][0]["byteLength"] = len(out_bin)
    write_glb(OUTPUT_GLB, out_gltf, out_bin)
    texture_paths = write_external_gltf(OUTPUT_GLTF, OUTPUT_BIN, OUTPUT_TEXTURE_DIR, out_gltf, out_bin)
    print(
        json.dumps(
            {
                "output": str(OUTPUT_GLB),
                "external_gltf": str(OUTPUT_GLTF),
                "external_bin": str(OUTPUT_BIN),
                "external_textures": texture_paths,
                "bytes": OUTPUT_GLB.stat().st_size,
                "poses": target_names,
                "animations": all_pose_names,
                "animated_nodes": animated_nodes,
                "max_vertex_delta_by_pose_m": pose_delta_max,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
