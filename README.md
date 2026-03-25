# 🤖 StockSentinel - 飞书股票监控机器人

通过 GitHub Actions 定时运行，自动监控股票行情并推送飞书卡片消息。

## ✨ 功能

- 📊 **技术指标** - BOLL + RSI + MACD 综合分析
- 💬 **飞书推送** - 美观的卡片消息，区分交易提醒/行情播报
- 🧩 **做T建议** - 基于持仓成本的个性化高抛低吸建议
- 🗄️ **数据存储** - SQLite 保存监控列表和历史数据
- ⏰ **交易时段** - 仅在 A 股交易时段（9:30-11:30, 13:00-15:00）推送

## 🚀 部署（GitHub Actions，完全免费）

### 1. 配置 Secrets

进入 GitHub 仓库 → `Settings` → `Secrets and variables` → `Actions` → `New repository secret`：
- Name: `FEISHU_WEBHOOK`
- Value: `你的飞书Webhook地址`

**获取 Webhook：** 飞书群聊 → 设置 → 群机器人 → 自定义机器人

### 2. 配置 Variables（可选）

点击 `Variables` 标签页 → `New repository variable`：
- Name: `STOCK_LIST`
- Value: `600519,000001,300750`（用逗号分隔股票代码）

> 也可以直接修改代码中的 `USER_POSITIONS` 来配置持仓信息（成本、股数、策略）。

### 3. 启用

进入 `Actions` 标签页，启用 Workflow。它会在每个交易日 9:30-15:00 每 10 分钟运行一次。

## 📊 持仓配置

在 `feishu_stock_bot.py` 的 `Config.USER_POSITIONS` 中配置你的持仓：

```python
USER_POSITIONS = {
    "sh601015": {"name": "陕西黑猫", "cost": 6.375, "holdings": 900, "strategy": "T"},
    "sh600984": {"name": "建设机械", "cost": 7.0, "holdings": 820, "strategy": "T"},
    "sh603993": {"name": "洛阳钼业", "cost": 0, "holdings": 0, "strategy": "Short"}
}
```

- `cost`：建仓成本价
- `holdings`：持仓股数
- `strategy`：`T`（做T高抛低吸）或 `Short`（短线）

## 📱 消息示例

```
【交易提醒】陕西黑猫

📈 陕西黑猫 (sh601015)
💰 现价: 6.50 (+1.96%)
💸 持仓: 900股 | 成本 6.375 | 盈亏 113 (+1.96%)
📊 指标: RSI=72.5 | MACD=0.035
📏 布林: 上6.80 / 中6.40 / 下6.00

⚠️ 建议操作:
💰 已盈利且接近高位，可考虑先卖出20%-30%做T，回落再接回
```

## 📁 项目结构

```
StockSentinel/
├── feishu_stock_bot.py        # 主程序（单次通知模式）
├── .github/workflows/         # GitHub Actions 定时任务
│   └── monitor.yml
├── requirements.txt           # 依赖
├── .gitignore
└── README.md
```

## 🔧 本地测试

```bash
pip install requests
export FEISHU_WEBHOOK='你的webhook'
python3 feishu_stock_bot.py
```

---

## 📄 License

MIT License

⚠️ 免责声明：仅供学习参考，不构成投资建议
