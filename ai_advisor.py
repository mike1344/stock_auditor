# -*- coding: utf-8 -*-
"""
AI 交易诊断模块
功能：
  1. 异常检测 —— 基于规则自动识别问题交易行为（无需 API）
  2. 策略归因 —— 量化拆解选股能力 vs 择时能力（无需 API）
  3. AI 诊断 —— 调用 LLM 综合评估交易行为，给出改进建议
"""

import sys
import os
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

# 修复 Windows 中文终端 GBK 编码下打印 emoji 报错的问题
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ============================================================
# 加载 .env 配置
# AI 诊断优先使用 AI_* 变量，未设置时回退到 OCR_* 变量
# 这样 OCR（需视觉模型）和 AI 诊断（纯文本）可以用不同的 API
# ============================================================
load_dotenv()

API_KEY = os.getenv("AI_API_KEY") or os.getenv("OCR_API_KEY")
BASE_URL = os.getenv("AI_BASE_URL") or os.getenv("OCR_BASE_URL")
MODEL = os.getenv("AI_MODEL") or os.getenv("OCR_MODEL")


# ============================================================
# 辅助函数
# ============================================================

def _format_money(val: float) -> str:
    """格式化金额，带正负号和千分位"""
    return f"{val:+,.2f}"


def _pct(val: float) -> str:
    """格式化百分比"""
    return f"{val:+.2f}%"


def _holding_bucket(days: int) -> str:
    """将持仓天数映射到分段标签"""
    if days <= 1:
        return "0-1天"
    elif days <= 5:
        return "2-5天"
    elif days <= 15:
        return "6-15天"
    elif days <= 30:
        return "16-30天"
    else:
        return "30+天"


# ============================================================
# 功能 1：规则型异常检测
# ============================================================

def _detect_big_losses(matched: list) -> list[dict]:
    """检测大额亏损交易"""
    anomalies = []
    for i, t in enumerate(matched):
        is_big_loss = t.profit < -200
        is_big_pct_loss = t.profit_pct < -5.0
        if is_big_loss or is_big_pct_loss:
            severity = "critical" if (is_big_loss and is_big_pct_loss) else "warning"
            anomalies.append({
                "type": "大额亏损",
                "severity": severity,
                "stock": f"{t.name}({t.code})",
                "description": (
                    f"{t.sell_date.strftime('%m-%d')} {t.name}："
                    f"亏损{_format_money(t.profit)}元（{_pct(t.profit_pct)}），"
                    f"买入@{t.buy_price} → 卖出@{t.sell_price}，持仓{t.holding_days}天"
                ),
                "trade_index": i,
            })
    return anomalies


def _detect_high_frequency(matched: list) -> list[dict]:
    """检测同日同股高频交易"""
    from collections import defaultdict
    # 按 (code, sell_date) 分组统计笔数
    groups: dict = defaultdict(list)
    for i, t in enumerate(matched):
        key = (t.code, t.sell_date.strftime("%Y-%m-%d"))
        groups[key].append(i)

    anomalies = []
    for (code, day), indices in groups.items():
        count = len(indices)
        if count > 3:
            name = matched[indices[0]].name
            severity = "critical" if count >= 6 else "warning"
            anomalies.append({
                "type": "高频交易",
                "severity": severity,
                "stock": f"{name}({code})",
                "description": (
                    f"{day} 在{name}上交易{count}次，"
                    f"存在高频交易倾向，手续费磨损可能较大"
                ),
                "trade_index": indices[0],
            })
    return anomalies


def _detect_same_day(matched: list) -> list[dict]:
    """检测超短线（当日买卖，T+0）"""
    anomalies = []
    for i, t in enumerate(matched):
        if t.holding_days == 0:
            severity = "critical" if t.profit < 0 else "warning"
            anomalies.append({
                "type": "超短线",
                "severity": severity,
                "stock": f"{t.name}({t.code})",
                "description": (
                    f"{t.sell_date.strftime('%m-%d')} {t.name}："
                    f"当日买入当日卖出（T+0），"
                    f"盈亏{_format_money(t.profit)}元，持仓不足1天"
                ),
                "trade_index": i,
            })
    return anomalies


def _detect_consecutive_losses(matched: list) -> list[dict]:
    """检测同股票连续亏损"""
    from collections import defaultdict
    # 按 code 分组，按 sell_date 排序
    by_stock: dict = defaultdict(list)
    for i, t in enumerate(matched):
        by_stock[t.code].append((t.sell_date, i, t))

    anomalies = []
    for code, trades in by_stock.items():
        trades.sort(key=lambda x: x[0])  # 按卖出日期排序
        streak = 0
        streak_indices = []
        for sell_date, idx, t in trades:
            if t.profit < 0:
                streak += 1
                streak_indices.append(idx)
            else:
                # 盈利打断连败
                if streak >= 3:
                    severity = "critical" if streak >= 5 else "warning"
                    name = t.name
                    anomalies.append({
                        "type": "连续亏损",
                        "severity": severity,
                        "stock": f"{name}({code})",
                        "description": (
                            f"{name}：连续{streak}笔亏损，"
                            f"可能存在情绪化交易或选股方向错误"
                        ),
                        "trade_index": streak_indices[0],
                    })
                streak = 0
                streak_indices = []
        # 处理末尾的连败
        if streak >= 3:
            severity = "critical" if streak >= 5 else "warning"
            anomalies.append({
                "type": "连续亏损",
                "severity": severity,
                "stock": f"{matched[streak_indices[0]].name}({code})",
                "description": (
                    f"{matched[streak_indices[0]].name}：连续{streak}笔亏损，"
                    f"可能存在情绪化交易或选股方向错误"
                ),
                "trade_index": streak_indices[0],
            })
    return anomalies


def _detect_chase_kill(matched: list) -> list[dict]:
    """检测追涨杀跌：买在更高位 → 亏损卖出"""
    from collections import defaultdict
    # 按 code 分组，按 buy_date 排序
    by_stock: dict = defaultdict(list)
    for i, t in enumerate(matched):
        by_stock[t.code].append((t.buy_date, t.sell_date, i, t))

    anomalies = []
    for code, trades in by_stock.items():
        trades.sort(key=lambda x: (x[0], x[1]))  # 按买入日期、卖出日期排序
        for j in range(len(trades) - 1):
            _, _, idx_a, t_a = trades[j]
            _, _, idx_b, t_b = trades[j + 1]

            # 跳过时间间隔超过 30 天的（不是同一轮决策）
            gap = (t_b.buy_date - t_a.sell_date).days
            if gap > 30 or gap < 0:
                continue

            # 条件：后一笔买入价 > 前一笔卖出价，且后一笔亏损
            if t_b.buy_price > t_a.sell_price and t_b.profit < 0:
                anomalies.append({
                    "type": "追涨杀跌",
                    "severity": "critical",
                    "stock": f"{t_b.name}({t_b.code})",
                    "description": (
                        f"{t_b.name}：{t_b.buy_date.strftime('%m-%d')}以{t_b.buy_price}买入"
                        f"（高于{t_a.sell_date.strftime('%m-%d')}卖出价{t_a.sell_price}），"
                        f"随后以{t_b.sell_price}亏损卖出，典型追涨杀跌"
                    ),
                    "trade_index": idx_b,
                })
    return anomalies


def detect_anomalies(matched: list) -> list[dict]:
    """
    扫描已完成配对的交易列表，检测行为异常
    参数:
        matched: MatchedTrade 列表
    返回:
        list[dict]，每个 dict 包含 type/severity/stock/description/trade_index
    """
    if not matched:
        return []

    # 依次运行五个检测器
    all_anomalies = []
    all_anomalies.extend(_detect_big_losses(matched))
    all_anomalies.extend(_detect_high_frequency(matched))
    all_anomalies.extend(_detect_same_day(matched))
    all_anomalies.extend(_detect_consecutive_losses(matched))
    all_anomalies.extend(_detect_chase_kill(matched))

    # 按严重度排序：critical 在前
    all_anomalies.sort(key=lambda a: (0 if a["severity"] == "critical" else 1, a["type"]))
    return all_anomalies


# ============================================================
# 功能 2：策略归因（选股能力 vs 择时能力）
# ============================================================

def _score_stock_selection(matched: list) -> tuple[float, str]:
    """评估选股能力，返回 (score, detail_string)"""
    from collections import defaultdict
    by_stock: dict = defaultdict(list)
    for t in matched:
        by_stock[t.code].append(t.profit_pct)

    total_stocks = len(by_stock)
    if total_stocks == 0:
        return 0.0, "无数据"

    pos_stocks = 0
    stock_avgs = {}
    for code, pcts in by_stock.items():
        avg = sum(pcts) / len(pcts)
        stock_avgs[code] = avg
        if avg > 0:
            pos_stocks += 1

    pos_ratio = pos_stocks / total_stocks
    raw = pos_ratio * 100 - 50  # [-50, +50]

    # 幅度加成
    pos_avgs = [v for v in stock_avgs.values() if v > 0]
    avg_pos_return = sum(pos_avgs) / len(pos_avgs) if pos_avgs else 0
    magnitude_boost = min(avg_pos_return / 2, 50)

    score = max(-100, min(100, raw + magnitude_boost))

    # 构建说明
    best_stock = max(stock_avgs, key=stock_avgs.get)
    worst_stock = min(stock_avgs, key=stock_avgs.get)
    # 找股票名
    name_map = {}
    for t in matched:
        name_map[t.code] = t.name
    detail = (
        f"共交易{total_stocks}只股票，{pos_stocks}只平均盈利（胜率{pos_ratio*100:.0f}%）。"
        f"最佳：{name_map.get(best_stock, best_stock)}（均收益{stock_avgs[best_stock]:+.2f}%），"
        f"最差：{name_map.get(worst_stock, worst_stock)}（均收益{stock_avgs[worst_stock]:+.2f}%）"
    )

    return round(score, 1), detail


def _score_timing(matched: list) -> tuple[float, str, dict]:
    """评估择时能力，返回 (score, detail_string, bucket_data)"""
    from collections import defaultdict
    # 按持仓天数分桶
    buckets_order = ["0-1天", "2-5天", "6-15天", "16-30天", "30+天"]
    buckets: dict = defaultdict(list)
    for t in matched:
        bucket = _holding_bucket(t.holding_days)
        buckets[bucket].append(t)

    bucket_data = {}
    for b in buckets_order:
        trades = buckets.get(b, [])
        if trades:
            wins = [t for t in trades if t.profit > 0]
            bucket_data[b] = {
                "count": len(trades),
                "win_rate": round(len(wins) / len(trades) * 100, 2),
                "avg_profit_pct": round(sum(t.profit_pct for t in trades) / len(trades), 2),
                "total_profit": round(sum(t.profit for t in trades), 2),
            }
        else:
            bucket_data[b] = {"count": 0, "win_rate": 0, "avg_profit_pct": 0, "total_profit": 0}

    # 短线加权（0-1天 + 2-5天）
    short_trades = [t for b in ["0-1天", "2-5天"] for t in buckets.get(b, [])]
    long_trades = [t for b in ["16-30天", "30+天"] for t in buckets.get(b, [])]

    if not short_trades and not long_trades:
        return 0.0, "持仓周期分布单一，无法评估择时能力", bucket_data

    if not short_trades:
        return 50.0, "无短线交易，偏好中长线持仓", bucket_data
    if not long_trades:
        return -30.0, "全部为短线交易，缺乏中长线持仓经验", bucket_data

    short_wr = sum(1 for t in short_trades if t.profit > 0) / len(short_trades) * 100
    long_wr = sum(1 for t in long_trades if t.profit > 0) / len(long_trades) * 100
    short_avg = sum(t.profit_pct for t in short_trades) / len(short_trades)
    long_avg = sum(t.profit_pct for t in long_trades) / len(long_trades)

    wr_diff = long_wr - short_wr
    profit_diff = long_avg - short_avg

    raw = wr_diff * 1.5 - 30

    if profit_diff > 0:
        raw += min(profit_diff * 3, 30)
    else:
        raw += max(profit_diff * 3, -30)

    score = max(-100, min(100, raw))

    detail = (
        f"短线（0-5天）：{len(short_trades)}笔，胜率{short_wr:.1f}%，均收益{short_avg:+.2f}%。"
        f"长线（16+天）：{len(long_trades)}笔，胜率{long_wr:.1f}%，均收益{long_avg:+.2f}%。"
    )
    if score > 20:
        detail += "长线收益显著优于短线，择时能力良好。"
    elif score < -20:
        detail += "短线收益弱于长线，存在过度交易倾向，建议延长持仓周期。"
    else:
        detail += "短线和长线收益差距不大，择时能力中性。"

    return round(score, 1), detail, bucket_data


def _form_conclusion(sel_score: float, tim_score: float) -> str:
    """根据选股和择时得分生成归因结论"""
    if sel_score > 20 and tim_score > 20:
        return "选股择时双优，交易体系较为成熟，继续保持纪律即可"
    elif sel_score > 20 and tim_score <= 20:
        return "选股能力强但择时偏弱，好股票被短线操作拖累。建议：对于盈利股票适当延长持仓周期，让利润奔跑"
    elif sel_score <= 20 and tim_score > 20:
        return "择时能力尚可但选股需加强，进出场时机不错但选的股票不够好。建议：加强基本面和技术面筛选，提高选股胜率"
    elif sel_score >= -20 and tim_score >= -20:
        return "选股择时均处于中性水平，交易体系尚未形成稳定优势。建议：建立选股 checklist，减少随意性交易"
    else:
        return "选股择时均需大幅提升。建议：暂停实盘，系统学习交易体系，用模拟盘验证策略后再回归"


def attribute_performance(matched: list) -> dict:
    """
    基于已完成交易进行策略归因分析（选股 vs 择时）
    参数:
        matched: MatchedTrade 列表
    返回:
        dict with keys: stock_selection_score, timing_score,
            stock_selection_detail, timing_detail, conclusion, bucket_analysis
    """
    if not matched:
        return {
            "stock_selection_score": 0.0,
            "timing_score": 0.0,
            "stock_selection_detail": "无交易数据",
            "timing_detail": "无交易数据",
            "conclusion": "数据不足，无法归因",
            "bucket_analysis": {},
        }

    # 数据过少时返回中性评估
    if len(matched) < 3:
        return {
            "stock_selection_score": 0.0,
            "timing_score": 0.0,
            "stock_selection_detail": f"仅{len(matched)}笔交易，数据量不足，无法可靠评估选股能力",
            "timing_detail": f"仅{len(matched)}笔交易，数据量不足，无法可靠评估择时能力",
            "conclusion": "交易笔数不足（<3笔），建议积累更多交易数据后再进行归因分析",
            "bucket_analysis": {},
        }

    sel_score, sel_detail = _score_stock_selection(matched)
    tim_score, tim_detail, bucket_data = _score_timing(matched)
    conclusion = _form_conclusion(sel_score, tim_score)

    return {
        "stock_selection_score": sel_score,
        "timing_score": tim_score,
        "stock_selection_detail": sel_detail,
        "timing_detail": tim_detail,
        "conclusion": conclusion,
        "bucket_analysis": bucket_data,
    }


# ============================================================
# 功能 3：AI 诊断（LLM 调用）
# ============================================================

def _build_diagnosis_prompt(
    summary: dict,
    matched: list,
    anomalies: list[dict],
    attribution: dict,
) -> str:
    """构建发送给 LLM 的诊断提示词"""
    # 最佳/最差交易
    sorted_trades = sorted(matched, key=lambda t: t.profit, reverse=True)
    best_3 = sorted_trades[:3]
    worst_3 = sorted_trades[-3:]

    best_lines = []
    for t in best_3:
        best_lines.append(
            f"  {t.name}({t.code}) {t.buy_date.strftime('%m-%d')}→{t.sell_date.strftime('%m-%d')} "
            f"盈利{_format_money(t.profit)}元（{_pct(t.profit_pct)}）持仓{t.holding_days}天"
        )

    worst_lines = []
    for t in worst_3:
        worst_lines.append(
            f"  {t.name}({t.code}) {t.buy_date.strftime('%m-%d')}→{t.sell_date.strftime('%m-%d')} "
            f"亏损{_format_money(t.profit)}元（{_pct(t.profit_pct)}）持仓{t.holding_days}天"
        )

    # 异常汇总
    anomaly_lines = []
    for a in anomalies:
        tag = "🔴" if a["severity"] == "critical" else "🟡"
        anomaly_lines.append(f"  {tag} [{a['type']}] {a['description']}")

    # 持仓周期分布
    bucket_lines = []
    for bucket, data in attribution.get("bucket_analysis", {}).items():
        if data["count"] > 0:
            bucket_lines.append(
                f"  {bucket}: {data['count']}笔, 胜率{data['win_rate']}%, "
                f"均收益{data['avg_profit_pct']:+.2f}%, 总盈亏{_format_money(data['total_profit'])}元"
            )

    prompt = f"""你是一位资深的交易行为分析师。请基于以下交易数据，对这位交易者进行全面诊断。

## 交易概览
- 总交易笔数：{summary.get('total_trades', 0)}
- 胜率：{summary.get('win_rate', 0)}%
- 总盈亏：{_format_money(summary.get('total_profit', 0))}元
- 平均每笔盈亏：{_format_money(summary.get('avg_profit_per_trade', 0))}元
- 最大单笔盈利：{_format_money(summary.get('max_profit', 0))}元
- 最大单笔亏损：{_format_money(summary.get('max_loss', 0))}元
- 平均持仓天数：{summary.get('avg_holding_days', 0)}天

## 最佳 3 笔交易
{chr(10).join(best_lines) if best_lines else '  无数据'}

## 最差 3 笔交易
{chr(10).join(worst_lines) if worst_lines else '  无数据'}

## 检测到的异常行为（共 {len(anomalies)} 个）
{chr(10).join(anomaly_lines) if anomaly_lines else '  未检测到明显异常'}

## 策略归因
- 选股得分：{attribution.get('stock_selection_score', 0)}（-100到+100）
- 择时得分：{attribution.get('timing_score', 0)}（-100到+100）
- 归因结论：{attribution.get('conclusion', '无')}
- 选股分析：{attribution.get('stock_selection_detail', '无')}
- 择时分析：{attribution.get('timing_detail', '无')}

## 各持仓周期表现
{chr(10).join(bucket_lines) if bucket_lines else '  无数据'}

请分析：
1. 这位交易者的行为模式特征（2-3 句话概括）
2. 最突出的 3 个问题（按严重程度排序）
3. 针对每个问题的具体、可操作的改进建议

要求：用中文回答，语气专业但友好。用数字编号，不要使用 markdown 标题符号。每个建议要具体可执行。"""

    return prompt


def _call_llm_text(prompt: str) -> str | None:
    """
    调用 LLM 进行纯文本对话
    参数:
        prompt: 提示词文本
    返回:
        LLM 的回复文本，失败时返回 None
    """
    if not API_KEY:
        print("⚠️  OCR_API_KEY 未配置，跳过 AI 诊断")
        return None
    if not BASE_URL:
        print("⚠️  OCR_BASE_URL 未配置，跳过 AI 诊断")
        return None
    if not MODEL:
        print("⚠️  OCR_MODEL 未配置，跳过 AI 诊断")
        return None

    try:
        client = OpenAI(
            api_key=API_KEY,
            base_url=BASE_URL,
            timeout=90.0,  # 诊断分析比 OCR 更复杂，给更长超时
        )
    except Exception as e:
        print(f"⚠️  初始化 API 客户端失败：{e}")
        return None

    try:
        print("🔄 正在调用 AI 模型进行交易诊断...")
        response = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,   # 低温度确保分析稳定
            max_tokens=2048,
        )
    except Exception as e:
        error_str = str(e).lower()
        if any(kw in error_str for kw in ["auth", "unauthorized", "invalid api key", "401"]):
            print(f"⚠️  API Key 无效或认证失败，跳过 AI 诊断")
        elif any(kw in error_str for kw in ["timeout", "timed out", "connection"]):
            print(f"⚠️  网络超时或连接失败，跳过 AI 诊断")
        else:
            print(f"⚠️  API 调用失败：{e}")
        return None

    content = response.choices[0].message.content
    if content is None:
        print("⚠️  AI 返回内容为空")
        return None

    return content.strip()


def ai_diagnosis(
    matched: list,
    summary: dict,
    anomalies: list[dict],
    attribution: dict,
) -> str | None:
    """
    调用 LLM 对交易行为进行综合诊断
    参数:
        matched:     已完成配对的交易列表
        summary:     来自 calc_summary() 的统计摘要
        anomalies:   来自 detect_anomalies() 的异常列表
        attribution: 来自 attribute_performance() 的归因结果
    返回:
        LLM 的诊断文本，失败或 API 未配置时返回 None
    """
    if not matched:
        print("⚠️  无交易数据，跳过 AI 诊断")
        return None

    prompt = _build_diagnosis_prompt(summary, matched, anomalies, attribution)
    return _call_llm_text(prompt)


# ============================================================
# 报告打印
# ============================================================

def _score_bar(score: float) -> str:
    """将分数转为可视化的条形"""
    if score >= 60:
        return "🟢🟢🟢"
    elif score >= 20:
        return "🟢🟢"
    elif score >= -20:
        return "🟡🟡"
    elif score >= -60:
        return "🟠🟠"
    else:
        return "🔴🔴"


def print_advisory_report(result: dict):
    """将 ai_advisory 的结果格式化打印到终端"""
    print()
    print("=" * 65)
    print("  🤖 AI 交 易 诊 断 报 告")
    print("=" * 65)
    print()

    # ── 异常检测 ──
    anomalies = result.get("anomalies", [])
    critical_count = sum(1 for a in anomalies if a["severity"] == "critical")
    warning_count = sum(1 for a in anomalies if a["severity"] == "warning")
    print(f"── 异常检测：发现 {len(anomalies)} 个异常（🔴 {critical_count} 严重 / 🟡 {warning_count} 警告）──")
    print()

    if anomalies:
        for a in anomalies:
            tag = "🔴" if a["severity"] == "critical" else "🟡"
            print(f"  {tag} [{a['type']}] {a['stock']}")
            print(f"     {a['description']}")
        print()
    else:
        print("  ✅ 未检测到明显的交易行为异常")
        print()

    # ── 策略归因 ──
    attribution = result.get("attribution", {})
    print("── 策略归因 ──")
    print()
    sel_score = attribution.get("stock_selection_score", 0)
    tim_score = attribution.get("timing_score", 0)
    print(f"  选股能力：{_score_bar(sel_score)} {sel_score:+.1f} 分")
    print(f"     {attribution.get('stock_selection_detail', '无')}")
    print()
    print(f"  择时能力：{_score_bar(tim_score)} {tim_score:+.1f} 分")
    print(f"     {attribution.get('timing_detail', '无')}")
    print()
    print(f"  📋 归因结论：{attribution.get('conclusion', '无')}")
    print()

    # 持仓周期分布
    bucket_data = attribution.get("bucket_analysis", {})
    if bucket_data:
        print("  ── 持仓周期分布 ──")
        for bucket, data in bucket_data.items():
            if data["count"] > 0:
                bar = "█" * max(1, int(data["count"] / max(1, max(d["count"] for d in bucket_data.values())) * 20))
                print(f"  {bucket:8s} {bar:20s} {data['count']:3d}笔  胜率{data['win_rate']:5.1f}%  均收益{data['avg_profit_pct']:+.2f}%")
        print()

    # ── AI 诊断 ──
    ai_text = result.get("ai_diagnosis")
    if ai_text:
        print("── AI 综合诊断 ──")
        print()
        print(ai_text)
        print()
    else:
        print("── AI 综合诊断：未获取（API 未配置或调用失败）──")
        print()

    print("=" * 65)
    print("  ✅ AI 诊断完成")
    print("=" * 65)


# ============================================================
# 主入口
# ============================================================

def ai_advisory(
    matched: list,
    open_positions: list = None,
    unmatched_sells: list = None,
    df_all=None,
) -> dict:
    """
    AI 交易顾问主入口：依次执行异常检测、策略归因、AI 诊断，并输出终端报告
    参数:
        matched:         已完成配对的交易列表
        open_positions:  当前持仓列表（可选）
        unmatched_sells: 无法配对的卖出列表（可选）
        df_all:          完整的交易 DataFrame（预留扩展）
    返回:
        dict with keys: anomalies, attribution, ai_diagnosis
    """
    # 空数据守卫
    if not matched:
        print("⚠️  无已完成配对的交易数据，跳过 AI 诊断")
        return {
            "anomalies": [],
            "attribution": attribute_performance([]),
            "ai_diagnosis": None,
        }

    # 步骤 1：异常检测
    anomalies = detect_anomalies(matched)

    # 步骤 2：策略归因
    attribution = attribute_performance(matched)

    # 步骤 3：AI 诊断（需要先计算 summary）
    # 延迟导入避免循环依赖
    from analyze_trades import calc_summary
    summary = calc_summary(matched)
    ai_text = ai_diagnosis(matched, summary, anomalies, attribution)

    # 整合结果
    result = {
        "anomalies": anomalies,
        "attribution": attribution,
        "ai_diagnosis": ai_text,
    }

    # 打印报告
    print_advisory_report(result)

    return result


# ============================================================
# 测试入口
# ============================================================
if __name__ == "__main__":
    import pandas as pd
    from dataclasses import dataclass

    @dataclass
    class MatchedTrade:
        code: str
        name: str
        buy_date: pd.Timestamp
        sell_date: pd.Timestamp
        buy_price: float
        sell_price: float
        volume: int
        profit: float
        profit_pct: float
        holding_days: int

    # 构造测试数据，覆盖多种异常场景
    matched = [
        # 正常盈利
        MatchedTrade("000001", "平安银行", pd.Timestamp("2026-03-01"), pd.Timestamp("2026-03-10"), 10.0, 11.0, 100, 100.0, 10.0, 9),
        MatchedTrade("000001", "平安银行", pd.Timestamp("2026-03-15"), pd.Timestamp("2026-03-25"), 10.5, 12.0, 200, 300.0, 14.29, 10),
        # 大额亏损
        MatchedTrade("600519", "贵州茅台", pd.Timestamp("2026-04-01"), pd.Timestamp("2026-04-03"), 1800.0, 1700.0, 100, -10000.0, -5.56, 2),
        # 超短线 + 亏损
        MatchedTrade("000858", "五粮液", pd.Timestamp("2026-04-10"), pd.Timestamp("2026-04-10"), 150.0, 148.0, 100, -200.0, -1.33, 0),
        MatchedTrade("000858", "五粮液", pd.Timestamp("2026-04-10"), pd.Timestamp("2026-04-10"), 151.0, 152.0, 50, 50.0, 0.66, 0),
        MatchedTrade("000858", "五粮液", pd.Timestamp("2026-04-10"), pd.Timestamp("2026-04-10"), 149.0, 147.0, 100, -200.0, -1.34, 0),
        MatchedTrade("000858", "五粮液", pd.Timestamp("2026-04-10"), pd.Timestamp("2026-04-10"), 150.5, 149.0, 80, -120.0, -1.00, 0),
        # 连续亏损（同股票3笔）
        MatchedTrade("002309", "中利集团", pd.Timestamp("2026-05-01"), pd.Timestamp("2026-05-05"), 8.0, 7.5, 200, -100.0, -6.25, 4),
        MatchedTrade("002309", "中利集团", pd.Timestamp("2026-05-08"), pd.Timestamp("2026-05-12"), 7.8, 7.2, 200, -120.0, -7.69, 4),
        MatchedTrade("002309", "中利集团", pd.Timestamp("2026-05-15"), pd.Timestamp("2026-05-20"), 7.5, 7.0, 200, -100.0, -6.67, 5),
        # 追涨杀跌
        MatchedTrade("601212", "白银有色", pd.Timestamp("2026-05-10"), pd.Timestamp("2026-05-15"), 7.5, 8.0, 100, 50.0, 6.67, 5),
        MatchedTrade("601212", "白银有色", pd.Timestamp("2026-05-20"), pd.Timestamp("2026-05-25"), 8.5, 7.8, 100, -70.0, -8.24, 5),
    ]

    # 运行诊断（LLM 调用在没有 API Key 时会被跳过）
    print("📋 测试数据：")
    print(f"   共 {len(matched)} 笔已完成交易")
    print(f"   预期检测到：大额亏损、超短线(4笔)、高频交易(4笔)、连续亏损、追涨杀跌")
    print()

    result = ai_advisory(matched)

    print()
    print("✅ ai_advisor 模块测试完成")
