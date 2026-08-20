# -*- coding: utf-8 -*-
"""指标计算: MA / ATR / 区间分位 / 多周期涨幅
输入统一为 closes(list, 最后一项是今日最新价, 由调用方用快照zxj拼上)
"""


def ma(closes, period):
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def atr(highs, lows, closes, period=14):
    """Wilder ATR. 返回ATR绝对值; 数据不足返回None"""
    n = len(closes)
    if n < period + 1:
        return None
    trs = []
    for i in range(1, n):
        h, l, pc = highs[i], lows[i], closes[i - 1]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    # Wilder平滑
    a = sum(trs[:period]) / period
    for tr in trs[period:]:
        a = (a * (period - 1) + tr) / period
    return a


def position_pct(closes, lookback=250):
    """当前价在最近lookback日区间的分位数(0-100). 数据不足时按全部可用数据算"""
    win = closes[-lookback:] if len(closes) >= 20 else None
    if not win:
        return None
    lo, hi = min(win), max(win)
    if hi == lo:
        return 50.0
    return (closes[-1] - lo) / (hi - lo) * 100.0


def pct_change(closes, days):
    """最近days日涨跌幅(%). 数据不足返回None"""
    if len(closes) <= days:
        return None
    return (closes[-1] / closes[-1 - days] - 1) * 100.0
