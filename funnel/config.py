# -*- coding: utf-8 -*-
"""趋势筛选工作台 -- 全局阈值配置
所有阈值集中在此, 调整只改这一个文件
"""

CONFIG = {
    # ========== 第一层: 大盘环境 (复用nextday v2回测验证过的闸门) ==========
    "market": {
        "index_code": "sh000001",        # 上证指数
        "ma_period": 10,                  # 指数均线周期
        "breadth_threshold": 55.0,        # 上涨家数占比阈值(%)
        "limit_up_threshold": 8,          # 涨停家数阈值
        "limit_up_pct_main": 9.8,         # 主板涨停判定涨幅(%)
        "limit_up_pct_cyb": 19.8,         # 创业板/科创板涨停判定涨幅(%)
    },

    # ========== 第二层: 板块强弱 ==========
    "sector": {
        "min_members": 10,                # 成分股少于10只的板块不参与排名(样本太小噪声大)
        "max_members": 400,               # 超大板块(银行等)区分度低, 跳过
        "top_n": 8,                       # 进入第三层的板块数量
        # 正在加强判定(当日进攻)
        "surge_day_gain": 1.0,            # 板块平均当日涨幅(%)
        "surge_vol_ratio": 1.2,           # 板块平均量比
        "surge_up_ratio": 60.0,           # 上涨家数占比(%)
        # 持续强势判定(多周期积累)
        "persist_d5_gain": 5.0,           # 板块平均5日涨幅(%)
        "persist_multi_pos_ratio": 30.0,  # 多周期全红个股占比(%)
    },

    # ========== 第三层: 个股预过滤 ==========
    "stock_filter": {
        "min_list_days": 60,              # 上市不足60日剔除(MA20算不出)
        "max_price": 200.0,               # 股价超过200剔除(一手成本过高, 可选)
        "exclude_st": True,
        "exclude_new_flag": True,         # 剔除N/C开头新股
    },

    # ========== 第四层: 五类分类(决策树优先级: 排除->高位->回调->启动->趋势) ==========
    "classify": {
        # 排除: 过热或趋势破坏
        "exclude_d20_overheat": 40.0,     # 20日涨幅超过40% = 过热
        "exclude_broken_d5": -5.0,        # 跌破MA20且5日跌幅超5% = 趋势破坏
        # 高位观察: 价格位置高 + 远离均线
        "high_position_pct": 90.0,        # 250日区间分位数(%)
        "high_ma20_dist": 15.0,           # 距MA20偏离度(%)
        # 回调观察: 强股回撤
        "pullback_d5": -3.0,              # 5日跌幅阈值(%)
        "pullback_d10_min": 5.0,          # 但10日涨幅仍为正且超此值(%)
        # 启动观察: 刚放量拉升
        "launch_d5_low": 5.0,             # 5日涨幅下限(%)
        "launch_d5_high": 20.0,           # 5日涨幅上限(超了算过热/高位)
        "launch_vol_ratio": 1.5,          # 量比下限
        "launch_ma20_low": -5.0,          # 距MA20偏离下限(%)
        "launch_ma20_high": 10.0,         # 距MA20偏离上限(%)
        # 趋势观察: 多周期走强但未到高位
        "trend_position_max": 90.0,       # 价格位置上限(%)
        # ATR异常波动判定
        "atr_period": 14,
        "atr_exceed_ratio": 2.0,          # 当日涨跌幅 > 2倍ATR% 判定为波动过大
    },

    # ========== 数据层 ==========
    "data": {
        "rank_page_size": 80,             # 腾讯排行每页条数
        "kline_days": 260,                # K线拉取根数(250日分位+MA20+ATR14够用)
        "kline_threads": 12,              # K线并发线程数(防腾讯501, 别超过16)
        "board_cache_days": 7,            # 板块成分缓存有效期(天)
    },

    # ========== 第五层: 评分与行动建议 ==========
    "scoring": {
        # 大盘环境加减分(叠加在百分制上)
        "market_adj": {"strong": 5, "normal": 0, "weak": -20},
        # 行动分档阈值
        "action_enter_score": 72,        # 可介入最低分
        "action_enter_pos_max": 70.0,    # 可介入最高位置分位(%)
        "action_avoid_score": 45,        # 低于此分直接回避
        "action_nochase_pos": 90.0,      # 位置高于此分位=不追高
        # 交易计划
        "stop_max_loss_pct": 8.0,        # 止损幅度上限(%), 超过则收紧
        "entry_near_ma_dist": 5.0,       # 距MA20偏离<=5%视为贴线, 现价可参考
        "entry_ma5_dist": 10.0,          # 偏离<=10%参考回踩MA5, 否则参考回踩MA10
    },

    # ========== 卖出规则(四条, 任一触发即卖) ==========
    "exit_rules": {
        # 1. 硬止损: 收盘价低于止损价
        # (止损价已由trade_plan给出: max(MA20, 入场-2ATR), 上限8%)
        # 2. 移动止盈: 最高盈利达到此值后, 从最高点回撤超过trail_pct即卖
        "trail_activate_pct": 10.0,      # 最高盈利达到10%启动移动止盈
        "trail_pct": 7.0,                # 从最高点回撤7%卖出
        # 3. 趋势破坏: 收盘价连续N日低于MA10
        "break_ma_days": 2,
        "break_ma_period": 10,
        # 4. 时间止损: 持有满N个交易日盈利不足min_gain则卖(资金不无限占用)
        "time_stop_days": 10,
        "time_stop_min_gain": 5.0,       # 10日盈利不足5%视为不及格
    },
}
