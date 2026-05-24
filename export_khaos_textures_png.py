from __future__ import annotations

import json
from pathlib import Path

import unreal


OUT_DIR = Path(
    r"C:\Users\hjvan\OneDrive\_Codex\Avatar\public_html\avatar\assets\textures"
)
REPORT = OUT_DIR / "khaos-textures-export.json"

TEXTURES = {
    "body_bc": "/Game/KhaosMDF322/Examples/Lilly/Body/Baked/T_Body_BC_VT",
    "body_n": "/Game/KhaosMDF322/Examples/Lilly/Body/Baked/T_Body_N_VT",
    "body_srmf": "/Game/KhaosMDF322/Examples/Lilly/Body/Baked/T_Body_SRMF_VT",
    "head_lod1_bc": "/Game/KhaosMDF322/Examples/Lilly/Face/Baked/T_Head_LOD1_BC_VT",
    "head_lod1_n": "/Game/KhaosMDF322/Examples/Lilly/Face/Baked/T_Head_LOD1_N_VT",
    "head_lod1_srmf": "/Game/KhaosMDF322/Examples/Lilly/Face/Baked/T_Head_LOD1_SRMF_VT",
    "eye_l_bc": "/Game/KhaosMDF322/Examples/Lilly/Face/Baked/T_EyeIrisL_BC",
    "eye_r_bc": "/Game/KhaosMDF322/Examples/Lilly/Face/Baked/T_EyeIrisR_BC",
    "sclera_l_bc": "/Game/KhaosMDF322/Examples/Lilly/Face/Baked/T_EyeScleraL_BC",
    "sclera_r_bc": "/Game/KhaosMDF322/Examples/Lilly/Face/Baked/T_EyeScleraR_BC",
    "teeth_bc": "/Game/KhaosMDF322/Examples/Lilly/Face/Baked/T_Teeth_BC",
    "outfit_bc": "/Game/KhaosMDF322/Textures/T_KhaosMDF322_Size6_BC",
    "outfit_n": "/Game/KhaosMDF322/Textures/T_KhaosMDF322_Size6_N",
    "outfit_srmd": "/Game/KhaosMDF322/Textures/T_KhaosMDF322_Size6_SRMD",
    "hair_cards_attr": "/Game/KhaosMDF322/Examples/Lilly/Grooms/Textures/Hair_S_BobLayered_CardsAtlas_Attribute",
    "hair_cards_rootuv": "/Game/KhaosMDF322/Examples/Lilly/Grooms/Textures/Hair_S_BobLayered_RootUVSeedCoverage",
    "hair_tex_0": "/Game/KhaosMDF322/Examples/Lilly/Grooms/Textures/Texture2D_0",
    "hair_tex_1": "/Game/KhaosMDF322/Examples/Lilly/Grooms/Textures/Texture2D_1",
}


def export_texture(slug: str, asset_path: str) -> dict[str, object]:
    asset = unreal.EditorAssetLibrary.load_asset(asset_path)
    row = {
        "slug": slug,
        "asset": asset_path,
        "class": asset.get_class().get_name() if asset else "",
        "ok": False,
        "png": "",
        "bytes": 0,
        "error": "",
    }
    if not asset:
        row["error"] = "load_failed"
        return row

    out_file = OUT_DIR / f"{slug}.png"
    task = unreal.AssetExportTask()
    task.set_editor_property("object", asset)
    task.set_editor_property("filename", str(out_file))
    task.set_editor_property("automated", True)
    task.set_editor_property("replace_identical", True)
    task.set_editor_property("prompt", False)
    task.set_editor_property("exporter", unreal.TextureExporterPNG())
    try:
        ok = unreal.Exporter.run_asset_export_task(task)
    except Exception as exc:
        row["error"] = str(exc)
        ok = False
    row["ok"] = bool(ok and out_file.exists() and out_file.stat().st_size > 64)
    row["png"] = str(out_file)
    row["bytes"] = out_file.stat().st_size if out_file.exists() else 0
    return row


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = [export_texture(slug, path) for slug, path in TEXTURES.items()]
    REPORT.write_text(json.dumps({"textures": rows}, indent=2) + "\n", encoding="utf-8")
    unreal.SystemLibrary.quit_editor()


main()
