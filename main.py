"""
main.py — CLI entry point.

Usage
-----
    python main.py 开拓者
    python main.py 希儿
    uv run main.py 银狼
"""

import fire
from func.voice import generate_voice

if __name__ == "__main__":
    fire.Fire(generate_voice)
