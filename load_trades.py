# -*- coding: utf-8 -*-
"""
交割单加载脚本
功能：自动扫描文件夹，选取最新交割单 Excel 文件，打印列名，清洗数据
可作为模块导入（调用 load_trades_data()）或直接运行
"""

# 导入所需模块
import sys                          # 系统相关，用于设置输出编码
import os                           # 文件路径与系统操作
from pathlib import Path            # 现代化的路径处理
from dotenv import load_dotenv      # 读取 .env 环境变量
import pandas as pd                 # 读取 Excel / 处理表格数据

# 修复 Windows 中文终端 GBK 编码下打印 emoji 报错的问题
# 将 stdout 的编码统一设为 utf-8，防止 UnicodeEncodeError
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 模块加载时读取 .env（仅一次）
load_dotenv()

# 定义中文列名 → 英文列名的映射关系
COLUMN_MAP = {
    "成交日期": "date",      # 交易日期
    "证券代码": "code",      # 股票代码
    "证券名称": "name",      # 股票名称
    "操作":    "action",     # 买入 / 卖出
    "成交数量": "volume",     # 成交股数
    "成交均价": "price",      # 成交均价
}


def load_trades_data(trade_path: str | None = None) -> pd.DataFrame | None:
    """
    从交割单文件夹中自动选取最新文件并清洗数据
    参数:
        trade_path: 交割单文件夹路径，为 None 时从 .env 的 TRADE_PATH 读取
    返回:
        清洗后的 DataFrame，失败时返回 None
    """
    # ============================================================
    # 第一步：确定 TRADE_PATH
    # ============================================================
    if trade_path is None:
        trade_path = os.getenv("TRADE_PATH")

    if trade_path is None:
        print("❌ 未在 .env 文件中找到 TRADE_PATH 配置，请检查")
        return None

    trade_dir = Path(trade_path)

    if not trade_dir.exists() or not trade_dir.is_dir():
        print(f"❌ 路径不存在或不是文件夹：{trade_dir}")
        return None

    # ============================================================
    # 第二步：扫描文件夹中所有以 "交割单" 结尾的 Excel 文件
    # ============================================================
    xls_files = list(trade_dir.glob("*交割单.xls"))
    xlsx_files = list(trade_dir.glob("*交割单.xlsx"))
    all_trade_files = xls_files + xlsx_files

    # ============================================================
    # 第三步：检查是否找到文件
    # ============================================================
    if len(all_trade_files) == 0:
        print("❌ 没找到任何交割单文件，请检查路径。")
        return None

    # ============================================================
    # 第四步：按修改时间排序，选取最新修改的那个
    # ============================================================
    all_trade_files.sort(key=lambda f: f.stat().st_mtime)
    latest_file = all_trade_files[-1]

    print(f"📂 找到 {len(all_trade_files)} 个交割单文件，已自动选择最新的：")
    print(f"   → {latest_file.name}")
    print(f"   → 修改时间：{pd.Timestamp(latest_file.stat().st_mtime, unit='s')}")
    print()

    # ============================================================
    # 第五步：根据文件后缀，用合适的方式读取文件
    # ============================================================
    file_suffix = latest_file.suffix.lower()

    if file_suffix == ".xls":
        try:
            df = pd.read_excel(latest_file, engine="xlrd")
            print("   → 读取方式：真实 Excel (.xls)")
        except Exception:
            try:
                df = pd.read_csv(latest_file, sep="\t", encoding="gbk")
                print("   → 读取方式：TSV 文本文件（同花顺格式，GBK 编码）")
            except Exception:
                print(f"❌ 无法读取该文件：{latest_file.name}")
                return None
    elif file_suffix == ".xlsx":
        df = pd.read_excel(latest_file, engine="openpyxl")
        print("   → 读取方式：真实 Excel (.xlsx)")
    else:
        print(f"❌ 不支持的文件格式：{file_suffix}")
        return None

    # ============================================================
    # 第六步：打印原始列名一览
    # ============================================================
    print("=" * 50)
    print("📋 原始列名如下：")
    print("=" * 50)
    for i, col_name in enumerate(df.columns, start=1):
        print(f"  [{i}] {col_name}")
    print()

    # ============================================================
    # 第七步：列名清洗与映射 —— 只保留 6 列，重命名为英文
    # ============================================================
    # 只提取映射表中指定的列，其余列丢弃
    available_cols = [c for c in COLUMN_MAP.keys() if c in df.columns]
    missing_cols = [c for c in COLUMN_MAP.keys() if c not in df.columns]
    if missing_cols:
        print(f"⚠️  缺少列：{missing_cols}")
    df = df[available_cols].copy()

    # 把中文列名重命名为英文列名
    df.rename(columns=COLUMN_MAP, inplace=True)

    # 修复股票代码：去除 ".0" 后缀
    df["code"] = df["code"].astype(str).str.replace(".0", "", regex=False)

    print(f"✅ 列名清洗完成，保留 {len(df.columns)} 列：{list(df.columns)}")
    print()

    # ============================================================
    # 第八步：数据过滤与格式转换
    # ============================================================
    before_filter = len(df)
    df = df[df["action"].isin(["买入", "卖出"])].copy()
    after_filter = len(df)
    print(f"✅ 操作类型过滤完成：{before_filter} 行 → {after_filter} 行（去掉 {before_filter - after_filter} 行非交易记录）")

    before_type = str(df["date"].dtype)
    df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d", errors="coerce")
    print(f"✅ 日期格式转换完成：{before_type} → datetime64")
    print()

    return df


# ============================================================
# 直接运行时的入口
# ============================================================
if __name__ == "__main__":
    df = load_trades_data()

    if df is not None:
        # 打印清洗后的前 5 行数据
        print("=" * 50)
        print("📋 清洗后前 5 行预览：")
        print("=" * 50)
        print(df.head(5).to_string(index=False))
        print()
        print(f"✅ 清洗完成，共 {len(df)} 行有效交易记录，{len(df.columns)} 列")
