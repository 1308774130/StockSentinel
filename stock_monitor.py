import requests
import time
import json
from datetime import datetime

# ===== 配置区 =====
FEISHU_WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/fa25dffb-041a-479c-a1d0-7dfe62e3af7a"
STOCK_CODES = ["sh603993", "sz001330"]  # 洛阳钼业(sh), 博纳影业(sz)
CHECK_INTERVAL = 1600  # 每60秒检查一次

# ===== 工具函数 =====
def get_stock_data(code):
    """从腾讯接口获取股票实时数据"""
    url = f"http://qt.gtimg.cn/q={code}"
    try:
        resp = requests.get(url, timeout=5)
        data = resp.text.split("～")
        if len(data) < 32:
            return None
        return {
            "name": data[1],
            "code": code,
            "price": float(data[3]),
            "pre_close": float(data[4]),
            "high": float(data[33]),
            "low": float(data[34]),
            "time": data[30]
        }
    except Exception as e:
        print(f"[ERROR] 获取 {code} 数据失败: {e}")
        return None

def calculate_rsi(prices, period=6):
    """计算RSI（简化版，使用最近N个价格）"""
    if len(prices) < period + 1:
        return None
    deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    gains = [d for d in deltas[-period:] if d > 0]
    losses = [-d for d in deltas[-period:] if d < 0]
    avg_gain = sum(gains) / period if gains else 0
    avg_loss = sum(losses) / period if losses else 0
    if avg_loss == 0:
        return 100 if avg_gain > 0 else 0
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return round(rsi, 2)

# 存储历史价格（用于RSI计算）
price_history = {code: [] for code in STOCK_CODES}

def send_feishu_message(title, content):
    """发送飞书消息卡片"""
    msg = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": title}, "template": "red"},
            "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": content}}]
        }
    }
    resp = requests.post(FEISHU_WEBHOOK, json=msg)
    print(f"[INFO] 飞书消息发送状态: {resp.status_code}")

# ===== 主监控逻辑 =====
def monitor_stocks():
    for code in STOCK_CODES:
        stock = get_stock_data(code)
        if not stock:
            continue

        # 更新价格历史
        price_history[code].append(stock["price"])
        if len(price_history[code]) > 20:
            price_history[code].pop(0)

        # 计算RSI(6)
        rsi6 = calculate_rsi(price_history[code], 6)
        if rsi6 is None:
            continue

        # 当前涨跌幅
        change_pct = (stock["price"] - stock["pre_close"]) / stock["pre_close"] * 100

        alerts = []

        # 条件1: RSI超买/超卖
        if rsi6 > 80:
            alerts.append(f"⚠️ RSI(6) = {rsi6}（超买）")
        elif rsi6 < 20:
            alerts.append(f"✅ RSI(6) = {rsi6}（超卖）")

        # 条件2: 日内涨跌幅过大
        if abs(change_pct) > 5:
            alerts.append(f"🚨 日内波动 {change_pct:.2f}%")

        # 发送提醒
        if alerts:
            content = f"**{stock['name']} ({stock['code']})**\n当前价: {stock['price']}\n{', '.join(alerts)}"
            send_feishu_message("【股票异动提醒】", content)
            print(f"[ALERT] {datetime.now().strftime('%H:%M:%S')} {stock['name']} 触发提醒")

# ===== 启动监控 =====
if __name__ == "__main__":
    print("🚀 股票监控机器人启动...")
    while True:
        try:
            monitor_stocks()
            time.sleep(CHECK_INTERVAL)
        except KeyboardInterrupt:
            print("\n🛑 监控已停止")
            break