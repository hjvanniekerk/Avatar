from __future__ import annotations

import json
from pathlib import Path

import unreal


OUT = Path(
    r"C:\Users\hjvan\OneDrive\_Codex\Avatar\public_html\avatar\assets\khaos-materials-inspect.json"
)
ASSETS = [
    "/Game/KhaosMDF322/Examples/Lilly/Body/Materials/MI_Body_Baked_VT",
    "/Game/KhaosMDF322/Examples/Lilly/Face/Materials/MI_Face_Skin_Baked_LOD1_VT",
    "/Game/KhaosMDF322/Examples/Lilly/Face/Materials/MI_EyeL_Baked",
    "/Game/KhaosMDF322/Examples/Lilly/Face/Materials/MI_EyeR_Baked",
    "/Game/KhaosMDF322/Examples/Lilly/Face/Materials/MI_Teeth_Baked",
    "/Game/KhaosMDF322/Examples/Lilly/Clothing/MID_MI_KhaosMDF322_Size6_398",
    "/Game/KhaosMDF322/Examples/Lilly/Grooms/MI_WI_Hair_S_BobLayered_Hair_Cards",
    "/Game/KhaosMDF322/Examples/Lilly/Grooms/MI_WI_Hair_S_BobLayered_Hair",
    "/Game/KhaosMDF322/Examples/Common/Materials/MI_Hair_Cards",
    "/Game/KhaosMDF322/Examples/Common/Materials/MI_Hair",
]


def object_path(obj) -> str:
    if not obj:
        return ""
    try:
        return obj.get_path_name()
    except Exception:
        return str(obj)


def get_params(asset) -> dict[str, object]:
    material_lib = unreal.MaterialEditingLibrary
    result: dict[str, object] = {
        "path": object_path(asset),
        "class": asset.get_class().get_name() if asset else "",
        "parent": "",
        "textures": {},
        "vectors": {},
        "scalars": {},
    }
    try:
        result["parent"] = object_path(asset.get_editor_property("parent"))
    except Exception:
        pass
    for getter, target in (
        ("get_texture_parameter_names", "textures"),
        ("get_vector_parameter_names", "vectors"),
        ("get_scalar_parameter_names", "scalars"),
    ):
        try:
            names = getattr(material_lib, getter)(asset)
        except Exception:
            names = []
        for name in names:
            key = str(name)
            try:
                if target == "textures":
                    value = material_lib.get_material_instance_texture_parameter_value(asset, name)
                    result[target][key] = object_path(value)
                elif target == "vectors":
                    value = material_lib.get_material_instance_vector_parameter_value(asset, name)
                    result[target][key] = str(value)
                else:
                    value = material_lib.get_material_instance_scalar_parameter_value(asset, name)
                    result[target][key] = float(value)
            except Exception as exc:
                result[target][key] = f"ERR:{exc}"
    return result


def main() -> None:
    rows = []
    for path in ASSETS:
        asset = unreal.EditorAssetLibrary.load_asset(path)
        rows.append(get_params(asset))
    OUT.write_text(json.dumps({"materials": rows}, indent=2) + "\n", encoding="utf-8")
    unreal.SystemLibrary.quit_editor()


main()
