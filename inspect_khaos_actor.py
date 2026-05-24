from __future__ import annotations

import json
from pathlib import Path

import unreal


LEVEL = "/Game/KhaosMDF322/Maps/Examples"
OUT_REPORT = Path(r"C:\Users\hjvan\OneDrive\_Codex\Avatar\public_html\avatar\assets\khaos-actor-inspect.json")
ACTOR_CANDIDATES = ("BP_Lilly", "BP_Holly")


def asset_path(obj) -> str:
    if not obj:
        return ""
    try:
        return obj.get_path_name()
    except Exception:
        return str(obj)


def safe_prop(obj, name: str):
    try:
        return obj.get_editor_property(name)
    except Exception:
        return None


def find_actor():
    actors = list(unreal.EditorLevelLibrary.get_all_level_actors())
    for candidate in ACTOR_CANDIDATES:
        for actor in actors:
            if actor.get_actor_label() == candidate or actor.get_class().get_name() == f"{candidate}_C":
                return actor
    return None


def main() -> None:
    unreal.EditorLevelLibrary.load_level(LEVEL)
    actor = find_actor()
    rows: list[dict[str, object]] = []
    if actor:
        for component in actor.get_components_by_class(unreal.ActorComponent):
            item: dict[str, object] = {
                "name": component.get_name(),
                "class": component.get_class().get_name(),
                "visible": bool(safe_prop(component, "visible")) if safe_prop(component, "visible") is not None else None,
                "hidden_in_game": bool(safe_prop(component, "hidden_in_game")) if safe_prop(component, "hidden_in_game") is not None else None,
                "assets": {},
            }
            for prop in ("skeletal_mesh_asset", "skeletal_mesh", "static_mesh", "groom_asset", "binding_asset"):
                value = safe_prop(component, prop)
                if value:
                    item["assets"][prop] = asset_path(value)
            if item["assets"] or any(token in str(item["class"]).lower() for token in ("mesh", "groom", "cloth", "outfit")):
                rows.append(item)
    result = {
        "ok": actor is not None,
        "level": LEVEL,
        "actor": actor.get_actor_label() if actor else "",
        "actor_class": actor.get_class().get_name() if actor else "",
        "components": rows,
    }
    OUT_REPORT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    unreal.log(f"[KhaosInspect] wrote {OUT_REPORT}")
    unreal.SystemLibrary.quit_editor()


main()
