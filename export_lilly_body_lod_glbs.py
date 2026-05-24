from __future__ import annotations

import json
from pathlib import Path

import unreal


OUT_DIR = Path(r"C:\Users\hjvan\OneDrive\_Codex\Avatar\public_html\avatar\assets")
ASSET = "/Game/KhaosMDF322/Examples/Lilly/Body/SKM_Lilly_BodyMesh"


def export_lod(asset, lod: int) -> dict[str, object]:
    out_glb = OUT_DIR / f"metahuman-body-lod{lod}.glb"
    options = unreal.GLTFExportOptions()
    for name, value in (
        ("export_uniform_scale", 0.01),
        ("default_level_of_detail", lod),
        ("export_source_model", False),
        ("export_vertex_skin_weights", False),
        ("export_morph_targets", False),
        ("export_animation_sequences", False),
        ("make_skinned_meshes_root", False),
        ("use_mesh_quantization", False),
        ("texture_image_format", unreal.GLTFTextureImageFormat.PNG),
        ("bake_material_inputs", unreal.GLTFMaterialBakeMode.DISABLED),
    ):
        try:
            options.set_editor_property(name, value)
        except Exception as exc:
            unreal.log(f"[LillyBodyLODGLBExport] skip option {name}: {exc}")
    messages = unreal.GLTFExporter.export_to_gltf(asset, str(out_glb), options, set())
    return {
        "lod": lod,
        "ok": out_glb.exists() and out_glb.stat().st_size > 1024,
        "glb": str(out_glb),
        "bytes": out_glb.stat().st_size if out_glb.exists() else 0,
        "messages": str(messages)[:500],
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    asset = unreal.EditorAssetLibrary.load_asset(ASSET)
    rows = [export_lod(asset, lod) for lod in range(0, 8)]
    report = {
        "ok": any(row["ok"] for row in rows),
        "asset": ASSET,
        "exports": rows,
    }
    (OUT_DIR / "metahuman-body-lods.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    unreal.SystemLibrary.quit_editor()


main()
