# -*- coding: utf-8 -*-
"""
工具注册表
功能：集中管理所有 LangChain Tool，方便统一导入和注册到 Agent
"""

import sys

# 修复 Windows 中文终端 GBK 编码下打印 emoji 报错的问题
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from src.tools.position import calc_position_tool


# ── 工具清单 ───────────────────────────────────────────────────

# 所有已注册的 LangChain Tool 列表
ALL_TOOLS: list = [
    calc_position_tool,
]

# 兼容别名：TOOLS 与 ALL_TOOLS 指向同一列表
TOOLS = ALL_TOOLS


# ── 便捷函数 ───────────────────────────────────────────────────

def get_all_tools() -> list:
    """返回全部已注册的 LangChain Tool"""
    return ALL_TOOLS


def get_tool_by_name(name: str):
    """按名称查找工具，未找到返回 None"""
    for t in ALL_TOOLS:
        if t.name == name:
            return t
    return None


def get_tool_names() -> list[str]:
    """返回全部已注册的工具名称"""
    return [t.name for t in ALL_TOOLS]


def register_tool(tool_obj):
    """
    动态注册一个额外的 LangChain Tool
    参数:
        tool_obj: 一个用 @tool 装饰过的函数
    """
    if tool_obj not in ALL_TOOLS:
        ALL_TOOLS.append(tool_obj)
    return tool_obj


# ── 测试入口 ───────────────────────────────────────────────────

if __name__ == "__main__":
    print("📋 已注册的工具：")
    for t in ALL_TOOLS:
        print(f"  🔧 {t.name}: {t.description[:80]}...")
    print(f"\n✅ 共 {len(ALL_TOOLS)} 个工具已就绪")
