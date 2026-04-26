"""
src/models.py

Pydantic v2 strict-mode data models and supporting infrastructure
for the character voice generation pipeline.
"""

from __future__ import annotations

import subprocess
import sys
from io import StringIO
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator


# ---------------------------------------------------------------------------
# TextMap hash reference (shared shape used in AvatarConfig and VoiceAtlas)
# ---------------------------------------------------------------------------

class HashRef(BaseModel):
    """A localisation key as stored in game data JSON, e.g. {"Hash": 12345}."""

    model_config = ConfigDict(strict=True, frozen=True)

    Hash: int

    @field_validator("Hash", mode="before")
    @classmethod
    def coerce_hash(cls, v: Any) -> int:
        # Game data occasionally stores hashes as strings.
        return int(v)


# ---------------------------------------------------------------------------
# ExcelOutput models
# ---------------------------------------------------------------------------

class AvatarConfigEntry(BaseModel):
    """One row from ExcelOutput/AvatarConfig.json."""

    model_config = ConfigDict(strict=False, populate_by_name=True)

    AvatarID: int
    AvatarName: HashRef


class VoiceAtlasEntry(BaseModel):
    """One row from ExcelOutput/VoiceAtlas.json."""

    model_config = ConfigDict(strict=False, populate_by_name=True)

    AvatarID: int
    VoiceTitle: HashRef
    Voice_M: HashRef
    IsBattleVoice: bool = False

    @field_validator("IsBattleVoice", mode="before")
    @classmethod
    def coerce_bool(cls, v: Any) -> bool:
        if isinstance(v, bool):
            return v
        if isinstance(v, int):
            return bool(v)
        return False
    
class ActivityAvatarDeliverConfigEntry(BaseModel):
    """One row from ExcelOutput/ActivityAvatarDeliverConfig.json."""

    model_config = ConfigDict(strict=False, populate_by_name=True)

    AvatarID: int
    Name: HashRef
    Desc: HashRef
    Sign: HashRef


# ---------------------------------------------------------------------------
# Output / clipboard helper
# ---------------------------------------------------------------------------

class OutputCollector:
    """
    Wraps stdout so that everything printed inside a ``with`` block is
    both shown in the terminal *and* accumulated for clipboard delivery.
    """

    def __init__(self) -> None:
        self._original_stdout = sys.stdout
        self._collected: list[str] = []

    # ------------------------------------------------------------------
    # Context-manager protocol
    # ------------------------------------------------------------------

    def __enter__(self) -> "OutputCollector":
        sys.stdout = self  # type: ignore[assignment]
        return self

    def __exit__(self, *_: Any) -> None:
        sys.stdout = self._original_stdout

    # ------------------------------------------------------------------
    # file-like interface expected by sys.stdout
    # ------------------------------------------------------------------

    def write(self, text: str) -> int:
        self._original_stdout.write(text)
        self._collected.append(text)
        return len(text)

    def flush(self) -> None:
        self._original_stdout.flush()

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    @property
    def text(self) -> str:
        return "".join(self._collected)

    def copy_to_clipboard(self) -> bool:
        """
        Try three strategies in order:
        1. ``clip.exe``   (Windows built-in)
        2. PowerShell ``Set-Clipboard``
        3. ``pbcopy``     (macOS)
        Returns *True* on success.
        """
        payload = self.text

        # --- Strategy 1: clip.exe ---
        try:
            proc = subprocess.Popen("clip", stdin=subprocess.PIPE, shell=True)
            proc.communicate(payload.encode("utf-16-le"))
            return True
        except Exception:
            pass

        # --- Strategy 2: PowerShell ---
        try:
            proc = subprocess.Popen(
                [
                    "powershell", "-NoProfile", "-Command",
                    "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8;"
                    " $input | Set-Clipboard",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            proc.communicate(payload.encode("utf-8-sig"))
            return True
        except Exception:
            pass

        # --- Strategy 3: pbcopy (macOS) ---
        try:
            proc = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
            proc.communicate(payload.encode("utf-8"))
            return True
        except Exception:
            pass

        print("复制到剪贴板失败（所有方法均不可用）", file=self._original_stdout)
        return False

    def notify_windows(self, character_name: str) -> None:
        """Fire a Windows balloon notification (best-effort, silent on failure)."""
        script = f"""
Add-Type -AssemblyName System.Windows.Forms
$n = New-Object System.Windows.Forms.NotifyIcon
$n.Icon = [System.Drawing.SystemIcons]::Information
$n.BalloonTipTitle = "语音生成完成"
$n.BalloonTipText = "角色'{character_name}'的语音内容已复制到剪贴板"
$n.Visible = $true
$n.ShowBalloonTip(5000)
Start-Sleep -Seconds 6
$n.Dispose()
"""
        try:
            subprocess.Popen(
                ["powershell", "-NoProfile", "-Command", script],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except Exception:
            pass
