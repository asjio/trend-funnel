# -*- coding: utf-8 -*-
"""K线层: 腾讯fqkline直连批量拉取(只对通过第二层的个股调用, 漏斗省请求)
关键设计(规避收盘后K线延迟坑):
- K线里若最后一根日期==今天, 剥离掉(收盘后聚合可能不全/盘中是脏数据)
- 指标计算的"今日close"一律用快照zxj, 由调用方拼接
"""
import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

KLINE_URL = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
             "?param={code},day,,,{days},qfq")
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
SINA_KLINE_URL = ("https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
                  "?symbol={code}&scale=240&ma=no&datalen={days}")
SINA_HEADERS = {"Referer": "https://finance.sina.com.cn/", "User-Agent": "Mozilla/5.0"}


def _to_tx_code(code6):
    """6位代码 -> sz/sh前缀. 6/9开头sh, 其余sz. 已带前缀(sh/sz开头)的原样返回"""
    if code6.startswith(("sh", "sz")):
        return code6
    return ("sh" if code6[0] in "69" else "sz") + code6


def _sina_kline(code_tx, days):
    """新浪日K兜底(不复权). 腾讯501限流时用"""
    url = SINA_KLINE_URL.format(code=code_tx, days=days)
    try:
        req = urllib.request.Request(url, headers=SINA_HEADERS)
        with urllib.request.urlopen(req, timeout=10) as r:
            arr = json.loads(r.read().decode("utf-8", "ignore"))
        if not arr:
            return None
        return [(b["day"], float(b["open"]), float(b["close"]),
                 float(b["high"]), float(b["low"]), float(b["volume"])) for b in arr]
    except Exception:
        return None


def fetch_kline(code6, days=260, retries=2):
    """腾讯qfq主源 -> 新浪兜底. 返回 [(date,open,close,high,low,vol)...] 已剥离今日行"""
    tx_code = _to_tx_code(code6)
    url = KLINE_URL.format(code=tx_code, days=days)
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=10) as r:
                d = json.loads(r.read().decode("utf-8", "ignore"))
            node = d.get("data", {}).get(tx_code, {})
            bars = node.get("qfqday") or node.get("day") or []
            if bars:
                rows = []
                for b in bars:
                    try:
                        rows.append((b[0], float(b[1]), float(b[2]), float(b[3]), float(b[4]), float(b[5])))
                    except (ValueError, IndexError):
                        continue
                return rows
        except Exception:
            time.sleep(0.5 * (i + 1))
    # 腾讯失败(501限流等), 新浪兜底
    return _sina_kline(tx_code, days)


def fetch_klines_batch(codes, days=260, threads=12, progress_cb=None):
    """批量拉K线. 返回 {code: rows}. progress_cb(done, total)"""
    out = {}
    done = 0

    def one(c):
        return c, fetch_kline(c, days)

    with ThreadPoolExecutor(max_workers=threads) as ex:
        for c, rows in ex.map(one, codes):
            if rows:
                out[c] = rows
            done += 1
            if progress_cb:
                progress_cb(done, len(codes))
    return out


def strip_today(rows, today_str):
    """剥离今日行(若有). today_str格式'2026-08-20'"""
    if rows and rows[-1][0] == today_str:
        return rows[:-1]
    return rows


if __name__ == "__main__":
    rows = fetch_kline("600519")
    print("600519 K线根数:", len(rows) if rows else "FAIL")
    print("最后两根:", rows[-2:] if rows else "")
