# -*- coding: utf-8 -*-
"""CLI入口: python -m funnel.main [--force-boards]"""
import argparse
import json
import sys

from .logic.pipeline import run_funnel, save_result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force-boards", action="store_true", help="强制重建板块缓存")
    args = ap.parse_args()

    def prog(stage, detail, pct):
        print(f"[{pct:3.0f}%] {stage}: {detail}", flush=True)

    result = run_funnel(force_boards=args.force_boards, progress_cb=prog)
    path = save_result(result)
    print(f"\n结果已保存: {path}")

    # 摘要
    meta, l1 = result["meta"], result["layer1"]
    print(f"\n=== 大盘环境: {l1['env']} ({l1['passed']}/3) ===")
    for r in l1["reasons"]:
        print("  ", r)
    print(f"\n=== 强板块({len(result['strong_sectors'])}): {'、'.join(result['strong_sectors'])} ===")
    print(f"候选个股: {meta['candidate_count']}只, K线成功: {meta['kline_count']}只")
    for cat, stocks in result["layer4"].items():
        print(f"\n--- {cat}: {len(stocks)}只 ---")
        for m in stocks[:5]:
            print(f"  {m['code']} {m['name']} 价{m['price']} MA20距{m['ma20_dist']}% "
                  f"d5={m['d5']} d10={m['d10']} d20={m['d20']} 位置{m['position_pct']}%")
            for r in m["reasons"]:
                print(f"      {r}")


if __name__ == "__main__":
    main()
