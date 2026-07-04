# src/data_integration.py
import pandas as pd
from pathlib import Path
from src.config import settings

# 同花顺列名映射：中文 → 英文
COLUMN_MAP = {
    "发生日期": "date",
    "证券代码": "code",
    "证券名称": "name",
    "操作":    "action",
    "成交数量": "volume",
    "成交均价": "price",
}

# 自动列名识别映射：多种中文列名 → 统一英文列名
AUTO_COLUMN_MAP = {
    "date":   ["发生日期", "成交日期", "交易日期", "日期"],
    "code":   ["证券代码", "股票代码", "代码"],
    "name":   ["证券名称", "股票名称", "名称"],
    "action": ["操作", "买卖方向", "方向", "类型"],
    "volume": ["成交数量", "数量", "股数"],
    "price":  ["成交均价", "成交价格", "价格", "均价"],
    "amount": ["成交金额", "发生金额", "金额"],
}


def _read_trade_file(filepath: Path) -> pd.DataFrame | None:
    """
    尝试多种方式读取一个交易文件，返回清洗后的 DataFrame。
    读取失败返回 None。
    """
    suffix = filepath.suffix.lower()

    # ── 策略 1：按扩展名选择 engine ──
    if suffix in (".xls", ".xlsx"):
        engine = "xlrd" if suffix == ".xls" else "openpyxl"
        try:
            df = pd.read_excel(filepath, engine=engine)
            print(f"   ✅ 用 engine='{engine}' 读取成功")
            return df
        except Exception as e:
            print(f"   ⚠️  engine='{engine}' 失败: {e}")

    # ── 策略 2：自动检测 engine ──
    try:
        df = pd.read_excel(filepath, engine=None)
        print(f"   ✅ 用 engine=None（自动检测）读取成功")
        return df
    except Exception as e:
        print(f"   ⚠️  engine=None 失败: {e}")

    # ── 策略 3：当作 CSV 尝试（GBK 编码，同花顺常用） ──
    try:
        df = pd.read_csv(filepath, encoding="gbk")
        print(f"   ✅ 用 read_csv(encoding='gbk') 读取成功")
        return df
    except Exception as e:
        print(f"   ⚠️  read_csv(gbk) 失败: {e}")

    # ── 策略 4：当作 CSV 尝试（UTF-8 编码） ──
    try:
        df = pd.read_csv(filepath, encoding="utf-8")
        print(f"   ✅ 用 read_csv(encoding='utf-8') 读取成功")
        return df
    except Exception as e:
        print(f"   ⚠️  read_csv(utf-8) 失败: {e}")

    # ── 全部失败 ──
    return None


def _clean_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """对读取后的 DataFrame 执行列名自动识别、映射与清洗"""
    original_cols = list(df.columns)
    renames = {}  # 记录重命名历史

    # ── 1. 自动列名识别与重命名 ──
    for eng_name, cn_candidates in AUTO_COLUMN_MAP.items():
        # 跳过已经存在的英文列
        if eng_name in df.columns:
            continue
        # 查找第一个匹配的中文列名
        for cn in cn_candidates:
            if cn in df.columns:
                df = df.rename(columns={cn: eng_name})
                renames[cn] = eng_name
                break

    # ── 2. 兼容旧版 COLUMN_MAP（仅对 AUTO_COLUMN_MAP 未覆盖的中文列名生效） ──
    # 找出仍在列名中的原始中文列（未被 AUTO_COLUMN_MAP 消费的）
    legacy_renames = {}
    for cn, eng in COLUMN_MAP.items():
        if cn in df.columns and cn not in renames:
            legacy_renames[cn] = eng
    if legacy_renames:
        df = df.rename(columns=legacy_renames)
        renames.update(legacy_renames)

    # ── 3. 打印列名变化 ──
    if renames:
        for old, new in renames.items():
            print(f"   🔄 列名映射: '{old}' → '{new}'")
    else:
        print(f"   ℹ️  未检测到需要映射的列名，原始列: {original_cols}")

    # ── 4. 兜底：如果仍然没有 date 列，尝试模糊匹配 ──
    if "date" not in df.columns:
        extra_date_candidates = ["日期", "时间", "date", "Date", "DATE"]
        for cand in extra_date_candidates:
            if cand in df.columns:
                df = df.rename(columns={cand: "date"})
                print(f"   🔄 兜底日期映射: '{cand}' → 'date'")
                break
        # 尝试包含 "日" 字的列名
        if "date" not in df.columns:
            for col in df.columns:
                if "日" in str(col):
                    df = df.rename(columns={col: "date"})
                    print(f"   🔄 模糊日期映射: '{col}' → 'date'")
                    break

    # ── 5. 校验必要列 ──
    required_cols = {"date", "code", "name", "action", "volume", "price"}
    missing = required_cols - set(df.columns)
    if missing:
        print(f"   ⚠️  仍然缺少必要列: {missing}")
        if "date" in missing or len(missing) >= 3:
            return pd.DataFrame()

    if "date" not in df.columns:
        return pd.DataFrame()

    # ── 6. 日期处理 ──
    date_series = df["date"].astype(str)

    # 检测是否包含时间信息（如 "2026-06-21 09:30:00"），提取日期部分
    sample = date_series.dropna().iloc[0] if len(date_series.dropna()) > 0 else ""
    has_time = (" " in str(sample)) or ("T" in str(sample))

    df["date"] = pd.to_datetime(date_series, errors="coerce")
    if has_time:
        df["date"] = df["date"].dt.date
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        print(f"   🕐 检测到时间信息，已提取日期部分")

    # ── 7. 修复股票代码：去除 ".0" 后缀 ──
    if "code" in df.columns:
        df["code"] = df["code"].astype(str).str.replace(".0", "", regex=False)

    # ── 8. 只保留「买入」「卖出」记录 ──
    if "action" in df.columns:
        before_filter = len(df)
        df = df[df["action"].isin(["买入", "卖出"])].copy()
        after_filter = len(df)
        if before_filter > after_filter:
            print(f"   ✅ 过滤非交易记录: {before_filter} → {after_filter} 行")

    print(f"   ✅ 列名映射完成，保留 {len(df.columns)} 列: {list(df.columns)}")
    return df


def integrate():
    """整合所有交易数据，生成 full_trades.csv"""
    trade_dir = settings.TRADE_PATH
    trade_files = list(trade_dir.glob("*.xls*"))

    if not trade_files:
        print(f"❌ 未找到任何交易文件，请检查路径: {trade_dir}")
        return

    # 按修改时间降序排列（最新的在前）
    trade_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    print(f"📂 找到 {len(trade_files)} 个文件，开始处理...")
    print()

    all_dfs = []
    success_count = 0

    for i, filepath in enumerate(trade_files, 1):
        print(f"[{i}/{len(trade_files)}] 📂 {filepath.name}")

        df = _read_trade_file(filepath)
        if df is None:
            print(f"   ❌ 所有读取方式均失败，跳过此文件\n")
            continue

        print(f"   📊 原始: {len(df)} 行, {len(df.columns)} 列")

        df = _clean_dataframe(df)
        if df.empty:
            print(f"   ❌ 清洗后无有效数据，跳过此文件\n")
            continue

        all_dfs.append(df)
        success_count += 1
        print()

    if not all_dfs:
        print("❌ 没有成功读取任何文件，流程终止")
        return

    # 合并所有文件并去重
    combined = pd.concat(all_dfs, ignore_index=True)
    before_dedup = len(combined)
    combined = combined.drop_duplicates(
        subset=["date", "code", "action", "volume", "price"],
        keep="first",
    )
    after_dedup = len(combined)
    if before_dedup > after_dedup:
        print(f"✅ 合并去重: {before_dedup} → {after_dedup} 行")

    # 按日期排序
    combined = combined.sort_values(by="date").reset_index(drop=True)
    print(f"📊 最终: {success_count} 个文件, {len(combined)} 行有效交易记录")
    print()

    # 保存到 processed 目录
    settings.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    output_path = settings.PROCESSED_DIR / "full_trades.csv"
    combined.to_csv(output_path, index=False, encoding="utf-8-sig")

    print(f"✅ 已保存到: {output_path}")
    print("📊 前5行预览:")
    print(combined.head(5).to_string(index=False))


if __name__ == "__main__":
    integrate()
