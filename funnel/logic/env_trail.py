# -*- coding: utf-8 -*-
"""持仓环境轨迹 —— 纯观测记录

每个交易日收盘后, 把当日大盘环境(strong/normal/weak)追加到每只持仓票名下,
形成该票在持有期间的环境序列, 供事后判断"环境轨迹"与持仓结果是否真的相关。

明确不做的事:
  - 不产生任何预警、不弹提示
  - 不修改任何卖出规则、不动 config 里的任何阈值
  - 不触发减仓/收紧止损等任何动作
这份数据只用于"以后能不能证明环境有用", 现在先攒样本。
"""
import datetime
import json
import os
import threading

DATA_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "data"))
TRAIL_FILE = os.path.join(DATA_DIR, "env_trail.json")

_lock = threading.Lock()
MAX_KEEP = 250          # 每只票最多保留的环境记录条数


def _today():
    return datetime.date.today().strftime("%Y-%m-%d")


def load_trail():
    """读全部环境轨迹: {code: {"name":..., "trail":[{"date":...,"env":...}]}}"""
    with _lock:
        try:
            with open(TRAIL_FILE, encoding="utf-8") as fp:
                data = json.load(fp)
            return data if isinstance(data, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError):
            return {}


def save_trail(trail):
    """原子写环境轨迹"""
    with _lock:
        tmp = TRAIL_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fp:
            json.dump(trail, fp, ensure_ascii=False, indent=2)
        os.replace(tmp, TRAIL_FILE)


def get_trail(code):
    """取某只票的环境序列"""
    rec = load_trail().get(str(code).strip())
    return rec.get("trail", []) if isinstance(rec, dict) else []


def read_today_env():
    """从当日 result.json 读大盘环境"""
    path = os.path.join(DATA_DIR, "result.json")
    try:
        with open(path, encoding="utf-8") as fp:
            d = json.load(fp)
        return (d.get("layer1") or {}).get("env")
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def record_today(env=None):
    """把今日环境追加到每只持仓; 同一天重复调用会覆盖而非重复追加
    返回记录到的持仓数量
    """
    date = _today()
    if not env:
        env = read_today_env()
    if not env:
        return 0

    from .holding import load_holdings
    holdings = load_holdings()
    if not holdings:
        return 0

    trail = load_trail()
    n = 0
    for h in holdings:
        code = str(h.get("code", "")).strip()
        if not code:
            continue
        rec = trail.setdefault(code, {"name": h.get("name") or code, "trail": []})
        rec["name"] = h.get("name") or rec.get("name") or code
        rows = rec.get("trail") or []
        # 丢弃 >= 今天的记录, 保证同一天只有一条(幂等)
        rows = [x for x in rows if isinstance(x, dict) and str(x.get("date", "")) < date]
        rows.append({"date": date, "env": env})
        rec["trail"] = rows[-MAX_KEEP:]
        n += 1
    if n:
        save_trail(trail)
    return n
