"""
func/voice.py

Core character-voice MediaWiki markup generator.

Public entry-point
------------------
    generate_voice(name: str) -> str

Prints the full wiki markup to stdout, copies it to the clipboard,
and (on Windows) fires a balloon notification.
"""

from __future__ import annotations

import json
import pathlib
from collections import Counter
from typing import Final

from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import TypeAdapter

# Internal imports — adjust if you move the package root.
from src.config import get as _cfg_get
from src.fmt import fmt as _fmt
from src.models import AvatarConfigEntry, OutputCollector, VoiceAtlasEntry
from src.textmap import resolve

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GAME_DATA_PATH: Final = pathlib.Path(
    _cfg_get("GAME_DATA_PATH", "~/GameData/turnbasedgamedata/")
).expanduser()
TEMPLATES_PATH: Final = pathlib.Path(__file__).parent.parent / "templates"

TRAILBLAZER_ID: Final = 8001
TRAILBLAZER_CHS_NAME: Final = "开拓者"

# 游戏数据中开拓者名字存为 {NICKNAME}，Formatter 输出"开拓者"（CHS）。
# JP/EN/KR 仍需替换为各语言 wiki 使用的正式名称。
_TRAILBLAZER_NAME_BY_LANG: Final[dict[str, str]] = {
    "chs": "开拓者",
    "jp":  "開拓者",
    "en":  "Trailblazer",
    "kr":  "개척자",
}

# Ordinal map for duplicate-title suffixes (extend as needed).
_ORDINAL: Final[dict[int, str]] = {
    1: "一", 2: "二", 3: "三", 4: "四", 5: "五",
    6: "六", 7: "七", 8: "八", 9: "九", 10: "十",
}

# Titles that require a 2× speed companion entry in the wiki.
_NEEDS_2X: Final[frozenset[str]] = frozenset({
    "战斗开始•弱点击破",
    "战斗开始•危险预警",
    "普攻•连携攻击•一",
    "普攻•连携攻击•二",
    "战技•召唤「衣匠」•一",
    "战技•召唤「衣匠」•二",
    "回合开始•一",
    "回合开始•二",
    "战技•一",
    "战技•二",
    "强化战技•一",
    "强化战技•二",
    "追加攻击•一",
    "追加攻击•二",
    "终结技•施放",
    "欢愉技•一",
    "欢愉技•二",
    "战技•施放•一",
    "战技•施放•二",
    "战技•互动•一",
    "战技•互动•二",
})

# ---------------------------------------------------------------------------
# Jinja2 environment
# ---------------------------------------------------------------------------

_jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_PATH)),
    autoescape=select_autoescape([]),   # plain text / wiki markup — no HTML escaping
    keep_trailing_newline=True,
    # Use <<- ... >> instead of {{ ... }} so MediaWiki double-braces pass through untouched.
    variable_start_string="<<-",
    variable_end_string=">>",
)

_tmpl_entry    = _jinja_env.get_template("voice_entry.j2")
_tmpl_entry_2x = _jinja_env.get_template("voice_entry_2x.j2")

# ---------------------------------------------------------------------------
# Data loading helpers
# ---------------------------------------------------------------------------

def _load_excel(relative: str) -> list[dict]:
    path = GAME_DATA_PATH / "ExcelOutput" / relative
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _load_avatar_config() -> list[AvatarConfigEntry]:
    adapter: TypeAdapter[list[AvatarConfigEntry]] = TypeAdapter(list[AvatarConfigEntry])
    return adapter.validate_python(_load_excel("AvatarConfig.json"))


def _load_voice_atlas() -> list[VoiceAtlasEntry]:
    adapter: TypeAdapter[list[VoiceAtlasEntry]] = TypeAdapter(list[VoiceAtlasEntry])
    return adapter.validate_python(_load_excel("VoiceAtlas.json"))


# ---------------------------------------------------------------------------
# Name → AvatarID resolution
# ---------------------------------------------------------------------------

def _resolve_avatar_id(name: str, config: list[AvatarConfigEntry]) -> int:
    if name == TRAILBLAZER_CHS_NAME:
        return TRAILBLAZER_ID
    for entry in config:
        if _fmt(resolve(entry.AvatarName.Hash, "chs")) == name:
            return entry.AvatarID
    raise ValueError(f"角色「{name}」在 AvatarConfig 中未找到")


# ---------------------------------------------------------------------------
# Title numbering
# ---------------------------------------------------------------------------

def _numbered_title(
    raw: str,
    run_counts: Counter[str],
    total_counts: Counter[str],
) -> str:
    """
    Return *raw* unchanged if it appears only once overall, otherwise append
    a Chinese ordinal suffix (•一, •二, …).
    """
    run_counts[raw] += 1
    if total_counts[raw] <= 1:
        return raw
    ordinal = _ORDINAL.get(run_counts[raw], str(run_counts[raw]))
    return f"{raw}•{ordinal}"


# ---------------------------------------------------------------------------
# Markup rendering helpers
# ---------------------------------------------------------------------------

def _render_entry(char_name: str, title: str, voice_hash: int) -> str:
    """Render one {{角色语音}} block via Jinja2."""
    return _tmpl_entry.render(
        char_name=char_name,
        title=title,
        content_chs=_fmt(resolve(voice_hash, "chs")),
        content_jp= _fmt(resolve(voice_hash, "jp" )).replace("开拓者", _TRAILBLAZER_NAME_BY_LANG["jp"]),
        content_en= _fmt(resolve(voice_hash, "en" )).replace("开拓者", _TRAILBLAZER_NAME_BY_LANG["en"]),
        content_kr= _fmt(resolve(voice_hash, "kr" )).replace("开拓者", _TRAILBLAZER_NAME_BY_LANG["kr"]),
    )


def _render_entry_2x(char_name: str, title: str) -> str:
    """Render the companion 2× speed block (no transcript content)."""
    return _tmpl_entry_2x.render(char_name=char_name, title=title)


# ---------------------------------------------------------------------------
# No-text voice table (static wiki markup, parametric on char_name)
# ---------------------------------------------------------------------------

def _no_text_table(name: str) -> str:
    def row(label: str, key: str, count: int) -> str:
        ordinals = "".join(
            f"{{{{player|{prefix}{name}-{key}•{_ORDINAL[i]}}}}}"
            for i in range(1, count + 1)
            for prefix in ("", "日-", "英-", "韩-")
        )
        # Build one row per language column.
        cols = []
        for prefix in ("", "日-", "英-", "韩-"):
            cells = "".join(
                f"{{{{player|{prefix}{name}-{key}•{_ORDINAL[i]}}}}}"
                for i in range(1, count + 1)
            )
            cols.append(f"| {cells}")
        return (
            "|-\n"
            f'| style="width:25%;text-align:center" | {label}\n'
            + "\n".join(cols)
        )

    lines = [
        '{| class="wikitable" style="width:100%"',
        "|-",
        "! 语音类型 !! 中 !! 日 !! 英 !! 韩",
        row("触发战斗", "触发战斗", 3),
        row("普攻",     "普攻-无文本", 4),
        row("轻受击",   "轻受击-无文本", 4),
        row("重受击",   "重受击-无文本", 4),
        "|}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_voice(name: str, silent: bool = False) -> str:
    """
    Generate full MediaWiki voice markup for *name* (Chinese display name),
    print it to stdout, and return the markup text.
    When *silent* is False (default), also copies to clipboard and fires a
    Windows balloon notification. Pass silent=True to suppress both.
    """
    avatar_config = _load_avatar_config()
    voice_atlas   = _load_voice_atlas()

    avatar_id = _resolve_avatar_id(name, avatar_config)

    # Display name used in wiki file links (Trailblazer uses canonical name).
    char_name = TRAILBLAZER_CHS_NAME if name == TRAILBLAZER_CHS_NAME else name

    # Filter to this character's voice lines, preserving original order.
    entries = [e for e in voice_atlas if e.AvatarID == avatar_id]

    # Pre-count how many times each processed title appears in total.
    total_counts: Counter[str] = Counter(
        _fmt(resolve(e.VoiceTitle.Hash, "chs"))
        for e in entries
    )
    run_counts: Counter[str] = Counter()

    # -----------------------------------------------------------------------
    with OutputCollector() as collector:
        print(f"{{{{角色语音表头|{name}|未完善=是}}}}")
        print("== 正式语音 ==")
        print("{{切换板|开始}}")
        print("{{切换板|默认显示|互动语音}}")
        print("{{切换板|默认折叠|战斗语音}}")
        print("{{切换板|显示内容}}")

        battle_section_started = False

        for entry in entries:
            # Switch to battle-voice section on first battle entry.
            if entry.IsBattleVoice and not battle_section_started:
                print("{{切换板|内容结束}}")
                print("{{切换板|折叠内容}}")
                battle_section_started = True

            raw_title = _fmt(resolve(entry.VoiceTitle.Hash, "chs"))
            final_title = _numbered_title(raw_title, run_counts, total_counts)

            # Main entry block.
            print(_render_entry(char_name, final_title, entry.Voice_M.Hash))

            # Optional 2× companion block (matched against *raw* title so the
            # check remains stable regardless of suffix numbering).
            if raw_title in _NEEDS_2X:
                print(_render_entry_2x(char_name, final_title))

        print("{{切换板|内容结束}}")
        print("{{切换板|结束}}")

        print("== 无文本语音 ==")
        print(_no_text_table(char_name))

    # -----------------------------------------------------------------------
    if not silent and collector.copy_to_clipboard():
        collector.notify_windows(name)
    return collector.text
