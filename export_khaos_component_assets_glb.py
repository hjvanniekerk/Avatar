from __future__ import annotations

import json
from pathlib import Path

import unreal


OUT_DIR = Path(r"C:\Users\hjvan\OneDrive\_Codex\Avatar\public_html\avatar\assets")
EXPORTS = [
    ("outfit-lod1", "/Game/KhaosMDF322/Examples/Lilly/Clothing/Lilly_Outfits", 1),
    ("hair-cards-lod1", "/Game/KhaosMDF322/Examples/Lilly/Grooms/Hair_S_BobLayered_CardsMesh_Group0_LOD1", 0),
    ("hair-helmet-lod5", "/Game/KhaosMDF322/Examples/Lilly/Grooms/Hair_S_BobLayered_Helmet_LOD5", 0),
]


def export_asset(slug: str, asset_path: str, lod: int) -> dict[str, object]:
    asset = unreal.EditorAssetLibrary.load_asset(asset_path)
    out_glb = OUT_DIR / f"metahuman-{slug}.glb"
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
        ("bake_material_inputs", unreal.GLTFMaterialBakeMode.DISABLED),
        ("export_material_variants", unreal.GLTFMaterialVariantMode.NONE),
    ):
        try:
            options.set_editor_property(name, value)
        except Exception as exc:
            unreal.log(f"[KhaosComponentExport] skip option {name}: {exc}")
    messages = unreal.GLTFExporter.export_to_gltf(asset, str(out_glb), options, set())
    return {
        "slug": slug,
        "asset": asset_path,
        "class": asset.get_class().get_name() if asset else "",
        "lod": lod,
        "ok": out_glb.exists() and out_glb.stat().st_size > 1024,
        "glb": str(out_glb),
        "bytes": out_glb.stat().st_size if out_glb.exists() else 0,
        "messages": str(messages)[:1000],
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = [export_asset(slug, asset_path, lod) for slug, asset_path, lod in EXPORTS]
    (OUT_DIR / "metahuman-components.json").write_text(json.dumps({"exports": rows}, indent=2) + "\n", encoding="utf-8")
    unreal.SystemLibrary.quit_editor()


main()
