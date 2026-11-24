# -*- coding: utf-8 -*-
"""全市场选股扫描（Muse 市场雷达「今日候选」专用入口）。

固定策略：momentum_quality（趋势质量 v1.1）。
固定规则排名：use_llm=False —— 最终排名只按引擎规则评分（factor），
不用 LLM 二次排序；入选理由由规则因子与信号字段生成。
结果写入 reports/screening_YYYYMMDD.md（含机器可读 JSON 块），
由工作流「回存报告到仓库」步骤随 DSA 报告一并提交，Muse 侧自动导入。

状态口径（与 Muse 页面约定一致）：
- ok：扫描成功且有候选
- no_candidates：扫描成功、无股票通过（页面显示「今日无候选」）
- data_insufficient：行情快照拿不到（页面显示「数据不足」）
- error：运行异常（页面显示「数据不足」并附原因）
"""
from __future__ import annotations

import dataclasses
import json
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

STRATEGY = "momentum_quality"
MARKET = "cn"

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _reason_from_rules(pick: dict) -> str:
    """在引擎未给 ranking_reason 时，用规则因子与信号拼入选理由（只陈述事实）。"""
    parts: list[str] = []
    fs = pick.get("factor_scores") or {}
    if fs:
        top = sorted(fs.items(), key=lambda kv: kv[1], reverse=True)[:3]
        parts.append("因子得分靠前：" + "、".join(f"{k} {v:.1f}" for k, v in top))
    if pick.get("ma_bullish"):
        parts.append("均线多头排列")
    if pick.get("price_above_ma20"):
        parts.append("股价位于 MA20 上方")
    if pick.get("macd_status"):
        parts.append(f"MACD 状态：{pick['macd_status']}")
    if pick.get("breakout_20d_pct") is not None:
        parts.append(f"距 20 日高点 {pick['breakout_20d_pct']:.1f}%")
    if pick.get("industry"):
        parts.append(f"所属行业：{pick['industry']}")
    return "；".join(parts)


def _render(result_dict: dict | None, status: str, error: str = "") -> str:
    now = datetime.now(timezone.utc)
    run_number = os.environ.get("GITHUB_RUN_NUMBER", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    lines: list[str] = []
    lines.append(f"# 全市场选股 · 趋势质量（{STRATEGY}）")
    lines.append("")
    lines.append(f"- 状态: {status}")
    lines.append(f"- 策略: {STRATEGY}")
    if result_dict:
        lines.append(f"- 策略版本: {result_dict.get('strategy_version', '')}")
        lines.append(f"- 排名模式: {result_dict.get('ranking_mode', '')}（llm_ranked={result_dict.get('llm_ranked')}）")
        lines.append(f"- 全市场快照数: {result_dict.get('snapshot_count')}")
        lines.append(f"- 硬过滤后剩余: {result_dict.get('after_filter_count')}")
        lines.append(f"- 快照来源: {result_dict.get('snapshot_source', '')}")
    lines.append(f"- 运行编号: run #{run_number} (run_id {run_id})")
    lines.append(f"- 生成时间(UTC): {now.strftime('%Y-%m-%d %H:%M:%S')}")
    if error:
        lines.append(f"- 异常: {error}")
    lines.append("")
    if result_dict:
        picks = result_dict.get("picks") or []
        if status == "ok":
            lines.append("## 候选")
            for p in picks:
                reason = p.get("ranking_reason") or _reason_from_rules(p)
                lines.append("")
                lines.append(f"### #{p.get('rank')} {p.get('name')} ({p.get('code')})")
                lines.append(f"- 最终分: {p.get('final_score')}（规则分 {p.get('screen_score')}）")
                lines.append(f"- 现价: {p.get('price')}  涨跌幅: {p.get('change_pct')}%  成交额: {p.get('amount')}")
                lines.append(f"- 入选理由: {reason}")
                lines.append(f"- 因子分: {json.dumps(p.get('factor_scores') or {}, ensure_ascii=False)}")
                risks = p.get('risk_summary') or ''
                flags = p.get('risk_flags') or []
                lines.append(f"- 主要风险: {risks} {flags if flags else ''}".strip())
                gaps = p.get('daily_quality_flags') or ''
                if gaps:
                    lines.append(f"- 数据缺口: {gaps}")
        deg = result_dict.get("degradation") or []
        serr = result_dict.get("source_errors") or []
        if deg:
            lines.append("")
            lines.append(f"降级说明: {json.dumps(deg, ensure_ascii=False)}")
        if serr:
            lines.append(f"数据源错误: {json.dumps(serr, ensure_ascii=False)}")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(result_dict, ensure_ascii=False, default=str))
        lines.append("```")
    return "\n".join(lines) + "\n"


def main() -> int:
    reports = ROOT / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    out = reports / f"screening_{day}.md"
    try:
        from src.services.screening.pipeline import screen

        result = screen(
            STRATEGY,
            market=MARKET,
            use_llm=False,  # 第一轮固定规则排名，不做 AI 二次排序
            explain_filters=True,
        )
        result_dict = dataclasses.asdict(result)
        picks = result_dict.get("picks") or []
        if (result_dict.get("snapshot_count") or 0) == 0:
            status = "data_insufficient"
        elif not picks:
            status = "no_candidates"
        else:
            status = "ok"
        out.write_text(_render(result_dict, status), encoding="utf-8")
        print(f"screening done: status={status} picks={len(picks)} -> {out}")
    except Exception:
        err = traceback.format_exc(limit=5)
        try:
            out.write_text(_render(None, "error", err[-800:]), encoding="utf-8")
        except Exception:
            pass
        print(f"screening failed:\n{err}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
