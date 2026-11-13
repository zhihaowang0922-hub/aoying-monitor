#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
币安带单员【熬鹰资本】(Portfolio ID: 5075281354358777856) 云端/本地双用监控程序
功能：
1. 自动检测最新开平仓操作，并实时推送到 Telegram
2. 内置轻量级 HTTP 健康检查服务，完美适配 Render / Zeabur 等免费云端平台 24 小时挂机
"""

import json
import os
import random
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    import requests
except ImportError:
    print("正在自动安装必要组件 requests，请稍候...")
    os.system(f'"{sys.executable}" -m pip install requests')
    import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.ini")
STATE_FILE = os.path.join(BASE_DIR, "monitor_state.json")

DEFAULT_PORTFOLIO_ID = "5075281354358777856"
ORDER_HISTORY_URL = (
    "https://www.binance.com/bapi/futures/v1/friendly/future/copy-trade/lead-data/order-history"
)
POSITIONS_URL = (
    "https://www.binance.com/bapi/futures/v1/friendly/future/copy-trade/lead-data/positions"
)

STATUS_INFO = {
    "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "last_check": "初始化中...",
    "status": "starting",
    "orders_pushed": 0,
    "last_error": "",
}


class HealthHandler(BaseHTTPRequestHandler):
    """为免费云平台（Render / Zeabur 等）和 UptimeRobot 保活提供网页状态响应"""

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>熬鹰资本监控状态</title></head>
<body style="font-family: sans-serif; max-width: 520px; margin: 40px auto; line-height: 1.6;">
  <h2>✅ 【熬鹰资本】币安带单监控运行中</h2>
  <p><b>运行状态：</b>{STATUS_INFO['status']}</p>
  <p><b>启动时间：</b>{STATUS_INFO['started_at']} (UTC)</p>
  <p><b>最近检测：</b>{STATUS_INFO['last_check']} (UTC)</p>
  <p><b>累计推送新单：</b>{STATUS_INFO['orders_pushed']} 条</p>
  <p style="color:#666;font-size:13px;">{STATUS_INFO['last_error']}</p>
</body>
</html>"""
        self.wfile.write(html.encode("utf-8"))

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        # 屏蔽高频保活访问日志，保持后台日志清爽
        return


def start_health_server():
    """在后台线程启动 HTTP 服务，满足云平台端口绑定要求"""
    port_str = os.environ.get("PORT", "").strip()
    if not port_str:
        return
    try:
        port = int(port_str)
        server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        print(f"[云端健康检查服务] 已监听端口 0.0.0.0:{port}")
    except Exception as e:
        print(f"[健康检查服务启动失败] {e}")


def is_cloud_env() -> bool:
    return bool(
        os.environ.get("PORT")
        or os.environ.get("RENDER")
        or os.environ.get("ZEABUR")
        or os.environ.get("RAILWAY_ENVIRONMENT")
    )


def load_config() -> dict:
    """读取环境变量及本地配置文件"""
    cfg = {
        "TELEGRAM_BOT_TOKEN": os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(),
        "TELEGRAM_CHAT_ID": os.environ.get("TELEGRAM_CHAT_ID", "").strip(),
        "PORTFOLIO_ID": os.environ.get("PORTFOLIO_ID", DEFAULT_PORTFOLIO_ID).strip() or DEFAULT_PORTFOLIO_ID,
        "POLL_INTERVAL": os.environ.get("POLL_INTERVAL", "8").strip() or "8",
        "BINANCE_COOKIE": os.environ.get("BINANCE_COOKIE", "").strip(),
        "BINANCE_CSRF_TOKEN": os.environ.get("BINANCE_CSRF_TOKEN", "").strip(),
        "PROXY": os.environ.get("PROXY", "").strip(),
    }
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k in cfg and v and not cfg[k]:
                    cfg[k] = v
    return cfg


def save_config(cfg: dict) -> None:
    if is_cloud_env():
        return
    content = (
        "# 熬鹰资本币安带单监控配置文件
"
        f"TELEGRAM_BOT_TOKEN={cfg.get('TELEGRAM_BOT_TOKEN', '')}
"
        f"TELEGRAM_CHAT_ID={cfg.get('TELEGRAM_CHAT_ID', '')}
"
        f"PORTFOLIO_ID={cfg.get('PORTFOLIO_ID', DEFAULT_PORTFOLIO_ID)}
"
        f"POLL_INTERVAL={cfg.get('POLL_INTERVAL', '8')}
"
        f"PROXY={cfg.get('PROXY', '')}
"
        f"BINANCE_COOKIE={cfg.get('BINANCE_COOKIE', '')}
"
        f"BINANCE_CSRF_TOKEN={cfg.get('BINANCE_CSRF_TOKEN', '')}
"
    )
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception:
        pass


def get_proxies(cfg: dict):
    proxy = cfg.get("PROXY", "").strip()
    if proxy:
        return {"http": proxy, "https": proxy}
    return None


def auto_detect_chat_id(token: str, proxies=None, endless: bool = False) -> str:
    """自动通过 Telegram getUpdates 获取用户的 Chat ID"""
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    print("请在 Telegram 中打开你创建的机器人，点击【开始 / Start】并随便发送一条消息（例如 1）...")
    STATUS_INFO["status"] = "等待你在 Telegram 给机器人发消息以完成绑定..."

    attempts = 0
    while True:
        attempts += 1
        try:
            resp = requests.get(url, timeout=10, proxies=proxies)
            data = resp.json()
            if not data.get("ok"):
                err = data.get("description", "")
                print(f"Token 验证失败: {err}")
                STATUS_INFO["last_error"] = f"Token 无效: {err}"
                if not endless:
                    return ""
            else:
                results = data.get("result", [])
                for item in reversed(results):
                    msg = item.get("message") or item.get("channel_post") or {}
                    chat = msg.get("chat") or {}
                    chat_id = chat.get("id")
                    if chat_id:
                        return str(chat_id)
        except Exception as e:
            if attempts == 1:
                print(f"等待连接 Telegram: {e}")
        if not endless and attempts >= 45:
            return ""
        time.sleep(3)


def prepare_config(cfg: dict) -> dict:
    """兼容云端无界面环境与本地双击交互"""
    token = cfg.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = cfg.get("TELEGRAM_CHAT_ID", "").strip()
    proxies = get_proxies(cfg)

    if is_cloud_env():
        while not token:
            STATUS_INFO["status"] = "缺少环境变量 TELEGRAM_BOT_TOKEN"
            print("[配置提示] 请在云平台环境变量中填入 TELEGRAM_BOT_TOKEN 后重启服务。")
            time.sleep(30)
            token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
            cfg["TELEGRAM_BOT_TOKEN"] = token

        if not chat_id:
            chat_id = auto_detect_chat_id(token, proxies=proxies, endless=True)
            cfg["TELEGRAM_CHAT_ID"] = chat_id
            print(f"✅ 已自动绑定你的 Telegram Chat ID: {chat_id}")
        return cfg

    # 本地电脑交互模式
    if not token:
        print("=" * 52)
        print("   欢迎使用【熬鹰资本】币安带单 Telegram 监控程序")
        print("=" * 52)
        token = input("
请粘贴你的 Telegram Bot Token 后按回车键: ").strip()
        if not token:
            sys.exit(1)
        cfg["TELEGRAM_BOT_TOKEN"] = token

    if not chat_id:
        chat_id = auto_detect_chat_id(token, proxies=proxies, endless=False)
        if not chat_id:
            chat_id = input("未自动检测到消息，请手动输入 Chat ID: ").strip()
            if not chat_id:
                sys.exit(1)
        cfg["TELEGRAM_CHAT_ID"] = chat_id

    save_config(cfg)
    return cfg


def send_telegram(cfg: dict, text: str) -> bool:
    url = f"https://api.telegram.org/bot{cfg['TELEGRAM_BOT_TOKEN']}/sendMessage"
    payload = {
        "chat_id": cfg["TELEGRAM_CHAT_ID"],
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=12, proxies=get_proxies(cfg))
        if resp.ok:
            return True
        print(f"[Telegram 发送失败] HTTP {resp.status_code}: {resp.text}")
        return False
    except Exception as e:
        print(f"[Telegram 网络异常] {e}")
        return False


def load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {"seen_orders": [], "positions": {}}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                data.setdefault("seen_orders", [])
                data.setdefault("positions", {})
                return data
    except Exception:
        pass
    return {"seen_orders": [], "positions": {}}


def save_state(state: dict) -> None:
    state["seen_orders"] = state.get("seen_orders", [])[-500:]
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def build_headers(cfg: dict) -> dict:
    pid = cfg["PORTFOLIO_ID"]
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Content-Type": "application/json",
        "Origin": "https://www.binance.com",
        "Referer": f"https://www.binance.com/zh-CN/copy-trading/lead-details/{pid}",
        "clienttype": "web",
        "lang": "zh-CN",
    }
    if cfg.get("BINANCE_COOKIE"):
        headers["Cookie"] = cfg["BINANCE_COOKIE"]
    if cfg.get("BINANCE_CSRF_TOKEN"):
        headers["csrftoken"] = cfg["BINANCE_CSRF_TOKEN"]
    return headers


def fetch_order_history(cfg: dict, page_size: int = 20) -> list:
    payload = {
        "portfolioId": cfg["PORTFOLIO_ID"],
        "pageNumber": 1,
        "pageSize": page_size,
    }
    resp = requests.post(
        ORDER_HISTORY_URL,
        headers=build_headers(cfg),
        json=payload,
        timeout=12,
        proxies=get_proxies(cfg),
    )
    if resp.status_code == 451:
        raise RuntimeError("HTTP 451: 当前云服务器所在国家/地区受币安限制，请更换为德国(Frankfurt)等欧洲节点！")
    resp.raise_for_status()
    res = resp.json()
    if not res.get("success", False):
        raise RuntimeError(f"币安操作记录接口返回异常: {res}")
    data = res.get("data") or {}
    if isinstance(data, list):
        return data
    return data.get("list") or data.get("rows") or []


def fetch_positions(cfg: dict) -> dict:
    resp = requests.get(
        POSITIONS_URL,
        headers=build_headers(cfg),
        params={"portfolioId": cfg["PORTFOLIO_ID"]},
        timeout=12,
        proxies=get_proxies(cfg),
    )
    if resp.status_code == 451:
        raise RuntimeError("HTTP 451: 当前云服务器所在国家/地区受币安限制，请更换为德国(Frankfurt)等欧洲节点！")
    resp.raise_for_status()
    res = resp.json()
    if not res.get("success", False):
        raise RuntimeError(f"币安持仓接口返回异常: {res}")
    raw_list = res.get("data") or []
    positions = {}
    for item in raw_list:
        amt = float(item.get("positionAmount", 0))
        if amt == 0:
            continue
        symbol = item.get("symbol", "UNKNOWN")
        pos_side = item.get("positionSide", "BOTH")
        direction = ("LONG" if amt > 0 else "SHORT") if pos_side == "BOTH" else pos_side
        positions[f"{symbol}_{direction}"] = {
            "symbol": symbol,
            "direction": direction,
            "amount": abs(amt),
            "entryPrice": float(item.get("entryPrice", 0)),
            "leverage": item.get("leverage", "?"),
            "unrealizedProfit": float(item.get("unrealizedProfit", 0)),
        }
    return positions


def format_action(side: str, position_side: str) -> str:
    side = (side or "").upper()
    pos = (position_side or "BOTH").upper()
    mapping = {
        ("BUY", "LONG"): "🟢 开多 (Open Long)",
        ("SELL", "LONG"): "💰 平多 (Close Long)",
        ("SELL", "SHORT"): "🔴 开空 (Open Short)",
        ("BUY", "SHORT"): "💰 平空 (Close Short)",
        ("BUY", "BOTH"): "🟢 买入 / 做多 (BUY)",
        ("SELL", "BOTH"): "🔴 卖出 / 做空 (SELL)",
    }
    return mapping.get((side, pos), f"{side} ({pos})")


def format_ts(ts) -> str:
    if not ts:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        val = float(ts)
        if val > 1e12:
            val /= 1000.0
        return datetime.fromtimestamp(val).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ts)


def order_uid(order: dict) -> str:
    oid = order.get("orderId") or order.get("id") or order.get("tranId")
    qty = order.get("executedQty") or order.get("origQty") or order.get("qty") or ""
    if oid:
        return f"{oid}_{qty}"
    return (
        f"{order.get('symbol')}_{order.get('side')}_"
        f"{order.get('positionSide')}_{order.get('orderTime')}_{qty}"
    )


def format_order_msg(order: dict) -> str:
    symbol = order.get("symbol", "UNKNOWN")
    action_str = format_action(order.get("side"), order.get("positionSide"))
    price = order.get("avgPrice") or order.get("price") or "市价"
    qty = order.get("executedQty") or order.get("origQty") or order.get("qty") or "?"
    t_str = format_ts(order.get("updateTime") or order.get("orderTime") or order.get("time"))

    lines = [
        "<b>【熬鹰资本 · 最新操作通知】</b>",
        f"动作：{action_str}",
        f"合约：<code>{symbol}</code>",
        f"成交数量：{qty}",
        f"成交均价：{price}",
    ]
    pnl = order.get("realizedPnl") or order.get("pnl")
    if pnl is not None:
        try:
            pnl_val = float(pnl)
            if pnl_val != 0:
                emoji = "📈" if pnl_val > 0 else "📉"
                lines.append(f"实现盈亏：{emoji} <b>{pnl_val:+.2f} USDT</b>")
        except Exception:
            pass
    lines.append(f"时间：{t_str} (UTC)")
    return "
".join(lines)


def diff_positions(old_pos: dict, new_pos: dict) -> list:
    msgs = []
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for key in sorted(set(old_pos.keys()) | set(new_pos.keys())):
        prev = old_pos.get(key)
        curr = new_pos.get(key)
        if prev is None and curr is not None:
            side_cn = "🟢 开多 (LONG)" if curr["direction"] == "LONG" else "🔴 开空 (SHORT)"
            msgs.append(
                f"<b>【熬鹰资本 · 新开仓】</b>
"
                f"合约：<code>{curr['symbol']}</code>
"
                f"方向：{side_cn} ({curr['leverage']}x)
"
                f"持仓数量：{curr['amount']}
"
                f"开仓均价：{curr['entryPrice']}
"
                f"时间：{now_str}"
            )
        elif prev is not None and curr is None:
            side_cn = "多单 (LONG)" if prev["direction"] == "LONG" else "空单 (SHORT)"
            msgs.append(
                f"<b>【熬鹰资本 · 全部平仓】</b>
"
                f"合约：<code>{prev['symbol']}</code>
"
                f"原方向：{side_cn}
"
                f"平仓前数量：{prev['amount']}
"
                f"原开仓均价：{prev['entryPrice']}
"
                f"时间：{now_str}"
            )
        elif prev is not None and curr is not None:
            diff_amt = round(curr["amount"] - prev["amount"], 6)
            if abs(diff_amt) > 1e-8:
                act = "➕ 加仓" if diff_amt > 0 else "➖ 部分减仓"
                side_cn = "做多 (LONG)" if curr["direction"] == "LONG" else "做空 (SHORT)"
                msgs.append(
                    f"<b>【熬鹰资本 · {act}】</b>
"
                    f"合约：<code>{curr['symbol']}</code> ({side_cn})
"
                    f"数量变化：{prev['amount']} ➔ <b>{curr['amount']}</b> ({diff_amt:+g})
"
                    f"最新均价：{curr['entryPrice']}
"
                    f"时间：{now_str}"
                )
    return msgs


def main():
    start_health_server()
    cfg = load_config()
    cfg = prepare_config(cfg)

    try:
        poll_interval = max(5.0, float(cfg.get("POLL_INTERVAL", 8)))
    except Exception:
        poll_interval = 8.0

    state = load_state()
    seen_list = state.get("seen_orders", [])
    seen_set = set(seen_list)
    old_positions = state.get("positions", {})
    first_boot = not os.path.exists(STATE_FILE)

    print(f"正在连接币安接口，初始化【熬鹰资本 ({cfg['PORTFOLIO_ID']})】监控...")

    mode = "order_history"
    try:
        orders = fetch_order_history(cfg, page_size=20)
        if first_boot:
            for item in orders:
                uid = order_uid(item)
                if uid not in seen_set:
                    seen_set.add(uid)
                    seen_list.append(uid)
            state["seen_orders"] = seen_list
            save_state(state)

        if len(orders) == 0:
            pos = fetch_positions(cfg)
            if pos:
                mode = "positions"
                old_positions = pos
                state["positions"] = pos
                save_state(state)

        STATUS_INFO["status"] = "正常运行中 (24/7 云端监控)"
        STATUS_INFO["last_check"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        STATUS_INFO["last_error"] = ""

        startup_msg = (
            f"✅ <b>【熬鹰资本】云端监控已成功启动！</b>
"
            f"带单账号 ID：<code>{cfg['PORTFOLIO_ID']}</code>
"
            f"监控频率：每 {int(poll_interval)} 秒自动检测一次
"
            f"后续只要他有开多、开空、加减仓或平仓操作，这里会第一时间通知你。"
        )
        print("监控已成功启动！")
        send_telegram(cfg, startup_msg)

    except Exception as e:
        STATUS_INFO["status"] = "连接币安异常"
        STATUS_INFO["last_error"] = str(e)[:300]
        err_text = (
            f"⚠️ <b>【熬鹰资本监控】首次连接币安失败</b>
"
            f"错误详情：<code>{str(e)[:220]}</code>"
        )
        print(f"启动检测异常: {e}")
        send_telegram(cfg, err_text)

    consecutive_errors = 0

    while True:
        try:
            STATUS_INFO["last_check"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if mode == "order_history":
                orders = fetch_order_history(cfg, page_size=20)
                consecutive_errors = 0
                STATUS_INFO["status"] = "正常运行中 (24/7 云端监控)"
                STATUS_INFO["last_error"] = ""

                new_msgs = []
                for order in reversed(orders):
                    if "executedQty" in order and float(order.get("executedQty") or 0) <= 0:
                        continue
                    uid = order_uid(order)
                    if uid not in seen_set:
                        seen_set.add(uid)
                        seen_list.append(uid)
                        new_msgs.append(format_order_msg(order))

                for m in new_msgs:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] 推送新操作:
{m}
")
                    if send_telegram(cfg, m):
                        STATUS_INFO["orders_pushed"] += 1

                if new_msgs:
                    state["seen_orders"] = seen_list
                    save_state(state)
            else:
                new_positions = fetch_positions(cfg)
                consecutive_errors = 0
                STATUS_INFO["status"] = "正常运行中 (持仓变动监控)"
                STATUS_INFO["last_error"] = ""

                new_msgs = diff_positions(old_positions, new_positions)
                for m in new_msgs:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] 推送持仓变动:
{m}
")
                    if send_telegram(cfg, m):
                        STATUS_INFO["orders_pushed"] += 1
                if new_msgs:
                    state["positions"] = new_positions
                    save_state(state)
                old_positions = new_positions

        except Exception as e:
            consecutive_errors += 1
            STATUS_INFO["last_error"] = f"连续异常 #{consecutive_errors}: {str(e)[:200]}"
            print(f"[{datetime.now().strftime('%H:%M:%S')}] 轮询异常 (#{consecutive_errors}): {e}")
            if consecutive_errors == 5:
                send_telegram(
                    cfg,
                    f"⚠️ <b>【监控异常提醒】</b>
"
                    f"连续 5 次获取币安数据失败。
"
                    f"错误信息：<code>{str(e)[:200]}</code>",
                )

        time.sleep(poll_interval + random.uniform(-0.8, 1.2))


if __name__ == "__main__":
    main()
