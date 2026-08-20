# -*- coding: utf-8 -*-
"""漏斗编排: 四层筛选
L1 大盘环境(强/正常/偏弱) -> L2 板块强弱(正在加强/持续强势) -> L3 个股预过滤 -> L4 五类分类
所有分类原因由阈值确定性生成, 不依赖LLM
"""
import datetime
import json
import os
import time

from ..config import CONFIG
from ..data import snapshot, boards, kline
from ..data.indicators import ma, atr, position_pct, pct_change

DATA_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "data"))


def _today_str():
    return datetime.date.today().strftime("%Y-%m-%d")


def _f(x, nd=2):
    try:
        return round(float(x), nd)
    except (TypeError, ValueError):
        return None


def _sina_index_kline(idx_code, datalen=20):
    """新浪指数日K兜底(腾讯fqkline对指数偶发501限流)"""
    import urllib.request
    url = ("https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
           f"?symbol={idx_code}&scale=240&ma=no&datalen={datalen}")
    try:
        req = urllib.request.Request(url, headers={"Referer": "https://finance.sina.com.cn/",
                                                   "User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            arr = json.loads(r.read().decode("utf-8", "ignore"))
        return [(b["day"], float(b["open"]), float(b["close"]), float(b["high"]),
                 float(b["low"]), float(b["volume"])) for b in arr]
    except Exception:
        return None


def _detect_data_status(snap_rows):
    """pre_market/intraday/closed: 量比覆盖率+当前时间联合判定"""
    lb_nonzero = sum(1 for s in snap_rows if s["lb"] > 0)
    has_lb = lb_nonzero >= len(snap_rows) * 0.1
    now = datetime.datetime.now()
    mins = now.hour * 60 + now.minute
    if not has_lb:
        return "pre_market"
    if mins >= 15 * 60 + 5 or now.weekday() >= 5:
        return "closed"
    return "intraday"


# ============================================================ L1
def layer1_market(snapshot_rows, progress_cb=None):
    """大盘环境: 指数>MA10 且 宽度>阈值 且 涨停家数>阈值 = 强; 过两条=正常; 否则偏弱"""
    mc = CONFIG["market"]
    idx_code = mc["index_code"]
    today = _today_str()
    conditions = []

    # 指数K线: 腾讯主源, 新浪兜底(防501限流)
    bars = kline.strip_today(kline.fetch_kline(idx_code, days=mc["ma_period"] + 5) or [], today)
    src = "腾讯"
    if len(bars) < mc["ma_period"]:
        sb = _sina_index_kline(idx_code, mc["ma_period"] + 5)
        if sb:
            bars = kline.strip_today(sb, today)
            src = "新浪兜底"
    closes = [b[2] for b in bars]
    idx_ma = ma(closes, mc["ma_period"]) if len(closes) >= mc["ma_period"] else None
    idx_price = _index_snapshot(idx_code)

    if idx_price and idx_ma:
        above_ma = idx_price > idx_ma
        detail = (f"上证{'站上' if above_ma else '跌破'}MA{mc['ma_period']} "
                  f"(现价{idx_price:.0f} vs 均线{idx_ma:.0f}, K线源:{src})")
    else:
        above_ma = None
        detail = "指数数据缺失: " + ("现价获取失败" if not idx_price else "K线源异常(均线算不出)")
    conditions.append({"name": f"指数站上MA{mc['ma_period']}", "passed": above_ma, "detail": detail})

    # 宽度: 上涨家数占比(盘前zdf全0, 宽度为0)
    total = len(snapshot_rows)
    up_cnt = sum(1 for s in snapshot_rows if s["zdf"] > 0)
    breadth = up_cnt / total * 100 if total else 0
    breadth_ok = breadth >= mc["breadth_threshold"]
    conditions.append({"name": f"上涨宽度≥{mc['breadth_threshold']}%", "passed": breadth_ok,
                       "detail": f"上涨家数 {up_cnt}/{total} = {breadth:.1f}%"})

    # 涨停家数(用快照zdf判定, 30/68开头20cm其余10cm)
    lu_cnt = 0
    for s in snapshot_rows:
        c6 = s["code"][-6:]
        th = mc["limit_up_pct_cyb"] if c6.startswith(("30", "68")) else mc["limit_up_pct_main"]
        if s["zdf"] >= th:
            lu_cnt += 1
    lu_ok = lu_cnt >= mc["limit_up_threshold"]
    conditions.append({"name": f"涨停≥{mc['limit_up_threshold']}家", "passed": lu_ok,
                       "detail": f"涨停 {lu_cnt} 家"})

    passed = sum(1 for c in conditions if c["passed"] is True)
    env = "strong" if passed == 3 else ("normal" if passed == 2 else "weak")
    return {"env": env, "passed": passed, "conditions": conditions,
            "reasons": [c["detail"] for c in conditions],
            "index_price": idx_price, "index_ma": _f(idx_ma), "breadth": _f(breadth, 1),
            "up_count": up_cnt, "limit_up_count": lu_cnt}


def _index_snapshot(idx_code, retries=3):
    """指数现价. 新浪主源+腾讯备源+K线收盘兜底, 三级防护"""
    import urllib.request
    # 主: 新浪
    url = f"https://hq.sinajs.cn/list={idx_code}"
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"Referer": "https://finance.sina.com.cn/",
                                                       "User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=8) as r:
                text = r.read().decode("gbk", "ignore")
            price = float(text.split('"')[1].split(",")[3])
            if price > 0:
                return price
        except Exception:
            pass
        time.sleep(0.5 * (i + 1))
    # 备: 腾讯快照
    try:
        req = urllib.request.Request(f"https://qt.gtimg.cn/q={idx_code}",
                                     headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            parts = r.read().decode("gbk", "ignore").split("~")
        price = float(parts[3])
        if price > 0:
            return price
    except Exception:
        pass
    # 兜底: K线最后一根收盘
    bars = kline.fetch_kline(idx_code, days=5)
    if bars:
        return bars[-1][2]
    return None


# ============================================================ L2
def layer2_sectors(snapshot_rows, boards_map, progress_cb=None):
    """板块强弱聚合. 返回按强度排序的板块列表"""
    sc = CONFIG["sector"]
    snap_by_code = {s["code"][-6:]: s for s in snapshot_rows}
    # 盘前量比全为0, surge判定的量比条件自动豁免(数据不可用不是板块弱势)
    lb_available = sum(1 for s in snapshot_rows if s["lb"] > 0) > len(snapshot_rows) * 0.1

    results = []
    for bc, info in boards_map.items():
        members = info["members"]
        if len(members) < sc["min_members"] or len(members) > sc["max_members"]:
            continue
        rows = [snap_by_code[c] for c in members if c in snap_by_code]
        if len(rows) < sc["min_members"]:
            continue
        n = len(rows)
        avg_zdf = sum(r["zdf"] for r in rows) / n
        avg_d5 = sum(r["zdf_d5"] for r in rows) / n
        avg_d20 = sum(r["zdf_d20"] for r in rows) / n
        avg_lb = sum(r["lb"] for r in rows) / n
        up_ratio = sum(1 for r in rows if r["zdf"] > 0) / n * 100
        # 多周期全红: d5/d10/d20均>0
        multi_pos = sum(1 for r in rows if r["zdf_d5"] > 0 and r["zdf_d10"] > 0 and r["zdf_d20"] > 0) / n * 100

        state, reasons = _sector_state(avg_zdf, avg_lb, up_ratio, avg_d5, multi_pos, lb_available)
        score = (avg_zdf * 2 + avg_d5 + min(avg_lb, 3) * 2 + up_ratio / 20 + multi_pos / 20)
        results.append({
            "code": bc, "name": info["name"], "type": info["type"], "member_count": n,
            "avg_zdf": _f(avg_zdf), "avg_d5": _f(avg_d5), "avg_d20": _f(avg_d20),
            "avg_lb": _f(avg_lb), "up_ratio": _f(up_ratio, 1), "multi_pos_ratio": _f(multi_pos, 1),
            "state": state, "score": _f(score), "reasons": reasons,
        })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results


def _sector_state(avg_zdf, avg_lb, up_ratio, avg_d5, multi_pos, lb_available=True):
    sc = CONFIG["sector"]
    reasons = []
    lb_cond = avg_lb >= sc["surge_vol_ratio"] if lb_available else True
    surge = (avg_zdf >= sc["surge_day_gain"] and lb_cond
             and up_ratio >= sc["surge_up_ratio"])
    persist = (avg_d5 >= sc["persist_d5_gain"] and multi_pos >= sc["persist_multi_pos_ratio"])
    if surge:
        lb_txt = f"量比{avg_lb:.2f}≥{sc['surge_vol_ratio']}" if lb_available else "量比数据不可用(盘前),已豁免"
        reasons.append(f"当日进攻: 均涨{avg_zdf:.2f}%≥{sc['surge_day_gain']}%, "
                       f"{lb_txt}, 上涨占比{up_ratio:.0f}%≥{sc['surge_up_ratio']}%")
    if persist:
        reasons.append(f"持续强势: 5日均涨{avg_d5:.2f}%≥{sc['persist_d5_gain']}%, "
                       f"多周期全红占比{multi_pos:.0f}%≥{sc['persist_multi_pos_ratio']}%")
    if surge and persist:
        return "surging_persistent", reasons
    if surge:
        return "surging", reasons
    if persist:
        return "persistent", reasons
    if avg_zdf < -0.5 and avg_d5 < 0:
        return "weak", ["当日均跌且5日均跌"]
    return "neutral", ["未达加强/强势阈值"]


# ============================================================ L3
def layer3_stock_filter(candidate_codes, snap_by_code):
    """个股预过滤: ST/新股/价格/代码段"""
    sf = CONFIG["stock_filter"]
    kept, dropped = [], []
    for c in candidate_codes:
        s = snap_by_code.get(c)
        if not s:
            dropped.append((c, "快照无数据(停牌/退市)"))
            continue
        name = s["name"] or ""
        if sf["exclude_st"] and ("ST" in name or "st" in name):
            dropped.append((c, f"ST股({name})"))
            continue
        if sf["exclude_new_flag"] and name[:1] in ("N", "C"):
            dropped.append((c, f"新股({name})"))
            continue
        if s["zxj"] and s["zxj"] > sf["max_price"]:
            dropped.append((c, f"股价{s['zxj']:.1f}>{sf['max_price']}"))
            continue
        kept.append(c)
    return kept, dropped


# ============================================================ L4
def layer4_classify(stocks_metrics, snapshot_map):
    """五类分类决策树: 排除->高位->回调->启动->趋势, 命中即停. ATR作波动标注"""
    cc = CONFIG["classify"]
    buckets = {"launch": [], "trend": [], "high_position": [], "pullback": [], "excluded": [], "unclassified": []}

    for m in stocks_metrics:
        price = m["price"]
        ma20 = m["ma20"]
        d5, d10, d20 = m["d5"], m["d10"], m["d20"]
        pos = m["position_pct"]
        snap_lb = snapshot_map.get(m["code"], {}).get("lb", 0)
        lb = snap_lb if snap_lb > 0 else (m.get("lb_kline") or 0)  # 盘前用K线量比兜底
        ma20_dist = m["ma20_dist"]
        reasons = []
        cat = None

        # --- 排除层
        if d20 is not None and d20 >= cc["exclude_d20_overheat"]:
            cat = "excluded"
            reasons.append(f"20日涨幅{d20:.1f}%≥{cc['exclude_d20_overheat']}%, 过热")
        elif ma20_dist is not None and ma20_dist < 0 and d5 is not None and d5 <= cc["exclude_broken_d5"]:
            cat = "excluded"
            reasons.append(f"跌破MA20(偏离{ma20_dist:.1f}%)且5日跌{d5:.1f}%, 趋势破坏")
        # --- 高位观察
        elif pos is not None and pos >= cc["high_position_pct"] and ma20_dist is not None and ma20_dist >= cc["high_ma20_dist"]:
            cat = "high_position"
            reasons.append(f"价格位置{pos:.0f}%分位≥{cc['high_position_pct']}%, 距MA20 +{ma20_dist:.1f}%≥{cc['high_ma20_dist']}%")
        # --- 回调观察
        elif d5 is not None and d5 <= cc["pullback_d5"] and d10 is not None and d10 >= cc["pullback_d10_min"]:
            cat = "pullback"
            reasons.append(f"5日回调{d5:.1f}%≤{cc['pullback_d5']}%, 但10日仍涨{d10:.1f}%≥{cc['pullback_d10_min']}%(强势股回撤)")
        # --- 启动观察
        elif (d5 is not None and cc["launch_d5_low"] <= d5 <= cc["launch_d5_high"]
              and lb >= cc["launch_vol_ratio"]
              and ma20_dist is not None and cc["launch_ma20_low"] <= ma20_dist <= cc["launch_ma20_high"]
              and (pos is None or pos < cc["high_position_pct"])):
            cat = "launch"
            reasons.append(f"5日涨{d5:.1f}%落在启动区间[{cc['launch_d5_low']},{cc['launch_d5_high']}]%, "
                           f"量比{lb:.2f}≥{cc['launch_vol_ratio']}, 距MA20 {ma20_dist:+.1f}%贴近均线")
        # --- 趋势观察
        elif (d10 is not None and d10 > 0 and d20 is not None and d20 > 0
              and ma20_dist is not None and ma20_dist > 0
              and (pos is None or pos < cc["trend_position_max"])):
            cat = "trend"
            reasons.append(f"10日+{d10:.1f}%/20日+{d20:.1f}%多周期走强, 站上MA20({ma20_dist:+.1f}%), "
                           f"位置{pos:.0f}%<{cc['trend_position_max']}%")
        else:
            cat = "unclassified"
            reasons.append("未命中任何分类阈值")

        # --- ATR波动标注
        if m.get("atr_pct") is not None and m.get("atr"):
            today_zdf = snapshot_map.get(m["code"], {}).get("zdf", 0)
            if abs(today_zdf) > cc["atr_exceed_ratio"] * m["atr_pct"]:
                m["atr_flag"] = True
                reasons.append(f"今日波动过大: 涨跌{today_zdf:+.1f}% > {cc['atr_exceed_ratio']}倍ATR({m['atr_pct']:.2f}%)")
            else:
                m["atr_flag"] = False

        m["category"] = cat
        m["reasons"] = reasons
        m["lb"] = _f(lb)
        buckets[cat].append(m)

    for b in buckets.values():
        b.sort(key=lambda x: (x.get("d5") or 0), reverse=True)
    return buckets


def _compute_stock_metrics(code, kbars, snap):
    """拼今日价 -> 计算 MA20/ATR/分位/多周期涨幅"""
    today = _today_str()
    kbars = kline.strip_today(kbars, today)
    price = snap.get("zxj") or (kbars[-1][2] if kbars else None)
    if not kbars or not price:
        return None
    closes = [b[2] for b in kbars]
    highs = [b[3] for b in kbars]
    lows = [b[4] for b in kbars]
    # 盘前/未开盘: zxj==最后一根K线收盘, 不重复追加(否则多周期涨幅全部错位一天)
    if abs(price - closes[-1]) > 1e-6:
        closes.append(price)
        highs.append(price)
        lows.append(price)

    ma20 = ma(closes, 20)
    ma5 = ma(closes, 5)
    ma10 = ma(closes, 10)
    cc = CONFIG["classify"]
    atr_v = atr(highs, lows, closes, cc["atr_period"])
    # 量比兜底: 快照lb盘前为0时, 用K线算"最新日量/前5日均量"
    lb_kline = None
    vols = [b[5] for b in kbars]
    if len(vols) >= 6 and vols[-1] > 0:
        avg5v = sum(vols[-6:-1]) / 5
        if avg5v > 0:
            lb_kline = round(vols[-1] / avg5v, 2)
    return {
        "code": code,
        "name": snap.get("name"),
        "price": _f(price),
        "ma5": _f(ma5),
        "ma10": _f(ma10),
        "ma20": _f(ma20),
        "ma20_dist": _f((price / ma20 - 1) * 100) if ma20 else None,
        "d5": _f(pct_change(closes, 5)),
        "d10": _f(pct_change(closes, 10)),
        "d20": _f(pct_change(closes, 20)),
        "position_pct": _f(position_pct(closes), 1),
        "atr": _f(atr_v),
        "atr_pct": _f(atr_v / price * 100) if atr_v else None,
        "atr_flag": None,
        "lb_kline": lb_kline,
        "list_days": len(kbars),
    }


# ============================================================ 总编排
def run_funnel(force_boards=False, progress_cb=None):
    """跑完整漏斗. progress_cb(stage, detail, pct). 返回结果dict"""
    t0 = time.time()

    def prog(stage, detail, pct):
        if progress_cb:
            progress_cb(stage, detail, pct)

    # L0 板块缓存
    prog("boards", "加载板块映射", 2)
    boards_map = boards.build_board_cache(force=force_boards)
    prog("boards", f"板块{len(boards_map)}个", 5)

    # 全A快照
    prog("snapshot", "拉取全A快照", 8)
    snap_rows, snap_total = snapshot.fetch_all_snapshot(progress_cb=lambda d, t: prog("snapshot", f"快照 {d}/{t}页", 8 + d / t * 20))
    snap_by_code = {s["code"][-6:]: s for s in snap_rows}
    data_status = _detect_data_status(snap_rows)

    # L1
    prog("L1", "大盘环境", 30)
    l1 = layer1_market(snap_rows)

    # L2
    prog("L2", "板块强弱", 35)
    sector_list = layer2_sectors(snap_rows, boards_map)
    strong_states = {"surging_persistent", "surging", "persistent"}
    strong_sectors = [s for s in sector_list if s["state"] in strong_states][:CONFIG["sector"]["top_n"]]

    # L3
    prog("L3", "个股预过滤", 40)
    candidates = []
    for s in strong_sectors:
        candidates.extend(boards_map[s["code"]]["members"])
    candidates = sorted(set(candidates))
    kept, dropped = layer3_stock_filter(candidates, snap_by_code)

    # K线拉取(只对kept)
    prog("kline", f"拉取K线 {len(kept)}只", 45)
    kdata = kline.fetch_klines_batch(kept, days=CONFIG["data"]["kline_days"],
                                     threads=CONFIG["data"]["kline_threads"],
                                     progress_cb=lambda d, t: prog("kline", f"K线 {d}/{t}", 45 + d / max(t, 1) * 40))

    # L4
    prog("L4", "五类分类", 88)
    metrics = []
    min_days = CONFIG["stock_filter"]["min_list_days"]
    for c in kept:
        if c not in kdata:
            continue
        m = _compute_stock_metrics(c, kdata[c], snap_by_code.get(c, {}))
        if not m:
            continue
        if m["list_days"] < min_days:
            continue  # 次新股剔除(无标注, 静默)
        metrics.append(m)
    buckets = layer4_classify(metrics, snap_by_code)

    # 板块归属标注
    prog("annotate", "标注板块归属", 93)
    s2b = _build_s2b(boards_map)
    for cat_stocks in buckets.values():
        for m in cat_stocks:
            m["boards"] = [boards_map[bc]["name"] for bc in s2b.get(m["code"], []) if bc in boards_map][:3]

    # 第五层: 评分+行动建议+交易计划
    prog("scoring", "评分与行动建议", 96)
    from .scoring import score_stock, assign_action, trade_plan
    actions = {"enter": [], "watch": [], "no_chase": [], "avoid": []}
    for cat_stocks in buckets.values():
        for m in cat_stocks:
            si = score_stock(m, l1["env"])
            m["score"] = si["total"]
            m["score_breakdown"] = si["breakdown"]
            m["market_adj"] = si["market_adj"]
            act = assign_action(m, si)
            m["action"] = act
            if act in ("enter", "watch"):
                m["plan"] = trade_plan(m, act)
            actions[act].append(m)
    for lst in actions.values():
        lst.sort(key=lambda x: x["score"], reverse=True)

    prog("done", "完成", 100)
    result = {
        "meta": {
            "run_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "data_status": data_status,
            "date": _today_str(),
            "snapshot_total": snap_total,
            "candidate_count": len(candidates),
            "kline_count": len(kdata),
            "elapsed_s": round(time.time() - t0, 1),
        },
        "layer1": l1,
        "layer2": sector_list,
        "strong_sectors": [s["name"] for s in strong_sectors],
        "layer3_dropped_sample": dropped[:30],
        "layer4": buckets,
        "actions": actions,
    }
    return result


def _build_s2b(boards_map):
    s2b = {}
    for bc, info in boards_map.items():
        for c in info["members"]:
            s2b.setdefault(c, []).append(bc)
    return s2b


def save_result(result):
    """双写: result.json(最新展示) + history/YYYY-MM-DD.json(历史归档,同日覆盖)"""
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, "result.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, default=str)
    # 历史归档
    hist_dir = os.path.join(DATA_DIR, "history")
    os.makedirs(hist_dir, exist_ok=True)
    hist_path = os.path.join(hist_dir, f"{result['meta']['date']}.json")
    with open(hist_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, default=str)
    return path


def list_history():
    """历史归档列表, 倒序(最新在前)"""
    hist_dir = os.path.join(DATA_DIR, "history")
    if not os.path.isdir(hist_dir):
        return []
    files = sorted([f[:-5] for f in os.listdir(hist_dir) if f.endswith(".json")], reverse=True)
    out = []
    for date in files:
        try:
            with open(os.path.join(hist_dir, date + ".json"), encoding="utf-8") as f:
                d = json.load(f)
            acts = d.get("actions", {})
            out.append({
                "date": date,
                "env": d.get("layer1", {}).get("env"),
                "enter_count": len(acts.get("enter", [])),
                "watch_count": len(acts.get("watch", [])),
            })
        except Exception:
            continue
    return out


def load_history(date):
    """读某日归档"""
    path = os.path.join(DATA_DIR, "history", f"{date}.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def reconcile_history(date, with_exit_sim=True):
    """对账: 历史某日可介入股, 卖出模拟(逐日K线回放判定卖出点) + 当前持仓盈亏"""
    hist = load_history(date)
    if not hist:
        return None
    enters = hist.get("actions", {}).get("enter", [])
    if not enters:
        return {"date": date, "items": [], "msg": "当日无可介入股"}

    from .exit_sim import simulate_exit
    from concurrent.futures import ThreadPoolExecutor
    items = []
    for m in enters:
        entry = (m.get("plan") or {}).get("entry") or m["price"]
        stop = (m.get("plan") or {}).get("stop")
        item = {
            "code": m["code"], "name": m["name"], "score": m.get("score"),
            "entry": entry, "stop": stop,
        }
        items.append(item)

    def sim_one(item):
        if item["entry"] and item["stop"]:
            s = simulate_exit(item["code"], item["entry"], item["stop"], date)
            item.update(s)
            item["sold"] = s["sell_date"] is not None
            item["win"] = (s["return_pct"] or 0) > 0
        return item

    with ThreadPoolExecutor(max_workers=8) as ex:
        items = list(ex.map(sim_one, items))

    items.sort(key=lambda x: (x.get("return_pct") if x.get("return_pct") is not None else -999), reverse=True)
    valid = [x["return_pct"] for x in items if x.get("return_pct") is not None]
    win = sum(1 for x in items if x.get("win"))
    sold_cnt = sum(1 for x in items if x.get("sold"))
    return {
        "date": date, "items": items,
        "stats": {
            "count": len(items), "sold": sold_cnt, "holding": len(items) - sold_cnt,
            "win_rate": round(win / len(items) * 100, 1) if items else None,
            "avg_return": round(sum(valid) / len(valid), 2) if valid else None,
        },
    }
