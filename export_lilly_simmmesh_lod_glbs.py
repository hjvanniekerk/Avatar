from __future__ import annotations

import json
from pathlib import Path

import unreal


OUT_DIR = Path(
    r"C:\Users\hjvan\OneDrive\_Codex\Avatar\public_html\avatar\assets"
)
ASSET_PATH = "/Game/KhaosMDF322/Examples/Lilly/Clothing/Lilly_Outfits_SimMesh"


def make_options(lod: int) -> unreal.GLTFExportOptions:
    options = unreal.GLTFExportOptions()
    for name, value in (
        ("export_uniform_scale", 0.01),
        ("default_level_of_detail", lod),
        ("export_source_model", False),
        ("adjust_normalmaps", True),
        ("export_vertex_colors", False),
        ("export_vertex_skin_weights", False),
        ("export_morph_targets", False),
        ("export_animation_sequences", False),
        ("make_skinned_meshes_root", False),
        ("use_mesh_quantization", False),
        ("texture_image_format", unreal.GLTFTextureImageFormat.PNG),
        ("bake_material_inputs", unreal.GLTFMaterialBakeMode.USE_MESH_DATA),
        ("export_material_variants", unreal.GLTFMaterialVariantMode.NONE),
    ):
        try:
            options.set_editor_property(name, value)
        except Exception as exc:
            unreal.log(f"[LillySimMeshLODExport] skip option {name}: {exc}")
    return options


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    asset = unreal.EditorAssetLibrary.load_asset(ASSET_PATH)
    rows = []
    for lod in range(8):
        out_glb = OUT_DIR / f"metahuman-outfit-sim-lod{lod}.glb"
        messages = unreal.GLTFExporter.export_to_gltf(asset, str(out_glb), make_options(lod), set())
        rows.append(
            {
                "lod": lod,
                "asset": ASSET_PATH,
                "ok": out_glb.exists() and out_glb.stat().st_size > 1024,
                "glb": str(out_glb),
                "bytes": out_glb.stat().st_size if out_glb.exists() else 0,
                "messages": str(messages)[:1000],
            }
        )
    (OUT_DIR / "metahuman-outfit-sim-lods.json").write_text(
        json.dumps({"asset": ASSET_PATH, "exports": rows}, indent=2) + "\n",
        encoding="utf-8",
    )
    unreal.SystemLibrary.quit_editor()


main()
