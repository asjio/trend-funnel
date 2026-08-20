# -*- coding: utf-8 -*-
"""第五层: 介入合理度评分(0-100) + 四档行动建议 + 交易计划
评分是风险管理工具, 不是赚钱保证: 它给出介入的理由/价格/止损/失效条件,
让每次买入都有明确计划, 错了知道错在哪且亏损有限
"""
from ..config import CONFIG


def score_stock(m, env):
    """五维度评分. m=个股指标dict(含category), env=大盘环境
    返回 {total, breakdown: [{name, score, max, detail}], market_adj}"""
    cfg = CONFIG["scoring"]
    cc = CONFIG["classify"]
    breakdown = []

    # 1. 趋势分(30): 多周期同向 + 站上MA20
    d5, d10, d20 = m["d5"], m["d10"], m["d20"]
    trend = 0
    parts = []
    if d5 is not None and d5 > 0:
        trend += 8; parts.append("5日正")
    if d10 is not None and d10 > 0:
        trend += 8; parts.append("10日正")
    if d20 is not None and d20 > 0:
        trend += 8; parts.append("20日正")
    if m["ma20_dist"] is not None and m["ma20_dist"] > 0:
        trend += 6; parts.append("站上MA20")
    breakdown.append({"name": "趋势", "score": trend, "max": 30,
                      "detail": "、".join(parts) if parts else "多周期未同向"})

    # 2. 位置分(25): 250日分位越低越好
    pos = m["position_pct"]
    if pos is None:
        pos_score, pos_detail = 10, "位置数据缺失, 给中性分"
    elif pos < 30:
        pos_score, pos_detail = 25, f"低位({pos:.0f}%分位)"
    elif pos < 50:
        pos_score, pos_detail = 20, f"中低位({pos:.0f}%分位)"
    elif pos < 70:
        pos_score, pos_detail = 15, f"中位({pos:.0f}%分位)"
    elif pos < 90:
        pos_score, pos_detail = 8, f"中高位({pos:.0f}%分位)"
    else:
        pos_score, pos_detail = 2, f"高位({pos:.0f}%分位)"
    breakdown.append({"name": "位置", "score": pos_score, "max": 25, "detail": pos_detail})

    # 3. 量能分(20): 量比健康区间
    lb = m.get("lb") or 0
    if 1.2 <= lb <= 3.0:
        lb_score, lb_detail = 20, f"量比{lb:.2f}健康放量"
    elif 0.8 <= lb < 1.2:
        lb_score, lb_detail = 12, f"量比{lb:.2f}量能平稳"
    elif 3.0 < lb <= 5.0:
        lb_score, lb_detail = 10, f"量比{lb:.2f}放量偏大"
    elif lb > 5.0:
        lb_score, lb_detail = 4, f"量比{lb:.2f}异常放量"
    else:
        lb_score, lb_detail = 6, f"量比{lb:.2f}缩量"
    breakdown.append({"name": "量能", "score": lb_score, "max": 20, "detail": lb_detail})

    # 4. 波动分(15): ATR%适中+今日无异常波动
    atr_pct = m.get("atr_pct")
    if atr_pct is None:
        vol_score, vol_detail = 7, "ATR数据缺失"
    elif m.get("atr_flag"):
        vol_score, vol_detail = 3, f"今日波动超{cc['atr_exceed_ratio']}倍ATR, 信号不可信"
    elif atr_pct > 6:
        vol_score, vol_detail = 6, f"ATR {atr_pct:.1f}%波动过大"
    elif atr_pct > 4:
        vol_score, vol_detail = 10, f"ATR {atr_pct:.1f}%波动偏大"
    else:
        vol_score, vol_detail = 15, f"ATR {atr_pct:.1f}%波动正常"
    breakdown.append({"name": "波动", "score": vol_score, "max": 15, "detail": vol_detail})

    # 5. 启动质量分(10): 分类加成
    cat_bonus = {"launch": 10, "trend": 8, "pullback": 6, "high_position": 2,
                 "excluded": 0, "unclassified": 3}
    cat_score = cat_bonus.get(m["category"], 3)
    breakdown.append({"name": "形态", "score": cat_score, "max": 10,
                      "detail": {"launch": "启动形态", "trend": "趋势形态", "pullback": "回调形态",
                                 "high_position": "高位形态", "excluded": "已排除",
                                 "unclassified": "无明确形态"}.get(m["category"], "?")})

    total = sum(b["score"] for b in breakdown)
    adj = cfg["market_adj"].get(env, 0)
    total = max(0, min(100, total + adj))
    return {"total": total, "breakdown": breakdown, "market_adj": adj}


def assign_action(m, score_info):
    """四档行动建议: enter(可介入)/watch(观察等待)/no_chase(不追高)/avoid(回避)"""
    cfg = CONFIG["scoring"]
    total = score_info["total"]
    pos = m["position_pct"] if m["position_pct"] is not None else 100
    cat = m["category"]

    if cat == "excluded" or total < cfg["action_avoid_score"]:
        return "avoid"
    if pos >= cfg["action_nochase_pos"]:
        return "no_chase"
    if total >= cfg["action_enter_score"] and pos < cfg["action_enter_pos_max"]:
        return "enter"
    return "watch"


def trade_plan(m, action):
    """交易计划: 参考介入价/止损价/止损幅度/失效条件. 仅enter/watch生成"""
    cfg = CONFIG["scoring"]
    price = m["price"]
    ma5, ma10, ma20 = m["ma5"], m["ma10"], m["ma20"]
    atr_v = m["atr"]
    dist = m["ma20_dist"] if m["ma20_dist"] is not None else 99

    # 参考介入价
    if dist <= cfg["entry_near_ma_dist"]:
        entry = price
        entry_txt = f"现价附近({price}), 贴近MA20"
    elif dist <= cfg["entry_ma5_dist"] and ma5:
        entry = ma5
        entry_txt = f"回踩MA5({ma5})"
    elif ma10:
        entry = ma10
        entry_txt = f"回踩MA10({ma10})"
    else:
        entry = price
        entry_txt = f"现价({price})"

    # 止损: max(MA20, 入场价-2*ATR), 幅度超上限则收紧
    stop_candidates = [v for v in [ma20, entry - 2 * atr_v] if v and atr_v]
    stop = max(stop_candidates) if stop_candidates else entry * 0.92
    loss_pct = (entry - stop) / entry * 100
    if loss_pct > cfg["stop_max_loss_pct"]:
        stop = entry * (1 - cfg["stop_max_loss_pct"] / 100)
        loss_pct = cfg["stop_max_loss_pct"]

    # 失效条件
    invalid = [f"收盘跌破止损价{stop:.2f}(止损幅度{loss_pct:.1f}%)"]
    if ma20 and stop < ma20:
        invalid.append(f"跌破MA20({ma20})且无法快速收回")
    invalid.append("板块整体转弱(板块内上涨占比连续2日<40%)")

    # 卖出条件(四条规则, 任一触发即卖)
    from .exit_sim import exit_conditions_text
    exit_conds = exit_conditions_text(stop)

    return {
        "entry": _f(entry),
        "entry_txt": entry_txt,
        "stop": _f(stop),
        "loss_pct": _f(loss_pct, 1),
        "invalid_conditions": invalid,
        "exit_conditions": exit_conds,
    }


def _f(x, nd=2):
    try:
        return round(float(x), nd)
    except (TypeError, ValueError):
        return None


ACTION_LABELS = {
    "enter": {"txt": "可介入", "guide": "评分达标+位置不高+大盘不弱。按计划执行: 参考介入价买入, 设好止损, 单只仓位不超过总资金10-15%"},
    "watch": {"txt": "观察等待", "guide": "有可取之处但当前不是好的介入点(分数不够或位置偏高)。加入自选, 等回调或趋势进一步确认再看"},
    "no_chase": {"txt": "不追高", "guide": "趋势还在但位置已到250日区间90%分位以上。这个位置买入, 向上空间小向下空间大, 纪律上放弃"},
    "avoid": {"txt": "回避", "guide": "过热、趋势破坏或综合评分过低。不碰"},
}
