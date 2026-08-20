# -*- coding: utf-8 -*-
"""卖出模拟: 用历史K线逐日回放, 判定每只可介入股会在哪天、因什么原因卖出
四条规则(任一触发即卖):
  1. 硬止损: 收盘价 < 止损价
  2. 移动止盈: 最高盈利>=trail_activate% 且 从最高点回撤>=trail_pct%
  3. 趋势破坏: 收盘价连续break_ma_days日 < MA{break_ma_period}
  4. 时间止损: 持有满time_stop_days个交易日且盈利<time_stop_min_gain%
"""
from ..data.kline import fetch_kline, strip_today
from ..data.indicators import ma
from ..config import CONFIG
import datetime


def simulate_exit(code, entry_price, stop_price, entry_date):
    """回放entry_date之后的K线, 返回卖出模拟结果
    entry_date: 'YYYY-MM-DD' 格式的归档日期(介入日)
    返回 dict: {sell_date, sell_price, sell_reason, return_pct, held_days, peak_gain_pct}
    """
    er = CONFIG["exit_rules"]
    today = datetime.date.today().strftime("%Y-%m-%d")

    # 拉足够长的K线(entry_date后最多60个交易日)
    bars = fetch_kline(code, days=80)
    if not bars:
        return {"sell_date": None, "sell_price": None, "sell_reason": "K线获取失败",
                "return_pct": None, "held_days": 0, "peak_gain_pct": None}

    bars = strip_today(bars, today)
    # 只取entry_date之后的K线
    future_bars = [b for b in bars if b[0] > entry_date]
    if not future_bars:
        return {"sell_date": None, "sell_price": None, "sell_reason": "无后续K线",
                "return_pct": None, "held_days": 0, "peak_gain_pct": None}

    # 为了算MA, 需要entry_date之前的K线作为预热
    pre_bars = [b for b in bars if b[0] <= entry_date]
    pre_closes = [b[2] for b in pre_bars]

    peak_price = entry_price
    below_ma_count = 0
    held_days = 0

    for i, bar in enumerate(future_bars):
        date, open_p, close, high, low, vol = bar
        held_days += 1
        peak_price = max(peak_price, high)
        cur_gain = (close / entry_price - 1) * 100
        peak_gain = (peak_price / entry_price - 1) * 100

        # 规则1: 硬止损
        if close < stop_price:
            return {"sell_date": date, "sell_price": close, "sell_reason": "硬止损",
                    "return_pct": round(cur_gain, 2), "held_days": held_days,
                    "peak_gain_pct": round(peak_gain, 2)}

        # 规则2: 移动止盈
        if peak_gain >= er["trail_activate_pct"]:
            drawdown = (peak_price - close) / peak_price * 100
            if drawdown >= er["trail_pct"]:
                return {"sell_date": date, "sell_price": close, "sell_reason": "移动止盈",
                        "return_pct": round(cur_gain, 2), "held_days": held_days,
                        "peak_gain_pct": round(peak_gain, 2)}

        # 规则3: 趋势破坏(收盘价连续N日 < MA10)
        all_closes = pre_closes + [b[2] for b in future_bars[:i + 1]]
        ma_n = ma(all_closes, er["break_ma_period"])
        if ma_n is not None and close < ma_n:
            below_ma_count += 1
        else:
            below_ma_count = 0
        if below_ma_count >= er["break_ma_days"]:
            return {"sell_date": date, "sell_price": close, "sell_reason": f"趋势破坏(连续{below_ma_count}日破MA{er['break_ma_period']})",
                    "return_pct": round(cur_gain, 2), "held_days": held_days,
                    "peak_gain_pct": round(peak_gain, 2)}

        # 规则4: 时间止损
        if held_days >= er["time_stop_days"] and cur_gain < er["time_stop_min_gain"]:
            return {"sell_date": date, "sell_price": close, "sell_reason": f"时间止损({held_days}日仅+{cur_gain:.1f}%)",
                    "return_pct": round(cur_gain, 2), "held_days": held_days,
                    "peak_gain_pct": round(peak_gain, 2)}

    # 未触发任何卖出条件 -> 仍持有
    last = future_bars[-1]
    cur_gain = (last[2] / entry_price - 1) * 100
    peak_gain = (peak_price / entry_price - 1) * 100
    return {"sell_date": None, "sell_price": None, "sell_reason": "仍持有(未触发卖出)",
            "return_pct": round(cur_gain, 2), "held_days": held_days,
            "peak_gain_pct": round(peak_gain, 2)}


def exit_conditions_text(stop_price):
    """生成卖出条件文字(展示在交易计划里)"""
    er = CONFIG["exit_rules"]
    return [
        f"1. 硬止损: 收盘跌破{stop_price} -> 次日开盘卖",
        f"2. 移动止盈: 最高盈利达{er['trail_activate_pct']}%后, 从最高点回撤{er['trail_pct']}% -> 卖",
        f"3. 趋势破坏: 连续{er['break_ma_days']}日收盘低于MA{er['break_ma_period']} -> 卖",
        f"4. 时间止损: 持有{er['time_stop_days']}个交易日盈利不足{er['time_stop_min_gain']}% -> 卖",
    ]
