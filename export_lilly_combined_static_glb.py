from __future__ import annotations

import json
from pathlib import Path

import unreal


OUT_DIR = Path(r"C:\Users\hjvan\OneDrive\_Codex\Avatar\public_html\avatar\assets")
OUT_GLB = OUT_DIR / "metahuman-combined-static.glb"
OUT_REPORT = OUT_DIR / "metahuman-combined-static.json"
ASSET = "/Game/KhaosMDF322/Examples/Lilly/Body/SKM_Lilly_CombinedSkelMesh"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    asset = unreal.EditorAssetLibrary.load_asset(ASSET)
    options = unreal.GLTFExportOptions()
    for name, value in (
        ("export_uniform_scale", 0.01),
        ("default_level_of_detail", 0),
        ("export_source_model", False),
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
            unreal.log(f"[LillyCombinedStaticGLBExport] skip option {name}: {exc}")
    messages = unreal.GLTFExporter.export_to_gltf(asset, str(OUT_GLB), options, set())
    result = {
        "ok": OUT_GLB.exists() and OUT_GLB.stat().st_size > 1024,
        "asset": ASSET,
        "glb": str(OUT_GLB),
        "bytes": OUT_GLB.stat().st_size if OUT_GLB.exists() else 0,
        "messages": str(messages)[:2000],
    }
    OUT_REPORT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    unreal.SystemLibrary.quit_editor()


main()
