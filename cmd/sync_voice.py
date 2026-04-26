"""
cmd/sync_voice.py

自动同步角色语音 wiki 页面的命令行工具。

工作流程
--------
  1. 从 wiki SMW API 获取全部已发布角色名单
  2. 对每个角色：调用 hsrparse 在本地生成最新 wikitext
  3. 拉取 wiki 上该角色的 {名字}/语音 页面现有内容
  4. 按优先级规则合并两份内容（详见 _merge 函数）
  5. 终端展示 unified diff，由用户逐一确认后推送

用法
----
  # 批量处理全部已发布角色
  python cmd/sync_voice.py

  # 仅处理单个角色（调试用）
  python cmd/sync_voice.py --name=银狼
"""

from __future__ import annotations

import difflib
import http.server
import os
import pathlib
import re
import sys
import threading
import urllib.parse
import webbrowser
from datetime import datetime

from mwclient import Site

# 将 hsrparse 根目录加入路径，使 func/src 包可被正确导入
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from func.voice import generate_voice  # noqa: E402
from src.config import get as _cfg_get  # noqa: E402

# ---------------------------------------------------------------------------
# Wiki 登录配置
# ---------------------------------------------------------------------------

# SESSDATA 读取优先级：
#   1. 项目根目录下的 .sessdata 文件（最方便：bwiki 退出后直接粘贴新值进去）
#   2. 环境变量 BILIBILI_SESSDATA
# 两者都没有时启动时会报错提示，而非用过期的硬编码值静默失败。
_SESSDATA_FILE = pathlib.Path(__file__).parent.parent / ".sessdata"


def _load_sessdata() -> str:
    """
    读取 SESSDATA cookie 值，按优先级依次尝试：
      1. env.json 中的 SESSDATA 字段（推荐）
      2. 项目根目录下的 .sessdata 文件（兼容旧配置）
      3. 环境变量 BILIBILI_SESSDATA
    """
    value = _cfg_get("SESSDATA")
    if value:
        return value
    if _SESSDATA_FILE.exists():
        value = _SESSDATA_FILE.read_text(encoding="utf-8").strip()
        if value:
            return value
    value = os.environ.get("BILIBILI_SESSDATA", "")
    if value:
        return value
    raise RuntimeError(
        "未找到 SESSDATA。请将 bwiki 的 SESSDATA cookie 值填入：\n"
        f"  {_SESSDATA_FILE.parent / 'env.json'}  （推荐，SESSDATA 字段）\n"
        f"  {_SESSDATA_FILE}  （旧方式）\n"
        "或设置环境变量 BILIBILI_SESSDATA。"
    )


def _login_wiki() -> Site:
    """
    登录 Bilibili SR wiki，返回已认证的 Site 对象。

    Bilibili wiki 对未认证请求返回 567，mwclient 默认在 __init__ 里
    就调用 site_init() 发出第一个请求。必须：
      1. do_init=False 跳过构造时的自动初始化
      2. 手动把 SESSDATA 写入 connection 的 cookie jar
      3. 再手动调 site_init()，此时请求已携带 cookie
    """
    # Bilibili WAF 会对默认的 python-requests / mwclient UA 返回 567，
    # 通过 custom_headers 构造参数注入完整的浏览器请求头，确保从第一个请求就伪装。
    browser_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/130.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://wiki.biligame.com/sr/",
        "Origin": "https://wiki.biligame.com",
    }
    site = Site(
        "wiki.biligame.com/sr",
        path="/",
        do_init=False,
        custom_headers=browser_headers,
        compress=False,   # 关闭 gzip 请求；Bilibili WAF 对 python UA + gzip 组合会直接返回 567
    )
    # mwclient 构造时会先写默认 UA、再 update(custom_headers)，所以上面 custom_headers
    # 中的 UA 会覆盖默认值。这里再显式 set 一次作为防御性保护，无副作用。
    site.connection.headers["User-Agent"] = browser_headers["User-Agent"]
    site.connection.cookies.update({"SESSDATA": _load_sessdata()})
    site.site_init()
    return site


# ---------------------------------------------------------------------------
# 角色名单获取
# ---------------------------------------------------------------------------

def _get_char_names(site: Site) -> list[str]:
    """
    通过 SemanticMediaWiki API 查询已发布角色名单。

    复用 site.connection（已携带 SESSDATA 的 requests.Session），
    避免重复配置 cookie 且确保与 mwclient 使用同一认证上下文。

    返回列表按名字长度倒序排列，防止短名字（如"三月七"）
    在后续字符串处理中提前匹配长名字（如"三月七•存护"）。
    """
    today = datetime.now().strftime("%Y年%m月%d日")
    resp = site.connection.get(
        "https://wiki.biligame.com/sr/api.php",
        params={
            "action": "ask",
            "format": "json",
            "query": (
                f"[[Category:角色]][[实装日期::<<={today}]]"
                "|format=plain|limit=1000|sort=创建日期"
            ),
        },
        timeout=15,
    )
    resp.raise_for_status()
    results = resp.json().get("query", {}).get("results", {})
    names = list(results.keys())
    # 采取倒序排列，未来新角色直接加到头
    names.reverse()
    return names


# ---------------------------------------------------------------------------
# Wikitext 解析工具
# ---------------------------------------------------------------------------

# 匹配单个 {{角色语音 ... }} 块。
# 角色语音模板的内容字段均为纯文本，不含跨行嵌套模板，
# 因此用「从 {{角色语音\n 到首个单独成行的 }}」来定界是可靠的。
_BLOCK_RE = re.compile(r"\{\{角色语音\n.*?\n\}\}", re.DOTALL)
_CHAIN_RE = re.compile(r"\|语音类型=队伍编成•([^\n]+)")


def _extract_chain_names(local_text: str) -> list[str]:
    """从本地生成的 wikitext 中提取所有「队伍编成•xx」语音条目的角色名。"""
    seen: dict[str, None] = {}
    for m in _CHAIN_RE.finditer(local_text):
        name = m.group(1).strip()
        if name:
            seen[name] = None
    return list(seen.keys())


def _parse_blocks(text: str) -> list[str]:
    """从 wikitext 中提取所有 {{角色语音}} 块，返回原始字符串列表。"""
    return _BLOCK_RE.findall(text)


def _get_param(block: str, param: str) -> str:
    """
    从模板块中提取指定参数值（去除首尾空白）。
    参数值以下一个 |参数= 行或模板结束符 }} 为终止标记。
    """
    m = re.search(
        rf"\|{re.escape(param)}=(.*?)(?=\n\||\n\}})",
        block,
        re.DOTALL,
    )
    return m.group(1).strip() if m else ""


def _set_param(block: str, param: str, new_value: str) -> str:
    """将模板块中指定参数的值替换为 new_value，其他内容保持不变。"""
    return re.sub(
        rf"(\|{re.escape(param)}=).*?(?=\n\||\n\}})",
        lambda m: m.group(1) + new_value,
        block,
        flags=re.DOTALL,
    )


def _is_2x(block: str) -> bool:
    """判断该块是否为 2x 速条目（语音类型含 ASCII 小括号后缀 (2x)）。"""
    return "(2x)" in _get_param(block, "语音类型")


# ---------------------------------------------------------------------------
# 合并算法
# ---------------------------------------------------------------------------

def _merge(local_text: str, wiki_text: str) -> str:
    """
    将本地生成内容与 wiki 现有内容合并，返回最终 wikitext。

    合并规则（优先级从高到低）
    -------------------------
    规则 1  wiki 中「语音类型」含 (2x) / （2x）的块
            → 整块保留 wiki 版本（人工填写的速度变体，不可覆盖）
              若 wiki 尚无此条目，保留本地生成的空骨架。

    规则 2  非 2x 块中，若本地与 wiki 的「语音文件」值不同
            → 采用 wiki 的值（wiki 编辑者已手动校正文件名）

    规则 3  其他字段（语音内容、各语言译文等）
            → 使用本地生成值（同步最新游戏数据中的文本）

    规则 4  本地有而 wiki 无的新语音条目
            → 直接保留（本地输出中已包含，无需额外处理）

    规则 5  wiki 有而本地无的非 2x 条目
            → 丢弃（游戏数据为准，该条目已从游戏中移除或更名）
    """
    # 建立 wiki 块索引：语音类型 -> 原始块字符串
    wiki_index: dict[str, str] = {
        _get_param(b, "语音类型"): b
        for b in _parse_blocks(wiki_text)
    }

    def _replace(m: re.Match[str]) -> str:
        local_block = m.group(0)
        voice_type = _get_param(local_block, "语音类型")

        # 规则 1：2x 条目以 wiki 为准
        if _is_2x(local_block):
            wiki_block = wiki_index.get(voice_type)
            if wiki_block is not None:
                return wiki_block
            # wiki 尚无此条目时保留本地空骨架
            return local_block

        # 规则 2：普通条目若「语音文件」与 wiki 不同，采用 wiki 的值
        wiki_block = wiki_index.get(voice_type)
        if wiki_block is not None:
            local_file = _get_param(local_block, "语音文件")
            wiki_file = _get_param(wiki_block, "语音文件")
            if local_file != wiki_file:
                # 仅替换「语音文件」字段，其余保持本地版本（规则 3）
                local_block = _set_param(local_block, "语音文件", wiki_file)

        return local_block

    return _BLOCK_RE.sub(_replace, local_text)


# ---------------------------------------------------------------------------
# Diff 显示与用户确认
# ---------------------------------------------------------------------------

def _show_diff_and_confirm(wiki_text: str, merged_text: str, char_name: str) -> bool:
    """
    启动本地 HTTP 服务，在浏览器中展示旁排 HTML diff 及确认按钮。

    流程：
      1. 快速检测是否有变更，无变更直接返回 False
      2. 在随机端口启动一次性 HTTP 服务器
      3. 浏览器打开 diff 页面，页面内嵌"推送"/"跳过"按钮
      4. 用户点击按钮后，浏览器自动关闭，脚本收到结果继续执行
    """
    has_diff = any(difflib.unified_diff(
        wiki_text.splitlines(),
        merged_text.splitlines(),
    ))

    if not has_diff:
        print(f"  [{char_name}] 无变更，跳过。")
        return False

    # 生成旁排 HTML
    html = difflib.HtmlDiff().make_file(
        wiki_text.splitlines(),
        merged_text.splitlines(),
        fromdesc=f"{char_name}/语音（wiki 现版本）",
        todesc=f"{char_name}/语音（合并结果）",
        context=True,
        numlines=3,
    )

    html = html.replace(' nowrap="nowrap"', '')

    # 替换 4 个空 <colgroup> 为带明确宽度的 <col>
    html = re.sub(
        r'(<colgroup></colgroup>\s*){4}',
        '<col style="width:1.5em"><col style="width:2.5em"><col style="width:46%">'
        '<col style="width:1.5em"><col style="width:2.5em"><col style="width:46%">',
        html,
    )

    html = html.replace(
        '</style>',
        """
    table.diff { table-layout: fixed; width: 100%; border-collapse: collapse; }
    table.diff td { white-space: pre-wrap; word-break: break-all; vertical-align: top; padding: 2px 4px; }
    table.diff td.diff_header { text-align: right; color: #888; font-size: 0.85em; }
</style>""",
    )

    # 在 </body> 前注入固定在底部的确认按钮栏
    button_bar = f"""
<div id="confirm-bar" style="
    position:fixed; bottom:0; left:0; right:0;
    background:#1e1e2e; color:#cdd6f4;
    padding:12px 24px; display:flex; align-items:center;
    gap:16px; font-family:sans-serif; font-size:15px;
    border-top:2px solid #45475a; z-index:9999;
">
  <span style="flex:1">确认推送 <strong>{char_name}/语音</strong>？</span>
  <a href="/confirm?choice=yes" target="_self"
     style="background:#a6e3a1;color:#1e1e2e;padding:8px 28px;border-radius:6px;
            text-decoration:none;font-weight:bold;">推送</a>
  <a href="/confirm?choice=no" target="_self"
     style="background:#f38ba8;color:#1e1e2e;padding:8px 28px;border-radius:6px;
            text-decoration:none;font-weight:bold;">跳过</a>
</div>
<div style="height:56px"></div>
</body>"""
    html = html.replace("</body>", button_bar)

    # -----------------------------------------------------------------------
    # 本地 HTTP 服务器：仅处理 / 和 /confirm 两条路由，回应后自动关闭
    # -----------------------------------------------------------------------
    decision: list[bool] = []   # 用列表包装，让内层函数可写入
    done = threading.Event()

    html_bytes = html.encode("utf-8")

    class _Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass  # 静默服务器日志

        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)

            if parsed.path == "/":
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html_bytes)))
                self.end_headers()
                self.wfile.write(html_bytes)

            elif parsed.path == "/confirm":
                params = urllib.parse.parse_qs(parsed.query)
                choice = params.get("choice", ["no"])[0]
                decision.append(choice == "yes")

                # 返回自动关闭页面
                close_html = (
                    '<!DOCTYPE html><html><head><meta charset="utf-8">'
                    "<title>Done</title></head><body>"
                    "<script>window.close();</script>"
                    '<p style="font-family:sans-serif;padding:2em">已收到，可关闭此页。</p>'
                    "</body></html>"
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(close_html)))
                self.end_headers()
                self.wfile.write(close_html)
                done.set()

            else:
                self.send_response(404)
                self.end_headers()

    # 端口 0 让 OS 自动分配空闲端口
    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]

    # 在后台线程处理请求；收到 /confirm 后设置 done 事件并停止服务器
    def _serve() -> None:
        while not done.is_set():
            server.handle_request()
        server.server_close()

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()

    webbrowser.open(f"http://127.0.0.1:{port}/")
    print(f"  [{char_name}] 已在浏览器打开 diff，等待确认……")

    done.wait()
    return bool(decision and decision[0])


# ---------------------------------------------------------------------------
# 单角色同步流程
# ---------------------------------------------------------------------------

def _sync_one(site: Site, char_name: str) -> str:
    """执行单个角色的完整同步流程：生成 → 拉取 → 合并 → diff → 推送。返回本地生成的 wikitext。"""
    print(f"\n{'=' * 60}")
    print(f"处理角色：{char_name}")

    # 第一步：本地生成最新 wikitext（静默模式：不复制剪贴板，不发气泡通知）
    local_text = generate_voice(char_name, silent=True)

    # 第二步：拉取 wiki 当前页面内容
    page = site.pages[f"{char_name}/语音"]
    wiki_text: str = page.text()

    # 第三步：若 wiki 页面为空（新角色首次创建），直接使用本地内容
    if not wiki_text.strip():
        print(f"  [{char_name}] wiki 页面为空，将直接创建。")
        merged_text = local_text
    else:
        merged_text = _merge(local_text, wiki_text)

    # 第四步：展示 diff，由用户确认是否推送
    if not _show_diff_and_confirm(wiki_text, merged_text, char_name):
        return local_text

    # 第五步：推送合并结果到 wiki
    # bot=False：wiki 不支持 bot 权限，使用普通用户身份（SESSDATA）推送
    page.save(text=merged_text, summary="更新角色语音", bot=False)
    print(f"  [{char_name}] 已推送。")
    return local_text


# ---------------------------------------------------------------------------
# 链式更新
# ---------------------------------------------------------------------------

_CHAIN_UPDATE_SKIP: frozenset[str] = frozenset({"开拓者"})


def _chain_update(site: Site, local_text: str, exclude: str | None = None) -> None:
    """
    从 local_text 中提取所有「队伍编成•xx」角色名，逐一调用 _sync_one。
    不递归：对这些角色不再做进一步的 chain-update。
    """
    names = _extract_chain_names(local_text)
    skip = _CHAIN_UPDATE_SKIP | ({exclude} if exclude else set())
    names = [n for n in names if n not in skip]
    if not names:
        print("\n[chain-update] 未发现队伍编成语音条目，跳过链式更新。")
        return
    print(f"\n[chain-update] 发现 {len(names)} 个关联角色：{'、'.join(names)}")
    for char_name in names:
        try:
            _sync_one(site, char_name)
        except Exception as e:
            print(f"  [chain-update][{char_name}] 处理失败：{e}")


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def main(name: str | None = None, start_from: str | None = None, chain_update: bool = False) -> None:
    """
    主入口函数，由 fire.Fire 解析命令行参数后调用。

    参数
    ----
    name         : 指定单个角色的中文名（调试用）。省略时批量处理全部已发布角色。
    start_from   : 批量模式下从指定角色名开始处理，跳过列表中该角色之前的所有角色。
                   省略时从头开始。
    chain_update : 链式更新（仅 --name 单角色模式有效）。完成 input1 后，自动提取
                   其语音中所有「队伍编成•xx」的角色名，依次同步这些角色（不递归）。

    用法示例
    --------
      python cmd/sync_voice.py                             # 从头批量处理
      python cmd/sync_voice.py --start_from=银狼           # 从"银狼"继续上次中断的批量处理
      python cmd/sync_voice.py --name=银狼                 # 仅处理单个角色
      python cmd/sync_voice.py --name=银狼 --chain-update  # 处理银狼后自动同步其队伍编成关联角色
    """
    site = _login_wiki()

    if name:
        # 单角色模式，适合测试和手动触发单次更新
        local_text = _sync_one(site, name)
        if chain_update:
            _chain_update(site, local_text, exclude=name)
    else:
        # 批量模式：从 wiki 获取完整角色名单后逐一处理
        char_names = _get_char_names(site)

        if start_from:
            if start_from not in char_names:
                print(f"错误：找不到角色「{start_from}」，请检查名字是否正确。")
                print(f"已获取的角色名单（共 {len(char_names)} 个）：")
                print("  " + "、".join(char_names))
                return
            skip_count = char_names.index(start_from)
            char_names = char_names[skip_count:]
            print(f"共获取到 {len(char_names) + skip_count} 个角色，从「{start_from}」开始，跳过前 {skip_count} 个，剩余 {len(char_names)} 个……")
        else:
            print(f"共获取到 {len(char_names)} 个角色，开始同步……")

        for char_name in char_names:
            try:
                _sync_one(site, char_name)
            except Exception as e:
                # 单角色失败不中断整体流程，打印错误后继续
                print(f"  [{char_name}] 处理失败：{e}")


if __name__ == "__main__":
    import fire
    fire.Fire(main)
