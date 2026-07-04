# -*- coding: utf-8 -*-
"""
交易分析模块
功能：基于清洗后的交易记录，进行 FIFO 配对、盈亏计算、胜率统计、持仓分析
输入：pandas DataFrame（与 load_trades.py / ocr_trade.py 输出格式一致）
"""

import sys
from pathlib import Path
import pandas as pd
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

# 修复 Windows 中文终端 GBK 编码下打印 emoji 报错的问题
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


# ============================================================
# 数据结构定义
# ============================================================

@dataclass
class MatchedTrade:
    """单笔配对完成的交易（买入 → 卖出）"""
    code: str                # 股票代码
    name: str                # 股票名称
    buy_date: pd.Timestamp   # 买入日期
    sell_date: pd.Timestamp  # 卖出日期
    buy_price: float         # 买入均价
    sell_price: float        # 卖出均价
    volume: int              # 成交股数
    profit: float            # 盈亏金额
    profit_pct: float        # 盈亏比例 (%)
    holding_days: int        # 持仓天数


@dataclass
class OpenPosition:
    """尚未卖出的持仓"""
    code: str
    name: str
    buy_date: pd.Timestamp
    buy_price: float
    volume: int              # 剩余未卖出的股数


# ============================================================
# FIFO 配对算法
# ============================================================

def match_trades_fifo(df: pd.DataFrame) -> tuple[list[MatchedTrade], list[OpenPosition], list[dict]]:
    """
    使用 FIFO（先进先出）规则将买入和卖出配对，计算每笔盈亏
    参数:
        df: 包含 date/code/name/action/volume/price 的 DataFrame
    返回:
        (matched_trades, open_positions, unmatched_sells)
        - matched_trades:  已完成配对的交易列表
        - open_positions:  尚未卖出的持仓列表
        - unmatched_sells: 无法配对的卖出（无对应买入记录）
    """
    # 按股票代码分组处理
    grouped = df.groupby("code")

    matched_trades: list[MatchedTrade] = []
    open_positions: list[OpenPosition] = []
    unmatched_sells: list[dict] = []

    for code, group in grouped:
        # 获取股票名称（取最新的一条记录的名称）
        stock_name = group["name"].iloc[-1]

        # 按日期排序（同一天按买入在前、卖出在后排序）
        # 先按日期，再按 action 排序确保买入先处理
        group = group.sort_values(by=["date", "action"], ascending=[True, True])

        # 买入队列：(买入日期, 买入价格, 剩余股数)
        buy_queue: deque = deque()

        for _, row in group.iterrows():
            action = row["action"]
            volume = int(row["volume"])
            price = float(row["price"])
            date = row["date"]

            if action == "买入":
                # 买入：加入队列尾部
                buy_queue.append({
                    "buy_date": date,
                    "buy_price": price,
                    "volume": volume,
                })

            elif action == "卖出":
                sell_volume = volume
                sell_price = price
                sell_date = date

                # 从队列头部开始匹配（FIFO）
                while sell_volume > 0 and len(buy_queue) > 0:
                    buy = buy_queue[0]  # 看队首但不弹出
                    matched_volume = min(buy["volume"], sell_volume)

                    # 计算这笔匹配的盈亏
                    profit = (sell_price - buy["buy_price"]) * matched_volume
                    profit_pct = (sell_price - buy["buy_price"]) / buy["buy_price"] * 100
                    holding_days = (sell_date - buy["buy_date"]).days

                    # 记录配对结果
                    matched_trades.append(MatchedTrade(
                        code=code,
                        name=stock_name,
                        buy_date=buy["buy_date"],
                        sell_date=sell_date,
                        buy_price=buy["buy_price"],
                        sell_price=sell_price,
                        volume=matched_volume,
                        profit=round(profit, 2),
                        profit_pct=round(profit_pct, 2),
                        holding_days=holding_days,
                    ))

                    # 更新买入队列
                    buy["volume"] -= matched_volume
                    sell_volume -= matched_volume

                    # 如果这笔买入已完全匹配，从队列移除
                    if buy["volume"] == 0:
                        buy_queue.popleft()

                # 如果卖出量还没匹配完但队列已空，记录为无法配对
                if sell_volume > 0:
                    unmatched_sells.append({
                        "code": code,
                        "name": stock_name,
                        "date": sell_date,
                        "price": sell_price,
                        "unmatched_volume": sell_volume,
                    })

        # 处理完后，队列中剩余的买入就是当前持仓
        for buy in buy_queue:
            open_positions.append(OpenPosition(
                code=code,
                name=stock_name,
                buy_date=buy["buy_date"],
                buy_price=buy["buy_price"],
                volume=buy["volume"],
            ))

    return matched_trades, open_positions, unmatched_sells


# ============================================================
# 统计分析函数
# ============================================================

def calc_summary(matched: list[MatchedTrade]) -> dict:
    """计算整体交易统计摘要"""
    if not matched:
        return {
            "total_trades": 0,
            "win_count": 0,
            "loss_count": 0,
            "win_rate": 0.0,
            "total_profit": 0.0,
            "total_return_pct": 0.0,
            "avg_profit": 0.0,
            "max_profit": 0.0,
            "max_loss": 0.0,
            "avg_holding_days": 0.0,
        }

    wins = [t for t in matched if t.profit > 0]
    losses = [t for t in matched if t.profit < 0]
    flat = [t for t in matched if t.profit == 0]

    return {
        "total_trades": len(matched),
        "win_count": len(wins),
        "loss_count": len(losses),
        "flat_count": len(flat),
        "win_rate": round(len(wins) / len(matched) * 100, 2),
        "total_profit": round(sum(t.profit for t in matched), 2),
        "avg_profit_per_trade": round(sum(t.profit for t in matched) / len(matched), 2),
        "max_profit": round(max(t.profit for t in matched), 2),
        "max_loss": round(min(t.profit for t in matched), 2),
        "avg_holding_days": round(sum(t.holding_days for t in matched) / len(matched), 1),
        "total_volume": sum(t.volume * t.buy_price for t in matched),  # 总投入金额
    }


def calc_per_stock(matched: list[MatchedTrade]) -> pd.DataFrame:
    """按股票汇总统计"""
    if not matched:
        return pd.DataFrame()

    records = []
    # 按股票代码分组
    stock_groups: dict[str, list[MatchedTrade]] = {}
    for t in matched:
        stock_groups.setdefault(t.code, []).append(t)

    for code, trades in stock_groups.items():
        name = trades[0].name
        wins = [t for t in trades if t.profit > 0]
        losses = [t for t in trades if t.profit < 0]
        records.append({
            "code": code,
            "name": name,
            "total_trades": len(trades),
            "win_count": len(wins),
            "loss_count": len(losses),
            "win_rate": round(len(wins) / len(trades) * 100, 2),
            "total_profit": round(sum(t.profit for t in trades), 2),
            "avg_profit": round(sum(t.profit for t in trades) / len(trades), 2),
            "avg_holding_days": round(sum(t.holding_days for t in trades) / len(trades), 1),
            "total_volume": sum(t.volume for t in trades),
        })

    df = pd.DataFrame(records)
    # 按总盈亏降序排列
    df = df.sort_values(by="total_profit", ascending=False).reset_index(drop=True)
    return df


def calc_monthly(matched: list[MatchedTrade]) -> pd.DataFrame:
    """按月统计盈亏"""
    if not matched:
        return pd.DataFrame()

    monthly: dict[str, dict] = {}
    for t in matched:
        month_key = t.sell_date.strftime("%Y-%m")
        if month_key not in monthly:
            monthly[month_key] = {"month": month_key, "trades": 0, "profit": 0.0, "wins": 0, "losses": 0}
        monthly[month_key]["trades"] += 1
        monthly[month_key]["profit"] += t.profit
        if t.profit > 0:
            monthly[month_key]["wins"] += 1
        elif t.profit < 0:
            monthly[month_key]["losses"] += 1

    df = pd.DataFrame(monthly.values())
    df["win_rate"] = df.apply(
        lambda r: round(r["wins"] / r["trades"] * 100, 2) if r["trades"] > 0 else 0, axis=1
    )
    df["profit"] = df["profit"].round(2)
    df = df.sort_values(by="month").reset_index(drop=True)
    return df


# ============================================================
# 报告输出
# ============================================================

def print_report(
    matched: list[MatchedTrade],
    open_positions: list[OpenPosition],
    unmatched_sells: list[dict],
):
    """打印完整分析报告到终端"""
    print()
    print("=" * 65)
    print("  📊 交 易 分 析 报 告")
    print("=" * 65)
    print()

    # ---------- 整体概览 ----------
    summary = calc_summary(matched)
    print("── 整体概览 ──")
    print(f"  总交易笔数（已完成配对）：{summary['total_trades']} 笔")
    print(f"  盈利笔数：{summary['win_count']} 笔")
    print(f"  亏损笔数：{summary['loss_count']} 笔")
    if summary.get("flat_count"):
        print(f"  持平笔数：{summary['flat_count']} 笔")
    print(f"  胜率：{summary['win_rate']}%")
    print(f"  总盈亏：{summary['total_profit']:+,.2f} 元")
    print(f"  平均每笔盈亏：{summary['avg_profit_per_trade']:+,.2f} 元")
    print(f"  最大单笔盈利：{summary['max_profit']:+,.2f} 元")
    print(f"  最大单笔亏损：{summary['max_loss']:+,.2f} 元")
    print(f"  平均持仓天数：{summary['avg_holding_days']} 天")
    print()

    # ---------- 每月统计 ----------
    monthly_df = calc_monthly(matched)
    if len(monthly_df) > 0:
        print("── 每月统计 ──")
        print(monthly_df.to_string(index=False))
        print()

    # ---------- 按股票汇总 ----------
    per_stock_df = calc_per_stock(matched)
    if len(per_stock_df) > 0:
        print("── 按股票汇总 ──")
        print(per_stock_df.to_string(index=False))
        print()

    # ---------- 最近 10 笔交易明细 ----------
    if len(matched) > 0:
        print("── 最近 10 笔交易明细 ──")
        recent = sorted(matched, key=lambda t: t.sell_date, reverse=True)[:10]
        detail_rows = []
        for t in recent:
            detail_rows.append({
                "股票": f"{t.name}({t.code})",
                "买入日": t.buy_date.strftime("%m-%d"),
                "卖出日": t.sell_date.strftime("%m-%d"),
                "买入价": t.buy_price,
                "卖出价": t.sell_price,
                "股数": t.volume,
                "盈亏": f"{t.profit:+,.2f}",
                "涨幅": f"{t.profit_pct:+.2f}%",
                "持仓": f"{t.holding_days}天",
            })
        detail_df = pd.DataFrame(detail_rows)
        print(detail_df.to_string(index=False))
        print()

    # ---------- 当前持仓 ----------
    if open_positions:
        print("── 当前持仓（尚未卖出）──")
        pos_rows = []
        total_cost = 0
        for p in open_positions:
            cost = p.buy_price * p.volume
            total_cost += cost
            pos_rows.append({
                "股票": f"{p.name}({p.code})",
                "买入日": p.buy_date.strftime("%Y-%m-%d"),
                "买入价": p.buy_price,
                "股数": p.volume,
                "成本": f"{cost:,.2f}",
            })
        pos_df = pd.DataFrame(pos_rows)
        print(pos_df.to_string(index=False))
        print(f"  持仓总成本：{total_cost:,.2f} 元")
        print()

    # ---------- 异常提醒 ----------
    if unmatched_sells:
        print("── ⚠️  无法配对的卖出（无对应买入记录）──")
        for s in unmatched_sells:
            print(f"  {s['name']}({s['code']}) {s['date'].strftime('%Y-%m-%d')} "
                  f"卖出 {s['unmatched_volume']}股 @{s['price']} — 无对应买入")
        print()

    print("=" * 65)
    print("  分析完成")
    print("=" * 65)


def export_to_excel(
    matched: list[MatchedTrade],
    open_positions: list[OpenPosition],
    output_path: Path,
):
    """将分析结果导出为 Excel 文件（多个 Sheet）"""
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        # Sheet 1: 全部交易明细
        if matched:
            rows = [{
                "股票代码": t.code,
                "股票名称": t.name,
                "买入日期": t.buy_date.strftime("%Y-%m-%d"),
                "卖出日期": t.sell_date.strftime("%Y-%m-%d"),
                "买入价": t.buy_price,
                "卖出价": t.sell_price,
                "成交股数": t.volume,
                "盈亏金额": t.profit,
                "盈亏比例%": t.profit_pct,
                "持仓天数": t.holding_days,
            } for t in matched]
            pd.DataFrame(rows).to_excel(writer, sheet_name="交易明细", index=False)

        # Sheet 2: 按股票汇总
        per_stock_df = calc_per_stock(matched)
        if len(per_stock_df) > 0:
            per_stock_df.to_excel(writer, sheet_name="按股票汇总", index=False)

        # Sheet 3: 每月统计
        monthly_df = calc_monthly(matched)
        if len(monthly_df) > 0:
            monthly_df.to_excel(writer, sheet_name="每月统计", index=False)

        # Sheet 4: 当前持仓
        if open_positions:
            pos_rows = [{
                "股票代码": p.code,
                "股票名称": p.name,
                "买入日期": p.buy_date.strftime("%Y-%m-%d"),
                "买入价": p.buy_price,
                "持有股数": p.volume,
                "持仓成本": p.buy_price * p.volume,
            } for p in open_positions]
            pd.DataFrame(pos_rows).to_excel(writer, sheet_name="当前持仓", index=False)

    print(f"✅ 分析报告已导出至：{output_path}")


# ============================================================
# 便捷入口：输入 DataFrame，一步完成分析
# ============================================================

def analyze(df: pd.DataFrame, export_path: Optional[Path] = None):
    """
    对交易数据执行完整分析
    参数:
        df:          清洗后的交易记录 DataFrame
        export_path: 可选，Excel 导出路径
    """
    # 执行 FIFO 配对
    matched, open_positions, unmatched_sells = match_trades_fifo(df)

    # 打印报告
    print_report(matched, open_positions, unmatched_sells)

    # 可选导出
    if export_path:
        export_to_excel(matched, open_positions, export_path)

    return matched, open_positions, unmatched_sells


# ============================================================
# 测试入口
# ============================================================
if __name__ == "__main__":
    # 模拟数据：包含同一只股票多次买卖，测试 FIFO 配对逻辑
    test_data = {
        "date": pd.to_datetime([
            "20260301", "20260305", "20260310", "20260315",
            "20260320", "20260325", "20260302", "20260308",
        ]),
        "code": [
            "000001", "000001", "000001", "000001",
            "000001", "000001", "600519", "600519",
        ],
        "name": [
            "平安银行", "平安银行", "平安银行", "平安银行",
            "平安银行", "平安银行", "贵州茅台", "贵州茅台",
        ],
        "action": [
            "买入", "买入", "卖出", "卖出",
            "买入", "卖出", "买入", "卖出",
        ],
        "volume": [100, 200, 150, 100, 200, 150, 50, 50],
        "price": [10.0, 10.5, 11.0, 10.8, 11.5, 12.0, 1800, 1850],
    }
    df = pd.DataFrame(test_data)
    print("📋 测试数据：")
    print(df.to_string(index=False))
    analyze(df)
