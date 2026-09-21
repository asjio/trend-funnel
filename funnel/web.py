# -*- coding: utf-8 -*-
"""趋势筛选工作台 Web服务
启动: python -m funnel.web  -> 127.0.0.1:8768
单文件FastAPI内嵌HTML, 运行进度用轮询(GET /api/status)
"""
import json
import os
import threading
import datetime

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .logic.pipeline import run_funnel, save_result, list_history, load_history, reconcile_history
from .config import CONFIG

DATA_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "data"))
RESULT_FILE = os.path.join(DATA_DIR, "result.json")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

app = FastAPI()
# PWA静态资源(manifest/图标/sw), 用相对路径访问, nginx反代到子路径时不受影响
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

_state = {"running": False, "stage": "", "detail": "", "pct": 0, "error": None}
_lock = threading.Lock()


@app.post("/api/run")
def api_run():
    if _state["running"]:
        return JSONResponse({"ok": False, "msg": "正在运行中, 请稍候"})
    _state.update({"running": True, "stage": "", "detail": "启动", "pct": 0, "error": None})

    def worker():
        try:
            def prog(stage, detail, pct):
                with _lock:
                    _state.update({"stage": stage, "detail": detail, "pct": int(pct)})
            result = run_funnel(progress_cb=prog)
            save_result(result)
            with _lock:
                _state.update({"running": False, "pct": 100, "detail": "完成"})
        except Exception as e:
            with _lock:
                _state.update({"running": False, "error": repr(e)})

    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True}


@app.get("/api/status")
def api_status():
    with _lock:
        out = dict(_state)
    out["has_result"] = os.path.exists(RESULT_FILE)
    if out["has_result"]:
        out["result_mtime"] = datetime.datetime.fromtimestamp(
            os.path.getmtime(RESULT_FILE)).strftime("%Y-%m-%d %H:%M:%S")
    return out


@app.get("/api/result")
def api_result():
    if not os.path.exists(RESULT_FILE):
        return JSONResponse({"ok": False, "msg": "尚未运行, 请先点击运行"})
    with open(RESULT_FILE, encoding="utf-8") as f:
        return json.load(f)


@app.get("/api/history")
def api_history():
    return list_history()


@app.get("/api/history/{date}/reconcile")
def api_history_reconcile(date: str):
    import re as _re
    if not _re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        return JSONResponse({"ok": False, "msg": "日期格式错误"})
    r = reconcile_history(date)
    if r is None:
        return JSONResponse({"ok": False, "msg": f"无{date}的归档"})
    return r


@app.get("/api/validation")
def validation_get():
    from .logic.validate import load_validation
    v = load_validation()
    if v is None:
        return {"ok": False, "msg": "尚未生成验证结果, 点下面的按钮生成"}
    v["ok"] = True
    return v


@app.post("/api/validation/refresh")
def validation_refresh():
    from .logic.validate import eval_rules
    try:
        out = eval_rules()
        out["ok"] = True
        return out
    except Exception as e:
        return {"ok": False, "msg": str(e)}


@app.get("/api/holdings")
def holdings_get():
    from .logic.holding import judge_all
    from .logic.env_trail import load_trail
    try:
        hs = judge_all()
        trail = load_trail()
        for h in hs:
            rec = trail.get(str(h.get("code", "")).strip()) or {}
            h["env_trail"] = (rec.get("trail") or [])[-7:]
        return {"holdings": hs}
    except Exception as e:
        return {"holdings": [], "error": str(e)}


@app.post("/api/holdings")
async def holdings_post(request: Request):
    from .logic.holding import load_holdings, save_holdings, find_from_archive
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "msg": "JSON格式错误"}, status_code=400)
    code = str(body.get("code", "")).strip()
    if not code:
        return JSONResponse({"ok": False, "msg": "缺少股票代码"}, status_code=400)
    code6 = code.lower()
    if not code6.startswith(("sh", "sz")):
        code6 = ("sh" if code6[0] in "69" else "sz") + code6
    for f in ("cost", "qty", "buy_date"):
        if f not in body or body[f] in (None, ""):
            return JSONResponse({"ok": False, "msg": f"缺少必填字段 {f}"}, status_code=400)
    try:
        cost = float(body["cost"])
        qty = int(body["qty"])
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "msg": "成本价/数量格式错误"}, status_code=400)
    name, stop = find_from_archive(code6)
    if body.get("stop") not in (None, ""):
        stop = float(body["stop"])
    if stop is None:
        return JSONResponse({"ok": False, "msg": "未在最近归档匹配到止损价, 请补stop参数"}, status_code=400)
    if not body.get("name") and not name:
        name = code6
    holdings = load_holdings()
    for h in holdings:
        if str(h.get("code", "")).lower() == code6:
            return JSONResponse({"ok": False, "msg": "该股票已在持仓中"}, status_code=400)
    holdings.append({
        "code": code6, "name": body.get("name") or name,
        "cost": cost, "qty": qty, "buy_date": str(body["buy_date"]),
        "stop": stop,
    })
    save_holdings(holdings)
    return {"ok": True, "msg": "已添加", "name": body.get("name") or name, "stop": stop}


@app.delete("/api/holdings/{code}")
def holdings_delete(code: str):
    from .logic.holding import load_holdings, save_holdings
    target = code.lower()
    holdings = load_holdings()
    kept = [h for h in holdings if str(h.get("code", "")).lower() != target]
    if len(kept) == len(holdings):
        return JSONResponse({"ok": False, "msg": "未找到该持仓"}, status_code=404)
    save_holdings(kept)
    return {"ok": True, "msg": "已移除"}


@app.get("/", response_class=HTMLResponse)
def index():
    return PAGE


@app.get("/sw.js")
def service_worker():
    """SW必须部署在根路径才能控制整站(scope限制)"""
    from fastapi.responses import Response
    with open(os.path.join(STATIC_DIR, "sw.js"), encoding="utf-8") as f:
        return Response(content=f.read(), media_type="application/javascript")


@app.get("/manifest.json")
def manifest():
    """manifest部署在根路径: start_url/scope的相对'.'才能解析到应用首页"""
    with open(os.path.join(STATIC_DIR, "manifest.json"), encoding="utf-8") as f:
        return JSONResponse(content=json.load(f))


PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<meta name="theme-color" content="#1a6ee0">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<meta name="apple-mobile-web-app-title" content="趋势漏斗">
<link rel="manifest" href="manifest.json">
<link rel="icon" type="image/png" href="static/icon-192.png">
<link rel="apple-touch-icon" href="static/icon-192.png">
<title>趋势筛选工作台</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #f5f6f8; color: #1f2329; font: 14px/1.6 "Microsoft YaHei", sans-serif; padding: 28px 32px; }

/* ---------- 头部 ---------- */
.header-row { display: flex; justify-content: space-between; align-items: flex-end; gap: 20px; flex-wrap: wrap; margin-bottom: 18px; }
h1 { font-size: 22px; font-weight: 700; letter-spacing: .5px; }
.sub { color: #8a919f; font-size: 12px; margin-top: 3px; }
.nav-bar { display: flex; gap: 6px; flex-wrap: wrap; }
.nav-pill { padding: 5px 14px; border-radius: 999px; border: 1px solid #e2e5ea; background: #fff;
            color: #5a6270; font-size: 12px; cursor: pointer; transition: all .18s ease; }
.nav-pill:hover { border-color: #1a6ee0; color: #1a6ee0; transform: translateY(-1px); box-shadow: 0 2px 6px rgba(26,110,224,.15); }

/* ---------- 卡片 ---------- */
.card { background: #fff; border-radius: 10px; padding: 18px 22px; margin-bottom: 14px;
        box-shadow: 0 1px 3px rgba(20,30,60,.06);
        opacity: 0; transform: translateY(10px); animation: fadeUp .45s ease forwards; }
@keyframes fadeUp { to { opacity: 1; transform: none; } }
h2 { font-size: 15px; font-weight: 600; margin-bottom: 12px; }
h2 span { font-weight: 400; }
.row { display: flex; gap: 16px; flex-wrap: wrap; }
.hint { font-size: 12px; color: #9aa1ad; max-width: 280px; line-height: 1.5; }
.summary { border-left: 3px solid #1a6ee0; }

/* ---------- 按钮/进度 ---------- */
.btn { background: #1a6ee0; color: #fff; border: none; border-radius: 8px; padding: 9px 26px;
       font-size: 14px; cursor: pointer; transition: all .18s ease; box-shadow: 0 2px 6px rgba(26,110,224,.25); }
.btn:hover { transform: translateY(-1px); box-shadow: 0 4px 10px rgba(26,110,224,.35); }
.btn:active { transform: translateY(0); }
.btn:disabled { background: #9dbce8; cursor: not-allowed; transform: none; box-shadow: none; }
.progress-wrap { background: #e8ebef; border-radius: 5px; height: 8px; width: 340px; overflow: hidden; display: none; }
.progress-bar { background: linear-gradient(90deg, #1a6ee0, #4a90e8); height: 100%; width: 0%; transition: width .4s ease; }
.progress-txt { font-size: 12px; color: #666; margin-top: 4px; }

/* ---------- 徽章/颜色 ---------- */
.badge { display: inline-block; padding: 2px 10px; border-radius: 10px; font-size: 12px; border: 1px solid; }
.badge.pre { color: #b26a00; border-color: #e0c08a; background: #fdf6e8; }
.badge.closed { color: #1a7a3a; border-color: #a8d8b8; background: #eef8f1; }
.env-strong { color: #c0392b; font-weight: 700; }
.env-normal { color: #1a6ee0; font-weight: 700; }
.env-weak { color: #1a7a3a; font-weight: 700; }
.up { color: #c0392b; } .down { color: #1a7a3a; }

/* ---------- 表格 ---------- */
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th { text-align: left; padding: 7px 8px; border-bottom: 1px solid #e5e8ec; color: #8a919f; font-weight: 500;
     font-size: 12px; white-space: nowrap; position: sticky; top: 0; background: #fff; }
td { padding: 6px 8px; border-bottom: 1px solid #f2f3f5; white-space: nowrap; transition: background .15s; }
tr:hover td { background: #f7faff; }
tbody.fade-in { animation: fadeUp .3s ease; }
.scrollbox { max-height: 340px; overflow-y: auto; }
.ellip { display: inline-block; max-width: 300px; overflow: hidden; text-overflow: ellipsis;
         white-space: nowrap; vertical-align: bottom; }
.ellip.s { max-width: 150px; }

/* ---------- 历史复盘: 按日期分组 ---------- */
.rgroup { border: 1px solid #e8eaee; border-radius: 10px; margin-bottom: 9px; overflow: hidden; background: #fff; }
.rgroup-head { display: flex; align-items: center; gap: 9px; padding: 10px 14px; cursor: pointer;
               user-select: none; transition: background .15s; flex-wrap: wrap; background: #fafbfc; }
.rgroup-head:hover { background: #f1f4f9; }
.rgroup-head .arrow { display: inline-block; color: #8a919f; font-size: 11px; width: 10px; transition: transform .18s; }
.rgroup.open .rgroup-head .arrow { transform: rotate(90deg); }
.rgroup-head .gdate { font-size: 14px; font-weight: 700; font-variant-numeric: tabular-nums; }
.rgroup-head .genv { font-size: 11px; padding: 1px 7px; border-radius: 3px; background: #eef3fb; color: #1a6ee0; }
.rgroup-head .genv.weak { background: #eef8f1; color: #1a7a3a; }
.rgroup-head .genv.strong { background: #fdeeee; color: #c0392b; }
.rgroup-head .gmeta { font-size: 12px; color: #8a919f; }
.rgroup-head .gnum { margin-left: auto; font-size: 13px; font-weight: 600; font-variant-numeric: tabular-nums; }
.rgroup-body { display: none; padding: 10px 14px 14px; border-top: 1px solid #eef0f3; }
.rgroup.open .rgroup-body { display: block; }
.rgroup-body .sub-h { font-size: 12px; color: #8a919f; margin: 0 0 6px; }
.trend-panel { margin-bottom: 12px; padding: 10px 12px 6px; border: 1px solid #e8eaee;
               border-radius: 10px; background: #fafbfc; }
.trend-panel .tp-title { font-size: 12px; color: #8a919f; margin-bottom: 6px; }
.trend-panel svg { display: block; width: 100%; height: auto; }
.bar-row { display: flex; align-items: center; gap: 6px; font-size: 11px; margin-bottom: 3px; }
.bar-row .bn { width: 84px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #5a6270; }
.bar-row .bt { flex: 1; height: 11px; background: #f0f2f5; border-radius: 2px; position: relative; overflow: hidden; }
.bar-row .bt::after { content: ""; position: absolute; left: 50%; top: 0; bottom: 0; width: 1px; background: #e3e7ec; }
.bar-row .bf { position: absolute; top: 0; bottom: 0; border-radius: 2px; z-index: 1; }
.bar-row .bv { width: 58px; text-align: right; font-variant-numeric: tabular-nums; }
.hbar-tools { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; flex-wrap: wrap; }
.hbar-tools .nav-pill { padding: 4px 12px; }

/* ---------- 统计数字 ---------- */
.stat { display: flex; gap: 32px; flex-wrap: wrap; }
.stat .item b { font-size: 24px; font-weight: 700; display: block; font-variant-numeric: tabular-nums; line-height: 1.3; }
.stat .item span { color: #8a919f; font-size: 12px; }

/* ---------- 条件卡 ---------- */
.cond-card { border: 1px solid #e8eaee; border-radius: 8px; padding: 10px 14px; min-width: 230px;
             transition: box-shadow .2s, transform .2s; }
.cond-card:hover { box-shadow: 0 3px 10px rgba(20,30,60,.08); transform: translateY(-1px); }
.cond-card .name { font-size: 12px; color: #8a919f; }
.cond-card .val { font-size: 13px; margin: 2px 0; }
.cond-card .flag { font-size: 12px; font-weight: 600; }
.cond-card.pass { border-left: 3px solid #c0392b; }
.cond-card.fail { border-left: 3px solid #1a7a3a; }
.cond-card.na { border-left: 3px solid #b8bcc4; }
.atr-flag { color: #fff; background: #c0392b; border-radius: 3px; padding: 0 6px; font-size: 11px; }
.reason { color: #98a0ad; font-size: 12px; white-space: normal; }

/* ---------- Tab ---------- */
.tabbar { display: flex; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }
.tab { padding: 6px 16px; border: 1px solid #e2e5ea; border-radius: 8px; cursor: pointer;
       background: #fff; font-size: 13px; transition: all .18s ease; user-select: none; }
.tab:hover { border-color: #1a6ee0; color: #1a6ee0; }
.tab.active { background: #1a6ee0; color: #fff; border-color: #1a6ee0; box-shadow: 0 2px 6px rgba(26,110,224,.25); }
.tab .cnt { margin-left: 6px; opacity: .75; font-variant-numeric: tabular-nums; }

/* 检查清单: 折叠式, 滚动到行动决策卡片时吸顶 */
.sticky-checklist { position: sticky; top: 8px; z-index: 5; background: #fdf6e8; border: 1px solid #e0c08a;
                    border-radius: 8px; padding: 10px 16px; margin-bottom: 12px; font-size: 13px; line-height: 2;
                    box-shadow: 0 2px 8px rgba(120,90,20,.1); }
.checklist-toggle { cursor: pointer; user-select: none; font-weight: 600; }
.checklist-body { margin-top: 4px; }

/* 复盘对错行底色(红=赚, 绿=亏, 与 .up/.down 一致) */
tr.row-win td { background: #fdeeee; }
tr.row-win:hover td { background: #fbe2e2; }
tr.row-loss td { background: #eef8f1; }
tr.row-loss:hover td { background: #e2f3e7; }

/* 交易手册三栏 */
.handbook-col { flex: 1; min-width: 260px; background: #fafbfc; border: 1px solid #eef0f3; border-radius: 8px; padding: 14px 16px; }
.handbook-col h3 { font-size: 13px; font-weight: 600; margin-bottom: 10px; color: #1f2329; }
.hb-item { font-size: 12px; color: #5a6270; line-height: 1.7; margin-bottom: 6px; }
.hb-item b { color: #1f2329; }

.disclaimer { color: #b3b8c2; font-size: 11px; margin-top: 22px; text-align: center; }

/* ---------- 移动端卡片(窄屏用卡片流替代表格) ---------- */
.m-cards { display: none; }
@media (max-width: 720px) {
  body { padding: 12px 10px; padding-bottom: calc(12px + env(safe-area-inset-bottom)); font-size: 13px; }
  .header-row { flex-direction: column; align-items: flex-start; gap: 10px; margin-bottom: 10px; }
  h1 { font-size: 17px; }
  .sub { font-size: 11px; }
  .nav-bar { overflow-x: auto; flex-wrap: nowrap; width: 100%; padding-bottom: 4px; -webkit-overflow-scrolling: touch; }
  .nav-pill { flex-shrink: 0; min-height: 34px; display: inline-flex; align-items: center; }
  .card { padding: 12px; margin-bottom: 10px; border-radius: 10px; }
  h2 { font-size: 14px; }
  .hint { max-width: none; font-size: 11px; }
  .btn { min-height: 44px; padding: 10px 24px; width: 100%; }
  .progress-wrap { width: 100%; }
  .stat { gap: 14px; flex-wrap: wrap; }
  .stat .item b { font-size: 19px; }
  .cond-card { min-width: 100%; flex: 1 1 100%; }
  .tabbar { overflow-x: auto; flex-wrap: nowrap; padding-bottom: 4px; }
  .tab { flex-shrink: 0; min-height: 38px; display: inline-flex; align-items: center; }
  .handbook-col { min-width: 100%; }
  .sticky-checklist { top: 4px; padding: 8px 12px; font-size: 12px; }
  .scrollbox { display: none; }   /* 表格在窄屏隐藏 */
  .m-cards { display: block; max-height: none; overflow: visible; }

  /* ---- 通用卡片 ---- */
  .m-card { background: #fff; border: 1px solid #e8eaee; border-radius: 10px; padding: 12px 14px; margin-bottom: 8px; }
  .m-card .top { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 6px; }
  .m-card .name { font-size: 15px; font-weight: 700; }
  .m-card .code { font-size: 12px; color: #8a919f; }
  .m-card .price { margin-left: auto; font-size: 14px; font-weight: 600; }
  .m-card .tags { display: flex; gap: 5px; flex-wrap: wrap; margin-bottom: 6px; }
  .m-card .mtag { font-size: 10px; padding: 1px 7px; border-radius: 3px; background: #eef3fb; color: #1a6ee0; }
  .m-card .metrics { display: grid; grid-template-columns: repeat(3, 1fr); gap: 4px 10px; font-size: 12px; margin-bottom: 6px; }
  .m-card .metrics .k { color: #8a919f; }
  .m-card .reason { white-space: normal; line-height: 1.5; }
  .m-card.win { border-left: 3px solid #c0392b; background: #fdf6f6; }
  .m-card.loss { border-left: 3px solid #1a7a3a; background: #f4faf6; }
  .m-card.strong { border-left: 3px solid #c0392b; }
  .rgroup-head { padding: 9px 10px; gap: 7px; }
  .rgroup-body { padding: 8px 10px 12px; }
  .bar-row .bn { width: 64px; }
  .bar-row .bv { width: 50px; }
  /* 评分大徽章 */
  .score-badge { min-width: 44px; height: 44px; border-radius: 50%; display: inline-flex; align-items: center; justify-content: center;
                 font-size: 16px; font-weight: 700; color: #fff; flex-shrink: 0; }
}
/* ---------- 持仓去留 ---------- */
#sec-holdings { border-left: 3px solid #1a6ee0; }
.h-card { background: #fff; border: 1px solid #e5e7eb; border-radius: 10px; padding: 14px 18px; margin-bottom: 12px; }
.h-head { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; margin-bottom: 10px; }
.h-name { font-size: 15px; font-weight: 600; }
.h-code { font-size: 12px; color: #9aa1ad; }
.h-verdict { font-size: 15px; font-weight: 700; padding: 3px 12px; border-radius: 6px; background: #f3f4f6; color: #374151; }
.h-verdict.sell { background: #fdecec; color: #c0392b; }
.h-stats { display: flex; gap: 16px; font-size: 12px; color: #374151; flex-wrap: wrap; }
.h-stats b { font-weight: 600; }
.h-rules { display: grid; grid-template-columns: repeat(2, 1fr); gap: 6px 14px; margin: 10px 0; }
.h-rule { display: flex; align-items: center; gap: 8px; font-size: 12px; padding: 5px 10px; border-radius: 6px; }
.h-rule .r-name { font-weight: 600; white-space: nowrap; }
.h-rule .r-state { font-size: 11px; padding: 0 7px; border-radius: 4px; white-space: nowrap; }
.h-rule .r-note { color: #4b5563; }
.rule-red { background: #fdecec; } .rule-red .r-state { background: #c0392b; color: #fff; } .rule-red .r-name { color: #c0392b; }
.rule-yellow { background: #fdf6e3; } .rule-yellow .r-state { background: #b07800; color: #fff; } .rule-yellow .r-name { color: #b07800; }
.rule-green { background: #ecf7f2; } .rule-green .r-state { background: #1a7a3a; color: #fff; } .rule-green .r-name { color: #1a7a3a; }
.holdings-add { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-top: 12px; }
.holdings-add input { border: 1px solid #d1d5db; border-radius: 6px; padding: 6px 9px; font-size: 12px; width: auto; }
.holdings-add .lbl { font-size: 12px; color: #666; }
.del-btn { margin-left: auto; background: none; border: 1px solid #e5e7eb; border-radius: 6px; padding: 4px 12px; font-size: 12px; color: #666; cursor: pointer; }
.del-btn:hover { border-color: #c0392b; color: #c0392b; }
#hMsg { font-size: 12px; color: #666; }
@media (max-width: 760px) { .h-rules { grid-template-columns: 1fr; } }
</style>
</head>
<body>
<div class="header-row">
  <div>
    <h1>趋势筛选工作台</h1>
    <div class="sub">四层漏斗筛选 + 五层行动决策 | 只分类不荐股, 原因全部由阈值确定性生成</div>
  </div>
  <div class="nav-bar" id="navBar"></div>
</div>

<div class="card" id="sec-validation">
  <h2>卖出/预警规则验证 <span style="font-size:12px;color:#888">前向收益口径 · 只看规则触发那一刻往后是赚还是亏</span></h2>
  <div id="validationBox"><div class="hint" style="max-width:none">加载中...</div></div>
  <div style="margin-top:8px;display:flex;gap:8px;align-items:center">
    <button class="btn" style="padding:6px 14px" onclick="refreshValidation()">重新验证</button>
    <span class="hint" style="max-width:none">首次生成要拉全部K线, 约 3-5 分钟; 之后只补新归档日</span>
  </div>
</div>

<div class="card" id="sec-holdings">
  <h2>持仓去留清单 <span style="font-size:12px;color:#888">每日 16:00 后自动判定 · 只认收盘价 · 任一规则触发即明日开盘卖出</span></h2>
  <div id="holdingsList"><div class="hint" style="max-width:none">加载中...</div></div>
  <div class="holdings-add">
    <span class="lbl">代码</span><input id="hCode" placeholder="如 002166">
    <span class="lbl">成本</span><input id="hCost" placeholder="6.756" style="width:70px">
    <span class="lbl">数量</span><input id="hQty" placeholder="200" style="width:60px">
    <span class="lbl">买入日</span><input id="hBuyDate" placeholder="2026-08-21" style="width:100px">
    <button class="btn" style="padding:6px 18px" onclick="addHolding()">添加持仓</button>
    <span id="hMsg"></span>
  </div>
</div>

<div class="card">
  <div class="row" style="align-items:center">
    <button class="btn" id="runBtn" onclick="runFunnel()">运行筛选</button>
    <div>
      <div class="progress-wrap" id="pbar"><div class="progress-bar" id="pfill"></div></div>
      <div class="progress-txt" id="ptxt"></div>
    </div>
    <div class="hint">收盘后(16:00后)运行才是定稿; 每次运行自动归档历史, 不会丢失</div>
    <div id="metaInfo" style="margin-left:auto; font-size:12px; color:#666; text-align:right"></div>
  </div>
</div>

<div class="card summary" id="sec-summary">
  <div class="stat" id="summaryStat"></div>
  <div class="reason" id="summarySectors" style="margin-top:8px"></div>
</div>

<div id="content" style="display:none">

<div class="card" id="sec-l1">
  <h2>第一层: 大盘环境</h2>
  <div id="l1env" style="font-size:18px; margin-bottom:10px"></div>
  <div id="l1conds" style="display:flex; gap:12px; flex-wrap:wrap"></div>
</div>

<div class="card" id="sec-l2">
  <h2>第二层: 板块强弱 <span style="font-size:12px;color:#888">进入第三层的强板块标红</span></h2>
  <div class="scrollbox"><table id="sectorTable">
    <thead><tr><th>板块</th><th>类型</th><th>状态</th><th>当日均涨%</th><th>5日均涨%</th><th>量比</th><th>上涨占比%</th><th>多周期全红%</th><th>成员</th><th>触发原因</th></tr></thead>
    <tbody></tbody>
  </table></div>
  <div class="m-cards" id="mSectors"></div>
</div>

<div class="card" id="sec-l3">
  <h2>第三层: 个股筛选漏斗</h2>
  <div class="stat" id="l3stat"></div>
</div>

<div class="card" id="sec-cat">
  <h2>第四层: 五类状态分类</h2>
  <div class="reason" style="margin-bottom:10px; line-height:1.8" id="catGuide"></div>
  <div class="tabbar" id="tabbar"></div>
  <div class="scrollbox" style="max-height:460px"><table id="stockTable">
    <thead><tr><th>代码</th><th>名称</th><th>现价</th><th>5日%</th><th>10日%</th><th>20日%</th><th>距MA20%</th><th>250日位置%</th><th>量比</th><th>ATR%</th><th>所属板块</th><th>触发原因</th></tr></thead>
    <tbody></tbody>
  </table></div>
  <div class="m-cards" id="mStocks"></div>
</div>

<div class="card" id="sec-action">
  <h2>第五层: 行动决策 <span style="font-size:12px;color:#888">评分(0-100)=趋势30+位置25+量能20+波动15+形态10, 再叠加大盘环境加减分</span></h2>
  <div class="tabbar" id="actionTabbar"></div>
  <div id="actionGuide" class="reason" style="margin-bottom:10px; line-height:1.8"></div>
  <div id="exitRulesBox" style="display:none; background:#eef3fb; border:1px solid #c4d8f0; border-radius:8px; padding:10px 16px; margin-bottom:12px; font-size:12px; line-height:1.9"></div>
  <div id="checklist" class="sticky-checklist" style="display:none"></div>
  <div class="scrollbox" style="max-height:520px"><table id="actionTable">
    <thead><tr><th>评分</th><th>代码</th><th>名称</th><th>现价</th><th>参考介入</th><th>止损价</th><th>止损幅度</th><th>评分明细</th><th>所属板块</th></tr></thead>
    <tbody></tbody>
  </table></div>
  <div class="m-cards" id="mActions"></div>
</div>

</div>

<div class="card" id="sec-guide">
  <h2>每日使用时间表 <span style="font-size:12px;color:#888">照着做, 不用记</span></h2>
  <table>
    <thead><tr><th style="width:180px">时间</th><th>该做什么</th><th style="width:120px">状态</th></tr></thead>
    <tbody>
      <tr><td><b>每个交易日 16:00后</b></td><td>打开本页面, 点"运行筛选", 等待约40秒出结果(自动归档)</td><td><span class="badge pre">必做</span></td></tr>
      <tr><td>16:01</td><td>看"今日结论": 环境偏弱 -> 当天不操作; 正常/强 -> 看"行动决策"的可介入档, 记下评分最高2-3只的参考介入价和止损价</td><td><span class="badge pre">必做</span></td></tr>
      <tr><td>次日 9:30-14:30</td><td>昨晚选出的股票, 在参考介入价附近挂单买入; 买入同时把止损价写进券商App条件单</td><td><span class="badge closed">仅昨日有可介入股时</span></td></tr>
      <tr><td>持仓期间</td><td>只做一件事: 收盘跌破止损价 -> 无条件卖出。不猜顶、不补仓、不加杠杆</td><td><span class="badge pre">纪律</span></td></tr>
      <tr><td>每周一次</td><td>看本页"历史复盘"对账: 胜率、哪只对了哪只错了, 检验筛选质量是否稳定</td><td><span class="badge closed">建议</span></td></tr>
    </tbody>
  </table>
</div>

<div class="card" id="sec-handbook">
  <h2>交易手册 <span style="font-size:12px;color:#888">这套规则的本质是什么, 想清楚再用</span></h2>
  <div class="row" style="gap:20px; align-items:stretch">
    <div class="handbook-col">
      <h3>一、从卖出规则看持有期上限</h3>
      <div class="hb-item"><b>时间止损</b>: 10个交易日盈利不足5%强制卖 -> 硬性天花板, 拿不过两周</div>
      <div class="hb-item"><b>硬止损</b>: 跌破止损价次日就卖 -> 错误的票通常1-3天就被清出去</div>
      <div class="hb-item"><b>趋势破坏</b>: 连续2日收盘低于MA10 -> 短期趋势一断就走</div>
      <div class="hb-item"><b>移动止盈</b>: 涨够10%后从高点回撤7% -> 趋势强的票能拿久一点, 但利润回吐到一定程度照样卖</div>
    </div>
    <div class="handbook-col">
      <h3>二、和长期持有的本质区别</h3>
      <div class="hb-item">长期持有赚的是"公司成长的钱", 看基本面和估值, 跌了敢扛甚至加仓</div>
      <div class="hb-item">这个工具赚的是"趋势的一段", 只看价量 -- 趋势在就拿着, 趋势断就走, 不问公司好不好</div>
      <div class="hb-item">选出来的票(CRO/黄金/医药等), 不是因为"值得投资", 是因为"正在涨且位置不高"。涨的势头停了, 逻辑就消失了</div>
    </div>
    <div class="handbook-col">
      <h3>三、正确的心理预期</h3>
      <div class="hb-item">一笔交易赚5-15%是常态, 不是翻倍</div>
      <div class="hb-item">持仓以天计, 不以月计</div>
      <div class="hb-item">卖飞是必然的 -- 趋势跟踪永远卖在"回头确认"的位置, 不可能卖在最高点, 这是设计如此, 不是缺陷</div>
    </div>
  </div>
</div>

<div class="card" id="sec-history">
  <h2>历史复盘 <span style="font-size:12px;color:#888">按归档日期分组(点日期行展开当日明细), 全部历史日期与趋势完整保留。卖出模拟: 按四条规则(硬止损/移动止盈/趋势破坏/时间止损)逐日回放K线判定卖出点。红=赚, 绿=亏</span></h2>
  <div class="stat" id="reconStats" style="margin-bottom:12px"></div>
  <div class="trend-panel" id="reconTrend" style="display:none"></div>
  <div class="hbar-tools" id="reconTools" style="display:none">
    <button class="nav-pill" onclick="toggleAllGroups(true)">全部展开</button>
    <button class="nav-pill" onclick="toggleAllGroups(false)">全部折叠</button>
    <span class="gmeta" id="reconHint"></span>
  </div>
  <div id="reconGroups"></div>
  <div class="m-cards" id="mRecon"></div>
</div>

<div class="disclaimer">本工具仅做数据分类展示, 不构成任何投资建议。阈值可在 funnel/config.py 调整。</div>

<script>
const CATS = [
  ["launch", "启动观察"], ["trend", "趋势观察"], ["pullback", "回调观察"],
  ["high_position", "高位观察"], ["excluded", "排除"], ["unclassified", "未分类"]
];
const STATE_TXT = {surging_persistent: "加强+持续", surging: "正在加强", persistent: "持续强势",
                   weak: "走弱", neutral: "中性"};
const ENV_TXT = {strong: ["强", "env-strong"], normal: ["正常", "env-normal"], weak: ["偏弱", "env-weak"]};
const CAT_GUIDE = {
  launch: "启动观察: 5日涨幅刚起步(5-20%)+放量+贴着20日均线。处于趋势早期, 还没走远, 是五类里'位置最早'的一类",
  trend: "趋势观察: 10日/20日多周期走强且站上均线。趋势已经走出来了, 但位置还没到极端高位",
  pullback: "回调观察: 近期在涨的强势股出现5日级别回撤。关注的是'回撤后能否企稳'",
  high_position: "高位观察: 250日区间90%分位以上且远离均线。涨得多、位置高, 追入的风险收益比已经很差",
  excluded: "排除: 20日涨超40%过热, 或跌破均线且持续下跌的趋势破坏股",
  unclassified: "未分类: 不满足任何一类的阈值条件"
};
const ACTION_META = {
  enter: {label: "可介入", color: "#c0392b", guide: "评分达标(≥72) + 位置<70%分位 + 大盘不弱。这是唯一可以考虑买入的一档。看右边表格的'参考介入/止损价', 按计划执行"},
  watch: {label: "观察等待", color: "#1a6ee0", guide: "有可取之处但当前不是好买点(分数不够或位置偏高)。加自选, 等回调到参考介入价附近或趋势进一步确认"},
  no_chase: {label: "不追高", color: "#b26a00", guide: "趋势还在但已到250日区间90%分位以上。此位置买入向上空间小向下空间大, 纪律上放弃"},
  avoid: {label: "回避", color: "#1a7a3a", guide: "过热/趋势破坏/评分过低。不碰, 不用看细节"}
};
const CHECKLIST = [
  "1. 大盘环境是'正常'或'强'吗? (偏弱日全部不买)",
  "2. 这只股票评分≥72 且 位置<70%分位吗?",
  "3. 止损价定好了吗? 这个亏损金额(总仓位x止损幅度)你能承受吗?",
  "4. 单只仓位是否≤总资金10-15%?",
  "5. 触发失效条件(跌破止损价)时, 你会无条件执行卖出吗?",
  "四条全过才买。任何一条犹豫, 就不买。"
];
let curResult = null;

function fmtPct(v) {
  if (v === null || v === undefined) return "-";
  const cls = v > 0 ? "up" : (v < 0 ? "down" : "");
  return `<span class="${cls}">${v > 0 ? "+" : ""}${v.toFixed(2)}</span>`;
}

async function runFunnel() {
  document.getElementById("runBtn").disabled = true;
  document.getElementById("pbar").style.display = "block";
  await fetch("api/run", {method: "POST"});
  pollStatus();
}

async function pollStatus() {
  const r = await fetch("api/status"); const s = await r.json();
  const pfill = document.getElementById("pfill"), ptxt = document.getElementById("ptxt");
  pfill.style.width = s.pct + "%";
  ptxt.textContent = s.running ? `${s.pct}% ${s.detail}` : "";
  if (s.running) { setTimeout(pollStatus, 1200); return; }
  if (s.error) { ptxt.textContent = "运行失败: " + s.error; document.getElementById("runBtn").disabled = false; return; }
  document.getElementById("pbar").style.display = "none";
  document.getElementById("runBtn").disabled = false;
  loadResult();
}

async function loadResult() {
  loadHoldings();
  const r = await fetch("api/result");
  if (!r.ok) return;
  const d = await r.json();
  if (d.ok === false) return;
  curResult = d;
  document.getElementById("content").style.display = "block";
  renderNav(); renderSummary(d); renderMeta(d); renderL1(d.layer1); renderSectors(d);
  renderL3(d); renderActionTabs(d); renderTabs(d); renderHistory();
}

async function loadHoldings() {
  try {
    const r = await fetch("api/holdings");
    if (!r.ok) { document.getElementById("holdingsList").innerHTML = '<div class="hint" style="max-width:none">持仓服务不可用</div>'; return; }
    const d = await r.json();
    const list = d.holdings || [];
    const box = document.getElementById("holdingsList");
    if (!list.length) { box.innerHTML = '<div class="hint" style="max-width:none">暂无持仓登记, 添加后每日自动判定去留</div>'; return; }
    box.innerHTML = list.map(renderHoldingCard).join("");
  } catch (e) {
    document.getElementById("holdingsList").innerHTML = '<div class="hint" style="max-width:none">持仓数据加载失败</div>';
  }
}

function renderHoldingCard(h) {
  const sell = h.verdict === "明日开盘卖出";
  const cls = sell ? "h-verdict sell" : "h-verdict";
  const earnCls = h.earn != null ? (h.earn >= 0 ? "up" : "down") : "";
  const earnTxt = h.earn != null ? (h.earn >= 0 ? "+" : "") + h.earn.toFixed(2) + "%" : "--";
  const peakTxt = h.peak_earn != null ? (h.peak_earn >= 0 ? "+" : "") + h.peak_earn.toFixed(2) + "%" : "--";
  const rules = (h.rules || []).map(r => {
    const stMap = { triggered: ["rule-red", "触发"], watch: ["rule-yellow", "临界"], safe: ["rule-green", "安全"] };
    const st = stMap[r.status] || ["rule-green", "安全"];
    return `<div class="h-rule ${st[0]}"><span class="r-name">${r.rule}</span><span class="r-state">${st[1]}</span><span class="r-note">${r.note}</span></div>`;
  }).join("");
  const trail = Array.isArray(h.env_trail) ? h.env_trail : [];
  const trailHtml = trail.length ? `<div style="margin-top:6px;font-size:12px">
      <span style="color:#888">持有期环境轨迹</span>
      ${trail.map(t => `<span class="genv ${t.env || ""}" style="margin-left:4px">${ENV_LABEL[t.env] || "-"}</span>`).join("")}
    </div>` : "";
  const err = h.error ? `<div class="hint" style="max-width:none;color:#c0392b">${h.error}</div>` : "";
  return `<div class="h-card">
    <div class="h-head">
      <span class="h-name">${h.name}</span><span class="h-code">${h.code}</span>
      <span class="${cls}">${h.verdict}</span>
      <button class="del-btn" onclick="delHolding('${h.code}')">卖出移除</button>
    </div>
    <div class="h-stats">
      <span>现价 <b>${h.last_close != null ? h.last_close.toFixed(2) : "--"}</b></span>
      <span>成本 <b>${h.cost}</b></span>
      <span>浮盈 <b class="${earnCls}">${earnTxt}</b></span>
      <span>止损价 <b>${h.stop}</b></span>
      <span>MA10 <b>${h.ma10 != null ? h.ma10.toFixed(2) : "--"}</b></span>
      <span>持有 <b>${h.hold_days}</b> 个交易日</span>
      <span>peak浮盈 <b class="${h.peak_earn >= 0 ? "up" : "down"}">${peakTxt}</b></span>
    </div>
    ${trailHtml}
    <div class="h-rules">${rules}</div>
    ${err}
  </div>`;
}

async function addHolding() {
  const code = document.getElementById("hCode").value.trim();
  const cost = document.getElementById("hCost").value.trim();
  const qty = document.getElementById("hQty").value.trim();
  const buyDate = document.getElementById("hBuyDate").value.trim();
  const msg = document.getElementById("hMsg");
  if (!code || !cost || !qty || !buyDate) { msg.textContent = "请填写代码/成本/数量/买入日期"; return; }
  const r = await fetch("api/holdings", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code: code, cost: cost, qty: qty, buy_date: buyDate }) });
  const d = await r.json();
  if (d.ok) { msg.textContent = "已添加: " + (d.name || "") + " 止损 " + d.stop; loadHoldings(); }
  else { msg.textContent = d.msg || "添加失败"; }
}

async function delHolding(code) {
  if (!confirm("确认已卖出并移除该持仓?")) return;
  const r = await fetch("api/holdings/" + code, { method: "DELETE" });
  const d = await r.json();
  if (d.ok) loadHoldings(); else alert(d.msg || "删除失败");
}

function renderNav() {
  const secs = [["sec-summary", "今日结论"], ["sec-l1", "大盘"], ["sec-l2", "板块"],
                ["sec-l3", "漏斗"], ["sec-cat", "五类分类"], ["sec-action", "行动决策"],
                ["sec-guide", "时间表"], ["sec-handbook", "交易手册"], ["sec-history", "复盘"]];
  document.getElementById("navBar").innerHTML = secs.map(([id, t]) =>
    `<span class="nav-pill" onclick="document.getElementById('${id}').scrollIntoView({behavior:'smooth',block:'start'})">${t}</span>`).join("");
}

function countUp(el, target) {
  const dur = 500, t0 = performance.now();
  function step(t) {
    const p = Math.min((t - t0) / dur, 1);
    el.textContent = Math.round(target * (1 - Math.pow(1 - p, 3)));
    if (p < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}

function renderSummary(d) {
  const [envTxt, envCls] = ENV_TXT[d.layer1.env] || ["?", ""];
  const acts = d.actions || {};
  const enterN = (acts.enter || []).length;
  const strong = (d.strong_sectors || []).slice(0, 4).join("、");
  const stat = document.getElementById("summaryStat");
  stat.innerHTML = `
    <div class="item"><b class="${envCls}" style="font-size:26px">${envTxt}</b><span>大盘环境(${d.layer1.passed}/3)</span></div>
    <div class="item"><b id="sumEnter" style="color:${enterN > 0 ? '#c0392b' : '#8a919f'}">0</b><span>可介入</span></div>
    <div class="item"><b id="sumWatch">0</b><span>观察等待</span></div>
    <div class="item"><b id="sumNochase">0</b><span>不追高</span></div>`;
  countUp(document.getElementById("sumEnter"), enterN);
  countUp(document.getElementById("sumWatch"), (acts.watch || []).length);
  countUp(document.getElementById("sumNochase"), (acts.no_chase || []).length);
  document.getElementById("summarySectors").innerHTML =
    `今日强势板块: <b style="color:#1f2329">${strong}</b>${enterN > 0 ? " | 买入前务必过一遍检查清单" : " | 今日无可介入标的, 空仓也是一种操作"}`;
}

function renderMeta(d) {
  const m = d.meta;
  const badgeMap = {
    pre_market: '<span class="badge pre">盘前数据(量比/当日涨幅不完整)</span>',
    intraday: '<span class="badge pre">盘中数据(未收盘, 数值会继续变化)</span>',
    closed: '<span class="badge closed">收盘数据</span>'
  };
  document.getElementById("metaInfo").innerHTML =
    `数据基准: ${m.date} ${badgeMap[m.data_status] || ''}<br>运行时间 ${m.run_time} | 耗时 ${m.elapsed_s}s | 全A ${m.snapshot_total}只`;
}

function renderL1(l1) {
  const [txt, cls] = ENV_TXT[l1.env] || ["?", ""];
  document.getElementById("l1env").innerHTML =
    `当前判定: <span class="${cls}">${txt}</span> <span style="font-size:13px;color:#888">(${l1.passed}/3 条件通过)</span>`;
  const conds = l1.conditions || l1.reasons.map(r => ({name: "", passed: r.includes("≥") || r.includes("站上"), detail: r}));
  document.getElementById("l1conds").innerHTML = conds.map(c => {
    const st = c.passed === null || c.passed === undefined ? "na" : (c.passed ? "pass" : "fail");
    const flag = c.passed === null || c.passed === undefined ? '<span class="flag" style="color:#999">数据缺失</span>'
      : (c.passed ? '<span class="flag" style="color:#c0392b">通过</span>' : '<span class="flag" style="color:#1a7a3a">未过</span>');
    return `<div class="cond-card ${st}"><div class="name">${c.name || ''}</div><div class="val">${c.detail}</div>${flag}</div>`;
  }).join("");
}

function renderSectors(d) {
  const strong = new Set(d.strong_sectors);
  const tbody = document.querySelector("#sectorTable tbody");
  tbody.innerHTML = d.layer2.slice(0, 30).map(s => {
    const isStrong = strong.has(s.name);
    return `<tr>
      <td style="${isStrong ? 'color:#c0392b;font-weight:600' : ''}">${s.name}</td>
      <td>${s.type === "industry" ? "行业" : "概念"}</td>
      <td>${STATE_TXT[s.state] || s.state}</td>
      <td>${fmtPct(s.avg_zdf)}</td><td>${fmtPct(s.avg_d5)}</td>
      <td>${s.avg_lb > 0 ? s.avg_lb.toFixed(2) : "-"}</td>
      <td>${s.up_ratio}%</td><td>${s.multi_pos_ratio}%</td><td>${s.member_count}</td>
      <td class="reason"><span class="ellip" title="${s.reasons.join("; ")}">${s.reasons.join("; ") || "-"}</span></td>
    </tr>`;
  }).join("");
  // 移动端卡片
  document.getElementById("mSectors").innerHTML = d.layer2.slice(0, 30).map(s => {
    const isStrong = strong.has(s.name);
    return `<div class="m-card ${isStrong ? 'strong' : ''}">
      <div class="top">
        <span class="name" style="${isStrong ? 'color:#c0392b' : ''}">${s.name}</span>
        <span class="code">${s.type === "industry" ? "行业" : "概念"} · ${STATE_TXT[s.state] || s.state} · ${s.member_count}成员</span>
      </div>
      <div class="metrics">
        <span><span class="k">当日均涨 </span>${fmtPct(s.avg_zdf)}%</span>
        <span><span class="k">5日均涨 </span>${fmtPct(s.avg_d5)}%</span>
        <span><span class="k">量比 </span>${s.avg_lb > 0 ? s.avg_lb.toFixed(2) : "-"}</span>
        <span><span class="k">上涨占比 </span>${s.up_ratio}%</span>
        <span><span class="k">多周期红 </span>${s.multi_pos_ratio}%</span>
      </div>
      <div class="reason">${s.reasons.join("; ") || "-"}</div>
    </div>`;
  }).join("");
}

function renderL3(d) {
  const m = d.meta;
  const classified = Object.values(d.layer4).reduce((a, b) => a + b.length, 0);
  const items = [
    [m.candidate_count, "强板块候选"], [m.kline_count, "K线获取成功"], [classified, "完成分类"]
  ];
  document.getElementById("l3stat").innerHTML = items.map(([v, t]) =>
    `<div class="item"><b>${v}</b><span>${t}</span></div>`).join("");
}

function renderActionTabs(d) {
  const bar = document.getElementById("actionTabbar");
  const acts = d.actions || {};
  bar.innerHTML = Object.keys(ACTION_META).map(k =>
    `<div class="tab" data-act="${k}" onclick="switchAction('${k}')">${ACTION_META[k].label}<span class="cnt">${(acts[k]||[]).length}</span></div>`).join("");
  const first = Object.keys(ACTION_META).find(k => (acts[k]||[]).length > 0) || "enter";
  switchAction(first);
}

function scoreBadge(v) {
  const color = v >= 72 ? "#c0392b" : (v >= 45 ? "#1a6ee0" : "#1a7a3a");
  return `<b style="color:${color}">${v}</b>`;
}

function switchAction(act) {
  document.querySelectorAll("#actionTabbar .tab").forEach(t =>
    t.classList.toggle("active", t.dataset.act === act));
  const meta = ACTION_META[act];
  document.getElementById("actionGuide").innerHTML = `<b style="color:${meta.color}">${meta.label}:</b> ${meta.guide}`;
  document.getElementById("checklist").style.display = (act === "enter") ? "block" : "none";
  // 卖出规则: 可介入档展示(取第一只股的exit_conditions, 各股止损价不同但规则一致)
  const exitBox = document.getElementById("exitRulesBox");
  if (act === "enter") {
    const first = (curResult.actions.enter || [])[0];
    const conds = (first && first.plan && first.plan.exit_conditions) || [];
    if (conds.length) {
      exitBox.style.display = "block";
      exitBox.innerHTML = `<b>什么时候卖(四条规则, 任一触发即卖, 全部在收盘后判断):</b><br>${conds.join("<br>")}`;
    } else { exitBox.style.display = "none"; }
  } else { exitBox.style.display = "none"; }
  if (act === "enter") {
    document.getElementById("checklist").innerHTML =
      `<span class="checklist-toggle" onclick="document.getElementById('checkBody').style.display = document.getElementById('checkBody').style.display === 'none' ? 'block' : 'none'">买入前检查清单(四条全过才买) [展开/收起]</span>
       <div class="checklist-body" id="checkBody">${CHECKLIST.join("<br>")}</div>`;
  }
  const stocks = (curResult.actions || {})[act] || [];
  document.querySelector("#actionTable tbody").innerHTML = stocks.map(m => {
    const plan = m.plan || {};
    const bd = (m.score_breakdown || []).map(b => `${b.name}${b.score}/${b.max}`).join(" ");
    return `<tr>
      <td>${scoreBadge(m.score)}${m.market_adj ? `<span class="reason"><br>大盘${m.market_adj > 0 ? "+" : ""}${m.market_adj}</span>` : ""}</td>
      <td>${m.code}</td><td>${m.name}</td><td>${m.price}</td>
      <td>${plan.entry_txt || "-"}</td>
      <td>${plan.stop ? plan.stop : "-"}</td>
      <td>${plan.loss_pct ? "-" + plan.loss_pct + "%" : "-"}</td>
      <td class="reason"><span class="ellip" title="${bd}">${bd}</span></td>
      <td class="reason"><span class="ellip s" title="${(m.boards || []).join("/") || "-"}">${(m.boards || []).join("/") || "-"}</span></td>
    </tr>`;
  }).join("") || '<tr><td colspan="9" style="text-align:center;color:#999">该档无股票(这是常态: 可介入档经常为空, 宁缺毋滥)</td></tr>';
  const at = document.querySelector("#actionTable tbody");
  at.classList.remove("fade-in"); void at.offsetWidth; at.classList.add("fade-in");
  // 移动端卡片: 评分徽章+关键价位突出
  document.getElementById("mActions").innerHTML = stocks.map(m => {
    const plan = m.plan || {};
    const color = m.score >= 72 ? "#c0392b" : (m.score >= 45 ? "#1a6ee0" : "#1a7a3a");
    const bd = (m.score_breakdown || []).map(b => `${b.name}${b.score}/${b.max}`).join(" ");
    return `<div class="m-card">
      <div class="top">
        <span class="score-badge" style="background:${color}">${m.score}</span>
        <div style="flex:1;min-width:0">
          <span class="name">${m.name}</span> <span class="code">${m.code}</span>
          ${m.market_adj ? `<span class="code"> 大盘${m.market_adj > 0 ? "+" : ""}${m.market_adj}</span>` : ""}
        </div>
        <span class="price">${m.price}</span>
      </div>
      <div class="metrics" style="grid-template-columns:repeat(3,1fr)">
        <span><span class="k">介入 </span><b>${plan.entry_txt || "-"}</b></span>
        <span><span class="k">止损 </span><b>${plan.stop ? plan.stop : "-"}</b></span>
        <span><span class="k">止损幅 </span><b>${plan.loss_pct ? "-" + plan.loss_pct + "%" : "-"}</b></span>
      </div>
      <div class="reason">${bd}</div>
    </div>`;
  }).join("") || '<div class="m-card" style="text-align:center;color:#999">该档无股票(可介入档经常为空, 宁缺毋滥)</div>';
}

function renderTabs(d) {
  const bar = document.getElementById("tabbar");
  bar.innerHTML = CATS.map(([k, label]) =>
    `<div class="tab" data-cat="${k}" onclick="switchTab('${k}')">${label}<span class="cnt">${d.layer4[k].length}</span></div>`).join("");
  switchTab(CATS.find(([k]) => d.layer4[k].length > 0)?.[0] || "launch");
}

function switchTab(cat) {
  document.querySelectorAll(".tab").forEach(t =>
    t.classList.toggle("active", t.dataset.cat === cat));
  document.getElementById("catGuide").textContent = CAT_GUIDE[cat] || "";
  const stocks = curResult.layer4[cat] || [];
  document.querySelector("#stockTable tbody").innerHTML = stocks.map(m => `<tr>
    <td>${m.code}</td><td>${m.name}</td><td>${m.price}</td>
    <td>${fmtPct(m.d5)}</td><td>${fmtPct(m.d10)}</td><td>${fmtPct(m.d20)}</td>
    <td>${fmtPct(m.ma20_dist)}</td><td>${m.position_pct ?? "-"}</td>
    <td>${m.lb > 0 ? m.lb.toFixed(2) : "-"}</td>
    <td>${m.atr_pct !== null ? m.atr_pct.toFixed(2) : "-"}</td>
    <td class="reason"><span class="ellip s" title="${(m.boards || []).join("/") || "-"}">${(m.boards || []).join("/") || "-"}</span></td>
    <td class="reason"><span class="ellip" title="${m.reasons.join("；")}">${m.reasons.map(r => m.atr_flag && r.includes("波动过大") ? r + "(波动异常)" : r).join("；")}</span></td>
  </tr>`).join("") || '<tr><td colspan="12" style="text-align:center;color:#999">该分类下无股票</td></tr>';
  const st = document.querySelector("#stockTable tbody");
  st.classList.remove("fade-in"); void st.offsetWidth; st.classList.add("fade-in");
  // 移动端卡片
  document.getElementById("mStocks").innerHTML = stocks.map(m => `<div class="m-card">
    <div class="top">
      <span class="name">${m.name}</span> <span class="code">${m.code}</span>
      <span class="price">${m.price}</span>
    </div>
    <div class="tags">${(m.boards || []).map(b => `<span class="mtag">${b}</span>`).join("")}</div>
    <div class="metrics">
      <span><span class="k">5日 </span>${fmtPct(m.d5)}%</span>
      <span><span class="k">10日 </span>${fmtPct(m.d10)}%</span>
      <span><span class="k">20日 </span>${fmtPct(m.d20)}%</span>
      <span><span class="k">距MA20 </span>${fmtPct(m.ma20_dist)}%</span>
      <span><span class="k">250日位 </span>${m.position_pct ?? "-"}%</span>
      <span><span class="k">量比 </span>${m.lb > 0 ? m.lb.toFixed(2) : "-"}</span>
    </div>
    <div class="reason">${m.reasons.map(r => m.atr_flag && r.includes("波动过大") ? r + "(波动异常)" : r).join("；")}</div>
  </div>`).join("") || '<div class="m-card" style="text-align:center;color:#999">该分类下无股票</div>';
}

loadResult();

// PWA: 注册Service Worker(根路径部署, scope覆盖整站, 兼容子路径反代)
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('sw.js').catch(() => {});
}

// ---------- 历史复盘: 按归档日期分组 ----------
const ENV_LABEL = {strong: "强势", normal: "中性", weak: "弱势"};

function pctTxt(v) {
  return (v === null || v === undefined) ? "-" : (v > 0 ? "+" : "") + v.toFixed(2) + "%";
}
function retColor(v) {
  return (v === null || v === undefined) ? "#8a919f"
       : (v > 0 ? "#c0392b" : (v < 0 ? "#1a7a3a" : "#5a6270"));
}

/* 跨日期趋势: 柱=当日平均收益率, 折线=累计收益率, 全部归档日期都保留 */
function renderTrendSVG(daily) {
  const W = 860, H = 190, PL = 44, PR = 16, PT = 12, PB = 24;
  const iw = W - PL - PR, ih = H - PT - PB;
  let vmax = 1;
  daily.forEach(d => { vmax = Math.max(vmax, Math.abs(d.avg), Math.abs(d.cum)); });
  vmax = Math.ceil(vmax * 1.15);
  const y = v => PT + ih / 2 - (v / vmax) * (ih / 2);
  const n = daily.length;
  const bw = Math.max(2, Math.min(24, iw / Math.max(n, 1) * 0.55));
  const x = i => (n === 1) ? PL + iw / 2 : PL + (iw - bw) * (i / (n - 1)) + bw / 2;
  const zeroY = y(0);
  let g = "";
  [vmax, vmax / 2, 0, -vmax / 2, -vmax].forEach(t => {
    g += `<line x1="${PL}" y1="${y(t).toFixed(1)}" x2="${W - PR}" y2="${y(t).toFixed(1)}" `
       + `stroke="${t === 0 ? "#dfe3e8" : "#f2f4f7"}" stroke-width="1"/>`;
    g += `<text x="${PL - 6}" y="${(y(t) + 3.5).toFixed(1)}" text-anchor="end" font-size="9.5" `
       + `fill="#8a919f">${t > 0 ? "+" : ""}${t.toFixed(0)}%</text>`;
  });
  daily.forEach((d, i) => {
    const yy = y(d.avg), h = Math.abs(zeroY - yy);
    g += `<rect x="${(x(i) - bw / 2).toFixed(1)}" y="${Math.min(yy, zeroY).toFixed(1)}" `
       + `width="${bw.toFixed(1)}" height="${Math.max(1, h).toFixed(1)}" fill="${retColor(d.avg)}" `
       + `opacity=".78" rx="1"><title>${d.date} 均${pctTxt(d.avg)} · ${d.n}只</title></rect>`;
  });
  g += `<polyline points="${daily.map((d, i) => x(i).toFixed(1) + "," + y(d.cum).toFixed(1)).join(" ")}" `
     + `fill="none" stroke="#1a6ee0" stroke-width="2" stroke-linejoin="round"/>`;
  daily.forEach((d, i) => {
    g += `<circle cx="${x(i).toFixed(1)}" cy="${y(d.cum).toFixed(1)}" r="2.4" fill="#1a6ee0">`
       + `<title>${d.date} 累计${pctTxt(d.cum)}</title></circle>`;
  });
  const step = Math.max(1, Math.ceil(n / 12));
  daily.forEach((d, i) => {
    if (i % step === 0 || i === n - 1) {
      g += `<text x="${x(i).toFixed(1)}" y="${H - 7}" text-anchor="middle" font-size="9" `
         + `fill="#8a919f">${d.date.slice(5)}</text>`;
    }
  });
  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">${g}</svg>`;
}

/* 当日个股收益分布: 零轴居中的双向条 */
function dayBars(rows, limit) {
  const arr = rows.slice(0, limit || 15);
  if (!arr.length) return '<div class="gmeta">该日无可介入股</div>';
  let maxAbs = 1;
  arr.forEach(r => { maxAbs = Math.max(maxAbs, Math.abs(r.ret || 0)); });
  return arr.map(r => {
    const v = r.ret || 0;
    const w = (Math.abs(v) / maxAbs * 50).toFixed(1);
    return `<div class="bar-row">
      <span class="bn" title="${(r.x.name || "")} ${r.x.code || ""}">${(r.x.name || r.x.code || "-").slice(0, 6)}</span>
      <span class="bt"><span class="bf" style="${v >= 0 ? "left:50%" : "right:50%"};width:${w}%;background:${retColor(v)}"></span></span>
      <span class="bv" style="color:${retColor(v)}">${pctTxt(v)}</span>
    </div>`;
  }).join("");
}

function toggleGroup(id) {
  const el = document.getElementById(id);
  if (el) el.classList.toggle("open");
}
function toggleAllGroups(open) {
  document.querySelectorAll(".rgroup").forEach(el => el.classList.toggle("open", !!open));
}

/* 限制并发的串行拉取, 避免把VPS打爆 */
async function mapLimit(arr, limit, fn) {
  const out = new Array(arr.length);
  let i = 0;
  async function worker() {
    while (i < arr.length) {
      const idx = i++;
      out[idx] = await fn(arr[idx], idx);
    }
  }
  await Promise.all(Array.from({length: Math.min(limit, arr.length)}, worker));
  return out;
}

async function renderHistory() {
  const r = await fetch("api/history");
  const list = await r.json();
  const stats = document.getElementById("reconStats");
  const box = document.getElementById("reconGroups");
  const mbox = document.getElementById("mRecon");
  const tools = document.getElementById("reconTools");
  const hint = document.getElementById("reconHint");
  const tp = document.getElementById("reconTrend");
  if (!list.length) {
    box.innerHTML = '<div class="hint" style="max-width:none;color:#999">暂无历史归档, 先运行筛选</div>';
    return;
  }
  const dates = list.slice().sort((a, b) => (a.date < b.date ? 1 : -1));   // 日期倒序, 新的在前
  const COLS = `<tr><th>代码</th><th>名称</th><th>评分</th><th>介入</th><th>止损</th>` +
               `<th>卖出日</th><th>卖出原因</th><th>持有天数</th><th>最高盈利%</th><th>收益率%</th><th>结果</th></tr>`;

  // 1) 先按日期建好全部占位块 —— 逐日对账要几十秒, 这样列表立刻可见, 之后边加载边填充
  box.innerHTML = dates.map((h, i) => `<div class="rgroup ${i < 2 ? "open" : ""}" id="rg-${h.date}">
    <div class="rgroup-head"><span class="arrow">&#9654;</span>
      <span class="gdate">${h.date}</span>
      <span class="genv ${h.env || ""}">${ENV_LABEL[h.env] || "-"}</span>
      <span class="gmeta">对账中…</span></div>
    <div class="rgroup-body"><span class="gmeta">正在拉K线回放…</span></div></div>`).join("");
  mbox.innerHTML = dates.map((h, i) => `<div class="rgroup ${i < 2 ? "open" : ""}" id="mrg-${h.date}">
    <div class="rgroup-head"><span class="arrow">&#9654;</span>
      <span class="gdate">${h.date}</span>
      <span class="genv ${h.env || ""}">${ENV_LABEL[h.env] || "-"}</span></div>
    <div class="rgroup-body"><span class="gmeta">对账中…</span></div></div>`).join("");
  tools.style.display = "flex";
  tp.style.display = "block";
  tp.innerHTML = '<div class="tp-title">跨日期趋势 · 加载中…</div>';

  const byDate = {};
  let winCnt = 0, lossCnt = 0, sumRet = 0, soldCnt = 0, doneCnt = 0;

  function rowHtml(x, cls, verdict) {
    return `<tr class="${cls}">
      <td>${x.code}</td><td>${x.name}</td>
      <td><b>${x.score ?? "-"}</b></td>
      <td>${x.entry ?? "-"}</td><td>${x.stop ?? "-"}</td>
      <td>${x.sell_date || (x.sold === false ? "持有中" : "-")}</td>
      <td class="reason"><span class="ellip s" title="${x.sell_reason || ""}">${x.sell_reason || "-"}</span></td>
      <td>${x.held_days ?? "-"}</td>
      <td>${x.peak_gain_pct !== null && x.peak_gain_pct !== undefined ? "+" + x.peak_gain_pct.toFixed(1) : "-"}</td>
      <td>${fmtPct(x.return_pct)}</td>
      <td><b>${verdict}</b></td>
    </tr>`;
  }
  function cardHtml(x, cls, verdict) {
    const mcls = cls === "row-win" ? "win" : (cls === "row-loss" ? "loss" : "");
    return `<div class="m-card ${mcls}">
      <div class="top">
        <span class="name">${x.name}</span> <span class="code">${x.code}</span>
        <span class="price" style="color:${retColor(x.return_pct)}">${fmtPct(x.return_pct)}%</span>
      </div>
      <div class="metrics">
        <span><span class="k">评分 </span>${x.score ?? "-"}</span>
        <span><span class="k">持有 </span>${x.held_days ?? "-"}天</span>
        <span><span class="k">介入 </span>${x.entry ?? "-"}</span>
        <span><span class="k">止损 </span>${x.stop ?? "-"}</span>
        <span><span class="k">最高 </span>${x.peak_gain_pct !== null && x.peak_gain_pct !== undefined ? "+" + x.peak_gain_pct.toFixed(1) + "%" : "-"}</span>
      </div>
      <div class="reason">卖出: ${x.sell_date || (x.sold === false ? "持有中" : "-")} · ${x.sell_reason || "-"} · <b>${verdict}</b></div>
    </div>`;
  }
  function headHtml(g, prefix) {
    return `<div class="rgroup-head" onclick="toggleGroup('${prefix}-${g.date}')">
      <span class="arrow">&#9654;</span>
      <span class="gdate">${g.date}</span>
      <span class="genv ${g.env || ""}">${ENV_LABEL[g.env] || "-"}</span>
      <span class="gmeta">${g.rows.length} 只 · 胜率 ${g.winRate === null ? "-" : g.winRate.toFixed(0) + "%"}</span>
      <span class="gnum" style="color:${retColor(g.avg)}">均 ${pctTxt(g.avg)}</span>
    </div>`;
  }
  function paint(g) {
    const el = document.getElementById("rg-" + g.date);
    if (el) el.innerHTML = headHtml(g, "rg") + `<div class="rgroup-body">
      <div class="trend-panel">
        <div class="tp-title">当日收益分布(前15只, 按评分排序)</div>
        ${dayBars(g.rows, 15)}
      </div>
      <div class="scrollbox" style="max-height:340px">
        <table><thead>${COLS}</thead><tbody>
        ${g.rows.map(({x, cls, verdict}) => rowHtml(x, cls, verdict)).join("")}
        </tbody></table>
      </div></div>`;
    const me = document.getElementById("mrg-" + g.date);
    if (me) me.innerHTML = headHtml(g, "mrg") + `<div class="rgroup-body">${
      g.rows.map(({x, cls, verdict}) => cardHtml(x, cls, verdict)).join("")
      || '<div class="m-card" style="text-align:center;color:#999">该日无可介入股</div>'}</div>`;
  }
  function repaintStats() {
    const matched = winCnt + lossCnt;
    stats.innerHTML = matched ? `
      <div class="item"><b style="color:${winCnt >= lossCnt ? "#c0392b" : "#1a7a3a"}">${(winCnt / matched * 100).toFixed(1)}%</b><span>胜率(${winCnt}赚/${lossCnt}亏)</span></div>
      <div class="item"><b>${(sumRet / matched).toFixed(2)}%</b><span>平均收益率</span></div>
      <div class="item"><b>${soldCnt}/${matched}</b><span>已卖出/总数</span></div>
      <div class="item"><b>${dates.length}</b><span>归档天数</span></div>` : "";
    hint.textContent = `已加载 ${doneCnt}/${dates.length} 个归档日期 · 默认展开最近 2 天`;
  }
  function repaintTrend() {
    const done = dates.filter(d => byDate[d.date]).slice().sort((a, b) => (a.date < b.date ? -1 : 1));
    if (!done.length) return;
    let cum = 0;
    const daily = done.map(h => {
      const g = byDate[h.date];
      const avg = g.avg === null ? 0 : g.avg;
      cum += avg;
      return {date: h.date, avg: avg, cum: cum, n: g.rows.length};
    });
    tp.innerHTML = `<div class="tp-title">跨日期趋势 · 已加载 ${daily.length}/${dates.length} 个归档日期 &nbsp;|&nbsp;
      <span style="color:#c0392b">&#9632;</span> 当日平均收益率 &nbsp;
      <span style="color:#1a6ee0">&#9473;</span> 累计收益率 ${pctTxt(daily[daily.length - 1].cum)}</div>`
      + renderTrendSVG(daily);
  }

  // 2) 逐日对账, 每完成一天立刻填充该天的分组(不等全部完成)
  await mapLimit(dates, 3, async (h) => {
    let d = null;
    try {
      const rr = await fetch(`api/history/${h.date}/reconcile`);
      d = await rr.json();
    } catch (e) {
      d = null;
    }
    if (!d || d.ok === false) { doneCnt++; repaintStats(); return; }
    const rows = (d.items || []).map(x => {
      const ret = x.return_pct;
      const noData = x.sell_reason === "无后续K线" || x.sell_reason === "K线获取失败";
      const cls = (ret === null || ret === undefined || noData) ? "" : (ret > 0 ? "row-win" : "row-loss");
      const verdict = noData ? "待买入(次日介入)"
        : (x.sold ? (ret > 0 ? "卖对了" : "止损卖出") : (ret > 0 ? "持有赚" : "持有亏"));
      if (ret !== null && ret !== undefined) {
        sumRet += ret;
        if (ret > 0) winCnt++; else lossCnt++;
      }
      if (x.sold) soldCnt++;
      return {x: x, cls: cls, verdict: verdict, score: x.score || 0, ret: ret};
    });
    rows.sort((a, b) => (b.score - a.score));   // 组内按评分降序, 与归档内排序一致
    const valid = rows.filter(v => v.ret !== null && v.ret !== undefined);
    const w = valid.filter(v => v.ret > 0).length;
    const g = {
      date: h.date, env: h.env, rows: rows,
      avg: valid.length ? valid.reduce((s, v) => s + v.ret, 0) / valid.length : null,
      winRate: valid.length ? w / valid.length * 100 : null
    };
    byDate[h.date] = g;
    doneCnt++;
    paint(g); repaintStats(); repaintTrend();
  });

  if (!doneCnt) {
    box.innerHTML = '<div class="hint" style="max-width:none;color:#999">暂无可复盘记录</div>';
  }
}

async function loadValidation() {
  try {
    const r = await fetch("api/validation");
    renderValidation(await r.json());
  } catch (e) {
    const box = document.getElementById("validationBox");
    if (box) box.innerHTML = '<div class="hint" style="max-width:none">加载失败</div>';
  }
}

function renderValidation(v) {
  const box = document.getElementById("validationBox");
  if (!v.ok) {
    box.innerHTML = '<div class="hint" style="max-width:none">' + (v.msg || "暂无验证结果") + '</div>';
    return;
  }
  const rows = (v.rules || []).map(r => {
    if (r.note) return `<tr><td style="text-align:left">${r.name}</td><td colspan="6" class="hint">${r.note}</td></tr>`;
    const bad = r.correct_rate !== undefined && r.correct_rate < 50;
    const cls = bad ? "down" : "up";
    const sgn = x => (x > 0 ? "+" : "") + x;
    return `<tr>
      <td style="text-align:left">${r.name}</td><td>${r.sample}</td><td>${r.hit_rate}%</td>
      <td>${r.lags_mean}日</td><td class="${r.fwd_mean < 0 ? 'down' : 'up'}">${sgn(r.fwd_mean)}%</td>
      <td class="${cls}" style="font-weight:600">${sgn(r.pair_mean)}%</td>
      <td class="${cls}" style="font-weight:600">${r.correct_rate}%</td>
    </tr>`;
  }).join("");
  box.innerHTML = `
    <div class="hint" style="max-width:none;margin-bottom:8px">
      样本 ${v.n_signals} 笔信号 / ${v.n_days} 个交易日 · 基准(持有第3日)前向收益 ${v.baseline_fwd_mean}%
    </div>
    <table style="width:100%;font-size:12px;border-collapse:collapse">
      <thead><tr style="color:#888;text-align:right">
        <th style="text-align:left">规则</th><th>样本</th><th>触发率</th><th>触发时持有</th><th>前向收益</th><th>配对差额</th><th>做对率</th>
      </tr></thead>
      <tbody style="text-align:right">${rows}</tbody>
    </table>
    <div class="hint" style="max-width:none;margin-top:8px">
      配对差额 = 继续持有 − 触发时卖出，为负才说明规则有价值；做对率需 &gt; 50% 才算比抛硬币强。
      低于 50% 的规则（绿色）一旦上线就是稳定亏钱。
    </div>`;
}

async function refreshValidation() {
  const btn = event.target;
  btn.disabled = true; btn.textContent = "验证中...";
  const box = document.getElementById("validationBox");
  box.innerHTML = '<div class="hint" style="max-width:none">正在拉取K线并回放, 请稍候...</div>';
  try {
    const r = await fetch("api/validation/refresh", { method: "POST" });
    renderValidation(await r.json());
  } catch (e) {
    box.innerHTML = '<div class="hint" style="max-width:none;color:#c0392b">生成失败: ' + e + '</div>';
  }
  btn.disabled = false; btn.textContent = "重新验证";
}

loadValidation();
</script>
</body>
</html>"""

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8768)
