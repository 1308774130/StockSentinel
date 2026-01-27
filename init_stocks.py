#!/usr/bin/env python3
"""批量添加监控股票"""

from feishu_stock_bot import Database, StockDataFetcher

# ===== 配置你要监控的股票 =====
stocks = [
    "600519",  # 贵州茅台
    "000001",  # 平安银行
    # 添加更多...
]

db = Database("stock_monitor.db")

for code in stocks:
    data = StockDataFetcher.get_stock_data(code)
    if data:
        db.add_stock(data["code"], data["name"])
        print(f"✅ {data['name']} ({data['code']})")
    else:
        print(f"❌ 未找到: {code}")

print("\n🎉 完成！现在可以运行: python feishu_stock_bot.py")
