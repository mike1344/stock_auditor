# 📊 Stock Auditor — 同花顺交易审计系统

对同花顺交割单进行自动导入、截图 OCR 识别、FIFO 配对、盈亏分析、AI 诊断和报告导出。

## 项目结构

```
stock_auditor/
├── .env                        # 配置文件（不入 git，放真实 Key）
├── .env.example                # 配置模板（入 git，不含真实 Key）
├── .gitignore                  # Git 忽略规则
├── requirements.txt            # Python 依赖
├── load_trades.py              # 模块 ①：Excel 交割单加载
├── ocr_trade.py                # 模块 ②：截图多模态 OCR 识别
├── analyze_trades.py           # 模块 ③：FIFO 配对 & 交易分析
├── ai_advisor.py               # 模块 ④：AI 交易诊断（异常检测 + 策略归因 + LLM 诊断）
├── pipeline.py                 # 模块 ⑤：统一管线（入口，5 阶段）
├── 交易分析报告.xlsx            # 导出的 Excel 报告
└── src/
    ├── __init__.py
    └── tools/
        ├── __init__.py
        ├── position.py          # 模块 ⑥：持仓统计工具（Polars + LangChain Tool）··
        └── registry.py          # 工具注册表（集中管理所有 LangChain Tool）
```

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 从模板创建配置文件
cp .env.example .env
# 然后编辑 .env，填入你的真实路径和 API Key

# 3. 运行完整管线
python pipeline.py
```

> ⚠️ **安全提示**：`.env` 已被 `.gitignore` 排除，不会提交到 Git。
> `.env.example` 是模板文件，可以安全分享。

## 已实现功能

### ① 交割单加载 (`load_trades.py`)

- 自动扫描文件夹，按修改时间选取最新交割单
- 兼容 `.xls`（真实 Excel / 同花顺 TSV 伪装）和 `.xlsx` 两种格式
- 自动检测编码（GBK TSV 降级读取）
- 列名清洗：21 列 → 6 列（date / code / name / action / volume / price）
- 过滤非交易记录（分红、利息等），保留「买入」「卖出」
- 日期格式标准化 + 股票代码小数点修复
- 可独立运行，也可作为模块导入调用 `load_trades_data()`

### ② 截图 OCR 识别 (`ocr_trade.py`)

- 图片 → base64 编码，发送至多模态大模型
- 基于 OpenAI SDK 兼容接口，支持任意兼容 API：
  - **通义千问 VL**（qwen-vl-max / qwen-vl-plus）
  - **GPT-4o / GPT-4 Vision**
  - 其他兼容 OpenAI 接口的模型服务
- 提示词约束模型返回纯 JSON 数组，禁止废话
- 智能容错：自动从非纯 JSON 文本中提取数组
- 输出格式与 `load_trades.py` 完全一致
- 错误处理：API Key 无效 / 网络超时 / 非 JSON 返回 → 明确诊断

### ③ 交易分析 (`analyze_trades.py`)

- **FIFO 配对算法**：按股票分组，先进先出匹配买卖
  - 支持部分成交（一次卖出匹配多笔买入）
  - 检测无法配对的卖出（数据覆盖不完整）
  - 识别当前持仓（已买未卖）
- **分析维度**：
  | 维度 | 内容 |
  |------|------|
  | 整体概览 | 总笔数、胜率、总盈亏、平均盈亏、最大盈亏、平均持仓 |
  | 每月统计 | 按月汇总交易笔数、盈亏、胜率 |
  | 按股汇总 | 每只股票的笔数、胜率、盈亏、持仓天数 |
  | 交易明细 | 最近 10 笔的买卖价、盈亏、涨幅、持仓天数 |
  | 当前持仓 | 尚未卖出的股票及成本 |
  | 异常提醒 | 无法配对的卖出记录 |
- **Excel 导出**：一份文件 4 个 Sheet（交易明细 / 按股汇总 / 每月统计 / 当前持仓）

### ④ AI 交易诊断 (`ai_advisor.py`)

- **异常检测**（纯规则，无需 API）：5 个检测器自动扫描交易行为
  | 检测器 | 条件 | 严重度 |
  |--------|------|--------|
  | 大额亏损 | 单笔亏损 >200 元 或跌幅 >5% | 🔴/🟡 |
  | 高频交易 | 同日同股 >3 笔 | 🔴/🟡 |
  | 超短线 | 持仓 0 天（T+0） | 🔴/🟡 |
  | 连续亏损 | 同股连续 ≥3 笔亏损 | 🔴/🟡 |
  | 追涨杀跌 | 高位买入后亏损卖出（30日内） | 🔴 |
- **策略归因**（纯数学，无需 API）：量化评估选股能力 vs 择时能力
  - 选股得分：正收益股票占比 + 收益幅度 → [-100, +100]
  - 择时得分：短线 vs 长线胜率和收益对比 → [-100, +100]
  - 持仓周期分布柱状图
  - 2×2 象限归因结论
- **AI 综合诊断**（需 API）：调用 LLM 分析行为模式，给出 3 个具体问题和改进建议
  - 复用 OCR 的 API 配置，无需额外设置
  - API 未配置时自动跳过，其余功能正常运行

### ⑤ 统一管线 (`pipeline.py`)

- 一键串联完整流程：加载 → OCR → 合并去重 → 分析 → 导出
- Excel 数据与 OCR 数据自动合并，按 (日期/代码/操作/数量/价格) 去重
- 任一数据源失败不影响另一源继续
- 终端输出完整分析报告 + 自动导出 Excel

### ⑥ 持仓统计工具 (`src/tools/position.py`)

- **数据源**：`data/processed/latest/full_trades.csv`
- **Polars 引擎**：基于 Polars DataFrame 进行高性能分组聚合
- 买入加权均价：`avg_cost = Σ(price × volume) / Σ(volume)`
- 当前价格模拟：`current_price = avg_cost × 1.05`
- 输出字段：
  | 字段 | 说明 |
  |------|------|
  | `code` | 股票代码 |
  | `name` | 股票名称 |
  | `total_buy_volume` | 总买入数量 |
  | `total_sell_volume` | 总卖出数量 |
  | `position_volume` | 持仓数量（买 - 卖） |
  | `avg_cost` | 加权均价 |
  | `current_price` | 模拟当前价 |
  | `market_value` | 市值 |
  | `cost_value` | 持仓成本 |
  | `profit_loss` | 浮动盈亏 |
  | `profit_loss_pct` | 盈亏比例 |
  | `position_weight` | 仓位占比 |
- **LangChain Tool**：`calc_position_tool` — 封装为 LangChain `@tool`，可直接注册到 Agent
- **工具注册表** (`src/tools/registry.py`)：集中管理所有 Tool，提供 `get_all_tools()` 等便捷接口

## 运行方式

```bash
# 方式 1：完整管线（推荐）
python pipeline.py

# 方式 2：只看 Excel 数据
python load_trades.py

# 方式 3：只看截图 OCR
python ocr_trade.py

# 方式 4：编程调用
python -c "from pipeline import run_pipeline; run_pipeline()"

# 方式 5：单独测试 AI 诊断模块
python ai_advisor.py

# 方式 6：持仓统计工具自测（内嵌模拟数据）
python -m src.tools.position

# 方式 7：编程调用持仓统计
python -c "from src.tools.position import calc_position_tool; print(calc_position_tool.invoke({'csv_path': 'data/processed/latest/full_trades.csv'}))"

# 方式 8：查看已注册的 LangChain 工具
python -m src.tools.registry
```

## 配置说明 (`.env`)

```ini
# Excel 交割单文件夹路径
TRADE_PATH=F:/同花顺交割单

# OCR 模型配置（二选一）

# 通义千问 VL
OCR_API_KEY=你的通义千问API密钥
OCR_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
OCR_MODEL=qwen-vl-max

# GPT-4o（取消注释下面三行，注释上面三行即可切换）
# OCR_API_KEY=你的OpenAI API密钥
# OCR_BASE_URL=https://api.openai.com/v1
# OCR_MODEL=gpt-4o

# 截图文件路径
SCREENSHOT_PATH=F:/stock_auditor/screenshot.png
```

## 后续可补充功能

### 数据采集增强

- [ ] **自动截图**：定时截取同花顺窗口，自动送入 OCR 管线
- [ ] **多截图拼接**：支持长截图（滚动截图）的拼接识别
- [ ] **历史截图批量导入**：指定文件夹，批量 OCR 识别并合并
- [ ] **实时行情接入**：通过 AKShare / Tushare / EastMoney 获取历史 K 线数据
- [ ] **多券商支持**：适配华泰、中信、东方财富等不同格式的交割单

### 分析能力增强

- [ ] **手续费 & 印花税**：从原始数据提取费用列，计入实际盈亏
- [ ] **资金曲线**：按时间序列绘制账户净值变化曲线
- [ ] **最大回撤**：计算账户历史最大回撤
- [ ] **夏普比率**：基于每日收益计算风险调整收益
- [x] **持仓集中度**：单只股票仓位占比分析（`position_weight` 字段）
- [ ] **交易行为分析**：交易时段分布、周几偏好、频率分析
- [ ] **移动止盈/止损回测**：模拟不同止盈止损策略的历史表现
- [ ] **跟大盘对比**：将账户曲线与上证/深证指数叠加对比
- [ ] **T+0 识别**：自动标注日内交易（当天买卖同一只股票）

### 报告与可视化

- [ ] **HTML 可视化报告**：使用 ECharts / Plotly 生成交互式图表
- [ ] **PDF 报告导出**：使用 WeasyPrint / ReportLab 生成正式 PDF
- [ ] **盈利分布直方图**：展示每笔盈亏的分布形态
- [ ] **持仓热力图**：按行业/概念展示持仓分布
- [ ] **日历热力图**：按日期展示每日盈亏（类似 GitHub 贡献图）

### 自动化

- [ ] **定时任务**：每天收盘后自动运行管线，生成当日报告
- [ ] **邮件/微信推送**：分析报告自动推送到手机
- [ ] **Web Dashboard**：使用 Streamlit / Flask 搭建本地 Web 看板
- [ ] **数据库存储**：交易记录持久化到 SQLite / PostgreSQL，支持历史回溯

### 智能化

- [x] **AI 交易诊断**：基于交易记录，由 LLM 生成个性化的交易改善建议
- [x] **异常检测**：自动识别异常大亏、频繁交易、追涨杀跌等行为
- [x] **策略归因**：区分选股能力和择时能力的贡献
- [x] **LangChain Tool 封装**：持仓统计工具已封装为 LangChain Tool，可注册到 AI Agent
- [ ] **模拟调仓建议**：基于历史数据，模拟不同策略的表现差异
