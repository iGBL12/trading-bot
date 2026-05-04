"""
AI Trade Monitor - Smart Local Version

Purpose:
- Monitor open trades after entry.
- Uses Twelve Data for price/indicators.
- Uses OpenAI optionally as an AI assistant to explain the best action.
- Smart monitoring interval:
  - Normal: every 15 minutes
  - Urgent: every 5 minutes
  - Critical: every 1 minute
- Works locally from VS Code without Telegram.

Install:
    pip install pandas requests python-dotenv openai

.env file:
    TWELVE_DATA_API_KEY=your_twelve_data_api_key
    OPENAI_API_KEY=your_openai_api_key   # optional but needed for AI summary

trade_log.json example:
[
  {
    "id": 1,
    "symbol": "AMD",
    "entry": 325,
    "stop": 310,
    "target": 350,
    "shares": 1,
    "reason": "Pullback MA50",
    "status": "OPEN"
  }
]

Run once:
    python ai_trade_monitor.py

Run smart loop:
    python ai_trade_monitor.py --loop

Important:
This is decision support only. It does not place trades and is not financial advice.
"""

import os
import json
import time
import sys
from datetime import datetime
from datetime import timedelta
from typing import Dict, List, Optional

import pandas as pd
import requests
from dotenv import load_dotenv

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

load_dotenv()

TWELVE_DATA_API_KEY = os.getenv("TWELVE_DATA_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")
TRADE_LOG_FILE = "trade_log.json"
TIMEFRAME = "1day"
OUTPUT_SIZE = 500
API_DELAY_SECONDS = 8

# Smart monitor intervals
MONITOR_NORMAL_SECONDS = 900       # 15 minutes
MONITOR_URGENT_SECONDS = 300       # 5 minutes
MONITOR_CRITICAL_SECONDS = 60      # 1 minute
NO_TRADE_SLEEP_SECONDS = 1800      # 30 minutes


# =========================
# DATA
# =========================

def fetch_daily_data(symbol: str) -> Optional[pd.DataFrame]:
    if not TWELVE_DATA_API_KEY:
        raise RuntimeError("TWELVE_DATA_API_KEY is missing in .env")

    time.sleep(API_DELAY_SECONDS)

    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": symbol,
        "interval": TIMEFRAME,
        "outputsize": OUTPUT_SIZE,
        "apikey": TWELVE_DATA_API_KEY,
        "format": "JSON",
    }

    try:
        response = requests.get(url, params=params, timeout=20)
        response.raise_for_status()
        data = response.json()

        if "values" not in data:
            print(f"No data for {symbol}: {data}")
            return None

        df = pd.DataFrame(data["values"])
        df["datetime"] = pd.to_datetime(df["datetime"])
        df = df.sort_values("datetime").reset_index(drop=True)

        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        return df.dropna()
    except Exception as exc:
        print(f"Failed to fetch {symbol}: {exc}")
        return None


def calculate_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["rsi"] = calculate_rsi(df["close"])
    df["ma20"] = df["close"].rolling(20).mean()
    df["ma50"] = df["close"].rolling(50).mean()
    df["ma200"] = df["close"].rolling(200).mean()
    df["avg_volume_20"] = df["volume"].rolling(20).mean()
    df["relative_volume"] = df["volume"] / df["avg_volume_20"]
    df["return_1d"] = df["close"].pct_change(1) * 100
    df["return_5d"] = df["close"].pct_change(5) * 100
    return df.dropna()


# =========================
# TRADE LOG
# =========================

def load_trades() -> List[Dict]:
    if not os.path.exists(TRADE_LOG_FILE):
        sample = [
            {
                "id": 1,
                "symbol": "AMD",
                "entry": 325,
                "stop": 310,
                "target": 350,
                "shares": 1,
                "reason": "Pullback MA50",
                "status": "OPEN",
            }
        ]
        with open(TRADE_LOG_FILE, "w", encoding="utf-8") as file:
            json.dump(sample, file, indent=2)
        print("Created sample trade_log.json. Edit it with your real trade and run again.")
        return sample

    with open(TRADE_LOG_FILE, "r", encoding="utf-8") as file:
        return json.load(file)


def save_trades(trades: List[Dict]) -> None:
    with open(TRADE_LOG_FILE, "w", encoding="utf-8") as file:
        json.dump(trades, file, indent=2, ensure_ascii=False)


# =========================
# MONITOR LOGIC
# =========================

def money(value: float) -> str:
    return f"${value:,.2f}"

def fetch_news(symbol: str) -> List[Dict]:
    if not FINNHUB_API_KEY:
        return []

    today = datetime.now().date()
    from_date = today - timedelta(days=7)

    url = "https://finnhub.io/api/v1/company-news"
    params = {
        "symbol": symbol,
        "from": from_date.isoformat(),
        "to": today.isoformat(),
        "token": FINNHUB_API_KEY,
    }

    try:
        response = requests.get(url, params=params, timeout=20)
        response.raise_for_status()
        data = response.json()
        return data[:5] if isinstance(data, list) else []
    except Exception as exc:
        print(f"News fetch failed for {symbol}: {exc}")
        return []


def ai_analyze_news(symbol: str, news_items: List[Dict]) -> Dict:
    if not OPENAI_API_KEY or OpenAI is None or not news_items:
        return {
            "sentiment": "UNKNOWN",
            "impact": "UNKNOWN",
            "risk": "UNKNOWN",
            "summary": "لا يوجد تحليل أخبار متاح.",
            "action": "NO_ACTION",
        }

    client = OpenAI(api_key=OPENAI_API_KEY)

    simplified_news = [
        {
            "headline": item.get("headline", ""),
            "summary": item.get("summary", ""),
            "datetime": item.get("datetime", ""),
            "source": item.get("source", ""),
        }
        for item in news_items[:5]
    ]

    prompt = f"""
حلل أخبار السهم {symbol} للمتداول قصير/متوسط المدى.

الأخبار:
{json.dumps(simplified_news, ensure_ascii=False, indent=2)}

أعد JSON فقط بهذا الشكل:
{{
  "sentiment": "POSITIVE أو NEGATIVE أو NEUTRAL",
  "impact": "LOW أو MEDIUM أو HIGH",
  "risk": "LOW أو MEDIUM أو HIGH",
  "summary": "ملخص عربي قصير",
  "action": "NO_ACTION أو WARNING أو EXIT_NOW أو HOLD"
}}

القواعد:
- إذا الخبر سلبي قوي أو تحقيق/دعوى/خفض توقعات/نتائج سيئة: action = WARNING أو EXIT_NOW حسب القوة.
- إذا الخبر عادي أو قديم: action = NO_ACTION.
- لا تبالغ، وكن محافظاً.
"""

    try:
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": "أنت محلل أخبار للأسهم ومنضبط في إدارة المخاطر."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
        )

        content = response.choices[0].message.content or "{}"
        return json.loads(content)

    except Exception as exc:
        return {
            "sentiment": "UNKNOWN",
            "impact": "UNKNOWN",
            "risk": "UNKNOWN",
            "summary": f"فشل تحليل الأخبار: {exc}",
            "action": "NO_ACTION",
        }

def monitor_trade(trade: Dict) -> Optional[Dict]:
    symbol = trade["symbol"].upper()
    df = fetch_daily_data(symbol)

    if df is None:
        print(f"⚠️ {symbol}: no data returned")
        return None

    if len(df) < 220:
        print(f"⚠️ {symbol}: only {len(df)} candles returned, need at least 220")
        return None

    df = add_indicators(df)
    if df.empty:
        print(f"⚠️ {symbol}: insufficient indicator data")
        return None

    last = df.iloc[-1]

    entry = float(trade["entry"])
    stop = float(trade["stop"])
    target = float(trade["target"])
    shares = int(trade.get("shares", 1))
    current = float(last["close"])

    pnl = (current - entry) * shares
    pnl_percent = ((current - entry) / entry) * 100

    risk_per_share = abs(entry - stop)
    current_r_multiple = (current - entry) / risk_per_share if risk_per_share != 0 else 0

    distance_to_stop = ((current - stop) / current) * 100
    distance_to_target = ((target - current) / current) * 100

    action = "HOLD"
    rules = []

    # =========================
    # NEWS + AI CHECK
    # =========================
    news_items = []
    news_analysis = {
        "sentiment": "UNKNOWN",
        "impact": "UNKNOWN",
        "risk": "UNKNOWN",
        "summary": "لم يتم تحليل الأخبار.",
        "action": "NO_ACTION",
    }

    try:
        news_items = fetch_news(symbol)
        news_analysis = ai_analyze_news(symbol, news_items)
    except NameError:
        rules.append("News AI skipped: fetch_news or ai_analyze_news is not defined.")
    except Exception as exc:
        rules.append(f"News AI failed: {exc}")

    news_action = str(news_analysis.get("action", "NO_ACTION")).upper()

    # =========================
    # PRICE / RISK RULES
    # =========================

    if news_action == "EXIT_NOW":
        action = "EXIT_NOW"
        rules.append(f"News AI Exit Alert: {news_analysis.get('summary')}")

    elif news_action == "WARNING":
        action = "WARNING"
        rules.append(f"News AI Warning: {news_analysis.get('summary')}")

    elif current <= stop:
        action = "EXIT_NOW"
        rules.append("Price reached or broke stop loss.")

    elif current >= target:
        action = "TAKE_PROFIT"
        rules.append("Price reached target.")

    elif current_r_multiple >= 1.0 and current > last["ma20"]:
        action = "RAISE_STOP"
        rules.append("Trade is above 1R profit. Consider raising stop to breakeven or under MA20.")

    elif last["rsi"] > 75 and pnl_percent > 3:
        action = "PARTIAL_PROFIT"
        rules.append("RSI is overbought while trade is profitable. Consider partial profit.")

    elif current < last["ma50"] and pnl_percent < 0:
        action = "WARNING"
        rules.append("Price is below MA50 and trade is negative.")

    else:
        rules.append("No important change. Hold the trade.")

    if pnl_percent > 0:
        suggested_stop = float(last["ma20"]) * 0.99

    else:
        suggested_stop = stop

    return {
        "id": trade.get("id"),
        "symbol": symbol,
        "entry": entry,
        "current": current,
        "stop": stop,
        "suggested_stop": suggested_stop,
        "target": target,
        "shares": shares,
        "pnl": pnl,
        "pnl_percent": pnl_percent,
        "r_multiple": current_r_multiple,
        "distance_to_stop_percent": distance_to_stop,
        "distance_to_target_percent": distance_to_target,
        "rsi": float(last["rsi"]),
        "ma20": float(last["ma20"]),
        "ma50": float(last["ma50"]),
        "ma200": float(last["ma200"]),
        "relative_volume": float(last["relative_volume"]),
        "return_1d": float(last["return_1d"]),
        "return_5d": float(last["return_5d"]),
        "news_items": news_items[:3] if news_items else [],
        "news_analysis": news_analysis,
        "action": action,
        "rules": rules,
        "reason": trade.get("reason", ""),
    }
def get_monitor_interval(report: Dict) -> int:
    distance_to_stop = abs(float(report["distance_to_stop_percent"]))
    distance_to_target = abs(float(report["distance_to_target_percent"]))
    r_multiple = float(report["r_multiple"])
    action = report["action"]

    if action in ["EXIT_NOW", "TAKE_PROFIT", "WARNING"]:
        return MONITOR_CRITICAL_SECONDS

    if action in ["RAISE_STOP", "PARTIAL_PROFIT"]:
        return MONITOR_URGENT_SECONDS

    if distance_to_stop <= 2 or distance_to_target <= 2:
        return MONITOR_URGENT_SECONDS

    if r_multiple >= 1:
        return MONITOR_URGENT_SECONDS

    return MONITOR_NORMAL_SECONDS

def should_alert(report: Dict) -> bool:
    return report["action"] in [
        "EXIT_NOW",
        "TAKE_PROFIT",
        "RAISE_STOP",
        "PARTIAL_PROFIT",
        "WARNING",
    ]

# =========================
# AI LAYER
# =========================

def ai_review(report: Dict) -> str:
    if not OPENAI_API_KEY or OpenAI is None:
        return "AI review disabled. Add OPENAI_API_KEY and install openai package to enable it."

    client = OpenAI(api_key=OPENAI_API_KEY)

    prompt = f"""
You are an assistant for a swing trader. You do NOT give guaranteed financial advice.
Your job is to review an open trade and suggest a disciplined action.

Use these rules:
- Respect the stop loss.
- If target is reached, suggest taking profit.
- If trade is above +1R, consider raising stop.
- If RSI is very high and profit is positive, consider partial profit.
- If setup is weak, suggest caution.
- Be concise and practical.
- Return Arabic output.

Trade report:
{json.dumps(report, indent=2)}

Return this format:
Decision: HOLD / EXIT_NOW / TAKE_PROFIT / RAISE_STOP / PARTIAL_PROFIT / WARNING
Reason:
Action Plan:
"""

    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": "You are a disciplined swing trading assistant."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
    )

    return response.choices[0].message.content or "No AI response."


# =========================
# OUTPUT
# =========================

def print_report(report: Dict, include_ai: bool = True) -> None:
    print("\n" + "=" * 70)
    print(f"TRADE MONITOR: {report['symbol']} | Trade #{report['id']}")
    print("=" * 70)

    print(f"Entry: {money(report['entry'])}")
    print(f"Current: {money(report['current'])}")
    print(f"Stop: {money(report['stop'])}")
    print(f"Suggested Stop: {money(report['suggested_stop'])}")
    print(f"Target: {money(report['target'])}")
    print(f"Shares: {report['shares']}")
    print(f"PnL: {money(report['pnl'])} ({report['pnl_percent']:.2f}%)")
    print(f"R-Multiple: {report['r_multiple']:.2f}R")
    print(f"Distance to Stop: {report['distance_to_stop_percent']:.2f}%")
    print(f"Distance to Target: {report['distance_to_target_percent']:.2f}%")

    print("\nIndicators:")
    print(f"RSI: {report['rsi']:.1f}")
    print(f"MA20: {money(report['ma20'])}")
    print(f"MA50: {money(report['ma50'])}")
    print(f"MA200: {money(report['ma200'])}")
    print(f"Relative Volume: {report['relative_volume']:.2f}x")
    print(f"1D Return: {report['return_1d']:.2f}%")
    print(f"5D Return: {report['return_5d']:.2f}%")

    news_analysis = report.get("news_analysis", {})
    print("\nNews AI Analysis:")
    print(f"Sentiment: {news_analysis.get('sentiment', 'UNKNOWN')}")
    print(f"Impact: {news_analysis.get('impact', 'UNKNOWN')}")
    print(f"Risk: {news_analysis.get('risk', 'UNKNOWN')}")
    print(f"Action: {news_analysis.get('action', 'NO_ACTION')}")
    print(f"Summary: {news_analysis.get('summary', 'لا يوجد ملخص.')}")

    news_items = report.get("news_items", [])
    if news_items:
        print("\nLatest News:")
        for item in news_items[:3]:
            headline = item.get("headline", "No headline")
            source = item.get("source", "Unknown")
            print(f"- {headline} | Source: {source}")

    print("\nRule-Based Action:")
    print(report["action"])

    print("\nRules:")
    for rule in report["rules"]:
        print(f"- {rule}")

    next_interval = get_monitor_interval(report)
    print(f"\nSmart Monitor: next check in {next_interval / 60:.0f} minutes")

    if include_ai:
        print("\nAI Review:")
        print(ai_review(report))
def fetch_news(symbol: str):
    api_key = os.getenv("FINNHUB_API_KEY")
    if not api_key:
        return []

    url = "https://finnhub.io/api/v1/company-news"
    params = {
        "symbol": symbol,
        "from": "2026-04-20",
        "to": "2026-04-27",
        "token": api_key,
    }

    try:
        res = requests.get(url, params=params)
        data = res.json()
        return data[:3]  # أهم 3 أخبار
    except:
        return []
    

def monitor_once(include_ai: bool = True) -> int:
    trades = load_trades()
    open_trades = [trade for trade in trades if trade.get("status") == "OPEN"]

    if not open_trades:
        print("No open trades found in trade_log.json")
        return NO_TRADE_SLEEP_SECONDS

    min_interval = MONITOR_NORMAL_SECONDS
    any_report = False

    for trade in open_trades:
        report = monitor_trade(trade)

        if report:
            any_report = True
            interval = get_monitor_interval(report)
            min_interval = min(min_interval, interval)

            # لا يطبع إلا إذا هناك حدث مهم
            if should_alert(report):
                print_report(report, include_ai=include_ai)
                print(f"🚨 ALERT: {report['symbol']} يحتاج إجراء: {report['action']}")
                print(f"⏱ Next check for {trade['symbol']} in {interval / 60:.0f} minutes")
            else:
                print(f"✅ {report['symbol']}: لا يوجد تغيير مهم. Action = HOLD")

    if not any_report:
        print("No valid reports generated. Will retry later.")
        return MONITOR_NORMAL_SECONDS

    return min_interval



def run_smart_loop() -> None:
    print("🚀 Smart Trade Monitor Started")
    print("Press CTRL+C to stop.")

    while True:
        try:
            print("\n" + "#" * 70)
            print(f"Smart monitor cycle: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print("#" * 70)

            next_interval = monitor_once(include_ai=True)
            print(f"\n🧠 Smart sleep: {next_interval / 60:.0f} minutes\n")
            time.sleep(next_interval)

        except KeyboardInterrupt:
            print("\nSmart monitor stopped by user.")
            break
        except Exception as exc:
            print(f"Unexpected error: {exc}")
            print("Retrying in 5 minutes...")
            time.sleep(MONITOR_URGENT_SECONDS)


def main() -> None:
    # Default: run once.
    # Use: python ai_trade_monitor.py --loop
    if "--loop" in sys.argv:
        run_smart_loop()
    else:
        monitor_once(include_ai=True)


if __name__ == "__main__":
    main()
