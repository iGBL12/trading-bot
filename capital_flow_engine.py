from dataclasses import dataclass
from typing import List, Optional, Dict
import pandas as pd

from scanner_engine import fetch_daily_data, add_indicators


SECTOR_GROUPS = {
    "Semiconductors / AI Chips": ["SMH", "NVDA", "AMD", "AVGO", "TSM", "MU"],
    "AI Infrastructure": ["ANET", "DELL", "SMCI", "NVDA", "AVGO"],
    "Big Tech / Cloud": ["QQQ", "XLK", "MSFT", "GOOGL", "AMZN", "META"],
    "Cybersecurity": ["PANW", "CRWD", "FTNT", "ZS", "OKTA"],
    "EV / Auto Tech": ["TSLA", "RIVN", "LCID", "XLY"],
}


@dataclass
class CapitalFlowSignal:
    group_name: str
    score: float
    status: str
    leaders: List[str]
    laggards: List[str]
    notes: List[str]


def _get_last(symbol: str) -> Optional[pd.Series]:
    df = fetch_daily_data(symbol)
    if df is None or len(df) < 220:
        return None

    df = add_indicators(df).dropna()
    if df.empty:
        return None

    return df.iloc[-1]


def _symbol_snapshot(symbol: str) -> Optional[Dict]:
    last = _get_last(symbol)
    if last is None:
        return None

    close = float(last["close"])
    ma20 = float(last["ma20"])
    ma50 = float(last["ma50"])
    ma200 = float(last["ma200"])
    rsi = float(last["rsi"])
    rv = float(last["relative_volume"])
    r5 = float(last["return_5d"])
    r20 = float(last["return_20d"])

    strength_score = 0.0
    notes = []

    if close > ma20:
        strength_score += 1
        notes.append("فوق MA20")

    if close > ma50:
        strength_score += 1.5
        notes.append("فوق MA50")

    if close > ma200:
        strength_score += 1.5
        notes.append("فوق MA200")

    if r5 > 2:
        strength_score += 1.5
        notes.append("زخم 5 أيام قوي")

    if r20 > 5:
        strength_score += 1
        notes.append("زخم 20 يوم إيجابي")

    if rv >= 1.3:
        strength_score += 1.5
        notes.append("فوليوم أعلى من الطبيعي")

    if 50 <= rsi <= 70:
        strength_score += 1
        notes.append("RSI صحي")

    elif rsi > 75:
        strength_score -= 1
        notes.append("RSI مرتفع جداً")

    strength_score = max(0, min(strength_score, 10))

    return {
        "symbol": symbol,
        "close": close,
        "rsi": rsi,
        "relative_volume": rv,
        "return_5d": r5,
        "return_20d": r20,
        "score": strength_score,
        "notes": notes,
    }


def analyze_capital_flow() -> List[CapitalFlowSignal]:
    results = []

    for group_name, symbols in SECTOR_GROUPS.items():
        snapshots = []

        for symbol in symbols:
            snap = _symbol_snapshot(symbol)
            if snap:
                snapshots.append(snap)

        if len(snapshots) < 3:
            continue

        leaders = [
            s for s in snapshots
            if s["score"] >= 6.5 and s["return_5d"] > 2
        ]

        laggards = [
            s for s in snapshots
            if 3.5 <= s["score"] <= 6.5 and s["return_5d"] < 2 and s["rsi"] < 70
        ]

        avg_score = sum(s["score"] for s in snapshots) / len(snapshots)
        avg_5d = sum(s["return_5d"] for s in snapshots) / len(snapshots)
        strong_volume_count = sum(1 for s in snapshots if s["relative_volume"] >= 1.3)

        flow_score = avg_score

        if len(leaders) >= 2:
            flow_score += 1.5

        if len(laggards) >= 1 and len(leaders) >= 2:
            flow_score += 1.0

        if strong_volume_count >= 2:
            flow_score += 1.0

        if avg_5d > 2:
            flow_score += 1.0

        flow_score = max(0, min(flow_score, 10))

        notes = [
            f"متوسط قوة المجموعة: {avg_score:.1f}/10",
            f"متوسط أداء 5 أيام: {avg_5d:.2f}%",
            f"عدد الأسهم بفوليوم قوي: {strong_volume_count}",
        ]

        if len(leaders) >= 2:
            notes.append("يوجد أكثر من قائد يتحرك داخل نفس المجموعة")

        if len(laggards) >= 1 and len(leaders) >= 2:
            notes.append("يوجد أسهم متأخرة قد تلحق بالقادة")

        if flow_score >= 8:
            status = "CAPITAL_FLOW_STRONG"
        elif flow_score >= 6.5:
            status = "CAPITAL_FLOW_WATCH"
        else:
            status = "NO_CLEAR_FLOW"

        results.append(
            CapitalFlowSignal(
                group_name=group_name,
                score=flow_score,
                status=status,
                leaders=[s["symbol"] for s in sorted(leaders, key=lambda x: x["score"], reverse=True)],
                laggards=[s["symbol"] for s in sorted(laggards, key=lambda x: x["score"], reverse=True)],
                notes=notes,
            )
        )

    results.sort(key=lambda x: x.score, reverse=True)
    return results


def format_capital_flow_report(signals: List[CapitalFlowSignal]) -> str:
    if not signals:
        return "لا توجد بيانات كافية لبناء خريطة السيولة."

    msg = "💰 خريطة توجه رؤوس الأموال\n\n"

    for s in signals[:5]:
        msg += (
            f"📌 {s.group_name}\n"
            f"الدرجة: {s.score:.1f}/10\n"
            f"الحالة: {s.status}\n"
            f"القادة: {', '.join(s.leaders) if s.leaders else 'لا يوجد'}\n"
            f"المتأخرون المرشحون: {', '.join(s.laggards) if s.laggards else 'لا يوجد'}\n"
            f"ملاحظات:\n"
        )

        for note in s.notes[:4]:
            msg += f"- {note}\n"

        msg += "\n"

    msg += (
        "الطريقة الصحيحة للاستفادة:\n"
        "القادة يحددون أين تدخل السيولة.\n"
        "المتأخرون ليسوا شراء مباشر، بل مراقبة لاختراق أو Pullback.\n"
    )

    return msg