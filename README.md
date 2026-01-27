# 🤖 StockSentinel - 飞书股票监控机器人

实时监控股票异动，自动发送飞书提醒，支持@机器人交互

## ✨ 功能

- 📊 **实时监控** - RSI超买超卖、涨跌幅异常、成交量放大
- 💬 **飞书提醒** - 美观的卡片消息推送
- 🤖 **@机器人** - 群里@机器人添加股票、查看/修改配置
- 🗄️ **数据存储** - SQLite 保存监控列表和历史数据
- ⏰ **防骚扰** - 30分钟冷却，避免重复提醒

## 🚀 快速部署

### 本地测试

```bash
# 1. 克隆或下载代码
git clone https://github.com/你的用户名/StockSentinel.git
cd StockSentinel

# 2. 安装依赖
pip install requests

# 3. 配置 Webhook（编辑第11行）
nano feishu_stock_bot.py
# 修改：FEISHU_WEBHOOK = "你的飞书webhook"

# 4. 运行
python3 feishu_stock_bot.py

# 5. 添加股票
>>> add 600519
>>> list
```

**获取 Webhook：** 飞书群聊 → 设置 → 群机器人 → 自定义机器人

---

### 🌐 服务器部署（推荐）

#### 前提条件
- 树莓派/NAS/旧电脑/Linux服务器
- 有 Python 3.7+ 环境

#### Step 1: 本地推送到 GitHub

```bash
# 在本地项目目录
cd /Users/lyralei/Desktop/repository/mine/StockSentinel

# 初始化 Git
git init
git add .
git commit -m "Initial commit"

# 创建 GitHub 仓库后推送（替换为你的仓库地址）
git remote add origin https://github.com/你的用户名/StockSentinel.git
git push -u origin main
```

💡 **创建仓库：** https://github.com/new （建议选 Private）

#### Step 2: 服务器克隆运行

```bash
# 1. SSH 连接服务器
ssh pi@树莓派IP
# 或 ssh 用户名@服务器IP

# 2. 克隆仓库
git clone https://github.com/你的用户名/StockSentinel.git
cd StockSentinel

# 3. 安装依赖
pip3 install requests

# 4. 配置 Webhook
nano feishu_stock_bot.py
# 修改第11行，保存退出（Ctrl+O, Enter, Ctrl+X）

# 5. 后台运行
nohup python3 feishu_stock_bot.py > bot.log 2>&1 &

# 6. 查看日志
tail -f bot.log
```

✅ **完成！** 机器人在服务器上 7×24 小时运行

#### 管理命令

```bash
# 查看运行状态
ps aux | grep feishu_stock_bot

# 停止
pkill -f feishu_stock_bot.py

# 重启
pkill -f feishu_stock_bot.py
nohup python3 feishu_stock_bot.py > bot.log 2>&1 &
```

#### 开机自启（可选）

```bash
# 创建服务
sudo nano /etc/systemd/system/stock-bot.service
```

写入：

```ini
[Unit]
Description=StockSentinel Bot
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/StockSentinel
ExecStart=/usr/bin/python3 /home/pi/StockSentinel/feishu_stock_bot.py
Restart=always

[Install]
WantedBy=multi-user.target
```

启动：

```bash
sudo systemctl daemon-reload
sudo systemctl start stock-bot
sudo systemctl enable stock-bot  # 开机自启
sudo systemctl status stock-bot  # 查看状态
```

---

### ☁️ GitHub Actions 部署 (完全免费)

利用 GitHub 免费的虚拟服务器定时运行（无需自己有服务器）。

#### 1. 配置 Secrets (密钥)
进入 GitHub 仓库 → `Settings` → `Secrets and variables` → `Actions` → `New repository secret`：
- Name: `FEISHU_WEBHOOK`
- Value: `你的飞书Webhook地址`

#### 2. 配置 Variables (变量)
点击 `Variables` 标签页 → `New repository variable`：
- Name: `STOCK_LIST`
- Value: `600519,000001,300750` (用逗号分隔股票代码)

#### 3. 启用
进入 `Actions` 标签页，启用 Workflow。它会在每个交易日 9:30-15:00 每20分钟运行一次。

⚠️ **注意**：GitHub Actions 模式下不支持 @机器人 交互，只能通过修改 `STOCK_LIST` 变量来管理股票。

---

---

## 🤖 飞书交互命令

配置应用后，可在群里 @机器人 操作

### 快速配置

1. **创建飞书应用** → https://open.feishu.cn/
2. **获取凭证** → App ID + App Secret
3. **配置权限** → `im:message` + `im:message:send_as_bot`
4. **事件订阅** → `http://你的IP:8080` + 订阅 `im.message.receive_v1`
5. **修改代码** → 填入 APP_ID 和 APP_SECRET（第12-13行）
6. **添加机器人到群**

### 支持的命令

| 命令 | 说明 | 示例 |
|-----|------|------|
| `add 代码` | 添加监控 | `@机器人 add 600519` |
| `remove 代码` | 移除监控 | `@机器人 remove 600519` |
| `list` | 查看列表 | `@机器人 list` |
| `config` | 查看配置 | `@机器人 config` |
| `改间隔 数字` | 修改检查间隔 | `@机器人 改间隔 30` |
| `改超买 数字` | 修改RSI超买 | `@机器人 改超买 85` |
| `改超卖 数字` | 修改RSI超卖 | `@机器人 改超卖 15` |
| `status` | 查看状态 | `@机器人 status` |
| `help` | 查看帮助 | `@机器人 help` |

💡 **没有公网IP？** 使用 ngrok 内网穿透

---

## 📊 预警条件

| 指标 | 默认值 | 说明 |
|-----|--------|------|
| RSI(6) | >80 或 <20 | 超买/超卖信号 |
| 涨跌幅 | ±5% | 日内波动过大 |
| 量比 | >2倍 | 成交量异常放大 |

## 🔧 批量添加股票

编辑 `init_stocks.py`：

```python
stocks = ["600519", "000001", "300750"]  # 你的股票列表
```

运行：`python init_stocks.py`

---

## 📱 消息示例

```
【股票异动提醒】

贵州茅台 (sh600519)
📈 当前价: 1850.00 (+5.25%)
📊 今日: 开 1820 | 高 1865 | 低 1815
💰 成交额: 125600万

⚠️ 异动信号:
• 🚀 日内波动 +5.25%
• 📊 量比 2.3x （成交量放大）

⏰ 2026-01-27 14:35:20
```

---

## 💰 成本对比

| 方案 | 硬件成本 | 月电费 | 优势 |
|-----|---------|--------|------|
| 树莓派 | ¥400（一次性） | ¥2 | 低功耗，7×24运行 |
| 旧电脑/NAS | 免费（已有） | ¥5-10 | 废物利用 |
| 本地电脑 | 免费 | - | 临时测试 |

---

## 🔄 更新代码

```bash
# 本地修改后
git add .
git commit -m "更新功能"
git push

# 服务器更新
ssh pi@服务器IP
cd stock-monitor
git pull
pkill -f feishu_stock_bot.py
nohup python3 feishu_stock_bot.py > bot.log 2>&1 &
```

---

## 🔍 常见问题

**Q: 支持哪些股票？**  
A: A股（沪深京），格式：`600519`、`000001`、`300750`

**Q: @机器人 不回复？**  
A: 检查是否配置 APP_ID、APP_SECRET 和事件订阅

**Q: 没有服务器怎么办？**  
A: 
- 用树莓派（¥400，推荐）
- 用家里旧电脑/笔记本
- 用 NAS
- 本地电脑临时运行

**Q: 没有公网IP？**  
A: 使用 ngrok 内网穿透：
```bash
ngrok http 8080
# 复制生成的地址到飞书事件订阅
```

**Q: 如何查看运行状态？**  
A: 
```bash
ssh 服务器IP
ps aux | grep feishu_stock_bot  # 查看进程
tail -f ~/stock-monitor/bot.log  # 查看日志
```

---

## 📁 项目结构

```
stock-monitor/
├── feishu_stock_bot.py    # 主程序
├── init_stocks.py         # 批量添加
├── requirements.txt       # 依赖
├── .gitignore            # Git忽略文件
├── Dockerfile            # Docker配置
├── docker-compose.yml    # Docker Compose
└── README.md             # 本文档
```

---

## 📄 License

MIT License

---

**⭐ 如果觉得有用，请给个 Star！**

⚠️ 免责声明：仅供学习参考，不构成投资建议
