"""
ماسح فرص تداول ذكي + إنذار مبكر - نسخة محلية بالعربي

الوظائف:
- يبحث عن صفقات ممتازة للدخول.
- يحدد منطقة الدخول، وقف الخسارة، الهدف الأول، الهدف الثاني.
- يصنف الصفقة حسب مستوى الخطورة.
- يضيف إنذار مبكر للأسهم التي تقترب من فرصة قبل ظهورها.
- يعمل مرة واحدة أو بشكل متكرر أثناء افتتاح السوق الأمريكي فقط.
- يسمح بإضافة/حذف/عرض الأسهم من قائمة المراقبة.

التثبيت:
    pip install pandas requests python-dotenv pytz

ملف .env:
    TWELVE_DATA_API_KEY=your_key
    ACCOUNT_SIZE=1300
    RISK_PER_TRADE=0.02

الاستخدام:
    python smart_arabic_scanner_predictive.py --scan
    python smart_arabic_scanner_predictive.py --loop
    python smart_arabic_scanner_predictive.py --list
    python smart_arabic_scanner_predictive.py --add NVDA
    python smart_arabic_scanner_predictive.py --remove AMD

تنبيه:
هذا النظام للمساعدة في القرار فقط، وليس نصيحة مالية أو أمر شراء/بيع.
"""

import os
import json
import math
import time
import argparse
from dataclasses import dataclass
from datetime import datetime, time as dtime
from typing import List, Optional, Tuple

import pandas as pd
import pytz
import requests
from dotenv import load_dotenv

load_dotenv()

TWELVE_DATA_API_KEY = os.getenv("TWELVE_DATA_API_KEY", "")
ACCOUNT_SIZE = float(os.getenv("ACCOUNT_SIZE", "1300"))
RISK_PER_TRADE = float(os.getenv("RISK_PER_TRADE", "0.02"))

WATCHLIST_FILE = "watchlist.json"
DEFAULT_WATCHLIST = ["AMD", "ANET", "DELL"]

MARKET_SYMBOLS = ["SPY", "QQQ"]
SENTIMENT_SYMBOLS = {
    "SPY": "S&P 500",
    "QQQ": "Nasdaq 100",
    "IWM": "Small Caps",
    "XLK": "Technology",
    "SMH": "Semiconductors",
}

TIMEFRAME = "1day"
OUTPUT_SIZE = 500
API_DELAY_SECONDS = 8
SCAN_INTERVAL_SECONDS = 1800
CLOSED_MARKET_SLEEP_SECONDS = 1800
MIN_EXCELLENT_SCORE = 8.0
MIN_GOOD_SCORE = 7.0
MIN_EARLY_WATCH_SCORE = 6.0


@dataclass
class MarketStatus:
    status: str
    score: float
    spy_trend: str
    qqq_trend: str
    message: str


@dataclass
class SentimentStatus:
    status: str
    score: float
    risk_level: str
    details: List[str]
    message: str


@dataclass
class TradeSetup:
    symbol: str
    close: float
    rsi: float
    ma20: float
    ma50: float
    ma200: float
    relative_volume: float
    trend_score: float
    smart_money_score: float
    trade_score: float
    entry_low: float
    entry_high: float
    stop_loss: float
    target_1: float
    target_2: float
    shares: int
    risk_amount: float
    risk_per_share: float
    setup_type: str
    risk_level: str
    decision: str
    early_watch: str
    early_watch_score: float
    early_watch_trigger: float
    arabic_summary: str
    notes: List[str]


def money(value: float) -> str:
    return f"${value:,.2f}"


def print_header(title: str):
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def is_market_open() -> bool:
    ny_tz = pytz.timezone("America/New_York")
    now = datetime.now(ny_tz)
    if now.weekday() >= 5:
        return False
    return dtime(9, 30) <= now.time() <= dtime(16, 0)


def load_watchlist() -> List[str]:
    if not os.path.exists(WATCHLIST_FILE):
        save_watchlist(DEFAULT_WATCHLIST)
        return DEFAULT_WATCHLIST.copy()
    try:
        with open(WATCHLIST_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
        symbols = data.get("symbols", DEFAULT_WATCHLIST)
        return sorted(list(set(s.upper().strip() for s in symbols if s.strip())))
    except Exception:
        save_watchlist(DEFAULT_WATCHLIST)
        return DEFAULT_WATCHLIST.copy()


def save_watchlist(symbols: List[str]) -> None:
    symbols = sorted(list(set(s.upper().strip() for s in symbols if s.strip())))
    with open(WATCHLIST_FILE, "w", encoding="utf-8") as file:
        json.dump({"symbols": symbols}, file, indent=2, ensure_ascii=False)


def add_symbol(symbol: str) -> None:
    symbols = load_watchlist()
    symbol = symbol.upper().strip()
    if symbol not in symbols:
        symbols.append(symbol)
        save_watchlist(symbols)
        print(f"✅ تم إضافة {symbol} إلى قائمة المراقبة.")
    else:
        print(f"ℹ️ {symbol} موجود مسبقاً.")


def remove_symbol(symbol: str) -> None:
    symbols = load_watchlist()
    symbol = symbol.upper().strip()
    if symbol in symbols:
        symbols.remove(symbol)
        save_watchlist(symbols)
        print(f"✅ تم حذف {symbol} من قائمة المراقبة.")
    else:
        print(f"ℹ️ {symbol} غير موجود.")


def print_watchlist() -> None:
    print_header("📋 قائمة المراقبة")
    for s in load_watchlist():
        print(f"- {s}")


def fetch_daily_data(symbol: str) -> Optional[pd.DataFrame]:
    if not TWELVE_DATA_API_KEY:
        raise RuntimeError("TWELVE_DATA_API_KEY غير موجود. أضفه في ملف .env")
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
            print(f"⚠️ لا توجد بيانات لـ {symbol}: {data}")
            return None
        df = pd.DataFrame(data["values"])
        df["datetime"] = pd.to_datetime(df["datetime"])
        df = df.sort_values("datetime").reset_index(drop=True)
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df.dropna(subset=["open", "high", "low", "close", "volume"])
    except Exception as exc:
        print(f"❌ فشل جلب بيانات {symbol}: {exc}")
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
    df["rsi"] = calculate_rsi(df["close"], 14)
    df["ma20"] = df["close"].rolling(20).mean()
    df["ma50"] = df["close"].rolling(50).mean()
    df["ma200"] = df["close"].rolling(200).mean()
    df["avg_volume_20"] = df["volume"].rolling(20).mean()
    df["relative_volume"] = df["volume"] / df["avg_volume_20"]
    df["high_20"] = df["high"].rolling(20).max()
    df["low_20"] = df["low"].rolling(20).min()
    df["return_3d"] = df["close"].pct_change(3) * 100
    df["return_5d"] = df["close"].pct_change(5) * 100
    df["return_20d"] = df["close"].pct_change(20) * 100
    return df


def analyze_market() -> MarketStatus:
    scores = []
    details = {}
    for symbol in MARKET_SYMBOLS:
        df = fetch_daily_data(symbol)
        if df is None or len(df) < 220:
            details[symbol] = "بيانات غير كافية"
            continue
        df = add_indicators(df).dropna()
        if df.empty:
            details[symbol] = "بيانات مؤشرات غير كافية"
            continue
        last = df.iloc[-1]
        score = 0.0
        if last["close"] > last["ma50"]:
            score += 1
        if last["close"] > last["ma200"]:
            score += 1
        if last["ma50"] > last["ma200"]:
            score += 1
        if last["rsi"] < 70:
            score += 1
        elif 70 <= last["rsi"] <= 80:
            score += 0.5
        scores.append(score)
        details[symbol] = f"الإغلاق {last['close']:.2f} | RSI {last['rsi']:.1f} | الدرجة {score}/4"
    if not scores:
        return MarketStatus("غير معروف", 0, details.get("SPY", "N/A"), details.get("QQQ", "N/A"), "تعذر تحليل السوق.")
    avg_score = sum(scores) / (len(scores) * 4) * 10
    if avg_score >= 7:
        return MarketStatus("صاعد", avg_score, details.get("SPY", "N/A"), details.get("QQQ", "N/A"), "السوق داعم للشراء، لكن لا تطارد السعر.")
    if avg_score >= 5:
        return MarketStatus("متذبذب", avg_score, details.get("SPY", "N/A"), details.get("QQQ", "N/A"), "السوق متوسط. اختر فقط الفرص عالية الجودة.")
    return MarketStatus("هابط", avg_score, details.get("SPY", "N/A"), details.get("QQQ", "N/A"), "السوق غير داعم للشراء حالياً.")


def analyze_sentiment() -> SentimentStatus:
    total_score = 0.0
    max_score = 0.0
    details = []
    for symbol, name in SENTIMENT_SYMBOLS.items():
        df = fetch_daily_data(symbol)
        if df is None or len(df) < 220:
            details.append(f"{symbol}: بيانات غير كافية")
            continue
        df = add_indicators(df).dropna()
        if df.empty:
            details.append(f"{symbol}: بيانات مؤشرات غير كافية")
            continue
        last = df.iloc[-1]
        score = 0.0
        if last["close"] > last["ma50"]:
            score += 1.5
        if last["close"] > last["ma200"]:
            score += 1.5
        if last["return_5d"] > 0:
            score += 1
        if 45 <= last["rsi"] <= 70:
            score += 1
        elif 70 < last["rsi"] <= 80:
            score += 0.5
        if last["return_20d"] > 0:
            score += 1
        total_score += score
        max_score += 6
        details.append(f"{symbol} ({name}): إغلاق {last['close']:.2f} | RSI {last['rsi']:.1f} | 5 أيام {last['return_5d']:.2f}% | درجة {score:.1f}/6")
    if max_score == 0:
        return SentimentStatus("غير معروف", 0, "غير معروف", details, "تعذر قياس معنويات السوق.")
    score = total_score / max_score * 10
    if score >= 7.5:
        return SentimentStatus("شهية مخاطرة", score, "منخفضة/متوسطة", details, "بيئة السوق داعمة، لكن تجنب المطاردة.")
    if score >= 5.5:
        return SentimentStatus("مختلطة", score, "متوسطة", details, "السوق مختلط. قلل المخاطرة.")
    if score >= 4:
        return SentimentStatus("دفاعية", score, "عالية", details, "البيئة ضعيفة. الأفضل مراقبة فقط.")
    return SentimentStatus("خروج مخاطرة", score, "عالية جداً", details, "السوق غير داعم للشراء.")


def trend_score(last: pd.Series) -> Tuple[float, List[str]]:
    score = 0.0
    notes = []
    if last["close"] > last["ma50"]:
        score += 2
        notes.append("السعر فوق متوسط 50 يوم")
    else:
        notes.append("السعر تحت متوسط 50 يوم")
    if last["close"] > last["ma200"]:
        score += 2
        notes.append("السعر فوق متوسط 200 يوم")
    else:
        notes.append("السعر تحت متوسط 200 يوم")
    if last["ma50"] > last["ma200"]:
        score += 1
        notes.append("الاتجاه العام إيجابي")
    else:
        notes.append("الاتجاه العام غير مثالي")
    if 40 <= last["rsi"] <= 60:
        score += 2
        notes.append("RSI في منطقة دخول جيدة")
    elif 60 < last["rsi"] <= 70:
        score += 1
        notes.append("RSI مرتفع قليلاً")
    elif 70 < last["rsi"] <= 75:
        score += 0.5
        notes.append("RSI مرتفع ويحتاج حذر")
    else:
        notes.append("RSI غير مناسب للدخول")
    if last["close"] >= last["ma50"] * 0.98 and last["close"] <= last["ma50"] * 1.08:
        score += 1
        notes.append("السعر قريب من MA50 وليس ممتداً")
    else:
        notes.append("السعر بعيد عن MA50")
    return min(score, 8), notes


def smart_money_score(df: pd.DataFrame) -> Tuple[float, List[str]]:
    last = df.iloc[-1]
    prev = df.iloc[-2]
    score = 0.0
    notes = []
    rel_vol = last["relative_volume"]
    if rel_vol >= 2.0:
        score += 2.5
        notes.append(f"حجم تداول قوي جداً: {rel_vol:.2f}x")
    elif rel_vol >= 1.5:
        score += 2.0
        notes.append(f"حجم تداول عالي: {rel_vol:.2f}x")
    elif rel_vol >= 1.2:
        score += 1.0
        notes.append(f"حجم تداول جيد: {rel_vol:.2f}x")
    else:
        notes.append(f"حجم تداول عادي: {rel_vol:.2f}x")
    previous_20_high = df.iloc[-21:-1]["high"].max()
    if last["close"] > previous_20_high and rel_vol >= 1.5:
        score += 2.0
        notes.append("اختراق مؤكد مع حجم تداول")
    elif last["close"] > previous_20_high:
        score += 1.0
        notes.append("اختراق لكن الحجم غير كافٍ")
    else:
        notes.append("لا يوجد اختراق واضح")
    daily_range = last["high"] - last["low"]
    if daily_range > 0:
        close_position = (last["close"] - last["low"]) / daily_range
        if close_position >= 0.75:
            score += 1.5
            notes.append("إغلاق قوي قرب أعلى اليوم")
        elif close_position >= 0.55:
            score += 0.75
            notes.append("إغلاق إيجابي متوسط")
        else:
            notes.append("الإغلاق ضعيف")
    if last["close"] > prev["close"]:
        score += 1.0
        notes.append("أغلق أعلى من اليوم السابق")
    else:
        notes.append("أغلق أقل من اليوم السابق")
    if last["close"] > last["ma20"]:
        score += 0.75
    if last["close"] > last["ma50"]:
        score += 0.75
        notes.append("فوق متوسطات مهمة")
    distance_from_ma50 = (last["close"] - last["ma50"]) / last["ma50"]
    if 0 <= distance_from_ma50 <= 0.08:
        score += 1.5
        notes.append("غير ممتد عن MA50")
    elif distance_from_ma50 > 0.12:
        score -= 1.0
        notes.append("ممتد جداً عن MA50")
    return max(0, min(score, 10)), notes


def detect_setup_type(df: pd.DataFrame) -> str:
    last = df.iloc[-1]
    previous_20_high = df.iloc[-21:-1]["high"].max()
    distance_from_ma50 = abs(last["close"] - last["ma50"]) / last["ma50"]
    if last["close"] > last["ma50"] and distance_from_ma50 <= 0.05:
        return "تصحيح قرب الدعم"
    if last["close"] > previous_20_high and last["relative_volume"] >= 1.2:
        return "اختراق"
    if last["close"] > last["ma50"] and last["close"] > last["ma200"]:
        return "استمرار اتجاه"
    return "لا يوجد نموذج واضح"


def calculate_entry_plan(setup_type: str, df: pd.DataFrame) -> Tuple[float, float, float, float, float]:
    last = df.iloc[-1]
    close = float(last["close"])
    ma50 = float(last["ma50"])
    ma20 = float(last["ma20"])
    low_20 = float(last["low_20"])
    previous_20_high = float(df.iloc[-21:-1]["high"].max())
    if setup_type == "تصحيح قرب الدعم":
        entry_low = ma50 * 0.98
        entry_high = ma50 * 1.02
        stop_loss = min(ma50 * 0.96, low_20 * 0.98)
        mid = (entry_low + entry_high) / 2
        target_1 = mid * 1.06
        target_2 = mid * 1.10
    elif setup_type == "اختراق":
        entry_low = previous_20_high
        entry_high = previous_20_high * 1.01
        stop_loss = previous_20_high * 0.97
        mid = (entry_low + entry_high) / 2
        target_1 = mid * 1.05
        target_2 = mid * 1.08
    elif setup_type == "استمرار اتجاه":
        entry_low = max(ma20 * 0.99, close * 0.97)
        entry_high = min(close * 1.005, ma20 * 1.03)
        stop_loss = min(ma20 * 0.97, ma50 * 0.98)
        mid = (entry_low + entry_high) / 2
        target_1 = mid * 1.05
        target_2 = mid * 1.08
    else:
        entry_low = close * 0.95
        entry_high = close * 0.98
        stop_loss = min(ma50 * 0.96, low_20 * 0.98)
        mid = (entry_low + entry_high) / 2
        target_1 = mid * 1.05
        target_2 = mid * 1.08
    return float(entry_low), float(entry_high), float(stop_loss), float(target_1), float(target_2)


def early_watch_signal(df: pd.DataFrame) -> Tuple[str, float, float, List[str]]:
    last = df.iloc[-1]
    close = float(last["close"])
    rsi = float(last["rsi"])
    ma20 = float(last["ma20"])
    ma50 = float(last["ma50"])
    ma200 = float(last["ma200"])
    rv = float(last["relative_volume"])
    return_3d = float(last["return_3d"])
    return_5d = float(last["return_5d"])
    score = 0.0
    notes = []
    if close > ma50 and ma50 > ma200:
        score += 2
        notes.append("الاتجاه العام ما زال إيجابياً")
    if 50 <= rsi <= 65:
        score += 3
        notes.append("RSI أصبح مناسباً للمراقبة")
    elif 65 < rsi <= 75:
        score += 1.5
        notes.append("RSI بدأ يبرد لكنه لا يزال مرتفعاً")
    elif rsi > 75:
        score += 0.5
        notes.append("RSI مرتفع جداً، نحتاج تصحيحاً أكبر")
    dist_ma20 = abs(close - ma20) / ma20
    dist_ma50 = abs(close - ma50) / ma50
    if dist_ma20 <= 0.03:
        score += 2
        notes.append("السعر قريب من MA20")
    elif dist_ma20 <= 0.06:
        score += 1
        notes.append("السعر يقترب من MA20")
    if dist_ma50 <= 0.05:
        score += 2
        notes.append("السعر قريب من MA50")
    elif dist_ma50 <= 0.10:
        score += 1
        notes.append("السعر يقترب من MA50")
    if return_3d < 0 and close > ma50:
        score += 1.5
        notes.append("بداية تصحيح قصيرة داخل ترند صاعد")
    elif return_5d < 0 and close > ma50:
        score += 1
        notes.append("تصحيح أسبوعي داخل ترند صاعد")
    if rv >= 1.2:
        score += 1
        notes.append("الحجم أعلى من الطبيعي")
    trigger = max(ma20, ma50 * 1.02)
    score = max(0, min(score, 10))
    if score >= 7.5:
        status = "قريب جداً من فرصة محتملة"
    elif score >= 6:
        status = "قيد المراقبة القوية"
    elif score >= 4.5:
        status = "مراقبة مبكرة"
    else:
        status = "لا توجد إشارة مبكرة مهمة"
    return status, score, trigger, notes


def classify_risk(trade_score_value: float, rsi: float, current_price: float, entry_high: float, smart_money: float) -> str:
    if trade_score_value >= 8.2 and 40 <= rsi <= 65 and current_price <= entry_high * 1.015 and smart_money >= 6.5:
        return "منخفضة"
    if trade_score_value >= 7.2 and rsi <= 70 and current_price <= entry_high * 1.03:
        return "متوسطة"
    if trade_score_value >= 6.0 and rsi <= 75:
        return "عالية"
    return "مرتفعة جداً"

def dynamic_risk_percent(trade_score: float, risk_level: str) -> float:
    """
    يحدد نسبة المخاطرة حسب قوة الصفقة ومستوى الخطورة.
    """
    if trade_score >= 8.5 and risk_level == "منخفضة":
        return 0.02   # 2%

    if trade_score >= 7.5 and risk_level in ["منخفضة", "متوسطة"]:
        return 0.015  # 1.5%

    if trade_score >= 7.0 and risk_level == "متوسطة":
        return 0.01   # 1%

    if risk_level == "عالية":
        return 0.005  # 0.5%

    return 0.0        # لا تدخل



def calculate_position_size(entry: float, stop: float, risk_percent: float) -> Tuple[int, float, float]:
    risk_amount = ACCOUNT_SIZE * risk_percent
    risk_per_share = abs(entry - stop)

    if risk_per_share <= 0 or risk_percent <= 0:
        return 0, risk_amount, risk_per_share

    shares = math.floor(risk_amount / risk_per_share)
    return max(shares, 0), risk_amount, risk_per_share



def build_trade_setup(symbol: str, market: MarketStatus, sentiment: SentimentStatus) -> Optional[TradeSetup]:
    df = fetch_daily_data(symbol)
    if df is None:
        print(f"⚠️ {symbol}: لا توجد بيانات")
        return None
    if len(df) < 220:
        print(f"⚠️ {symbol}: البيانات غير كافية ({len(df)} شمعة فقط)")
        return None
    df = add_indicators(df).dropna()
    if len(df) < 10:
        print(f"⚠️ {symbol}: بيانات المؤشرات غير كافية")
        return None
    last = df.iloc[-1]
    current_price = float(last["close"])
    rsi = float(last["rsi"])
    t_score, t_notes = trend_score(last)
    sm_score, sm_notes = smart_money_score(df)
    setup_type = detect_setup_type(df)
    early_status, early_score, early_trigger, early_notes = early_watch_signal(df)
    market_factor = 2 if market.status == "صاعد" else 1 if market.status == "متذبذب" else 0
    sentiment_factor = 1 if sentiment.status == "شهية مخاطرة" else 0.5 if sentiment.status == "مختلطة" else -1
    setup_factor = {
        "تصحيح قرب الدعم": 1.0,
        "اختراق": 0.75,
        "استمرار اتجاه": 0.25,
        "لا يوجد نموذج واضح": -1.0,
    }.get(setup_type, -1.0)
    trade_score_value = (t_score * 0.38) + (sm_score * 0.35) + (market_factor * 0.8) + sentiment_factor + setup_factor
    trade_score_value = max(0, min(trade_score_value, 10))
    entry_low, entry_high, stop_loss, target_1, target_2 = calculate_entry_plan(setup_type, df)
    late_reasons = []
    if rsi > 75:
        late_reasons.append("RSI مرتفع جداً")
        trade_score_value = min(trade_score_value, 5.0)
    if current_price > target_1:
        late_reasons.append("السعر تجاوز الهدف الأول")
        trade_score_value = min(trade_score_value, 5.0)
    if current_price > entry_high * 1.03:
        late_reasons.append("السعر بعيد عن منطقة الدخول")
        trade_score_value = min(trade_score_value, 5.0)
    risk_level = classify_risk(trade_score_value, rsi, current_price, entry_high, sm_score)
    if market.status == "هابط" or sentiment.status == "خروج مخاطرة":
        decision = "تجنب - السوق غير داعم"
    elif late_reasons:
        decision = "انتظار - الدخول متأخر"
    elif setup_type == "لا يوجد نموذج واضح":
        decision = "مراقبة فقط - لا يوجد نموذج دخول واضح"
    elif trade_score_value >= MIN_EXCELLENT_SCORE and risk_level in ["منخفضة", "متوسطة"]:
        decision = "صفقة ممتازة - انتظر دخول السعر في المنطقة"
    elif trade_score_value >= MIN_GOOD_SCORE:
        decision = "صفقة جيدة - تحتاج تأكيد"
    elif trade_score_value >= 5:
        decision = "مراقبة فقط"
    else:
        decision = "تجاهل حالياً"
    mid_entry = (entry_low + entry_high) / 2
    risk_percent = dynamic_risk_percent(trade_score_value, risk_level)
    shares, risk_amount, risk_per_share = calculate_position_size(mid_entry, stop_loss, risk_percent)
    notes = t_notes + sm_notes + [
        f"نوع النموذج: {setup_type}",
        f"معنويات السوق: {sentiment.status} ({sentiment.score:.1f}/10)",
        f"الإنذار المبكر: {early_status} ({early_score:.1f}/10)",
        f"سعر التنبيه المبكر: {money(early_trigger)}",
    ] + early_notes + late_reasons
    if shares <= 0:
        notes.append("حجم الحساب/المخاطرة لا يسمح بكمية آمنة")
        notes.append(f"نسبة المخاطرة المستخدمة: {risk_percent * 100:.1f}%")
    arabic_summary = (
        f"{symbol}: {decision}. نوع الصفقة: {setup_type}. المخاطرة: {risk_level}. "
        f"الدخول: {money(entry_low)} - {money(entry_high)}، الوقف: {money(stop_loss)}، "
        f"الأهداف: {money(target_1)} ثم {money(target_2)}. "
        f"الإنذار المبكر: {early_status} بدرجة {early_score:.1f}/10."
    )
    print(f"✅ {symbol}: {decision} | الدرجة {trade_score_value:.1f}/10 | إنذار مبكر {early_score:.1f}/10")
    return TradeSetup(
        symbol=symbol,
        close=current_price,
        rsi=rsi,
        ma20=float(last["ma20"]),
        ma50=float(last["ma50"]),
        ma200=float(last["ma200"]),
        relative_volume=float(last["relative_volume"]),
        trend_score=t_score,
        smart_money_score=sm_score,
        trade_score=trade_score_value,
        entry_low=float(entry_low),
        entry_high=float(entry_high),
        stop_loss=float(stop_loss),
        target_1=float(target_1),
        target_2=float(target_2),
        shares=shares,
        risk_amount=risk_amount,
        risk_per_share=risk_per_share,
        setup_type=setup_type,
        risk_level=risk_level,
        decision=decision,
        early_watch=early_status,
        early_watch_score=early_score,
        early_watch_trigger=early_trigger,
        arabic_summary=arabic_summary,
        notes=notes,
    )


def print_market(market: MarketStatus):
    print_header("📊 فلتر السوق")
    print(f"الحالة: {market.status}")
    print(f"الدرجة: {market.score:.1f}/10")
    print(f"SPY: {market.spy_trend}")
    print(f"QQQ: {market.qqq_trend}")
    print(f"القرار: {market.message}")


def print_sentiment(sentiment: SentimentStatus):
    print_header("🧭 معنويات السوق")
    print(f"الحالة: {sentiment.status}")
    print(f"الدرجة: {sentiment.score:.1f}/10")
    print(f"مستوى المخاطرة: {sentiment.risk_level}")
    print(f"القرار: {sentiment.message}")


def print_setup(setup: TradeSetup):
    print_header(f"🔥 فرصة: {setup.symbol}")
    print(setup.arabic_summary)
    print("\nالتفاصيل:")
    print(f"السعر الحالي: {money(setup.close)}")
    print(f"نوع النموذج: {setup.setup_type}")
    print(f"تصنيف المخاطرة: {setup.risk_level}")
    print(f"RSI: {setup.rsi:.1f}")
    print(f"MA20: {money(setup.ma20)}")
    print(f"MA50: {money(setup.ma50)}")
    print(f"MA200: {money(setup.ma200)}")
    print(f"Relative Volume: {setup.relative_volume:.2f}x")
    print(f"Smart Money Score: {setup.smart_money_score:.1f}/10")
    print(f"Trade Score: {setup.trade_score:.1f}/10")
    print(f"الإنذار المبكر: {setup.early_watch} ({setup.early_watch_score:.1f}/10)")
    print(f"سعر التنبيه المبكر: {money(setup.early_watch_trigger)}")
    print("\nالخطة:")
    print(f"منطقة الدخول: {money(setup.entry_low)} - {money(setup.entry_high)}")
    print(f"وقف الخسارة: {money(setup.stop_loss)}")
    print(f"الهدف الأول: {money(setup.target_1)}")
    print(f"الهدف الثاني: {money(setup.target_2)}")
    print("\nحجم الصفقة:")
    print(f"المخاطرة بالدولار: {money(setup.risk_amount)}")
    print(f"المخاطرة لكل سهم: {money(setup.risk_per_share)}")
    print(f"عدد الأسهم المقترح: {setup.shares}")
    print("\nالملاحظات:")
    for note in setup.notes[:12]:
        print(f"- {note}")


def scan_once() -> List[TradeSetup]:
    if not TWELVE_DATA_API_KEY:
        raise RuntimeError("TWELVE_DATA_API_KEY غير موجود. أضفه في ملف .env")
    watchlist = load_watchlist()
    print_header(f"🚀 بدء فحص السوق - {now_text()}")
    print(f"رأس المال: {money(ACCOUNT_SIZE)}")
    print(f"المخاطرة لكل صفقة: {RISK_PER_TRADE * 100:.1f}%")
    print(f"قائمة المراقبة: {', '.join(watchlist)}")
    print(f"تأخير API: {API_DELAY_SECONDS} ثواني لكل طلب")
    market = analyze_market()
    sentiment = analyze_sentiment()
    print_market(market)
    print_sentiment(sentiment)
    all_setups: List[TradeSetup] = []
    best_setups: List[TradeSetup] = []
    early_watch_list: List[TradeSetup] = []
    for symbol in watchlist:
        print(f"\n🔎 فحص {symbol}...")
        setup = build_trade_setup(symbol, market, sentiment)
        if setup:
            all_setups.append(setup)
            if setup.trade_score >= MIN_GOOD_SCORE and "تجنب" not in setup.decision and "انتظار - الدخول متأخر" not in setup.decision:
                best_setups.append(setup)
            elif setup.early_watch_score >= MIN_EARLY_WATCH_SCORE:
                early_watch_list.append(setup)
    all_setups.sort(key=lambda item: item.trade_score, reverse=True)
    best_setups.sort(key=lambda item: item.trade_score, reverse=True)
    early_watch_list.sort(key=lambda item: item.early_watch_score, reverse=True)
    print_header("📌 ترتيب جميع الأسهم")
    if not all_setups:
        print("لا توجد نتائج. قد يكون السبب حدود API أو نقص البيانات.")
    else:
        for i, s in enumerate(all_setups, start=1):
            print(f"{i}. {s.symbol} | {s.setup_type} | الدرجة {s.trade_score:.1f}/10 | المخاطرة {s.risk_level} | إنذار مبكر {s.early_watch_score:.1f}/10 | {s.decision}")
    print_header("🔥 أفضل الفرص فقط")
    if not best_setups:
        print("لا توجد صفقة ممتازة الآن. القرار الاحترافي: الانتظار.")
    else:
        for setup in best_setups[:3]:
            print_setup(setup)
            print("\n🚨 تنبيه محلي: توجد فرصة تستحق المتابعة. راقب السعر داخل منطقة الدخول فقط.")
    print_header("👀 إنذارات مبكرة قبل الفرصة")
    if not early_watch_list:
        print("لا توجد أسهم قريبة من فرصة واضحة حالياً.")
    else:
        for setup in early_watch_list[:5]:
            print(
                f"{setup.symbol} | {setup.early_watch} | درجة {setup.early_watch_score:.1f}/10 | "
                f"سعر تنبيه تقريبي {money(setup.early_watch_trigger)} | السعر الحالي {money(setup.close)} | {setup.decision}"
            )
    return best_setups


def run_loop() -> None:
    print("🚀 بدأ نظام الفحص المتكرر")
    print("سيعمل فقط أثناء افتتاح السوق الأمريكي. لإيقافه اضغط CTRL+C")
    while True:
        try:
            if is_market_open():
                scan_once()
                print(f"\n⏱️ الفحص القادم بعد {SCAN_INTERVAL_SECONDS / 60:.0f} دقيقة.\n")
                time.sleep(SCAN_INTERVAL_SECONDS)
            else:
                print(f"⏸️ السوق مغلق الآن ({now_text()}). سيتم إعادة التحقق بعد {CLOSED_MARKET_SLEEP_SECONDS / 60:.0f} دقيقة.")
                time.sleep(CLOSED_MARKET_SLEEP_SECONDS)
        except KeyboardInterrupt:
            print("\nتم إيقاف النظام من المستخدم.")
            break
        except Exception as exc:
            print(f"❌ خطأ غير متوقع: {exc}")
            print("إعادة المحاولة بعد 5 دقائق...")
            time.sleep(300)

def classify_trade_risk(report: Dict) -> str:
    r = report["r_multiple"]
    dist_stop = report["distance_to_stop_percent"]
    rsi = report["rsi"]

    if r >= 1 and dist_stop > 5 and rsi < 70:
        return "منخفضة"

    if r >= 0.5 and dist_stop > 3:
        return "متوسطة"

    if dist_stop <= 2 or rsi > 75:
        return "عالية"

    return "مرتفعة جداً"

def main():
    parser = argparse.ArgumentParser(description="ماسح فرص تداول ذكي بالعربي مع إنذار مبكر")
    parser.add_argument("--scan", action="store_true", help="تشغيل فحص واحد")
    parser.add_argument("--loop", action="store_true", help="تشغيل فحص متكرر أثناء افتتاح السوق")
    parser.add_argument("--list", action="store_true", help="عرض قائمة المراقبة")
    parser.add_argument("--add", type=str, help="إضافة سهم إلى قائمة المراقبة")
    parser.add_argument("--remove", type=str, help="حذف سهم من قائمة المراقبة")
    args = parser.parse_args()
    if args.add:
        add_symbol(args.add)
    elif args.remove:
        remove_symbol(args.remove)
    elif args.list:
        print_watchlist()
    elif args.loop:
        run_loop()
    else:
        scan_once()


if __name__ == "__main__":
    main()
