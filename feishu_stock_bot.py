#!/usr/bin/env python3
"""
飞书股票监控机器人
功能：
1. @机器人添加/删除监控股票
2. 后台实时监控股票异动
3. 智能预警：RSI超买超卖、涨跌幅异常、成交量放大
4. 飞书交互式命令
"""

import os
import json
import time
import sqlite3
import requests
import threading
from datetime import datetime
from typing import Dict, List, Optional
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs
import hashlib
import hmac

# ===== 配置区 =====
class Config:
    # 飞书配置
    FEISHU_APP_ID = os.getenv("FEISHU_APP_ID", "")
    FEISHU_APP_SECRET = os.getenv("FEISHU_APP_SECRET", "")
    # 优先从环境变量获取，如果没有则为空（强制用户配置）
    FEISHU_WEBHOOK = os.getenv("FEISHU_WEBHOOK", "")
    FEISHU_VERIFICATION_TOKEN = os.getenv("FEISHU_VERIFICATION_TOKEN", "")
    
    # HTTP 服务器配置（用于接收飞书消息）
    HTTP_PORT = int(os.getenv("HTTP_PORT", "8080"))
    
    # 监控配置
    CHECK_INTERVAL = 60  # 检查间隔(秒)
    RSI_PERIOD = 6  # RSI周期
    RSI_OVERBOUGHT = 80  # RSI超买阈值
    RSI_OVERSOLD = 20  # RSI超卖阈值
    PRICE_CHANGE_THRESHOLD = 5  # 涨跌幅预警阈值(%)
    VOLUME_RATIO_THRESHOLD = 2  # 成交量放大倍数
    
    # 股票列表（环境变量优先，用于 GitHub Actions）
    STOCK_LIST = os.getenv("STOCK_LIST", "")
    
    # 数据库
    DB_PATH = "stock_monitor.db"


# ===== 数据库管理 =====
class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.init_db()
    
    def init_db(self):
        """初始化数据库"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 监控股票表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS monitor_stocks (
                code TEXT PRIMARY KEY,
                name TEXT,
                added_time TEXT,
                user_id TEXT
            )
        """)
        
        # 价格历史表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS price_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT,
                price REAL,
                volume REAL,
                timestamp TEXT
            )
        """)
        
        # 预警记录表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS alert_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT,
                alert_type TEXT,
                content TEXT,
                timestamp TEXT
            )
        """)
        
        conn.commit()
        conn.close()
        
        # 如果有环境变量配置的股票，自动添加
        if Config.STOCK_LIST:
            self.sync_env_stocks()
            
    def sync_env_stocks(self):
        """同步环境变量中的股票到数据库"""
        codes = Config.STOCK_LIST.split(",")
        print(f"[INFO] 检测到环境变量配置股票: {len(codes)}只")
        for code in codes:
            code = code.strip()
            if not code: continue
            # 简单检查是否已存在，不存在则获取信息添加
            # 这里为了简单，每次启动都尝试添加（add_stock有去重）
            try:
                # 只有当数据库里没有这个名字时才去联网获取，避免每次启动都大量请求
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
            except Exception as e:
                print(f"[WARN] 自动添加股票失败 {code}: {e}")
    
    def add_stock(self, code: str, name: str, user_id: str = ""):
        """添加监控股票"""
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
    
    def remove_stock(self, code: str):
        """移除监控股票"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM monitor_stocks WHERE code = ?", (code,))
        conn.commit()
        conn.close()
    
    def get_all_stocks(self) -> List[Dict]:
        """获取所有监控股票"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT code, name FROM monitor_stocks")
        stocks = [{"code": row[0], "name": row[1]} for row in cursor.fetchall()]
        conn.close()
        return stocks
    
    def add_price_record(self, code: str, price: float, volume: float):
        """添加价格记录"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO price_history (code, price, volume, timestamp) VALUES (?, ?, ?, ?)",
            (code, price, volume, datetime.now().isoformat())
        )
        # 只保留最近100条记录
        cursor.execute("""
            DELETE FROM price_history 
            WHERE code = ? AND id NOT IN (
                SELECT id FROM price_history WHERE code = ? ORDER BY id DESC LIMIT 100
            )
        """, (code, code))
        conn.commit()
        conn.close()
    
    def get_price_history(self, code: str, limit: int = 20) -> List[float]:
        """获取价格历史"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT price FROM price_history WHERE code = ? ORDER BY id DESC LIMIT ?",
            (code, limit)
        )
        prices = [row[0] for row in cursor.fetchall()]
        conn.close()
        return list(reversed(prices))
    
    def get_volume_history(self, code: str, limit: int = 5) -> List[float]:
        """获取成交量历史"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT volume FROM price_history WHERE code = ? ORDER BY id DESC LIMIT ?",
            (code, limit)
        )
        volumes = [row[0] for row in cursor.fetchall()]
        conn.close()
        return list(reversed(volumes))


# ===== 股票数据获取 =====
class StockDataFetcher:
    @staticmethod
    def normalize_code(code: str) -> str:
        """标准化股票代码"""
        code = code.strip().upper()
        # 移除常见前缀
        for prefix in ['SH', 'SZ', 'BJ']:
            if code.startswith(prefix):
                code = code[2:]
                break
        
        # 添加市场前缀
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
        """从腾讯接口获取股票实时数据"""
        normalized_code = StockDataFetcher.normalize_code(code)
        url = f"http://qt.gtimg.cn/q={normalized_code}"
        
        try:
            resp = requests.get(url, timeout=5)
            resp.raise_for_status()
            
            # 解析数据
            text = resp.text
            if "pv_none_match" in text:
                return None
            
            data = text.split("～")
            if len(data) < 35:
                return None
            
            return {
                "name": data[1],
                "code": normalized_code,
                "price": float(data[3]) if data[3] else 0,
                "pre_close": float(data[4]) if data[4] else 0,
                "open": float(data[5]) if data[5] else 0,
                "high": float(data[33]) if data[33] else 0,
                "low": float(data[34]) if data[34] else 0,
                "volume": float(data[6]) if data[6] else 0,  # 成交量(手)
                "amount": float(data[37]) if data[37] else 0,  # 成交额(万)
                "time": data[30]
            }
        except Exception as e:
            print(f"[ERROR] 获取 {code} 数据失败: {e}")
            return None


# ===== 技术指标计算 =====
class TechnicalAnalysis:
    @staticmethod
    def calculate_rsi(prices: List[float], period: int = 6) -> Optional[float]:
        """计算RSI指标"""
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
    
    @staticmethod
    def calculate_volume_ratio(volumes: List[float]) -> Optional[float]:
        """计算量比（当前成交量/平均成交量）"""
        if len(volumes) < 2:
            return None
        
        current = volumes[-1]
        avg = sum(volumes[:-1]) / len(volumes[:-1])
        
        if avg == 0:
            return None
        
        return round(current / avg, 2)


# ===== 飞书消息发送 =====
class FeishuNotifier:
    def __init__(self, webhook_url: str, app_id: str = "", app_secret: str = ""):
        self.webhook_url = webhook_url
        self.app_id = app_id
        self.app_secret = app_secret
        self.access_token = None
        self.token_expire_time = 0
    
    def get_tenant_access_token(self):
        """获取 tenant_access_token（用于主动发消息）"""
        if not self.app_id or not self.app_secret:
            return None
        
        # 检查token是否过期
        if self.access_token and time.time() < self.token_expire_time:
            return self.access_token
        
        url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
        data = {
            "app_id": self.app_id,
            "app_secret": self.app_secret
        }
        
        try:
            resp = requests.post(url, json=data, timeout=5)
            result = resp.json()
            if result.get("code") == 0:
                self.access_token = result["tenant_access_token"]
                self.token_expire_time = time.time() + result.get("expire", 7200) - 300
                return self.access_token
        except Exception as e:
            print(f"[ERROR] 获取token失败: {e}")
        
        return None
    
    def reply_message(self, message_id: str, content: str):
        """回复消息"""
        token = self.get_tenant_access_token()
        if not token:
            print("[WARN] 未配置APP凭证，无法回复消息")
            return False
        
        url = f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/reply"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        data = {
            "content": json.dumps({"text": content}),
            "msg_type": "text"
        }
        
        try:
            resp = requests.post(url, headers=headers, json=data, timeout=5)
            resp.raise_for_status()
            return True
        except Exception as e:
            print(f"[ERROR] 回复消息失败: {e}")
            return False
    
    def send_card(self, title: str, content: str, color: str = "red"):
        """发送飞书卡片消息"""
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
    
    def send_alert(self, stock_name: str, stock_code: str, alerts: List[str], stock_data: Dict):
        """发送异动提醒"""
        change_pct = (stock_data["price"] - stock_data["pre_close"]) / stock_data["pre_close"] * 100
        
        content = f"""**{stock_name} ({stock_code})**
📈 当前价: **{stock_data['price']}** ({change_pct:+.2f}%)
📊 今日: 开 {stock_data['open']} | 高 {stock_data['high']} | 低 {stock_data['low']}
💰 成交额: {stock_data['amount']:.0f}万

⚠️ **异动信号:**
{chr(10).join(f"• {alert}" for alert in alerts)}

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""
        
        # 根据涨跌幅选择颜色
        color = "red" if change_pct > 0 else "green" if change_pct < 0 else "blue"
        
        self.send_card("【股票异动提醒】", content, color)
    
    def send_stock_list(self, stocks: List[Dict]):
        """发送监控列表"""
        if not stocks:
            content = "📭 当前没有监控的股票"
        else:
            content = "📊 **监控列表:**\n\n"
            content += "\n".join(f"{i+1}. {s['name']} ({s['code']})" for i, s in enumerate(stocks))
        
        self.send_card("监控股票列表", content, "blue")


# ===== 股票监控器 =====
class StockMonitor:
    def __init__(self, db: Database, notifier: FeishuNotifier, config: Config):
        self.db = db
        self.notifier = notifier
        self.config = config
        self.running = False
        self.alert_cooldown = {}  # 预警冷却时间（避免频繁提醒）
    
    def check_alert_cooldown(self, code: str, alert_type: str) -> bool:
        """检查预警冷却时间（30分钟内同类型预警只发一次）"""
        key = f"{code}:{alert_type}"
        now = time.time()
        
        if key in self.alert_cooldown:
            if now - self.alert_cooldown[key] < 1800:  # 30分钟
                return False
        
        self.alert_cooldown[key] = now
        return True
    
    def monitor_single_stock(self, stock: Dict):
        """监控单只股票"""
        code = stock["code"]
        name = stock["name"]
        
        # 获取实时数据
        data = StockDataFetcher.get_stock_data(code)
        if not data or data["price"] == 0:
            return
        
        # 保存价格记录
        self.db.add_price_record(code, data["price"], data["volume"])
        
        # 获取历史数据
        price_history = self.db.get_price_history(code, 20)
        volume_history = self.db.get_volume_history(code, 5)
        
        if len(price_history) < 7:
            return  # 数据不足
        
        # 计算技术指标
        rsi = TechnicalAnalysis.calculate_rsi(price_history, self.config.RSI_PERIOD)
        volume_ratio = TechnicalAnalysis.calculate_volume_ratio(volume_history)
        change_pct = (data["price"] - data["pre_close"]) / data["pre_close"] * 100
        
        # 异动检测
        alerts = []
        
        # 1. RSI超买/超卖
        if rsi is not None:
            if rsi > self.config.RSI_OVERBOUGHT:
                if self.check_alert_cooldown(code, "rsi_overbought"):
                    alerts.append(f"⚠️ RSI({self.config.RSI_PERIOD}) = {rsi:.1f} （超买）")
            elif rsi < self.config.RSI_OVERSOLD:
                if self.check_alert_cooldown(code, "rsi_oversold"):
                    alerts.append(f"✅ RSI({self.config.RSI_PERIOD}) = {rsi:.1f} （超卖）")
        
        # 2. 涨跌幅异常
        if abs(change_pct) > self.config.PRICE_CHANGE_THRESHOLD:
            if self.check_alert_cooldown(code, "price_change"):
                emoji = "🚀" if change_pct > 0 else "💥"
                alerts.append(f"{emoji} 日内波动 {change_pct:+.2f}%")
        
        # 3. 成交量放大
        if volume_ratio and volume_ratio > self.config.VOLUME_RATIO_THRESHOLD:
            if self.check_alert_cooldown(code, "volume_spike"):
                alerts.append(f"📊 量比 {volume_ratio:.1f}x （成交量放大）")
        
        # 发送提醒
        if alerts:
            self.notifier.send_alert(name, code, alerts, data)
            print(f"[ALERT] {datetime.now().strftime('%H:%M:%S')} {name} 触发 {len(alerts)} 个预警")
    
    def check_all_stocks(self):
        """检查所有股票一次"""
        try:
            stocks = self.db.get_all_stocks()
            if not stocks:
                print("[INFO] 没有监控的股票，等待添加...")
            else:
                print(f"[INFO] {datetime.now().strftime('%H:%M:%S')} 开始检查 {len(stocks)} 只股票...")
                for stock in stocks:
                    self.monitor_single_stock(stock)
        except Exception as e:
            print(f"[ERROR] 监控检查异常: {e}")

    def monitor_loop(self):
        """监控主循环"""
        while self.running:
            self.check_all_stocks()
            time.sleep(self.config.CHECK_INTERVAL)
    
    def start(self):
        """启动监控"""
        if self.running:
            return
        
        self.running = True
        thread = threading.Thread(target=self.monitor_loop, daemon=True)
        thread.start()
        print("🚀 股票监控已启动")
    
    def stop(self):
        """停止监控"""
        self.running = False
        print("🛑 股票监控已停止")


# ===== 命令处理器 =====
class CommandHandler:
    def __init__(self, db: Database, notifier: FeishuNotifier, monitor: StockMonitor, config: Config):
        self.db = db
        self.notifier = notifier
        self.monitor = monitor
        self.config = config
    
    def handle_add(self, code: str) -> str:
        """添加股票"""
        data = StockDataFetcher.get_stock_data(code)
        if not data:
            return f"❌ 未找到股票: {code}"
        
        normalized_code = StockDataFetcher.normalize_code(code)
        if self.db.add_stock(normalized_code, data["name"]):
            return f"✅ 已添加: {data['name']} ({normalized_code})\n当前价: {data['price']}"
        else:
            return "❌ 添加失败"
    
    def handle_remove(self, code: str) -> str:
        """移除股票"""
        normalized_code = StockDataFetcher.normalize_code(code)
        self.db.remove_stock(normalized_code)
        return f"✅ 已移除: {code}"
    
    def handle_list(self) -> str:
        """查看列表"""
        stocks = self.db.get_all_stocks()
        if not stocks:
            return "📭 当前没有监控的股票"
        
        result = "📊 **监控列表:**\n\n"
        result += "\n".join(f"{i+1}. {s['name']} ({s['code']})" for i, s in enumerate(stocks))
        return result
    
    def handle_status(self) -> str:
        """查看状态"""
        stocks = self.db.get_all_stocks()
        status = "🟢 运行中" if self.monitor.running else "🔴 已停止"
        return f"📊 监控状态: {status}\n📈 监控股票: {len(stocks)}只"
    
    def handle_config(self) -> str:
        """查看监控条件"""
        return f"""⚙️ **当前监控条件:**

🔄 检查间隔: {self.config.CHECK_INTERVAL}秒
📊 RSI周期: {self.config.RSI_PERIOD}
⚠️ RSI超买: >{self.config.RSI_OVERBOUGHT}
✅ RSI超卖: <{self.config.RSI_OVERSOLD}
📈 涨跌幅预警: ±{self.config.PRICE_CHANGE_THRESHOLD}%
💹 量比预警: >{self.config.VOLUME_RATIO_THRESHOLD}倍

💡 修改方法: @我 改间隔 30"""
    
    def handle_set_interval(self, interval: int) -> str:
        """修改检查间隔"""
        if interval < 10 or interval > 600:
            return "❌ 间隔应在 10-600 秒之间"
        self.config.CHECK_INTERVAL = interval
        return f"✅ 检查间隔已改为: {interval}秒"
    
    def handle_set_rsi(self, overbought: int = None, oversold: int = None) -> str:
        """修改RSI阈值"""
        if overbought:
            if overbought < 70 or overbought > 90:
                return "❌ RSI超买应在 70-90 之间"
            self.config.RSI_OVERBOUGHT = overbought
        
        if oversold:
            if oversold < 10 or oversold > 30:
                return "❌ RSI超卖应在 10-30 之间"
            self.config.RSI_OVERSOLD = oversold
        
        return f"✅ RSI阈值已更新\n超买: {self.config.RSI_OVERBOUGHT}\n超卖: {self.config.RSI_OVERSOLD}"
    
    def handle_help(self) -> str:
        """帮助信息"""
        return """📖 **命令帮助:**

**添加/删除股票**
• @我 add 600519
• @我 remove 600519
• @我 list（查看列表）

**查看/修改配置**
• @我 config（查看当前配置）
• @我 改间隔 30（修改检查间隔）
• @我 改超买 85（修改RSI超买）
• @我 改超卖 15（修改RSI超卖）

**其他**
• @我 status（查看状态）
• @我 help（查看帮助）

💡 支持的股票代码: 600519, 000001, 300750 等"""
    
    def parse_command(self, text: str) -> str:
        """解析并执行命令"""
        text = text.strip().lower()
        parts = text.split()
        
        if not parts:
            return self.handle_help()
        
        cmd = parts[0]
        
        # 添加股票
        if cmd == "add" and len(parts) > 1:
            return self.handle_add(parts[1])
        
        # 移除股票
        elif cmd == "remove" and len(parts) > 1:
            return self.handle_remove(parts[1])
        
        # 查看列表
        elif cmd == "list":
            return self.handle_list()
        
        # 查看状态
        elif cmd == "status":
            return self.handle_status()
        
        # 查看配置
        elif cmd == "config":
            return self.handle_config()
        
        # 修改间隔
        elif cmd in ["改间隔", "间隔"] and len(parts) > 1:
            try:
                return self.handle_set_interval(int(parts[1]))
            except ValueError:
                return "❌ 请输入有效的数字"
        
        # 修改RSI
        elif cmd in ["改超买", "超买"] and len(parts) > 1:
            try:
                return self.handle_set_rsi(overbought=int(parts[1]))
            except ValueError:
                return "❌ 请输入有效的数字"
        
        elif cmd in ["改超卖", "超卖"] and len(parts) > 1:
            try:
                return self.handle_set_rsi(oversold=int(parts[1]))
            except ValueError:
                return "❌ 请输入有效的数字"
        
        # 帮助
        elif cmd in ["help", "帮助", "?"]:
            return self.handle_help()
        
        else:
            return f"❓ 未知命令: {cmd}\n\n发送 @我 help 查看帮助"


# ===== 飞书消息接收服务器 =====
class FeishuWebhookHandler(BaseHTTPRequestHandler):
    """处理飞书事件回调"""
    
    command_handler = None  # 将由外部设置
    notifier = None
    config = None
    
    def do_POST(self):
        """处理POST请求"""
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        
        try:
            data = json.loads(post_data.decode('utf-8'))
            
            # URL验证
            if data.get("type") == "url_verification":
                challenge = data.get("challenge", "")
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"challenge": challenge}).encode())
                return
            
            # 处理消息事件
            if data.get("header", {}).get("event_type") == "im.message.receive_v1":
                event = data.get("event", {})
                message = event.get("message", {})
                
                # 只处理文本消息
                if message.get("message_type") == "text":
                    content = json.loads(message.get("content", "{}"))
                    text = content.get("text", "").strip()
                    message_id = message.get("message_id", "")
                    
                    # 移除 @机器人 的部分
                    text = text.replace("@_user_1", "").strip()
                    
                    print(f"[INFO] 收到消息: {text}")
                    
                    # 处理命令
                    if self.command_handler:
                        response = self.command_handler.parse_command(text)
                        
                        # 回复消息
                        if response and self.notifier:
                            self.notifier.reply_message(message_id, response)
            
            # 响应成功
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"code": 0}).encode())
        
        except Exception as e:
            print(f"[ERROR] 处理消息失败: {e}")
            self.send_response(500)
            self.end_headers()
    
    def log_message(self, format, *args):
        """禁用默认日志"""
        pass


def start_webhook_server(handler: CommandHandler, notifier: FeishuNotifier, config: Config):
    """启动Webhook服务器"""
    FeishuWebhookHandler.command_handler = handler
    FeishuWebhookHandler.notifier = notifier
    FeishuWebhookHandler.config = config
    
    server = HTTPServer(('0.0.0.0', config.HTTP_PORT), FeishuWebhookHandler)
    print(f"🌐 Webhook服务器启动: http://0.0.0.0:{config.HTTP_PORT}")
    
    # 在后台线程运行
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    
    return server


import sys

# ===== 主程序 =====
def main():
    print("=" * 50)
    print("🤖 飞书股票监控机器人 v1.0")
    print("=" * 50)
    
    # 检查命令行参数
    is_once = "--once" in sys.argv
    
    # 初始化组件
    config = Config()
    
    # 检查 Webhook 是否配置
    if not config.FEISHU_WEBHOOK:
        print("❌ 错误: 未配置 FEISHU_WEBHOOK")
        print("💡 请设置环境变量 FEISHU_WEBHOOK，或在 Secrets 中配置")
        if is_once: return
        sys.exit(1)
        
    db = Database(config.DB_PATH)
    notifier = FeishuNotifier(config.FEISHU_WEBHOOK, config.FEISHU_APP_ID, config.FEISHU_APP_SECRET)
    monitor = StockMonitor(db, notifier, config)
    handler = CommandHandler(db, notifier, monitor, config)
    
    # 如果是单次运行模式（用于 GitHub Actions）
    if is_once:
        print("🚀 单次运行模式启动...")
        monitor.check_all_stocks()
        print("✅ 单次检查完成")
        return

    # 启动监控
    monitor.start()
    
    # 启动 Webhook 服务器（如果配置了 APP 凭证）
    webhook_server = None
    if config.FEISHU_APP_ID and config.FEISHU_APP_SECRET:
        try:
            webhook_server = start_webhook_server(handler, notifier, config)
            print("✅ 飞书交互模式已启用")
        except Exception as e:
            print(f"⚠️  Webhook服务器启动失败: {e}")
            print("   将继续使用命令行模式")
    else:
        print("💡 提示: 配置 FEISHU_APP_ID 和 FEISHU_APP_SECRET 可启用飞书交互")
    
    # 发送启动消息
    notifier.send_card(
        "🤖 机器人已启动",
        f"""股票监控机器人已成功启动！

⏱️ 检查间隔: {config.CHECK_INTERVAL}秒
📊 预警条件:
• RSI超买: >{config.RSI_OVERBOUGHT}
• RSI超卖: <{config.RSI_OVERSOLD}
• 涨跌幅: >{config.PRICE_CHANGE_THRESHOLD}%
• 量比: >{config.VOLUME_RATIO_THRESHOLD}x

💡 **飞书交互命令:**
在群里 @我 + 命令，例如：
• @我 add 600519（添加股票）
• @我 list（查看列表）
• @我 config（查看配置）
• @我 help（查看帮助）""",
        "blue"
    )
    
    # 交互式命令行
    print("\n💡 命令列表:")
    print("  add 600519        - 添加监控股票")
    print("  remove 600519     - 移除监控股票")
    print("  list              - 查看监控列表")
    print("  status            - 查看运行状态")
    print("  quit              - 退出程序\n")
    
    while True:
        try:
            cmd = input(">>> ").strip().split()
            if not cmd:
                continue
            
            command = cmd[0].lower()
            
            if command == "add" and len(cmd) > 1:
                result = handler.handle_add(cmd[1])
                print(result)
            
            elif command == "remove" and len(cmd) > 1:
                result = handler.handle_remove(cmd[1])
                print(result)
            
            elif command == "list":
                handler.handle_list()
            
            elif command == "status":
                result = handler.handle_status()
                print(result)
            
            elif command == "quit":
                monitor.stop()
                print("👋 再见！")
                break
            
            else:
                print("❌ 未知命令")
        
        except KeyboardInterrupt:
            monitor.stop()
            print("\n👋 再见！")
            break
        except Exception as e:
            print(f"❌ 错误: {e}")


if __name__ == "__main__":
    main()
