# -*- coding: utf-8 -*-
"""趋势筛选工作台 Web服务
启动: python -m funnel.web  -> 127.0.0.1:8768
单文件FastAPI内嵌HTML, 运行进度用轮询(GET /api/status)
"""
import json
import os
import threading
import datetime

from fastapi import FastAPI
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

/* 复盘对错行底色 */
tr.row-win td { background: #eef8f1; }
tr.row-win:hover td { background: #e2f3e7; }
tr.row-loss td { background: #fdeeee; }
tr.row-loss:hover td { background: #fbe2e2; }

/* 交易手册三栏 */
.handbook-col { flex: 1; min-width: 260px; background: #fafbfc; border: 1px solid #eef0f3; border-radius: 8px; padding: 14px 16px; }
.handbook-col h3 { font-size: 13px; font-weight: 600; margin-bottom: 10px; color: #1f2329; }
.hb-item { font-size: 12px; color: #5a6270; line-height: 1.7; margin-bottom: 6px; }
.hb-item b { color: #1f2329; }

.disclaimer { color: #b3b8c2; font-size: 11px; margin-top: 22px; text-align: center; }

/* ---------- 移动端适配 ---------- */
@media (max-width: 720px) {
  body { padding: 14px 12px; padding-bottom: calc(14px + env(safe-area-inset-bottom)); font-size: 13px; }
  .header-row { flex-direction: column; align-items: flex-start; gap: 10px; margin-bottom: 12px; }
  h1 { font-size: 18px; }
  .nav-bar { overflow-x: auto; flex-wrap: nowrap; width: 100%; padding-bottom: 4px; -webkit-overflow-scrolling: touch; }
  .nav-pill { flex-shrink: 0; min-height: 34px; display: inline-flex; align-items: center; }
  .card { padding: 14px 14px; margin-bottom: 10px; border-radius: 8px; }
  .row { gap: 10px; }
  .hint { max-width: none; }
  .btn { min-height: 44px; padding: 10px 24px; }
  .progress-wrap { width: 100%; }
  .stat { gap: 18px; }
  .stat .item b { font-size: 20px; }
  /* 宽表格: 横向滚动, 表头首列吸附 */
  .scrollbox { overflow-x: auto; -webkit-overflow-scrolling: touch; }
  table { width: max-content; min-width: 100%; }
  th, td { padding: 8px 10px; }
  th:first-child, td:first-child { position: sticky; left: 0; background: #fff; z-index: 1; }
  tr:hover td:first-child { background: #f7faff; }
  .ellip { max-width: 180px; }
  .ellip.s { max-width: 110px; }
  .tabbar { overflow-x: auto; flex-wrap: nowrap; padding-bottom: 4px; }
  .tab { flex-shrink: 0; min-height: 38px; display: inline-flex; align-items: center; }
  .cond-card { min-width: 46%; flex: 1; }
  .handbook-col { min-width: 100%; }
  .sticky-checklist { top: 4px; padding: 8px 12px; font-size: 12px; }
}
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

<div class="card">
  <div class="row" style="align-items:center">
    <button class="btn" id="runBtn" onclick="runFunnel()">运行筛选</button>
    <div>
      <div class="progress-wrap" id="pbar"><div class="progress-bar" id="pfill"></div></div>
      <div class="progress-txt" id="ptxt"></div>
    </div>
    <div class="hint">收盘后(15:30后)运行才是定稿; 每次运行自动归档历史, 不会丢失</div>
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
</div>

</div>

<div class="card" id="sec-guide">
  <h2>每日使用时间表 <span style="font-size:12px;color:#888">照着做, 不用记</span></h2>
  <table>
    <thead><tr><th style="width:180px">时间</th><th>该做什么</th><th style="width:120px">状态</th></tr></thead>
    <tbody>
      <tr><td><b>每个交易日 15:30后</b></td><td>打开本页面, 点"运行筛选", 等待约40秒出结果(自动归档)</td><td><span class="badge pre">必做</span></td></tr>
      <tr><td>15:31</td><td>看"今日结论": 环境偏弱 -> 当天不操作; 正常/强 -> 看"行动决策"的可介入档, 记下评分最高2-3只的参考介入价和止损价</td><td><span class="badge pre">必做</span></td></tr>
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
  <h2>历史复盘 <span style="font-size:12px;color:#888">卖出模拟: 按四条规则(硬止损/移动止盈/趋势破坏/时间止损)逐日回放K线判定卖出点。绿底=赚, 红底=亏</span></h2>
  <div class="stat" id="reconStats" style="margin-bottom:12px"></div>
  <div class="scrollbox" style="max-height:420px">
    <table id="reconTable">
      <thead><tr><th>归档日期</th><th>代码</th><th>名称</th><th>评分</th><th>介入</th><th>止损</th><th>卖出日</th><th>卖出原因</th><th>持有天数</th><th>最高盈利%</th><th>收益率%</th><th>结果</th></tr></thead>
      <tbody></tbody>
    </table>
  </div>
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
  const r = await fetch("api/result");
  if (!r.ok) return;
  const d = await r.json();
  if (d.ok === false) return;
  curResult = d;
  document.getElementById("content").style.display = "block";
  renderNav(); renderSummary(d); renderMeta(d); renderL1(d.layer1); renderSectors(d);
  renderL3(d); renderActionTabs(d); renderTabs(d); renderHistory();
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
}

loadResult();

// PWA: 注册Service Worker(根路径部署, scope覆盖整站, 兼容子路径反代)
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('sw.js').catch(() => {});
}

async function renderHistory() {
  // 拉全部归档日期, 逐日对账, 汇总成一张表
  const r = await fetch("api/history");
  const list = await r.json();
  const tbody = document.querySelector("#reconTable tbody");
  const stats = document.getElementById("reconStats");
  if (!list.length) {
    tbody.innerHTML = '<tr><td colspan="9" style="color:#999">暂无历史归档, 先运行筛选</td></tr>';
    return;
  }
  tbody.innerHTML = '<tr><td colspan="12" style="color:#888">卖出模拟回放中(逐只拉K线, 约需半分钟)...</td></tr>';
  const allRows = [];
  let winCnt = 0, lossCnt = 0, sumRet = 0, soldCnt = 0;
  for (const h of list) {
    const rr = await fetch(`api/history/${h.date}/reconcile`);
    const d = await rr.json();
    if (d.ok === false) continue;
    for (const x of (d.items || [])) {
      const ret = x.return_pct;
      const cls = ret === null || ret === undefined ? "" : (ret > 0 ? "row-win" : "row-loss");
      if (ret !== null && ret !== undefined) {
        sumRet += ret;
        if (ret > 0) winCnt++; else lossCnt++;
      }
      if (x.sold) soldCnt++;
      const noData = x.sell_reason === "无后续K线" || x.sell_reason === "K线获取失败";
      const verdict = noData ? "待买入(次日介入)" : (x.sold ? (ret > 0 ? "卖对了" : "止损卖出") : (ret > 0 ? "持有赚" : "持有亏"));
      allRows.push({date: h.date, x, cls: noData ? "" : cls, verdict});
    }
  }
  const matched = winCnt + lossCnt;
  stats.innerHTML = matched ? `
    <div class="item"><b style="color:${winCnt >= lossCnt ? "#c0392b" : "#1a7a3a"}">${(winCnt / matched * 100).toFixed(1)}%</b><span>胜率(${winCnt}赚/${lossCnt}亏)</span></div>
    <div class="item"><b>${(sumRet / matched).toFixed(2)}%</b><span>平均收益率</span></div>
    <div class="item"><b>${soldCnt}/${matched}</b><span>已卖出/总数</span></div>
    <div class="item"><b>${list.length}</b><span>归档天数</span></div>` : "";
  tbody.innerHTML = allRows.map(({date, x, cls, verdict}) => `<tr class="${cls}">
    <td>${date}</td><td>${x.code}</td><td>${x.name}</td>
    <td><b>${x.score ?? "-"}</b></td>
    <td>${x.entry ?? "-"}</td><td>${x.stop ?? "-"}</td>
    <td>${x.sell_date || (x.sold === false ? "持有中" : "-")}</td>
    <td class="reason"><span class="ellip s" title="${x.sell_reason || ""}">${x.sell_reason || "-"}</span></td>
    <td>${x.held_days ?? "-"}</td>
    <td>${x.peak_gain_pct !== null && x.peak_gain_pct !== undefined ? "+" + x.peak_gain_pct.toFixed(1) : "-"}</td>
    <td>${fmtPct(x.return_pct)}</td>
    <td><b>${verdict}</b></td>
  </tr>`).join("") || '<tr><td colspan="12" style="color:#999">暂无可介入股记录</td></tr>';
  const tb = document.querySelector("#reconTable tbody");
  tb.classList.remove("fade-in"); void tb.offsetWidth; tb.classList.add("fade-in");
}
</script>
</body>
</html>"""

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8768)
