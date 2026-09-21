# -*- coding: utf-8 -*-
"""持仓去留判定: 四条确定性卖出规则(只认收盘价, 无任何LLM)
规则与 config.CONFIG["exit_rules"] 阈值一致, 语义不许改:
  1. 硬止损:   最新收盘价 < 止损价 -> 明日开盘卖出
  2. 趋势破坏: 连续2个交易日收盘价 < MA10 -> 明日开盘卖出
  3. 时间止损: 持有交易日数 >= 10 且 浮盈 < 5% -> 明日开盘卖出
  4. 移动止盈: peak浮盈 >= 10% 且 从peak回撤 >= 7% -> 明日开盘卖出
盘中(15:00前)K线当日行视为脏数据必须剥离, 只认已收盘的完整日。
"""
import datetime
import json
import os
import threading

from ..config import CONFIG
from ..data import kline

DATA_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "data"))
HOLDINGS_FILE = os.path.join(DATA_DIR, "holdings.json")

_lock = threading.Lock()
_kline_cache = {}   # code -> (date_str, rows)  当日缓存, 同一天不重复拉


def _today():
    return datetime.date.today().strftime("%Y-%m-%d")


def _before_close():
    """A股15:00收盘; 15:00之前视为盘中, 今日K线行是脏数据必须剥离"""
    now = datetime.datetime.now()
    return now.hour < 15 or (now.hour == 15 and now.minute < 1)


def load_holdings():
    """读持仓文件; 不存在/损坏返回空列表"""
    with _lock:
        try:
            with open(HOLDINGS_FILE, encoding="utf-8") as fp:
                data = json.load(fp)
            h = data.get("holdings", [])
            return h if isinstance(h, list) else []
        except (FileNotFoundError, json.JSONDecodeError, AttributeError):
            return []


def save_holdings(holdings):
    """原子写持仓文件"""
    with _lock:
        tmp = HOLDINGS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fp:
            json.dump({"holdings": holdings}, fp, ensure_ascii=False, indent=2)
        os.replace(tmp, HOLDINGS_FILE)


def _get_kline(code):
    """取K线(当日缓存); 盘中剥离今日行, 收盘后保留完整日"""
    today = _today()
    if code in _kline_cache and _kline_cache[code][0] == today:
        return _kline_cache[code][1]
    rows = kline.fetch_kline(code, days=CONFIG["data"]["kline_days"])
    rows = rows or []
    if _before_close():
        rows = kline.strip_today(rows, today)
    _kline_cache[code] = (today, rows)
    return rows


def _ma_series(closes, period):
    """滚动MA序列, 与closes等长, 前period-1个为None"""
    out = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        out[i] = sum(closes[i - period + 1:i + 1]) / period
    return out


def _holding_days(buy_date, dates):
    """持有交易日数: 买入日(含)到最新收盘日的交易日数"""
    if not buy_date:
        return len(dates)
    return sum(1 for d in dates if d >= buy_date)


def judge_one(h):
    """对单只持仓判定, 返回含全部状态的dict(判定失败返回带error的dict)"""
    code = str(h.get("code", "")).strip()
    name = str(h.get("name", "") or code)
    out = {"code": code, "name": name, "verdict": "数据不足", "error": None,
           "last_close": None, "cost": h.get("cost"), "stop": h.get("stop"),
           "qty": h.get("qty", 0), "buy_date": h.get("buy_date", ""),
           "ma10": None, "earn": None, "peak_earn": None, "hold_days": 0,
           "rules": [], "triggered_rules": []}
    try:
        cost = float(h["cost"])
        stop = float(h["stop"])
    except (KeyError, TypeError, ValueError):
        out["error"] = "成本价或止损价缺失"
        out["verdict"] = "数据错误"
        return out

    rows = _get_kline(code)
    if not rows:
        out["error"] = "K线获取失败"
        return out

    dates = [r[0] for r in rows]
    closes = [r[2] for r in rows]
    last_close = closes[-1]
    ma10_now = None

    ex = CONFIG["exit_rules"]
    ma_p = ex["break_ma_period"]
    ma10s = _ma_series(closes, ma_p)
    if ma10s[-1] is not None:
        ma10_now = ma10s[-1]

    # 买入以来的最高收盘价(peak)
    buy_idx = 0
    bd = str(h.get("buy_date", "") or "")
    if bd:
        for i, d in enumerate(dates):
            if d >= bd:
                buy_idx = i
                break
    peak = max(closes[buy_idx:])
    earn = (last_close / cost - 1) * 100
    peak_earn = (peak / cost - 1) * 100
    dd_from_peak = (peak - last_close) / peak * 100 if peak > 0 else 0.0
    hold_days = _holding_days(bd, dates)

    # ---- 规则1: 硬止损 ----
    r1 = last_close < stop
    r1_dist = (stop - last_close) / last_close * 100  # 正=已跌破, 负=距离止损
    rules = [{
        "rule": "硬止损",
        "triggered": r1,
        "status": "triggered" if r1 else ("watch" if r1_dist >= -1.0 else "safe"),
        "note": (f"现价 {last_close:.2f} 跌破止损 {stop:.2f}" if r1
                 else f"现价 {last_close:.2f} 高于止损 {stop:.2f}, 距止损 {abs(r1_dist):.2f}%"),
    }]

    # ---- 规则2: 趋势破坏(连续2日收盘 < MA10) ----
    under = 0
    for i in range(len(closes) - 1, -1, -1):
        if ma10s[i] is not None and closes[i] < ma10s[i]:
            under += 1
        else:
            break
    r2 = under >= ex["break_ma_days"]
    rules.append({
        "rule": "趋势破坏",
        "triggered": r2,
        "status": "triggered" if r2 else ("watch" if under == 1 else "safe"),
        "note": (f"连续{under}个交易日收盘低于MA10({ma10_now:.2f})" if r2
                 else (f"MA10下方第{under}天, 再{ex['break_ma_days'] - under}天触发"
                       if under > 0 else f"收盘 {last_close:.2f} 站上 MA10 {ma10_now:.2f}")),
    })

    # ---- 规则3: 时间止损(持有>=N日 且 浮盈<5%) ----
    r3 = hold_days >= ex["time_stop_days"] and earn < ex["time_stop_min_gain"]
    rules.append({
        "rule": "时间止损",
        "triggered": r3,
        "status": "triggered" if r3 else ("watch" if hold_days >= ex["time_stop_days"] * 0.7 else "safe"),
        "note": (f"持有 {hold_days} 个交易日且浮盈 {earn:+.2f}% 未达 {ex['time_stop_min_gain']:.0f}%"
                 if r3 else f"持有 {hold_days}/{ex['time_stop_days']} 个交易日, 浮盈 {earn:+.2f}%"),
    })

    # ---- 规则4: 移动止盈(peak浮盈>=10% 且 从peak回撤>=7%) ----
    r4 = peak_earn >= ex["trail_activate_pct"] and dd_from_peak >= ex["trail_pct"]
    r4_note = (f"peak浮盈 {peak_earn:+.2f}% 回撤 {dd_from_peak:.2f}% 达到卖出线"
               if r4 else
               (f"peak浮盈 {peak_earn:+.2f}% 回撤 {dd_from_peak:.2f}%, 距{ex['trail_pct']:.0f}%线还差 {ex['trail_pct'] - dd_from_peak:.2f}%"
                if peak_earn >= ex["trail_activate_pct"]
                else f"peak浮盈 {peak_earn:+.2f}%(激活需 {ex['trail_activate_pct']:.0f}%), 回撤 {dd_from_peak:.2f}%"))
    rules.append({
        "rule": "移动止盈",
        "triggered": r4,
        "status": "triggered" if r4 else ("watch" if peak_earn >= ex["trail_activate_pct"] * 0.7 else "safe"),
        "note": r4_note,
    })

    triggered = [r["rule"] for r in rules if r["triggered"]]
    out.update({
        "verdict": "明日开盘卖出" if triggered else "继续持有",
        "last_close": round(last_close, 3),
        "stop": stop,
        "ma10": round(ma10_now, 3) if ma10_now is not None else None,
        "earn": round(earn, 2),
        "peak_earn": round(peak_earn, 2),
        "dd_from_peak": round(dd_from_peak, 2),
        "hold_days": hold_days,
        "peak": round(peak, 3),
        "rules": rules,
        "triggered_rules": triggered,
    })
    return out


def judge_all():
    """对全部持仓逐只判定"""
    holdings = load_holdings()
    return [judge_one(h) for h in holdings]


def find_from_archive(code6):
    """在最近归档(history最新->旧)的 layer4/actions 里查找名称与plan.stop
    返回 (name, stop) 或 (None, None)
    """
    from .pipeline import list_history, load_history
    target = str(code6).lower()[-6:]
    hist = list_history()
    dates = [str(h.get("date", "")) for h in hist
             if isinstance(h, dict) and h.get("date")]
    for d in sorted(set(dates), reverse=True):
        hist_day = load_history(d)
        if not hist_day:
            continue
        for sec_key in ("layer4", "actions"):
            sec = hist_day.get(sec_key)
            items = []
            if isinstance(sec, dict):
                for v in sec.values():
                    if isinstance(v, list):
                        items.extend(v)
            elif isinstance(sec, list):
                items = sec
            for it in items:
                if not isinstance(it, dict):
                    continue
                if str(it.get("code", "")).lower()[-6:] == target:
                    plan = it.get("plan") or {}
                    return it.get("name"), plan.get("stop")
    return None, None
