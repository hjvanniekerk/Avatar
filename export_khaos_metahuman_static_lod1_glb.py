from __future__ import annotations

import json
from pathlib import Path

import unreal


LEVEL = "/Game/KhaosMDF322/Maps/Examples"
OUT_DIR = Path(r"C:\Users\hjvan\OneDrive\_Codex\Avatar\public_html\avatar\assets")
OUT_GLB = OUT_DIR / "metahuman-lod1-static.glb"
OUT_REPORT = OUT_DIR / "metahuman-lod1-static.json"
ACTOR_CANDIDATES = ("BP_Lilly", "BP_Holly")


def find_actor():
    actors = list(unreal.EditorLevelLibrary.get_all_level_actors())
    for candidate in ACTOR_CANDIDATES:
        for actor in actors:
            if actor.get_actor_label() == candidate or actor.get_class().get_name() == f"{candidate}_C":
                return actor
    return None


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    unreal.EditorLevelLibrary.load_level(LEVEL)
    actor = find_actor()
    if not actor:
        OUT_REPORT.write_text(json.dumps({"ok": False, "error": "actor_not_found"}, indent=2) + "\n", encoding="utf-8")
        unreal.SystemLibrary.quit_editor()
        return
    options = unreal.GLTFExportOptions()
    for name, value in (
        ("export_uniform_scale", 0.01),
        ("default_level_of_detail", 1),
        ("export_source_model", False),
        ("adjust_normalmaps", True),
        ("export_vertex_colors", False),
        ("export_vertex_skin_weights", False),
        ("export_morph_targets", False),
        ("export_animation_sequences", False),
        ("make_skinned_meshes_root", False),
        ("use_mesh_quantization", False),
        ("texture_image_format", unreal.GLTFTextureImageFormat.PNG),
        ("bake_material_inputs", unreal.GLTFMaterialBakeMode.DISABLED),
        ("export_material_variants", unreal.GLTFMaterialVariantMode.NONE),
    ):
        try:
            options.set_editor_property(name, value)
        except Exception as exc:
            unreal.log(f"[MetaHumanLOD1StaticGLBExport] skip option {name}: {exc}")
    world = unreal.EditorLevelLibrary.get_editor_world()
    messages = unreal.GLTFExporter.export_to_gltf(world, str(OUT_GLB), options, {actor})
    result = {
        "ok": OUT_GLB.exists() and OUT_GLB.stat().st_size > 1024,
        "actor": actor.get_actor_label(),
        "actor_class": actor.get_class().get_name(),
        "glb": str(OUT_GLB),
        "bytes": OUT_GLB.stat().st_size if OUT_GLB.exists() else 0,
        "default_level_of_detail": 1,
        "export_vertex_skin_weights": False,
        "messages": str(messages)[:2000],
    }
    OUT_REPORT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    unreal.SystemLibrary.quit_editor()


main()
