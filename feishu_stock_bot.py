#!/usr/bin/env python3
"""
飞书股票监控机器人（单向通知模式）
功能：
1. 定时检查监控股票行情与技术指标
2. 基于 BOLL + RSI + MACD 的个性化做T建议
3. 通过飞书 Webhook 推送卡片消息
"""

import os
import sys
import json
import time
import sqlite3
import requests
from datetime import datetime
from typing import Dict, List, Optional


# ===== 配置区 =====
class Config:
    FEISHU_WEBHOOK = os.getenv("FEISHU_WEBHOOK", "")

    # 策略配置 (BOLL + RSI + MACD)
    RSI_PERIOD = 14
    RSI_OVERBOUGHT = 70
    RSI_OVERSOLD = 30
    BOLL_PERIOD = 20
    BOLL_STD = 2
    PRICE_CHANGE_THRESHOLD = 7
    VOLUME_RATIO_THRESHOLD = 2

    # 用户持仓配置
    USER_POSITIONS = {
        "sh601015": {"name": "陕西黑猫", "cost": 6.375, "holdings": 900, "strategy": "T"},
        "sh600984": {"name": "建设机械", "cost": 7.0, "holdings": 820, "strategy": "T"},
        "sh603993": {"name": "洛阳钼业", "cost": 0, "holdings": 0, "strategy": "Short"}
    }

    # 股票列表（合并环境变量和用户持仓）
    _env_stocks = os.getenv("STOCK_LIST", "")
    _stock_set = set(USER_POSITIONS.keys())
    if _env_stocks:
        _stock_set.update([s.strip() for s in _env_stocks.split(",") if s.strip()])
    STOCK_LIST = ",".join(_stock_set)

    DB_PATH = "stock_monitor.db"


# ===== 数据库管理 =====
class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.init_db()

    def init_db(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS monitor_stocks (
                code TEXT PRIMARY KEY,
                name TEXT,
                added_time TEXT,
                user_id TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS price_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT,
                price REAL,
                volume REAL,
                timestamp TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS alert_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT,
                alert_type TEXT,
                content TEXT,
                timestamp TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_positions (
                code TEXT PRIMARY KEY,
                name TEXT,
                cost REAL DEFAULT 0,
                holdings INTEGER DEFAULT 0,
                strategy TEXT DEFAULT 'T',
                updated_time TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trade_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT,
                name TEXT,
                action TEXT,
                price REAL,
                shares INTEGER,
                realized_pnl REAL DEFAULT 0,
                timestamp TEXT
            )
        """)

        conn.commit()
        conn.close()

        if Config.STOCK_LIST:
            self.sync_env_stocks()

    def sync_env_stocks(self):
        if not Config.STOCK_LIST:
            print("[WARN] 环境变量 STOCK_LIST 为空")
            return

        codes = Config.STOCK_LIST.split(",")
        print(f"[INFO] 检测到环境变量配置股票: {len(codes)}只 -> {codes}")
        for code in codes:
            code = code.strip()
            if not code:
                continue
            try:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM monitor_stocks WHERE code=?", (StockDataFetcher.normalize_code(code),))
                res = cursor.fetchone()
                conn.close()

                if not res:
                    data = StockDataFetcher.get_stock_data(code)
                    if data:
                        self.add_stock(data["code"], data["name"])
                        print(f"[INFO] 自动添加股票: {data['name']}")
                    else:
                        print(f"[WARN] 获取股票数据失败: {code}")
            except Exception as e:
                print(f"[WARN] 自动添加股票失败 {code}: {e}")

    def add_stock(self, code: str, name: str, user_id: str = ""):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT OR REPLACE INTO monitor_stocks (code, name, added_time, user_id) VALUES (?, ?, ?, ?)",
                (code, name, datetime.now().isoformat(), user_id)
            )
            conn.commit()
            return True
        except Exception as e:
            print(f"[ERROR] 添加股票失败: {e}")
            return False
        finally:
            conn.close()

    def get_all_stocks(self) -> List[Dict]:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT code, name FROM monitor_stocks")
        stocks = [{"code": row[0], "name": row[1]} for row in cursor.fetchall()]
        conn.close()
        return stocks

    def get_user_position(self, code: str) -> Optional[Dict]:
        normalized_code = StockDataFetcher.normalize_code(code)
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT code, name, cost, holdings, strategy, updated_time
            FROM user_positions WHERE code = ?
        """, (normalized_code,))
        row = cursor.fetchone()
        conn.close()
        if not row:
            return None
        return {
            "code": row[0], "name": row[1],
            "cost": row[2] or 0, "holdings": row[3] or 0,
            "strategy": row[4] or "T", "updated_time": row[5] or ""
        }

    def get_recent_trades(self, code: str, limit: int = 5) -> List[Dict]:
        normalized_code = StockDataFetcher.normalize_code(code)
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT action, price, shares, realized_pnl, timestamp
            FROM trade_records WHERE code = ? ORDER BY id DESC LIMIT ?
        """, (normalized_code, limit))
        rows = cursor.fetchall()
        conn.close()
        return [
            {"action": r[0], "price": r[1], "shares": r[2],
             "realized_pnl": r[3] or 0, "timestamp": r[4]}
            for r in rows
        ]


# ===== 股票数据获取 =====
class StockDataFetcher:
    @staticmethod
    def normalize_code(code: str) -> str:
        code = code.strip().upper()
        for prefix in ['SH', 'SZ', 'BJ']:
            if code.startswith(prefix):
                code = code[2:]
                break
        if code.isdigit():
            if code.startswith('6'):
                return f"sh{code}"
            elif code.startswith(('0', '3')):
                return f"sz{code}"
            elif code.startswith(('4', '8')):
                return f"bj{code}"
        return code.lower()

    @staticmethod
    def get_stock_data(code: str) -> Optional[Dict]:
        normalized_code = StockDataFetcher.normalize_code(code)
        url = f"http://qt.gtimg.cn/q={normalized_code}"
        try:
            resp = requests.get(url, timeout=5)
            resp.raise_for_status()
            try:
                text = resp.content.decode('gbk')
            except UnicodeDecodeError:
                text = resp.text
            if "pv_none_match" in text:
                return None
            data = text.split("~")
            if len(data) < 35:
                data = text.split("～")
                if len(data) < 35:
                    return None
            return {
                "name": data[1], "code": normalized_code,
                "price": float(data[3]) if data[3] else 0,
                "pre_close": float(data[4]) if data[4] else 0,
                "open": float(data[5]) if data[5] else 0,
                "high": float(data[33]) if data[33] else 0,
                "low": float(data[34]) if data[34] else 0,
                "volume": float(data[6]) if data[6] else 0,
                "amount": float(data[37]) if data[37] else 0,
                "time": data[30]
            }
        except Exception as e:
            print(f"[ERROR] 获取 {code} 数据失败: {e}")
            return None

    @staticmethod
    def get_kline_history(code: str, scale: str = 'day', limit: int = 60) -> List[Dict]:
        normalized_code = StockDataFetcher.normalize_code(code)
        url = f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={normalized_code},{scale},,,{limit},qfq"
        try:
            resp = requests.get(url, timeout=5)
            if resp.status_code != 200:
                return []
            data = resp.json()
            if 'data' in data and normalized_code in data['data']:
                kline_data = data['data'][normalized_code].get(scale, [])
                if not kline_data:
                    kline_data = data['data'][normalized_code].get(f"qfq{scale}", [])
                history = []
                for item in kline_data:
                    if len(item) >= 6:
                        history.append({
                            "date": item[0], "open": float(item[1]),
                            "close": float(item[2]), "high": float(item[3]),
                            "low": float(item[4]), "volume": float(item[5])
                        })
                return history
        except Exception as e:
            print(f"[ERROR] 获取K线失败 {code}: {e}")
        return []


# ===== 技术指标计算 =====
class TechnicalAnalysis:
    @staticmethod
    def calculate_ema(prices: List[float], period: int) -> List[float]:
        if not prices:
            return []
        ema = []
        multiplier = 2 / (period + 1)
        for i, price in enumerate(prices):
            if i == 0:
                ema.append(price)
            else:
                ema.append((price - ema[-1]) * multiplier + ema[-1])
        return ema

    @staticmethod
    def calculate_macd(prices: List[float], fast: int = 12, slow: int = 26, signal: int = 9) -> Optional[Dict[str, float]]:
        if len(prices) < slow + signal:
            return None
        ema_fast = TechnicalAnalysis.calculate_ema(prices, fast)
        ema_slow = TechnicalAnalysis.calculate_ema(prices, slow)
        min_len = min(len(ema_fast), len(ema_slow))
        ema_fast = ema_fast[-min_len:]
        ema_slow = ema_slow[-min_len:]
        dif = [f - s for f, s in zip(ema_fast, ema_slow)]
        dea = TechnicalAnalysis.calculate_ema(dif, signal)
        if not dif or not dea:
            return None
        return {"dif": dif[-1], "dea": dea[-1], "macd": (dif[-1] - dea[-1]) * 2}

    @staticmethod
    def calculate_rsi(prices: List[float], period: int = 6) -> Optional[float]:
        if len(prices) < period + 1:
            return None
        deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
        gains = [d for d in deltas[-period:] if d > 0]
        losses = [-d for d in deltas[-period:] if d < 0]
        avg_gain = sum(gains) / period if gains else 0
        avg_loss = sum(losses) / period if losses else 0
        if avg_loss == 0:
            return 100 if avg_gain > 0 else 0
        rs = avg_gain / avg_loss
        return round(100 - (100 / (1 + rs)), 2)

    @staticmethod
    def calculate_boll(prices: List[float], period: int = 20, std_dev: int = 2) -> Optional[Dict[str, float]]:
        if len(prices) < period:
            return None
        recent_prices = prices[-period:]
        mb = sum(recent_prices) / period
        variance = sum([((x - mb) ** 2) for x in recent_prices]) / period
        std = variance ** 0.5
        return {"up": mb + std_dev * std, "mb": mb, "dn": mb - std_dev * std}


# ===== 飞书消息发送 =====
class FeishuNotifier:
    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    def send_card(self, title: str, content: str, color: str = "red"):
        msg = {
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True},
                "header": {
                    "title": {"tag": "plain_text", "content": title},
                    "template": color
                },
                "elements": [
                    {"tag": "div", "text": {"tag": "lark_md", "content": content}}
                ]
            }
        }
        try:
            resp = requests.post(self.webhook_url, json=msg, timeout=5)
            resp.raise_for_status()
            print(f"[INFO] 飞书消息发送成功: {title}")
            return True
        except Exception as e:
            print(f"[ERROR] 飞书消息发送失败: {e}")
            return False


# ===== 股票监控器 =====
class StockMonitor:
    def __init__(self, db: Database, notifier: FeishuNotifier, config: Config):
        self.db = db
        self.notifier = notifier
        self.config = config

    def is_trading_session(self, now_local: datetime) -> bool:
        if now_local.weekday() >= 5:
            return False
        hhmm = now_local.hour * 100 + now_local.minute
        return (930 <= hhmm <= 1130) or (1300 <= hhmm <= 1500)

    def build_t_advice(self, user_pos: Dict, current_price: float, rsi_val: Optional[float],
                       boll: Optional[Dict[str, float]], macd: Optional[Dict[str, float]],
                       recent_trades: List[Dict]) -> List[str]:
        advice = []
        cost = user_pos.get("cost", 0) or 0
        holdings = user_pos.get("holdings", 0) or 0
        if holdings <= 0 or cost <= 0:
            return advice

        pnl_pct = (current_price - cost) / cost * 100
        is_oversold = (rsi_val is not None) and (rsi_val < self.config.RSI_OVERSOLD)
        is_overbought = (rsi_val is not None) and (rsi_val > self.config.RSI_OVERBOUGHT)
        is_boll_low = bool(boll) and current_price <= boll["dn"] * 1.01
        is_boll_high = bool(boll) and current_price >= boll["up"] * 0.99
        is_macd_gold = bool(macd) and macd["dif"] > macd["dea"] and macd["macd"] > 0

        if pnl_pct < 0:
            if is_boll_low and is_oversold:
                advice.append("🧩 套牢区可考虑分批买回做T（10%-20%仓位）以摊薄成本，不建议止损清仓")
            else:
                advice.append("🛡️ 当前低于成本，建议以等待反弹做T为主，不做止损清仓")
        else:
            if is_boll_high and is_overbought:
                advice.append("💰 已盈利且接近高位，可考虑先卖出20%-30%做T，回落再接回")
            elif is_macd_gold:
                advice.append("📈 趋势仍偏强，先持有；若回踩中轨/成本附近再分批接回")
            else:
                advice.append("⚖️ 已盈利但信号一般，可小仓位高抛低吸做T，避免一次性清仓")

        if recent_trades:
            last = recent_trades[0]
            if last.get("action") == "sell" and current_price <= (last.get("price", current_price) * 0.985):
                advice.append("🔁 最近有卖出且已回落超1.5%，可考虑分批买回完成一轮T")

        return advice

    def monitor_single_stock(self, stock: Dict) -> Optional[Dict]:
        code = stock["code"]
        name = stock["name"]

        user_pos = self.db.get_user_position(code)
        if not user_pos:
            user_pos = self.config.USER_POSITIONS.get(code)
        if not user_pos:
            for k, v in self.config.USER_POSITIONS.items():
                if k in code or code in k:
                    user_pos = v
                    break

        data = StockDataFetcher.get_stock_data(code)
        if not data or data["price"] == 0:
            return None

        current_price = data["price"]
        change_pct = (current_price - data["pre_close"]) / data["pre_close"] * 100

        history = StockDataFetcher.get_kline_history(code, scale='day', limit=60)

        alerts = []
        rsi_val = None
        boll = None
        macd = None

        if history and len(history) >= 30:
            close_prices = [h["close"] for h in history]
            close_prices.append(current_price)

            rsi_val = TechnicalAnalysis.calculate_rsi(close_prices, self.config.RSI_PERIOD)
            boll = TechnicalAnalysis.calculate_boll(close_prices, self.config.BOLL_PERIOD, self.config.BOLL_STD)
            macd = TechnicalAnalysis.calculate_macd(close_prices)

            if boll and rsi_val is not None and macd:
                is_oversold = rsi_val < self.config.RSI_OVERSOLD
                is_overbought = rsi_val > self.config.RSI_OVERBOUGHT
                is_boll_low = current_price <= boll["dn"] * 1.01
                is_boll_high = current_price >= boll["up"] * 0.99
                is_macd_gold = macd["macd"] > 0 and macd["dif"] > macd["dea"]

                if user_pos:
                    strategy = user_pos.get("strategy", "")
                    cost = user_pos.get("cost", 0)

                    if strategy == "T":
                        if is_boll_low and is_oversold:
                            alerts.append(f"🟢 **T+0买入机会**: 触及布林下轨({boll['dn']:.2f}) + RSI超卖({rsi_val:.1f})")
                        if is_boll_high and is_overbought:
                            profit_msg = ""
                            if cost > 0 and current_price > cost:
                                profit_pct = (current_price - cost) / cost * 100
                                profit_msg = f" (浮盈 {profit_pct:.1f}%)"
                            alerts.append(f"🔴 **T+0卖出机会**: 触及布林上轨({boll['up']:.2f}) + RSI超买({rsi_val:.1f}){profit_msg}")
                    elif strategy == "Short":
                        if is_macd_gold and rsi_val > 50:
                            alerts.append("🚀 **短线追涨**: MACD金叉 + RSI强势区域")
                        elif is_boll_low and is_oversold:
                            alerts.append("🟢 **短线抄底**: 触及布林下轨 + RSI超卖")

                if not alerts:
                    if is_boll_low and is_oversold:
                        alerts.append("🟢 触底反弹信号: BOLL下轨 + RSI超卖")
                    elif is_boll_high and is_overbought:
                        alerts.append("🔴 顶部风险信号: BOLL上轨 + RSI超买")

        if abs(change_pct) > 7:
            emoji = "🚀" if change_pct > 0 else "💥"
            alerts.append(f"{emoji} 股价剧烈波动: {change_pct:+.2f}%")

        msg_content = f"📈 **{name} ({code})**\n"
        msg_content += f"💰 现价: {current_price} ({change_pct:+.2f}%)\n"

        if user_pos:
            cost = user_pos.get("cost", 0)
            holdings = user_pos.get("holdings", 0)
            if cost > 0:
                profit = (current_price - cost) * holdings
                profit_pct = (current_price - cost) / cost * 100
                emoji = "🧧" if profit > 0 else "💸"
                msg_content += f"{emoji} 持仓: {holdings}股 | 成本 {cost} | 盈亏 {profit:.0f} ({profit_pct:+.1f}%)\n"

        if boll and rsi_val is not None and macd:
            msg_content += f"📊 指标: RSI={rsi_val:.1f} | MACD={macd['macd']:.3f}\n"
            msg_content += f"📏 布林: 上{boll['up']:.2f} / 中{boll['mb']:.2f} / 下{boll['dn']:.2f}\n"

        if user_pos:
            recent_trades = self.db.get_recent_trades(code, limit=3)
            t_advice = self.build_t_advice(user_pos, current_price, rsi_val, boll, macd, recent_trades)
            if t_advice:
                alerts.extend(t_advice)

        if alerts:
            msg_content += "\n⚠️ **建议操作:**\n" + "\n".join(alerts)
            color = "red" if any("卖" in a for a in alerts) else "green"
            self.notifier.send_card(f"【交易提醒】{name}", msg_content, color)
        else:
            self.notifier.send_card(f"【行情播报】{name}", msg_content, "blue")

        return {
            "name": name, "code": code, "price": current_price,
            "change_pct": change_pct, "rsi": rsi_val if rsi_val else 0,
            "has_alert": bool(alerts)
        }

    def check_all_stocks(self):
        monitored_list = []
        try:
            stocks = self.db.get_all_stocks()
            now_local = datetime.now()
            if not self.is_trading_session(now_local):
                print(f"[INFO] {now_local.strftime('%H:%M:%S')} 非交易时段，跳过检查")
                return monitored_list
            if not stocks:
                print("[INFO] 没有监控的股票")
            else:
                print(f"[INFO] {now_local.strftime('%H:%M:%S')} 开始检查 {len(stocks)} 只股票...")
                for stock in stocks:
                    self.monitor_single_stock(stock)
                    monitored_list.append(stock)
        except Exception as e:
            print(f"[ERROR] 监控检查异常: {e}")
        return monitored_list


# ===== 主程序 =====
def main():
    print("=" * 50)
    print("🤖 飞书股票监控机器人 v1.0")
    print("=" * 50)

    config = Config()

    if not config.FEISHU_WEBHOOK:
        print("❌ 错误: 未配置 FEISHU_WEBHOOK")
        print("💡 请设置环境变量 FEISHU_WEBHOOK，或在 Secrets 中配置")
        return

    db = Database(config.DB_PATH)
    notifier = FeishuNotifier(config.FEISHU_WEBHOOK)
    monitor = StockMonitor(db, notifier, config)

    print("🚀 单次运行模式启动...")

    monitored_stocks = monitor.check_all_stocks()
    db_stocks = db.get_all_stocks()
    in_trading_now = monitor.is_trading_session(datetime.now())

    if (len(db_stocks) == 0) and in_trading_now:
        notifier.send_card(
            "⚠️ 监控列表为空",
            "请在 GitHub Variables 中配置 STOCK_LIST 或检查代码中的 USER_POSITIONS",
            "yellow"
        )

    print("✅ 单次检查完成")


if __name__ == "__main__":
    main()
