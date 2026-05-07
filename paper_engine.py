import os
import json
from datetime import datetime

PAPER_PORTFOLIO_FILE = "paper_portfolio.json"
PAPER_TRADES_FILE = "paper_trades.json"

INITIAL_CAPITAL = 100000.0
MAX_OPEN_TRADES = 5
MAX_POSITION_PCT = 0.20


def load_json(path, default):
    if not os.path.exists(path):
        save_json(path, default)
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def get_portfolio(user_id):
    data = load_json(PAPER_PORTFOLIO_FILE, {})

    uid = str(user_id)
    if uid not in data:
        data[uid] = {
            "cash": INITIAL_CAPITAL,
            "realized_pnl": 0.0,
            "enabled": True,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        save_json(PAPER_PORTFOLIO_FILE, data)

    return data[uid]


def save_portfolio(user_id, portfolio):
    data = load_json(PAPER_PORTFOLIO_FILE, {})
    data[str(user_id)] = portfolio
    save_json(PAPER_PORTFOLIO_FILE, data)


def get_open_trades(user_id):
    trades = load_json(PAPER_TRADES_FILE, [])
    return [
        t for t in trades
        if t["user_id"] == user_id and t["status"] == "OPEN"
    ]


def calculate_risk_pct(setup):
    if setup.trade_score >= 8.8 and setup.risk_level == "منخفضة":
        return 0.02

    if setup.trade_score >= 8.0 and setup.risk_level in ["منخفضة", "متوسطة"]:
        return 0.015

    if setup.trade_score >= 7.5:
        return 0.01

    return 0.0


def paper_auto_buy(user_id, setup):
    portfolio = get_portfolio(user_id)

    if not portfolio.get("enabled", True):
        return None

    open_trades = get_open_trades(user_id)

    if len(open_trades) >= MAX_OPEN_TRADES:
        return None

    if any(t["symbol"] == setup.symbol for t in open_trades):
        return None

    if "صفقة ممتازة" not in setup.decision:
        return None

    if setup.risk_level not in ["منخفضة", "متوسطة"]:
        return None

    risk_pct = calculate_risk_pct(setup)

    if risk_pct <= 0:
        return None

    entry = float(setup.close)
    stop = float(setup.stop_loss)
    target = float(setup.target_1)

    risk_per_share = entry - stop

    if risk_per_share <= 0:
        return None

    risk_amount = INITIAL_CAPITAL * risk_pct
    shares_by_risk = int(risk_amount / risk_per_share)

    max_position_value = INITIAL_CAPITAL * MAX_POSITION_PCT
    shares_by_cap = int(max_position_value / entry)

    shares = min(shares_by_risk, shares_by_cap)

    if shares <= 0:
        return None

    cost = shares * entry

    if cost > portfolio["cash"]:
        shares = int(portfolio["cash"] / entry)
        cost = shares * entry

    if shares <= 0:
        return None

    trades = load_json(PAPER_TRADES_FILE, [])
    new_id = max([t.get("id", 0) for t in trades], default=0) + 1

    trade = {
        "id": new_id,
        "user_id": user_id,
        "symbol": setup.symbol,
        "entry": entry,
        "stop": stop,
        "target": target,
        "shares": shares,
        "cost": cost,
        "risk_amount": risk_amount,
        "risk_pct": risk_pct,
        "trade_score": setup.trade_score,
        "smart_money_score": setup.smart_money_score,
        "early_watch_score": setup.early_watch_score,
        "setup_type": setup.setup_type,
        "risk_level": setup.risk_level,
        "decision": setup.decision,
        "status": "OPEN",
        "reason": "AUTO PAPER TRADE",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    portfolio["cash"] -= cost

    trades.append(trade)
    save_json(PAPER_TRADES_FILE, trades)
    save_portfolio(user_id, portfolio)

    return (
        f"🤖 شراء افتراضي تلقائي\n\n"
        f"السهم: {setup.symbol}\n"
        f"الدخول: {entry:.2f}$\n"
        f"الكمية: {shares}\n"
        f"قيمة الصفقة: {cost:.2f}$\n"
        f"وقف الخسارة: {stop:.2f}$\n"
        f"الهدف: {target:.2f}$\n"
        f"المخاطرة: {risk_pct * 100:.1f}%\n"
        f"درجة الصفقة: {setup.trade_score:.1f}/10"
    )


def close_paper_trade(user_id, trade_id, exit_price, reason):
    trades = load_json(PAPER_TRADES_FILE, [])
    portfolio = get_portfolio(user_id)

    for t in trades:
        if t["id"] == trade_id and t["user_id"] == user_id and t["status"] == "OPEN":
            proceeds = t["shares"] * exit_price
            cost = t["shares"] * t["entry"]
            pnl = proceeds - cost
            pnl_percent = (pnl / cost) * 100 if cost > 0 else 0

            t["status"] = "CLOSED"
            t["exit"] = exit_price
            t["closed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            t["close_reason"] = reason
            t["pnl"] = pnl
            t["pnl_percent"] = pnl_percent

            portfolio["cash"] += proceeds
            portfolio["realized_pnl"] += pnl

            save_json(PAPER_TRADES_FILE, trades)
            save_portfolio(user_id, portfolio)

            return (
                f"📤 إغلاق صفقة افتراضية\n\n"
                f"السهم: {t['symbol']}\n"
                f"سبب الخروج: {reason}\n"
                f"الدخول: {t['entry']:.2f}$\n"
                f"الخروج: {exit_price:.2f}$\n"
                f"الربح/الخسارة: {pnl:.2f}$\n"
                f"النسبة: {pnl_percent:.2f}%"
            )

    return None


def paper_summary(user_id):
    portfolio = get_portfolio(user_id)
    open_trades = get_open_trades(user_id)

    invested = sum(t["shares"] * t["entry"] for t in open_trades)
    total_value = portfolio["cash"] + invested

    msg = "💼 المحفظة الافتراضية\n\n"
    msg += f"رأس المال الابتدائي: {INITIAL_CAPITAL:,.2f}$\n"
    msg += f"الكاش: {portfolio['cash']:,.2f}$\n"
    msg += f"القيمة المستثمرة: {invested:,.2f}$\n"
    msg += f"الأرباح/الخسائر المحققة: {portfolio['realized_pnl']:,.2f}$\n"
    msg += f"عدد الصفقات المفتوحة: {len(open_trades)}\n"
    msg += f"الحالة: {'مفعّل' if portfolio.get('enabled', True) else 'متوقف'}\n"

    if open_trades:
        msg += "\nالصفقات المفتوحة:\n"
        for t in open_trades:
            msg += (
                f"\n#{t['id']} {t['symbol']}\n"
                f"دخول: {t['entry']:.2f}$ | وقف: {t['stop']:.2f}$ | هدف: {t['target']:.2f}$\n"
                f"كمية: {t['shares']} | درجة: {t['trade_score']:.1f}/10\n"
            )

    return msg


def set_paper_enabled(user_id, enabled):
    portfolio = get_portfolio(user_id)
    portfolio["enabled"] = enabled
    save_portfolio(user_id, portfolio)