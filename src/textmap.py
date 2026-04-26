"""
src/textmap.py

Lazy-loaded singleton TextMap store.
All four language maps (CHS / JP / EN / KR) are loaded once on first access
and cached for the lifetime of the process.
"""

from __future__ import annotations

import json
import pathlib
from functools import lru_cache
from typing import Final

GAME_DATA_PATH: Final = pathlib.Path("~/GameData/turnbasedgamedata/").expanduser()


def _load_json(path: pathlib.Path) -> dict[str, str]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


@lru_cache(maxsize=1)
def get_maps() -> tuple[
    dict[str, str],  # CHS
    dict[str, str],  # JP
    dict[str, str],  # EN
    dict[str, str],  # KR
]:
    """
    Return (CHS, JP, EN, KR) text maps.
    The result is cached — subsequent calls are free.
    """
    base = GAME_DATA_PATH / "TextMap"

    chs = _load_json(base / "TextMapCHS.json")
    jp  = _load_json(base / "TextMapJP.json")
    en  = _load_json(base / "TextMapEN.json")

    # KR ships in two shards; merge them (shard 1 wins on collision).
    kr: dict[str, str] = _load_json(base / "TextMapKR_0.json")
    kr.update(_load_json(base / "TextMapKR_1.json"))

    return chs, jp, en, kr


def resolve(hash_key: int | str, lang: str = "chs") -> str:
    """
    Look up a hash in the requested language map.

    Args:
        hash_key: The numeric hash, as int or str.
        lang:     One of ``"chs"``, ``"jp"``, ``"en"``, ``"kr"``.

    Returns:
        The resolved string, or an empty string if the key is absent.
    """
    chs, jp, en, kr = get_maps()
    maps = {"chs": chs, "jp": jp, "en": en, "kr": kr}
    return maps[lang].get(str(hash_key), "")
