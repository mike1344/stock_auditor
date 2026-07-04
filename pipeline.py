# -*- coding: utf-8 -*-
"""
统一管线（Pipeline）
功能：串联 Excel 导入 + 截图 OCR → 合并去重 → 交易分析 → AI 诊断 → 报告导出
一次运行，完成从数据加载到智能分析的全流程（共 5 阶段）
"""

import sys
from pathlib import Path
import pandas as pd

# 修复 Windows 中文终端 GBK 编码下打印 emoji 报错的问题
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 导入项目内模块
from load_trades import load_trades_data
from ocr_trade import ocr_trades_data
from analyze_trades import analyze
from ai_advisor import ai_advisory


def merge_dataframes(df_excel: pd.DataFrame, df_ocr: pd.DataFrame | None) -> pd.DataFrame:
    """
    合并两个数据源并去重
    去重逻辑：按 (date, code, action, volume, price) 五列精确匹配
    参数:
        df_excel: 从交割单 Excel 加载的数据
        df_ocr:   从截图 OCR 识别的数据（可为 None）
    返回:
        合并去重后的 DataFrame
    """
    if df_ocr is None or len(df_ocr) == 0:
        print("⚠️  未获取到 OCR 数据，仅使用 Excel 数据源")
        return df_excel

    print(f"📊 Excel 数据：{len(df_excel)} 行")
    print(f"📊 OCR  数据：{len(df_ocr)} 行")

    # 确保 OCR 数据的列类型与 Excel 一致
    for col in ["date", "code", "name", "action", "volume", "price"]:
        if col in df_ocr.columns:
            if col == "date":
                df_ocr[col] = pd.to_datetime(df_ocr[col])
            elif col == "volume":
                df_ocr[col] = df_ocr[col].astype(int)
            elif col == "price":
                df_ocr[col] = df_ocr[col].astype(float)
            elif col == "code":
                df_ocr[col] = df_ocr[col].astype(str)
            elif col == "name":
                df_ocr[col] = df_ocr[col].astype(str)
            elif col == "action":
                df_ocr[col] = df_ocr[col].astype(str)

    # 合并两个 DataFrame
    combined = pd.concat([df_excel, df_ocr], ignore_index=True)

    # 按 (date, code, action, volume, price) 去重
    dup_keys = ["date", "code", "action", "volume", "price"]
    before_dedup = len(combined)
    combined = combined.drop_duplicates(subset=dup_keys, keep="first")
    after_dedup = len(combined)

    if before_dedup > after_dedup:
        print(f"✅ 去重完成：{before_dedup} 行 → {after_dedup} 行（去除 {before_dedup - after_dedup} 条重复记录）")
    else:
        print("✅ 未发现重复记录，两个数据源互补")

    # 按日期排序
    combined = combined.sort_values(by="date").reset_index(drop=True)
    print(f"📊 合并后总计：{len(combined)} 行有效交易记录")
    print()

    return combined


def run_pipeline(
    export_excel: bool = True,
    output_dir: str | None = None,
):
    """
    执行完整管线：加载 → 合并 → 分析 → 导出
    参数:
        export_excel: 是否导出 Excel 报告
        output_dir:   报告输出目录，默认为项目根目录
    """
    print("=" * 65)
    print("  🚀 统 一 交 易 分 析 管 线")
    print("=" * 65)
    print()

    # 确定输出目录
    if output_dir is None:
        output_dir = Path(__file__).parent
    else:
        output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── 阶段 1：加载 Excel 交割单 ──
    print("── 阶段 1/5：加载 Excel 交割单 ──")
    print()
    df_excel = load_trades_data()
    if df_excel is None or len(df_excel) == 0:
        print("❌ Excel 交割单加载失败，管线终止")
        print("   请检查 .env 中的 TRADE_PATH 配置")
        return
    print(f"✅ 阶段 1 完成：Excel 数据 {len(df_excel)} 行")
    print()

    # ── 阶段 2：加载截图 OCR ──
    print("── 阶段 2/5：加载截图 OCR ──")
    print()
    df_ocr = ocr_trades_data()
    if df_ocr is None:
        print("⚠️  截图 OCR 未能获取数据（可能是截图不存在或 API 未配置），跳过此阶段")
        print()
    else:
        print(f"✅ 阶段 2 完成：OCR 数据 {len(df_ocr)} 行")
        print()

    # ── 阶段 3：合并去重 ──
    print("── 阶段 3/5：合并去重 ──")
    print()
    df_all = merge_dataframes(df_excel, df_ocr)
    print(f"✅ 阶段 3 完成：合并后 {len(df_all)} 行")
    print()

    # ── 阶段 4：分析 & 导出 ──
    print("── 阶段 4/5：交易分析 ──")
    export_path = output_dir / "交易分析报告.xlsx" if export_excel else None
    matched, open_positions, unmatched_sells = analyze(df_all, export_path=export_path)

    # ── 阶段 5：AI 智能诊断 ──
    print("── 阶段 5/5：AI 智能诊断 ──")
    print()
    advisory_result = ai_advisory(matched, open_positions, unmatched_sells, df_all)

    # 汇总结论
    from analyze_trades import calc_summary
    summary = calc_summary(matched)
    print()
    print("=" * 65)
    print("  🏁 管 线 执 行 完 毕")
    print("=" * 65)
    print(f"  数据来源：Excel 交割单{' + 截图 OCR' if df_ocr is not None else ''}")
    print(f"  总交易数：{summary['total_trades']} 笔")
    print(f"  总盈亏：{summary['total_profit']:+,.2f} 元")
    print(f"  胜率：{summary['win_rate']}%")
    if advisory_result.get("anomalies"):
        critical = sum(1 for a in advisory_result["anomalies"] if a["severity"] == "critical")
        warning = sum(1 for a in advisory_result["anomalies"] if a["severity"] == "warning")
        print(f"  异常检测：🔴 {critical} 严重 / 🟡 {warning} 警告")
    if advisory_result.get("attribution", {}).get("conclusion"):
        print(f"  策略归因：{advisory_result['attribution']['conclusion'][:60]}...")
    if export_path:
        print(f"  报告文件：{export_path}")
    print()

    return df_all, matched, open_positions, unmatched_sells, advisory_result


# ============================================================
# 测试入口：用现有交割单数据跑一遍完整管线
# ============================================================
if __name__ == "__main__":
    run_pipeline(export_excel=True)
