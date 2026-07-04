# -*- coding: utf-8 -*-
"""
持仓统计工具
功能：基于交易记录，按股票代码聚合计算当前持仓、成本、市值、盈亏及仓位占比
数据源：data/processed/full_trades.csv
"""

import sys
from pathlib import Path

import polars as pl
from langchain_core.tools import tool


# ── 核心计算函数 ──────────────────────────────────────────────

def calc_position(trades_df: pl.DataFrame) -> list[dict]:
    """
    基于交易流水计算当前持仓。
    参数:
        trades_df: Polars DataFrame，包含字段 code / name / action / price / volume
    返回:
        list[dict]，每项包含 code, name, shares, avg_cost, current_price,
        profit_pct, market_value, weight
    """
    # ── 0. 输入校验与清洗 ──
    required = {"code", "name", "action", "price", "volume"}
    missing = required - set(trades_df.columns)
    if missing:
        raise ValueError(f"DataFrame 缺少必要字段: {missing}")

    # 去除前后空格，统一字符串匹配
    trades_df = trades_df.with_columns([
        pl.col("action").str.strip_chars().alias("action"),
        pl.col("code").cast(pl.Utf8).str.strip_chars().alias("code"),
        pl.col("name").cast(pl.Utf8).str.strip_chars().alias("name"),
    ])

    # 过滤脏数据
    before = len(trades_df)
    trades_df = trades_df.filter(
        pl.col("price").is_not_null() & pl.col("price").is_finite() & (pl.col("price") > 0) &
        pl.col("volume").is_not_null() & pl.col("volume").is_finite() & (pl.col("volume") > 0) &
        pl.col("action").is_in(["买入", "卖出"])
    )
    if len(trades_df) < before:
        print(f"⚠️ 已过滤 {before - len(trades_df)} 行脏数据")

    if trades_df.is_empty():
        return []

    # ── 1. 买入分组：总买入量 + 加权均价 ──
    buys = (
        trades_df
        .filter(pl.col("action") == "买入")
        .group_by("code")
        .agg([
            pl.col("name").first(),
            pl.col("volume").sum().alias("buy_vol"),
            (pl.col("price") * pl.col("volume")).sum().alias("buy_amount"),
        ])
        .filter(pl.col("buy_vol") > 0)
        .with_columns([
            (pl.col("buy_amount") / pl.col("buy_vol")).alias("avg_cost"),
        ])
    )

    # ── 2. 卖出分组：总卖出量 ──
    sells = (
        trades_df
        .filter(pl.col("action") == "卖出")
        .group_by("code")
        .agg([
            pl.col("volume").sum().alias("sell_vol"),
        ])
    )

    # ── 3. 合并，计算持仓 ──
    positions = (
        buys
        .join(sells, on="code", how="left")
        .with_columns([pl.col("sell_vol").fill_null(0)])
        .with_columns([
            (pl.col("buy_vol") - pl.col("sell_vol")).alias("shares"),
        ])
        .filter(pl.col("shares") > 0)
    )

    if positions.is_empty():
        return []

    # ── 4. 计算衍生字段 ──
    positions = positions.with_columns([
        (pl.col("avg_cost") * 1.05).alias("current_price"),
    ])
    positions = positions.with_columns([
        (pl.col("shares") * pl.col("current_price")).alias("market_value"),
    ])
    total_mv = positions["market_value"].sum()

    positions = positions.with_columns([
        pl.when(pl.col("avg_cost").is_not_null() & (pl.col("avg_cost") > 0))
          .then((pl.col("current_price") - pl.col("avg_cost")) / pl.col("avg_cost"))
          .otherwise(0.0)
          .alias("profit_pct"),
        pl.when(total_mv > 0)
          .then(pl.col("market_value") / total_mv)
          .otherwise(0.0)
          .alias("weight"),
    ])

    # ── 5. 排序并输出 ──
    positions = positions.sort("market_value", descending=True)

    result = (
        positions
        .select([
            "code",
            "name",
            pl.col("shares").cast(pl.Int64),
            pl.col("avg_cost").round(4),
            pl.col("current_price").round(4),
            (pl.col("profit_pct") * 100).round(2).alias("profit_pct"),
            pl.col("market_value").round(2),
            (pl.col("weight") * 100).round(2).alias("weight"),
        ])
        .to_dicts()
    )

    return result


# ── LangChain Tool 封装 ───────────────────────────────────────

@tool
def calc_position_tool(csv_path: str = "data/processed/full_trades.csv") -> str:
    """统计当前持仓情况"""
    csv_full_path = Path(csv_path)
    if not csv_full_path.exists():
        return f"❌ 数据文件不存在：{csv_full_path.resolve()}"

    try:
        trades_df = pl.read_csv(csv_full_path)
    except Exception as e:
        return f"❌ 读取 CSV 文件失败：{e}"

    # 校验必要字段
    required_cols = {"code", "name", "action", "price", "volume"}
    missing = required_cols - set(trades_df.columns)
    if missing:
        return f"❌ CSV 缺少必要字段：{missing}"

    positions = calc_position(trades_df)

    if not positions:
        return "📊 当前无持仓记录（所有买入均已卖出）。"

    # 格式化输出
    lines = ["📊 当前持仓统计", "=" * 70]
    header = (f"{'代码':<10} {'名称':<10} {'持仓量':>8} {'成本价':>10} "
              f"{'当前价':>10} {'盈亏%':>8} {'市值':>14} {'占比':>8}")
    lines.append(header)
    lines.append("-" * 70)

    total_mv = 0.0
    for p in positions:
        lines.append(
            f"{p['code']:<10} {p['name']:<10} {p['shares']:>8} "
            f"{p['avg_cost']:>10.4f} {p['current_price']:>10.4f} "
            f"{p['profit_pct']:>+7.2f}% {p['market_value']:>14.2f} {p['weight']:>7.2f}%"
        )
        total_mv += p["market_value"]

    lines.append("-" * 70)
    lines.append(f"{'合计':<20} {'':>8} {'':>10} {'':>10} {'':>8} {total_mv:>14.2f}")
    lines.append("=" * 70)

    return "\n".join(lines)


# ── 测试入口 ───────────────────────────────────────────────────

if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    csv_path = "data/processed/full_trades.csv"
    if Path(csv_path).exists():
        df = pl.read_csv(csv_path)
        result = calc_position(df)
        print(f"持仓数量: {len(result)}")
        for item in result:
            print(item)
    else:
        print(f"⚠️ 数据文件不存在: {csv_path}，使用模拟数据测试")
        test_data = pl.DataFrame({
            "code": ["000001", "000001", "000001", "600519", "600519", "000858"],
            "name": ["平安银行", "平安银行", "平安银行", "贵州茅台", "贵州茅台", "五粮液"],
            "action": ["买入", "买入", "卖出", "买入", "卖出", "买入"],
            "price": [10.0, 10.5, 11.0, 1800.0, 1850.0, 150.0],
            "volume": [100, 200, 150, 50, 50, 200],
        })

        result = calc_position(test_data)
        print(f"持仓数量: {len(result)}")
        for item in result:
            print(item)

        print()
        print("📋 测试 calc_position_tool（使用临时 CSV）：")
        import tempfile
        import os
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8") as f:
            test_data.write_csv(f.name)
            tmp_path = f.name
        try:
            output = calc_position_tool.invoke({"csv_path": tmp_path})
            print(output)
        finally:
            os.unlink(tmp_path)
