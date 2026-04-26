from __future__ import annotations

import html as _html
import pathlib
from typing import Final

from gsz import SRGameData
from gsz.format import Formatter, Syntax

_GAME_DATA_PATH: Final = pathlib.Path("~/GameData/turnbasedgamedata/").expanduser()
_game: Final = SRGameData(_GAME_DATA_PATH)
_formatter: Final = Formatter(syntax=Syntax.MediaWiki, game=_game)


def fmt(text: str) -> str:
    return _html.unescape(_formatter.format(text))
