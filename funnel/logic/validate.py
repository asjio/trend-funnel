# -*- coding: utf-8 -*-
"""前向收益验证口径(卖出/预警规则的唯一合格判定方式)

背景: 用"命中这组规则的票最终平均赚亏多少"来评价规则, 必然得到错误结论 ——
 因为"最终收益"里包含了规则触发之前就已经发生的盈亏, 还夹杂事后才知道的信息。
 正确的口径是前向收益: 从规则触发的那一刻往后看。

本模块对每个候选规则输出五个指标:
  hit_rate      在持有期内触发过的票占比(触发率, 100% 等于永远报警、没有区分度)
  lags_mean     触发时平均已持有几个交易日(太长 = 信号来得太晚)
  fwd_mean      触发后 -> 实际卖出 的前向收益均值(越低说明规则越有效)
  pair_mean     配对差额 = 继续持有到底 - 触发时就卖
                负值 = 按规则卖出更好(规则有效); 正值 = 早卖反而少赚
  correct_rate  配对差额 < 0 的占比, 必须 > 50% 才算比抛硬币强

判定标准: 只有 fwd_mean 明显低于基准 且 correct_rate 明显 > 50% 的规则, 才配上线。
"""
import json
import os
import statistics
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

DATA_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "data"))
HIST_DIR = os.path.join(DATA_DIR, "history")
CACHE_FILE = os.path.join(DATA_DIR, "validate_cache.json")
OUT_FILE = os.path.join(DATA_DIR, "validation.json")

BASE_DELAY = 1          # 规则在收盘后触发 -> 最快次日执行, 前向收益从次日算起


def _ma_series(closes, period):
    out = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        out[i] = sum(closes[i - period + 1:i + 1]) / period
    return out


def _env_by_date():
    """读全部归档日的大盘环境: {date: env}"""
    out = {}
    if not os.path.isdir(HIST_DIR):
        return out
    for f in sorted(os.listdir(HIST_DIR)):
        if not f.endswith(".json"):
            continue
        try:
            with open(os.path.join(HIST_DIR, f), encoding="utf-8") as fp:
                d = json.load(fp)
            env = (d.get("layer1") or {}).get("env")
            if env:
                out[f[:-5]] = env
        except (json.JSONDecodeError, OSError):
            continue
    return out


def _collect_jobs(env):
    """收集全部可介入信号"""
    jobs = []
    if not os.path.isdir(HIST_DIR):
        return jobs
    for f in sorted(os.listdir(HIST_DIR)):
        if not f.endswith(".json"):
            continue
        date = f[:-5]
        try:
            with open(os.path.join(HIST_DIR, f), encoding="utf-8") as fp:
                d = json.load(fp)
        except (json.JSONDecodeError, OSError):
            continue
        for m in (d.get("actions") or {}).get("enter") or []:
            plan = m.get("plan") or {}
            entry = plan.get("entry") or m.get("price")
            stop = plan.get("stop")
            if not (entry and stop):
                continue
            jobs.append({"date": date, "code": str(m.get("code", "")).strip(),
                         "name": m.get("name"), "entry": float(entry), "stop": float(stop)})
    return jobs


def _build_rows(jobs, env):
    """拉取K线, 构造每笔信号的持有期序列"""
    from ..data import kline

    def work(j):
        try:
            bars = kline.fetch_kline(j["code"], days=80)
        except Exception:
            return None
        if not bars:
            return None
        fut = [b for b in bars if b[0] > j["date"]]
        if len(fut) < 2:
            return None
        pre = [b[2] for b in bars if b[0] <= j["date"]]
        entry = j["entry"]
        return {"date": j["date"], "code": j["code"], "name": j["name"],
                "entry": entry, "stop": j["stop"],
                "fut": [[b[0], b[2]] for b in fut],        # [date, close]
                "pre": pre,
                "env": [env.get(b[0]) for b in fut]}

    rows = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        for i, r in enumerate(ex.map(work, jobs), 1):
            if r:
                rows.append(r)
            if i % 100 == 0:
                print(f"  ...{i}/{len(jobs)}", flush=True)
    return rows


def _replay_from(row, start, stop=None):
    """从 start 索引起回放剩余K线, 复刻 exit_sim 四规则(顺序一致)
    返回 (卖出收益%, 执行价, 终点 close)
    """
    from ..config import CONFIG
    er = CONFIG["exit_rules"]
    period = er["break_ma_period"]
    base = len(row["pre"])
    closes = list(row["pre"]) + [c for _, c in row["fut"]]     # 一次性构建, 避免循环内重建
    entry = row["entry"]
    stop_price = row["stop"] if stop is None else stop
    exec_price = row["fut"][start][1]
    peak = max([entry] + [c for _, c in row["fut"][:start + 1]])
    below = 0
    # 触发日当天已经持有且未卖出(触发发生在收盘后), 因此规则判定从次日起才开始
    for i in range(start + 1, len(row["fut"])):
        gi = base + i
        close = closes[gi]
        peak = max(peak, close)
        gain = (close / entry - 1) * 100
        pgain = (peak / entry - 1) * 100
        if close < stop_price:
            return round(gain, 2), exec_price, close
        if pgain >= er["trail_activate_pct"] and (peak - close) / peak * 100 >= er["trail_pct"]:
            return round(gain, 2), exec_price, close
        ma_n = sum(closes[gi - period + 1:gi + 1]) / period if gi + 1 >= period else None
        below = below + 1 if (ma_n is not None and close < ma_n) else 0
        if below >= er["break_ma_days"]:
            return round(gain, 2), exec_price, close
        if i + 1 >= er["time_stop_days"] and gain < er["time_stop_min_gain"]:
            return round(gain, 2), exec_price, close
    last_close = row["fut"][-1][1]
    return round((last_close / entry - 1) * 100, 2), exec_price, last_close


def _hit_index(trail, rule):
    """返回规则首次触发的索引, 未触发返回 None"""
    kind = rule["kind"]
    n = rule["n"]
    if kind == "consec_weak":
        streak = 0
        for i, e in enumerate(trail):
            streak = streak + 1 if e == "weak" else 0
            if streak >= n:
                return i
        return None
    if kind == "cum_weak":
        cnt = 0
        for i, e in enumerate(trail):
            if e == "weak":
                cnt += 1
            if cnt >= n:
                return i
        return None
    if kind == "always_at":          # 固定第 n 天, 用作基准对照
        return n - 1 if len(trail) >= n else None
    return None


DEFAULT_RULES = [
    {"key": "base_day3", "name": "基准: 持有第3日", "desc": "对照基线, 第3日收盘无条件卖",
     "kind": "always_at", "n": 3},
    {"key": "warn_2w", "name": "连续2日弱势", "desc": "环境连续2日为 weak 时提示收紧",
     "kind": "consec_weak", "n": 2},
    {"key": "warn_3w", "name": "连续3日弱势", "desc": "环境连续3日为 weak 时提示收紧",
     "kind": "consec_weak", "n": 3},
    {"key": "warn_cum3w", "name": "累计3个弱势日", "desc": "持有期内累计出现3个 weak 日",
     "kind": "cum_weak", "n": 3},
]


def eval_rules(rebuild=False, extra_rules=None):
    """对全部候选规则做前向收益验证, 结果写入 data/validation.json"""
    env = _env_by_date()
    jobs = _collect_jobs(env)
    # 增量缓存: 重复运行时只补缺失的信号, 避免单次拉取被超时打断后前功尽弃
    sig = sorted(env.keys())[-1] if env else ""
    cached = []
    if not rebuild:
        try:
            with open(CACHE_FILE, encoding="utf-8") as fp:
                cached = json.load(fp).get("rows") or []
        except (FileNotFoundError, json.JSONDecodeError, AttributeError):
            cached = []
    known = {(r.get("code"), r.get("date")) for r in cached}
    missing = [j for j in jobs if (j["code"], j["date"]) not in known]
    if not missing:
        rows = cached
    else:
        print(f"  缓存 {len(cached)} 条, 待补 {len(missing)} 条", flush=True)
        rows = list(cached)
        batch_size = 300          # 分批拉取并落盘, 中途被打断也不丢已完成的部分
        for i in range(0, len(missing), batch_size):
            rows += _build_rows(missing[i:i + batch_size], env)
            try:
                with open(CACHE_FILE, "w", encoding="utf-8") as fp:
                    json.dump({"sig": sig, "rows": rows}, fp, ensure_ascii=False)
            except OSError:
                pass
            print(f"  已累计 {len(rows)} 条", flush=True)

    rules = DEFAULT_RULES + (extra_rules or [])
    out_rules = []

    for rule in rules:
        hit = 0
        lags = []
        fwds = []
        pairs = []
        # 每行的原始持仓路径只算一次: "继续持有到底"的结果不应因为
        # "我们从中间某天开始观察"而改变(从中间重开会重置 peak/连破计数等状态)
        fulls = {}
        for r in rows:
            idx = _hit_index(r["env"], rule)
            if idx is None:
                continue
            exec_i = idx + BASE_DELAY
            if exec_i >= len(r["fut"]):
                continue
            key = id(r)
            if key not in fulls:
                fulls[key] = _replay_from(r, 0)
            ret_full, _, term_full = fulls[key]
            hit += 1
            lags.append(idx + 1)
            exec_price = r["fut"][exec_i][1]
            gain_at = (exec_price / r["entry"] - 1) * 100
            fwds.append((term_full / exec_price - 1) * 100)
            pairs.append(ret_full - gain_at)          # 继续持有 - 触发时卖出

        if hit < 20:
            out_rules.append({"key": rule["key"], "name": rule["name"], "desc": rule.get("desc", ""),
                              "sample": hit, "note": "样本不足"})
            continue
        out_rules.append({
            "key": rule["key"], "name": rule["name"], "desc": rule.get("desc", ""),
            "sample": hit,
            "hit_rate": round(hit / len(rows) * 100, 1),
            "lags_mean": round(statistics.mean(lags), 1),
            "fwd_mean": round(statistics.mean(fwds), 2),
            "fwd_med": round(statistics.median(fwds), 2),
            "fwd_down_rate": round(sum(1 for x in fwds if x < 0) / len(fwds) * 100, 1),
            "pair_mean": round(statistics.mean(pairs), 2),
            "correct_rate": round(sum(1 for x in pairs if x < 0) / len(pairs) * 100, 1),
        })

    base_rule = next((r for r in out_rules if r.get("key") == "base_day3"), None)
    out = {"as_of": sig, "n_days": len(env), "n_signals": len(rows),
           "baseline_fwd_mean": (base_rule or {}).get("fwd_mean"),
           "rules": out_rules}
    with open(OUT_FILE, "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=2)
    return out


def load_validation():
    try:
        with open(OUT_FILE, encoding="utf-8") as fp:
            return json.load(fp)
    except (FileNotFoundError, json.JSONDecodeError):
        return None
