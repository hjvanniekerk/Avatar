from __future__ import annotations

import json
from pathlib import Path

import unreal


OUT_REPORT = Path(r"C:\Users\hjvan\OneDrive\_Codex\Avatar\public_html\avatar\assets\khaos-assets-inspect.json")
ASSETS = [
    "/Game/KhaosMDF322/Examples/Lilly/Clothing/Lilly_Outfits",
    "/Game/KhaosMDF322/Examples/Lilly/Clothing/Lilly_Outfits_SimMesh",
    "/Game/KhaosMDF322/Examples/Lilly/Clothing/CA_Lilly_Outfits",
    "/Game/KhaosMDF322/Examples/Lilly/Clothing/DF_Lilly_Outfits",
    "/Game/KhaosMDF322/ClothSim/BodyShapes/SK_KhaosMDF322S_Size6_CombinedSkelMesh",
    "/Game/KhaosMDF322/Examples/Lilly/Grooms/Hair_S_BobLayered_CardsMesh_Group0_LOD1",
    "/Game/KhaosMDF322/Examples/Lilly/Grooms/Hair_S_BobLayered_CardsMesh_Group0_LOD3",
    "/Game/KhaosMDF322/Examples/Lilly/Grooms/Hair_S_BobLayered_Helmet_LOD5",
]


def prop_path(obj, name: str) -> str:
    try:
        value = obj.get_editor_property(name)
        if value:
            return value.get_path_name()
    except Exception:
        pass
    return ""


def main() -> None:
    rows = []
    for asset_path in ASSETS:
        asset = unreal.EditorAssetLibrary.load_asset(asset_path)
        data = unreal.EditorAssetLibrary.find_asset_data(asset_path)
        row = {
            "path": asset_path,
            "loaded": asset is not None,
            "class": asset.get_class().get_name() if asset else "",
            "asset_class": str(data.asset_class_path.asset_name) if data else "",
            "skeletal_mesh": prop_path(asset, "skeletal_mesh") if asset else "",
            "static_mesh": prop_path(asset, "static_mesh") if asset else "",
        }
        rows.append(row)
    OUT_REPORT.write_text(json.dumps({"assets": rows}, indent=2) + "\n", encoding="utf-8")
    unreal.SystemLibrary.quit_editor()


main()
