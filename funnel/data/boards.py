# -*- coding: utf-8 -*-
"""板块层: 新浪行业+概念板块映射(缓存7天) + 成分股拉取
数据源(2026-08-20实测):
- 板块列表: money.finance.sina.com.cn/q/view/newFLJK.php?param=hangye (行业84) / param=class (概念175)
- 成分股: Market_Center.getHQNodeData?node=gn_xxx&num=100 (一次拿全)
产出: data/boards.json {board_code: {name, type, members: [code...]}}
       data/stock_to_boards.json {code: [board_code...]} (反向索引)
"""
import json
import os
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from ..config import CONFIG

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
BOARD_FILE = os.path.normpath(os.path.join(DATA_DIR, "boards.json"))
S2B_FILE = os.path.normpath(os.path.join(DATA_DIR, "stock_to_boards.json"))

HEADERS = {"Referer": "https://finance.sina.com.cn/", "User-Agent": "Mozilla/5.0"}
LIST_URL = "http://money.finance.sina.com.cn/q/view/newFLJK.php?param={param}"
MEMBER_URL = ("http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
              "Market_Center.getHQNodeData?page=1&num=100&sort=symbol&asc=1&node={node}")


def _get(url, enc="gbk", retries=3):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=12) as r:
                return r.read().decode(enc, "ignore")
        except Exception:
            if i == retries - 1:
                raise
            time.sleep(1 + i)


def fetch_board_list(param):
    """param: hangye=行业, class=概念. 返回 {board_code: (name, member_count)}"""
    text = _get(LIST_URL.format(param=param))
    boards = {}
    for m in re.finditer(r'"([^"]+)":"([^"]+)"', text):
        parts = m.group(2).split(",")
        if len(parts) >= 3:
            boards[m.group(1)] = (parts[1], int(parts[2]) if parts[2].isdigit() else 0)
    return boards


def fetch_members(board_code):
    """拿一个板块的全部成分股代码(去市场前缀的6位代码)"""
    text = _get(MEMBER_URL.format(node=board_code))
    try:
        arr = json.loads(text)
    except json.JSONDecodeError:
        return []
    codes = []
    for it in arr:
        c = it.get("code") or ""
        if len(c) == 6:
            codes.append(c)
    return codes


def build_board_cache(force=False, progress_cb=None):
    """构建板块缓存. 返回boards字典. 缓存未过期且不force时直接读文件"""
    min_n, max_n = CONFIG["sector"]["min_members"], CONFIG["sector"]["max_members"]
    if not force and os.path.exists(BOARD_FILE):
        age_days = (time.time() - os.path.getmtime(BOARD_FILE)) / 86400
        if age_days < CONFIG["data"]["board_cache_days"]:
            with open(BOARD_FILE, encoding="utf-8") as f:
                return json.load(f)

    all_list = {}
    for param, btype in [("hangye", "industry"), ("class", "concept")]:
        lst = fetch_board_list(param)
        for code, (name, cnt) in lst.items():
            all_list[code] = (name, cnt, btype)

    # 过滤掉样本太小/太大的板块再拉成分(省请求)
    todo = {c: v for c, v in all_list.items() if min_n <= v[1] <= max_n}
    boards = {}
    done = 0
    total = len(todo)

    def one(item):
        code, (name, cnt, btype) = item
        return code, name, btype, fetch_members(code)

    with ThreadPoolExecutor(max_workers=8) as ex:
        for code, name, btype, members in ex.map(one, todo.items()):
            if members:
                boards[code] = {"name": name, "type": btype, "members": members}
            done += 1
            if progress_cb:
                progress_cb(done, total)

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(BOARD_FILE, "w", encoding="utf-8") as f:
        json.dump(boards, f, ensure_ascii=False)
    # 反向索引
    s2b = {}
    for bc, info in boards.items():
        for c in info["members"]:
            s2b.setdefault(c, []).append(bc)
    with open(S2B_FILE, "w", encoding="utf-8") as f:
        json.dump(s2b, f, ensure_ascii=False)
    return boards


def load_boards():
    if os.path.exists(BOARD_FILE):
        with open(BOARD_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


if __name__ == "__main__":
    t0 = time.time()
    b = build_board_cache(force=True, progress_cb=lambda d, t: print(f"\r  boards {d}/{t}", end=""))
    print(f"\n板块数={len(b)} 耗时={time.time()-t0:.1f}s")
    names = [(v["name"], v["type"], len(v["members"])) for v in b.values()]
    print("样例:", names[:5])
