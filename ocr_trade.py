# -*- coding: utf-8 -*-
"""
同花顺截图识别模块
功能：调用多模态大模型 API（通义千问 VL / GPT-4o），从截图中识别交易表格
输出与 load_trades.py 一致的 pandas.DataFrame 格式
"""

# 导入所需模块
import sys
import os
import json
import base64
from pathlib import Path
from dotenv import load_dotenv
import pandas as pd
from openai import OpenAI

# 修复 Windows 中文终端 GBK 编码下打印 emoji 报错的问题
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ============================================================
# 加载 .env 配置
# ============================================================
load_dotenv()

API_KEY = os.getenv("OCR_API_KEY")                    # API 密钥
BASE_URL = os.getenv("OCR_BASE_URL")                  # API 地址（兼容 OpenAI SDK）
MODEL = os.getenv("OCR_MODEL")                        # 模型名称
SCREENSHOT_PATH = os.getenv("SCREENSHOT_PATH")        # 截图文件路径


def encode_image(image_path: Path) -> str:
    """
    将图片文件读取并编码为 base64 字符串
    参数:
        image_path: 图片文件路径
    返回:
        base64 编码后的字符串
    """
    # 以二进制模式读取图片文件
    with open(image_path, "rb") as f:
        image_bytes = f.read()

    # 将二进制数据编码为 base64 字符串
    base64_str = base64.b64encode(image_bytes).decode("utf-8")
    return base64_str


def build_prompt() -> str:
    """
    构建发送给多模态模型的提示词
    返回:
        提示词文本
    """
    # 提示词：要求模型从截图中识别交易表格，返回纯 JSON 数组
    prompt = """请识别这张同花顺交易截图中的所有交易记录。

要求：
1. 提取每一笔交易的以下字段：
   - date: 成交日期（格式 YYYYMMDD，如 20260323）
   - code: 证券代码（纯数字字符串，如 "601212"）
   - name: 证券名称（如 "中国中铁"）
   - action: 操作类型（"买入" 或 "卖出"）
   - price: 成交均价（数字）
   - volume: 成交数量（数字）

2. 返回格式：严格的 JSON 数组，每个元素是一个对象，不要包含任何其他文字

返回示例：
[{"date": "20260323", "code": "601212", "name": "中国中铁", "action": "买入", "price": 5.89, "volume": 1000}]"""
    return prompt


def call_ocr_api(image_base64: str) -> str:
    """
    调用多模态 API 识别截图中的交易数据
    参数:
        image_base64: base64 编码的图片字符串
    返回:
        API 返回的原始文本内容
    抛出:
        ValueError: API Key 无效或认证失败
        ConnectionError: 网络超时或连接失败
        RuntimeError: 其他 API 调用错误
    """
    # 检查 API Key 是否已配置
    if not API_KEY:
        raise ValueError(
            "❌ OCR_API_KEY 未配置，请在 .env 文件中设置 OCR_API_KEY=你的API密钥"
        )

    # 检查 BASE_URL 是否已配置
    if not BASE_URL:
        raise ValueError(
            "❌ OCR_BASE_URL 未配置，请在 .env 文件中设置 OCR_BASE_URL"
        )

    # 检查 MODEL 是否已配置
    if not MODEL:
        raise ValueError(
            "❌ OCR_MODEL 未配置，请在 .env 文件中设置 OCR_MODEL"
        )

    # 初始化 OpenAI 兼容客户端（base_url 指向通义千问或其他兼容 API）
    try:
        client = OpenAI(
            api_key=API_KEY,
            base_url=BASE_URL,
            timeout=60.0,  # 设置 60 秒超时
        )
    except Exception as e:
        raise RuntimeError(f"❌ 初始化 API 客户端失败：{e}")

    # 构建消息：包含图片和文本提示词
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{image_base64}",
                        "detail": "high",  # 高精度识别，确保表格内容清晰可辨
                    },
                },
                {
                    "type": "text",
                    "text": build_prompt(),
                },
            ],
        }
    ]

    # 调用 API
    try:
        print("🔄 正在调用多模态 API 识别截图...")
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            temperature=0.0,  # 温度设为 0，确保输出稳定一致
            max_tokens=4096,
        )
    except Exception as e:
        error_str = str(e).lower()

        # 判断是否为认证错误（API Key 无效）
        if any(kw in error_str for kw in ["auth", "unauthorized", "invalid api key", "401"]):
            raise ValueError(
                f"❌ API Key 无效或认证失败，请检查 .env 中的 OCR_API_KEY\n"
                f"   原始错误：{e}"
            )

        # 判断是否为网络超时
        if any(kw in error_str for kw in ["timeout", "timed out", "connection"]):
            raise ConnectionError(
                f"❌ 网络超时或连接失败，请检查网络和 OCR_BASE_URL 配置\n"
                f"   原始错误：{e}"
            )

        # 其他未知错误
        raise RuntimeError(f"❌ API 调用失败：{e}")

    # 提取返回的文本内容
    content = response.choices[0].message.content

    if content is None:
        raise RuntimeError("❌ API 返回内容为空")

    return content.strip()


def parse_ocr_result(raw_text: str) -> pd.DataFrame:
    """
    将 API 返回的 JSON 字符串解析为 pandas DataFrame
    参数:
        raw_text: API 返回的原始文本
    返回:
        包含交易记录的 DataFrame，字段与 load_trades.py 输出一致
    抛出:
        ValueError: 返回内容不是合法的 JSON 格式
    """
    # 尝试直接解析 JSON
    try:
        records = json.loads(raw_text)
    except json.JSONDecodeError:
        # 直接解析失败，尝试从文本中提取 JSON 数组部分
        # 有些模型可能会在 JSON 前后附加说明文字
        print("⚠️  直接解析 JSON 失败，尝试从返回内容中提取 JSON 数组...")

        # 查找第一个 '[' 和最后一个 ']' 之间的内容
        start = raw_text.find("[")
        end = raw_text.rfind("]")

        if start != -1 and end != -1 and start < end:
            json_str = raw_text[start:end + 1]
            try:
                records = json.loads(json_str)
            except json.JSONDecodeError as e:
                # 仍然失败，记录原始内容并报错
                print(f"❌ 提取后仍无法解析 JSON，原始返回内容如下：")
                print("-" * 60)
                print(raw_text[:2000])  # 只打印前 2000 个字符
                print("-" * 60)
                raise ValueError(
                    f"❌ API 返回了非 JSON 格式的内容，解析失败：{e}\n"
                    f"   原始内容已打印在上方，请检查提示词或模型配置"
                )
        else:
            # 找不到 JSON 数组，记录原始内容并报错
            print(f"❌ 返回内容中未找到 JSON 数组，原始内容如下：")
            print("-" * 60)
            print(raw_text[:2000])
            print("-" * 60)
            raise ValueError(
                "❌ API 返回了非 JSON 格式的内容，未找到 JSON 数组\n"
                "   原始内容已打印在上方，请检查提示词或模型配置"
            )

    # 校验返回的数据结构
    if not isinstance(records, list):
        raise ValueError(f"❌ 期望返回 JSON 数组，实际返回类型为：{type(records)}")

    if len(records) == 0:
        print("⚠️  API 返回了空数组，截图中可能没有识别到交易记录")
        return pd.DataFrame(columns=["date", "code", "name", "action", "price", "volume"])

    # 转为 DataFrame
    df = pd.DataFrame(records)

    # 确保包含所有需要的列
    required_columns = ["date", "code", "name", "action", "price", "volume"]
    for col in required_columns:
        if col not in df.columns:
            print(f"⚠️  返回数据缺少列 '{col}'，以空值填充")
            df[col] = None

    # 只保留需要的 6 列（按指定顺序）
    df = df[required_columns].copy()

    # 数据类型转换（与 load_trades.py 保持一致）
    df["code"] = df["code"].astype(str).str.replace(".0", "", regex=False)
    df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d", errors="coerce")

    # 只保留 action 为 "买入" 或 "卖出" 的行
    before_filter = len(df)
    df = df[df["action"].isin(["买入", "卖出"])].copy()
    after_filter = len(df)
    if before_filter > after_filter:
        print(f"✅ 操作类型过滤：{before_filter} 行 → {after_filter} 行（去掉 {before_filter - after_filter} 行非交易记录）")

    return df


def ocr_trades_data(screenshot_path: str | None = None) -> pd.DataFrame | None:
    """
    从截图识别交易数据，返回清洗后的 DataFrame
    参数:
        screenshot_path: 截图文件路径，为 None 时从 .env 的 SCREENSHOT_PATH 读取
    返回:
        清洗后的 DataFrame，失败时返回 None
    """
    # 第一步：获取截图路径
    if screenshot_path is None:
        screenshot_path = SCREENSHOT_PATH

    if screenshot_path is None:
        print("❌ 未在 .env 文件中找到 SCREENSHOT_PATH 配置")
        return None

    img_path = Path(screenshot_path)

    if not img_path.exists():
        print(f"❌ 截图文件不存在：{img_path}")
        return None

    print(f"📂 截图路径：{img_path}")
    print(f"   → 文件大小：{img_path.stat().st_size / 1024:.1f} KB")
    print()

    # 第二步：编码图片为 base64
    try:
        print("🔄 正在编码图片...")
        image_base64 = encode_image(img_path)
        print(f"✅ 图片编码完成，base64 长度：{len(image_base64)} 字符")
        print()
    except Exception as e:
        print(f"❌ 图片编码失败：{e}")
        return None

    # 第三步：调用多模态 API
    try:
        raw_result = call_ocr_api(image_base64)
    except ValueError as e:
        print(e)
        return None
    except ConnectionError as e:
        print(e)
        return None
    except RuntimeError as e:
        print(e)
        return None

    print(f"✅ API 调用成功，返回内容长度：{len(raw_result)} 字符")
    print()

    # 第四步：解析结果为 DataFrame
    try:
        df = parse_ocr_result(raw_result)
    except ValueError as e:
        print(e)
        return None

    return df


def main():
    """
    主流程：编码图片 → 调用 API → 解析结果 → 打印预览
    """
    print("=" * 60)
    print("📸 同花顺截图识别模块")
    print("=" * 60)
    print()

    df = ocr_trades_data()

    if df is not None and len(df) > 0:
        print("=" * 60)
        print("📋 识别结果预览（前 5 行）：")
        print("=" * 60)
        print(df.head(5).to_string(index=False))
        print()
        print(f"✅ 识别完成，共 {len(df)} 条交易记录，{len(df.columns)} 列：{list(df.columns)}")


# ============================================================
# 测试入口
# ============================================================
if __name__ == "__main__":
    main()
