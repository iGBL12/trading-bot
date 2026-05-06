from dataclasses import dataclass
from typing import List, Optional
import pandas as pd

from scanner_engine import fetch_daily_data, add_indicators


SECTOR_MAP = {
    "AMD": ["SMH", "QQQ", "NVDA", "AVGO", "TSM", "ANET"],
    "NVDA": ["SMH", "QQQ", "AMD", "AVGO", "TSM", "ANET"],
    "ANET": ["XLK", "QQQ", "NVDA", "AMD", "AVGO"],
    "MSFT": ["XLK", "QQQ", "NVDA", "AMD", "AVGO"],
    "TSLA": ["XLY", "QQQ", "RIVN", "LCID"],
}


@dataclass
class SectorSignal:
    symbol: str
    score: float
    status: str
    trigger: float
    summary: str
    notes: List[str]


def _last_row(symbol: str) -> Optional[pd.Series]:
    df = fetch_daily_data(symbol)
    if df is None or len(df) < 220:
        return None

    df = add_indicators(df).dropna()
    if df.empty:
        return None

    return df.iloc[-1]


def analyze_sector_rotation(symbol: str) -> Optional[SectorSignal]:
    symbol = symbol.upper()
    peers = SECTOR_MAP.get(symbol, [])

    if not peers:
        return None

    stock = _last_row(symbol)
    if stock is None:
        return None

    notes = []
    score = 0.0

    stock_close = float(stock["close"])
    stock_ma20 = float(stock["ma20"])
    stock_ma50 = float(stock["ma50"])
    stock_rsi = float(stock["rsi"])
    stock_rv = float(stock["relative_volume"])
    stock_5d = float(stock["return_5d"])

    strong_peers = 0
    leader_moves = []

    for peer in peers:
        row = _last_row(peer)
        if row is None:
            continue

        peer_close = float(row["close"])
        peer_ma20 = float(row["ma20"])
        peer_ma50 = float(row["ma50"])
        peer_rv = float(row["relative_volume"])
        peer_5d = float(row["return_5d"])

        peer_strong = (
            peer_close > peer_ma20
            and peer_close > peer_ma50
            and peer_5d > 2
        )

        if peer_strong:
            strong_peers += 1
            leader_moves.append(f"{peer} قوي خلال 5 أيام: {peer_5d:.2f}%")

        if peer_rv >= 1.3 and peer_5d > 1:
            score += 0.7

    if strong_peers >= 2:
        score += 3
        notes.append("عدة أسهم/صناديق مرتبطة تتحرك بقوة")

    if stock_close > stock_ma50:
        score += 1.5
        notes.append("السهم فوق MA50")

    if stock_close > stock_ma20:
        score += 1
        notes.append("السهم فوق MA20")

    if stock_5d < 2 and strong_peers >= 2:
        score += 2
        notes.append("السهم متأخر نسبياً عن القطاع وقد يلحق")

    if 50 <= stock_rsi <= 70:
        score += 1.5
        notes.append("RSI مناسب للمراقبة")

    elif stock_rsi > 75:
        score -= 1
        notes.append("RSI مرتفع جداً، لا تطارد")

    if stock_rv >= 1.2:
        score += 1
        notes.append("حجم السهم أعلى من الطبيعي")

    notes.extend(leader_moves[:4])

    score = max(0, min(score, 10))
    trigger = max(stock_ma20, stock_close * 1.02)

    if score >= 7.5:
        status = "SECTOR_ROTATION_STRONG"
        summary = "🚀 تنبيه قطاعي قوي"
    elif score >= 6:
        status = "SECTOR_ROTATION_WATCH"
        summary = "👀 مراقبة دوران سيولة"
    else:
        status = "NO_SECTOR_EDGE"
        summary = "لا توجد إشارة قطاعية كافية"

    return SectorSignal(
        symbol=symbol,
        score=score,
        status=status,
        trigger=trigger,
        summary=summary,
        notes=notes,
    )


def format_sector_alert(signal: SectorSignal) -> str:
    notes = "\n".join([f"- {n}" for n in signal.notes[:8]])

    return (
        f"{signal.summary}: {signal.symbol}\n\n"
        f"الدرجة: {signal.score:.1f}/10\n"
        f"الحالة: {signal.status}\n"
        f"سعر تنبيه تقريبي: {signal.trigger:.2f}\n\n"
        f"الأسباب:\n{notes}\n\n"
        f"القرار:\n"
        f"لا تطارد السعر. راقب اختراق واضح أو Pullback/Base قبل الدخول."
    )