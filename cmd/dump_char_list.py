"""
cmd/dump_char_list.py

诊断工具：直接复用 sync_voice 里的 _login_wiki + _get_char_names，
验证批量处理顺序是否符合预期。

用法
----
  python cmd/dump_char_list.py
"""

from __future__ import annotations

import os
import pathlib
import sys
from datetime import datetime

# 将 hsrparse 根目录加入路径，并把 cmd/ 本身也加入，这样可以直接 import sync_voice
_HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE))

# 从 sync_voice 直接导入，确保两个脚本的认证 / 查询逻辑完全一致
from sync_voice import _login_wiki, _get_char_names  # noqa: E402


def main() -> None:
    print("调用 _login_wiki() ……")
    site = _login_wiki()
    print(f"  ✅ 登录成功，用户名：{getattr(site, 'username', '未知')}")

    print("\n调用 _get_char_names(site) ……")
    names = _get_char_names(site)
    print(f"  ✅ 共获取到 {len(names)} 个角色。\n")

    # ---- 打印处理顺序 ----
    print("=== 脚本实际处理顺序（与 sync_voice 批量模式一致） ===")
    for i, n in enumerate(names, 1):
        print(f"  {i:3d}. {n}")

    # ---- 写入文件，便于对照 ----
    out_path = pathlib.Path(__file__).parent / "char_list.txt"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"# 导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"# 共 {len(names)} 个角色（顺序 = sync_voice 批量处理顺序）\n\n")
        for n in names:
            f.write(n + "\n")
    print(f"\n已写入：{out_path}")


if __name__ == "__main__":
    import fire
    fire.Fire(main)
