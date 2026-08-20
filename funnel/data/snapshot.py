# -*- coding: utf-8 -*-
"""数据层: 腾讯全A快照排行(58页分页拉取)
字段: code/name/zxj/zdf/zdf_d5/zdf_d10/zdf_d20/lb/hsl/turnover/ltsz/zsz
盘前(9:30前): zdf/lb/volume全为0, 多周期字段(zdf_d5等)仍有昨日数据
"""
import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

RANK_URL = ("https://proxy.finance.qq.com/cgi/cgi-bin/rank/hs/getBoardRankList"
            "?board_code=aStock&sort_type=PriceRatio&direct=down"
            "&offset={offset}&count={count}")

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# 保留字段(其余丢弃, 减少内存)
KEEP_FIELDS = ["code", "name", "zxj", "zdf", "zdf_d5", "zdf_d10", "zdf_d20",
               "lb", "hsl", "turnover", "ltsz", "stock_type"]


def _fetch_page(offset, count, retries=3):
    url = RANK_URL.format(offset=offset, count=count)
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=10) as r:
                d = json.loads(r.read().decode("utf-8", "ignore"))
            return d.get("data", {}).get("rank_list", []), d.get("data", {}).get("total", 0)
        except Exception:
            if i == retries - 1:
                raise
            time.sleep(1 + i)


def fetch_all_snapshot(page_size=80, progress_cb=None):
    """拉全A快照, 返回(list of dict, total). progress_cb(done_pages, total_pages)"""
    first, total = _fetch_page(0, page_size)
    pages = (total + page_size - 1) // page_size
    rows = [_clean(x) for x in first]
    if progress_cb:
        progress_cb(1, pages)

    def one(p):
        lst, _ = _fetch_page(p * page_size, page_size)
        return p, [_clean(x) for x in lst]

    with ThreadPoolExecutor(max_workers=6) as ex:
        for p, lst in ex.map(one, range(1, pages)):
            rows.extend(lst)
            if progress_cb:
                progress_cb(p + 1, pages)
    return rows, total


def _clean(item):
    out = {}
    for k in KEEP_FIELDS:
        v = item.get(k)
        if k in ("code", "name", "stock_type"):
            out[k] = v
        else:
            try:
                out[k] = float(v)
            except (TypeError, ValueError):
                out[k] = 0.0
    return out


if __name__ == "__main__":
    t0 = time.time()
    rows, total = fetch_all_snapshot(progress_cb=lambda d, t: print(f"\r  pages {d}/{t}", end=""))
    print(f"\n拿到 {len(rows)}/{total} 只, 耗时 {time.time()-t0:.1f}s")
    print("样例:", json.dumps(rows[100], ensure_ascii=False))
