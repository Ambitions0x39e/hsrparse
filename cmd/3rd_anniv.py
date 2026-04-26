from __future__ import annotations

import json
import pathlib
import sys
from typing import Final

from pydantic import TypeAdapter

# Path bootstrap: ensures `src.*` imports work when run from any directory.
_HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(_HERE.parent))

from src.fmt import fmt as _fmt
from src.models import ActivityAvatarDeliverConfigEntry, AvatarConfigEntry
from src.textmap import resolve

GAME_DATA_PATH: Final = pathlib.Path("~/GameData/turnbasedgamedata/").expanduser()

def _load_excel(relative: str) -> list[dict]:
    path = GAME_DATA_PATH / "ExcelOutput" / relative
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)
    
def _load_activity_avatar_deliver_config() -> list[ActivityAvatarDeliverConfigEntry]:
    adapter: TypeAdapter[list[ActivityAvatarDeliverConfigEntry]] = TypeAdapter(list[ActivityAvatarDeliverConfigEntry])
    return adapter.validate_python(_load_excel("ActivityAvatarDeliverConfig.json"))

def _load_avatar_config() -> list[AvatarConfigEntry]:
    adapter: TypeAdapter[list[AvatarConfigEntry]] = TypeAdapter(list[AvatarConfigEntry])
    return adapter.validate_python(_load_excel("AvatarConfig.json"))

with open(GAME_DATA_PATH / "ExcelOutput" / "ActivityAvatarDeliverConfig.json", "r", encoding="utf-8") as fh:
    _activity_avatar_deliver_config = json.load(fh)
    
for data in _activity_avatar_deliver_config:
    data["Name"] = _fmt(resolve(data["Name"]["Hash"]))
    data["Desc"] = _fmt(resolve(data["MailDesc"]["Hash"]))
    data["Sign"] = _fmt(resolve(data["Sign"]["Hash"]))

    print(f"{data['Name']}")
    print("=" * 15)
    print(f"{data['Desc']}")
    print(f"{data['Sign']}")
    print("\n\n")