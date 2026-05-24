from __future__ import annotations

import json
from pathlib import Path

import unreal


LEVEL = "/Game/KhaosMDF322/Maps/Examples"
OUT_DIR = Path(r"C:\Users\hjvan\OneDrive\_Codex\Avatar\public_html\avatar\assets")
OUT_GLB = OUT_DIR / "metahuman-current.glb"
OUT_REPORT = OUT_DIR / "metahuman-current.json"
ACTOR_CANDIDATES = ("BP_Lilly", "BP_Holly")


def asset_path(obj) -> str:
    if not obj:
        return ""
    try:
        return obj.get_path_name()
    except Exception:
        return str(obj)


def actor_mesh_components(actor) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for component in actor.get_components_by_class(unreal.ActorComponent):
        cls = component.get_class().get_name()
        item = {"name": component.get_name(), "class": cls, "asset": ""}
        for prop in ("skeletal_mesh_asset", "skeletal_mesh", "static_mesh", "groom_asset", "binding_asset"):
            try:
                value = component.get_editor_property(prop)
                if value:
                    item["asset"] = asset_path(value)
                    break
            except Exception:
                pass
        if item["asset"] or any(token in cls.lower() for token in ("mesh", "groom", "cloth")):
            rows.append(item)
    return rows


def find_real_actor():
    actors = list(unreal.EditorLevelLibrary.get_all_level_actors())
    for candidate in ACTOR_CANDIDATES:
        for actor in actors:
            if actor.get_actor_label() == candidate or actor.get_class().get_name() == f"{candidate}_C":
                return actor
    return None


def export_actor(actor) -> dict[str, object]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    options = unreal.GLTFExportOptions()
    for name, value in (
        ("export_uniform_scale", 0.01),
        ("adjust_normalmaps", True),
        ("export_vertex_colors", False),
        ("export_vertex_skin_weights", True),
        ("export_morph_targets", False),
        ("export_animation_sequences", False),
        ("make_skinned_meshes_root", True),
        ("use_mesh_quantization", False),
        ("texture_image_format", unreal.GLTFTextureImageFormat.PNG),
        ("bake_material_inputs", unreal.GLTFMaterialBakeMode.USE_MESH_DATA),
        ("export_material_variants", unreal.GLTFMaterialVariantMode.NONE),
    ):
        try:
            options.set_editor_property(name, value)
        except Exception as exc:
            unreal.log(f"[MetaHumanGLBExport] skip option {name}: {exc}")
    selected = {actor}
    world = unreal.EditorLevelLibrary.get_editor_world()
    messages = unreal.GLTFExporter.export_to_gltf(world, str(OUT_GLB), options, selected)
    ok = OUT_GLB.exists() and OUT_GLB.stat().st_size > 1024
    return {
        "ok": ok,
        "actor": actor.get_actor_label(),
        "actor_class": actor.get_class().get_name(),
        "glb": str(OUT_GLB),
        "bytes": OUT_GLB.stat().st_size if OUT_GLB.exists() else 0,
        "components": actor_mesh_components(actor),
        "messages": str(messages)[:2000],
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    unreal.EditorLevelLibrary.load_level(LEVEL)
    actor = find_real_actor()
    if not actor:
        result = {"ok": False, "error": "No BP_Lilly/BP_Holly actor found in Khaos examples level", "level": LEVEL}
    else:
        result = export_actor(actor)
        result["level"] = LEVEL
    OUT_REPORT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    unreal.log(f"[MetaHumanGLBExport] wrote {OUT_REPORT}")
    unreal.SystemLibrary.quit_editor()


main()
