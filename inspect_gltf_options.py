from __future__ import annotations

import json
from pathlib import Path

import unreal


OUT = Path(
    r"C:\Users\hjvan\OneDrive\_Codex\Avatar\public_html\avatar\assets\gltf-options-inspect.json"
)


def main() -> None:
    options = unreal.GLTFExportOptions()
    props = {}
    for name in dir(options):
        if name.startswith("_") or name in {"cast", "get_class", "get_fname", "get_name", "get_outer", "get_path_name", "is_valid"}:
            continue
        try:
            props[name] = str(options.get_editor_property(name))
        except Exception as exc:
            try:
                props[name] = str(getattr(options, name))
            except Exception:
                props[name] = f"ERR:{exc}"

    enums = {}
    for name in [
        "GLTFMaterialBakeMode",
        "GLTFTextureImageFormat",
        "GLTFMaterialPropertyGroup",
        "GLTFMaterialVariantMode",
    ]:
        enum = getattr(unreal, name, None)
        if enum:
            enums[name] = [entry for entry in dir(enum) if entry.isupper()]

    OUT.write_text(json.dumps({"props": props, "enums": enums}, indent=2) + "\n", encoding="utf-8")
    unreal.SystemLibrary.quit_editor()


main()
