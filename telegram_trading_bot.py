import os, json, random, asyncio
from datetime import datetime, timedelta
from dotenv import load_dotenv
from capital_flow_engine import analyze_capital_flow, format_capital_flow_report
from telegram import ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters
)
from scanner_engine import build_trade_setup, analyze_market, analyze_sentiment, is_market_open
from sector_engine import analyze_sector_rotation, format_sector_alert
from monitor_engine import monitor_trade, should_alert
from scanner_engine import scan_once


load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_IDS = [
    int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()
]

USERS_FILE = "users.json"
CODES_FILE = "codes.json"
TRADES_FILE = "trades.json"
WATCHLISTS_FILE = "user_watchlists.json"
AUTO_SCAN_SECONDS = 1800  # كل 30 دقيقة
ALERT_MEMORY_FILE = "sent_alerts.json"
DEFAULT_WATCHLIST = ["AMD", "NVDA", "MSFT"]

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["📊 فحص السوق", "👀 إنذار قطاعي"],
        ["📋 قائمة المراقبة", "📈 صفقاتي"],
        ["🕒 وقت السوق", "ℹ️ المساعدة"],
    ],
    resize_keyboard=True
)

def load_json(path):
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump([], f)
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def alert_key(user_id, symbol, alert_type):
    today = datetime.now().strftime("%Y-%m-%d")
    return f"{today}:{user_id}:{symbol}:{alert_type}"

async def markettime_cmd(update, context):
    import pytz
    from datetime import datetime

    ny_tz = pytz.timezone("America/New_York")
    riyadh_tz = pytz.timezone("Asia/Riyadh")

    now_ny = datetime.now(ny_tz)
    now_riyadh = datetime.now(riyadh_tz)

    await update.message.reply_text(
        f"🕒 توقيت الرياض: {now_riyadh.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"🕒 توقيت نيويورك: {now_ny.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"حالة السوق حسب البوت: {'✅ مفتوح' if is_market_open() else '❌ مغلق'}"
    )


async def auto_scanner(context):
    app = context.application

    try:
        if not is_market_open():
            print("Auto scanner: market closed")
            return

        users = load_json(USERS_FILE)
        active_users = [u for u in users if is_user_active(u["telegram_id"])]

        if not active_users:
            print("Auto scanner: no active users")
            return

        market = await asyncio.to_thread(analyze_market)
        sentiment = await asyncio.to_thread(analyze_sentiment)

        for user in active_users:
            user_id = user["telegram_id"]
            symbols = get_user_watchlist(user_id)

            for symbol in symbols:
                setup = await asyncio.to_thread(
                    build_trade_setup,
                    symbol,
                    market,
                    sentiment
                )

                # =========================
                # إذا لم توجد صفقة عادية
                # افحص هل هناك دوران سيولة قطاعي
                # =========================
                if not setup:
                    sector_signal = await asyncio.to_thread(
                        analyze_sector_rotation,
                        symbol
                    )

                    if sector_signal and sector_signal.score >= 7.5:
                        key = alert_key(user_id, symbol, "SECTOR_ROTATION")

                        if not was_alert_sent(key):
                            await app.bot.send_message(
                                chat_id=user_id,
                                text=format_sector_alert(sector_signal)
                            )
                            mark_alert_sent(key)

                    continue

                # =========================
                # فرصة ممتازة
                # =========================
                if "صفقة ممتازة" in setup.decision:
                    key = alert_key(user_id, setup.symbol, "BEST_SETUP")

                    if not was_alert_sent(key):
                        await app.bot.send_message(
                            chat_id=user_id,
                            text=(
                                f"🔥 فرصة ممتازة: {setup.symbol}\n\n"
                                f"{setup.arabic_summary}\n\n"
                                f"الدخول: {setup.entry_low:.2f} - {setup.entry_high:.2f}\n"
                                f"الوقف: {setup.stop_loss:.2f}\n"
                                f"الهدف 1: {setup.target_1:.2f}\n"
                                f"الهدف 2: {setup.target_2:.2f}\n"
                                f"المخاطرة: {setup.risk_level}\n"
                                f"الدرجة: {setup.trade_score:.1f}/10"
                            )
                        )
                        mark_alert_sent(key)

                # =========================
                # إنذار مبكر
                # =========================
                elif setup.early_watch_score >= 7.5:
                    key = alert_key(user_id, setup.symbol, "EARLY_WATCH")

                    if not was_alert_sent(key):
                        await app.bot.send_message(
                            chat_id=user_id,
                            text=(
                                f"👀 إنذار مبكر قوي: {setup.symbol}\n\n"
                                f"الحالة: {setup.early_watch}\n"
                                f"درجة: {setup.early_watch_score:.1f}/10\n"
                                f"السعر الحالي: {setup.close:.2f}\n"
                                f"سعر تنبيه تقريبي: {setup.early_watch_trigger:.2f}"
                            )
                        )
                        mark_alert_sent(key)

                # =========================
                # تنبيه قطاعي حتى لو فيه setup
                # لكن لم يصل لفرصة ممتازة
                # =========================
                else:
                    sector_signal = await asyncio.to_thread(
                        analyze_sector_rotation,
                        symbol
                    )

                    if sector_signal and sector_signal.score >= 8.0:
                        key = alert_key(user_id, symbol, "SECTOR_ROTATION")

                        if not was_alert_sent(key):
                            await app.bot.send_message(
                                chat_id=user_id,
                                text=format_sector_alert(sector_signal)
                            )
                            mark_alert_sent(key)

        print("Auto scanner completed")

    except Exception as e:
        print(f"Auto scanner error: {e}")

def was_alert_sent(key):
    alerts = load_json(ALERT_MEMORY_FILE)
    return key in alerts


def mark_alert_sent(key):
    alerts = load_json(ALERT_MEMORY_FILE)
    alerts.append(key)
    save_json(ALERT_MEMORY_FILE, alerts)

def is_admin(user_id):
    return user_id in ADMIN_IDS


def is_user_active(user_id):
    users = load_json(USERS_FILE)
    for u in users:
        if u["telegram_id"] == user_id:
            exp = datetime.strptime(u["expires_at"], "%Y-%m-%d")
            return exp >= datetime.now()
    return False


def generate_code():
    return f"TRD-{random.randint(1000,9999)}-{random.randint(1000,9999)}"


def get_user_watchlist(user_id):
    data = load_json(WATCHLISTS_FILE)
    item = next((x for x in data if x["user_id"] == user_id), None)

    if not item:
        item = {"user_id": user_id, "symbols": DEFAULT_WATCHLIST.copy()}
        data.append(item)
        save_json(WATCHLISTS_FILE, data)

    return item["symbols"]


def save_user_watchlist(user_id, symbols):
    data = load_json(WATCHLISTS_FILE)
    item = next((x for x in data if x["user_id"] == user_id), None)

    symbols = sorted(list(set([s.upper().strip() for s in symbols if s.strip()])))

    if item:
        item["symbols"] = symbols
    else:
        data.append({"user_id": user_id, "symbols": symbols})

    save_json(WATCHLISTS_FILE, data)


async def help_cmd(update, context):
    await update.message.reply_text(
        "📌 أوامر البوت:\n\n"
        "الحساب:\n"
        "/myid\n"
        "/login CODE\n"
        "/status\n\n"
        "قائمة المراقبة:\n"
        "/watchlist\n"
        "/addsymbol NVDA\n"
        "/removesymbol AMD\n\n"
        "الفحص:\n"
        "/scan\n\n"
        "/sector AMD\n"
        "/markettime\n\n"
        "الصفقات: /addtrade SYMBOL ENTRY STOP TARGET SHARES \n"
        "/addtrade AMD 325 310 350 1\n"
        "/trades\n"
        "/closetrade 1\n"
        "/monitor\n\n"
        "/flow\n"
        "أوامر الأدمن:\n"
        "/create_code Abdul 30\n"
        "/users"
    )


async def myid(update, context):
    await update.message.reply_text(f"Telegram ID:\n{update.effective_user.id}")


async def start(update, context):
    await update.message.reply_text(
        "أهلاً بك في بوت التداول الذكي 🚀",
        reply_markup=MAIN_KEYBOARD
    )

async def create_code(update, context):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ هذا الأمر للأدمن فقط")
        return

    try:
        name = context.args[0]
        days = int(context.args[1])
    except:
        await update.message.reply_text("الاستخدام:\n/create_code Abdul 30")
        return

    codes = load_json(CODES_FILE)
    code = generate_code()
    expires_at = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")

    codes.append({
        "code": code,
        "name": name,
        "duration_days": days,
        "created_at": datetime.now().strftime("%Y-%m-%d"),
        "expires_at": expires_at,
        "used": False,
        "telegram_id": None,
        "active": True
    })

    save_json(CODES_FILE, codes)

    await update.message.reply_text(
        f"✅ تم إنشاء كود اشتراك\n\n"
        f"الاسم: {name}\n"
        f"الكود: `{code}`\n"
        f"ينتهي: {expires_at}",
        parse_mode="Markdown"
    )


async def login(update, context):
    user_id = update.effective_user.id

    try:
        code_input = context.args[0].strip()
    except:
        await update.message.reply_text("الاستخدام:\n/login CODE")
        return

    codes = load_json(CODES_FILE)
    users = load_json(USERS_FILE)

    for c in codes:
        if c["code"] == code_input:
            if not c.get("active", True):
                await update.message.reply_text("❌ الكود غير مفعل")
                return

            if c.get("used") and c.get("telegram_id") != user_id:
                await update.message.reply_text("❌ الكود مستخدم من شخص آخر")
                return

            exp = datetime.strptime(c["expires_at"], "%Y-%m-%d")
            if exp < datetime.now():
                await update.message.reply_text("❌ الكود منتهي")
                return

            c["used"] = True
            c["telegram_id"] = user_id

            existing = next((u for u in users if u["telegram_id"] == user_id), None)
            if existing:
                existing["name"] = c["name"]
                existing["expires_at"] = c["expires_at"]
                existing["code"] = code_input
            else:
                users.append({
                    "telegram_id": user_id,
                    "name": c["name"],
                    "code": code_input,
                    "expires_at": c["expires_at"],
                    "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                })

            save_json(CODES_FILE, codes)
            save_json(USERS_FILE, users)

            await update.message.reply_text(
                f"✅ تم تسجيل الدخول\n"
                f"الاسم: {c['name']}\n"
                f"ينتهي الاشتراك: {c['expires_at']}"
            )
            return

    await update.message.reply_text("❌ كود غير صحيح")


async def status(update, context):
    user_id = update.effective_user.id
    users = load_json(USERS_FILE)

    for u in users:
        if u["telegram_id"] == user_id:
            active = is_user_active(user_id)
            await update.message.reply_text(
                f"الحالة: {'✅ فعال' if active else '❌ منتهي'}\n"
                f"الاسم: {u['name']}\n"
                f"ينتهي: {u['expires_at']}"
            )
            return

    await update.message.reply_text("لم تسجل الدخول. استخدم /login CODE")


async def watchlist_cmd(update, context):
    user_id = update.effective_user.id
    if not is_user_active(user_id):
        await update.message.reply_text("❌ اشتراكك غير فعال.")
        return

    symbols = get_user_watchlist(user_id)
    await update.message.reply_text(
        "📋 قائمة المراقبة:\n\n" + "\n".join([f"- {s}" for s in symbols])
    )


async def addsymbol_cmd(update, context):
    user_id = update.effective_user.id
    if not is_user_active(user_id):
        await update.message.reply_text("❌ اشتراكك غير فعال.")
        return

    try:
        symbol = context.args[0].upper().strip()
    except:
        await update.message.reply_text("الاستخدام:\n/addsymbol NVDA")
        return

    symbols = get_user_watchlist(user_id)

    if symbol not in symbols:
        symbols.append(symbol)
        save_user_watchlist(user_id, symbols)
        await update.message.reply_text(f"✅ تم إضافة {symbol} إلى قائمة المراقبة.")
    else:
        await update.message.reply_text(f"ℹ️ {symbol} موجود مسبقاً.")


async def removesymbol_cmd(update, context):
    user_id = update.effective_user.id
    if not is_user_active(user_id):
        await update.message.reply_text("❌ اشتراكك غير فعال.")
        return

    try:
        symbol = context.args[0].upper().strip()
    except:
        await update.message.reply_text("الاستخدام:\n/removesymbol AMD")
        return

    symbols = get_user_watchlist(user_id)

    if symbol in symbols:
        symbols.remove(symbol)
        save_user_watchlist(user_id, symbols)
        await update.message.reply_text(f"✅ تم حذف {symbol} من قائمة المراقبة.")
    else:
        await update.message.reply_text(f"ℹ️ {symbol} غير موجود في القائمة.")


async def addtrade(update, context):
    user_id = update.effective_user.id
    if not is_user_active(user_id):
        await update.message.reply_text("❌ اشتراكك غير فعال. استخدم /login CODE")
        return

    try:
        symbol = context.args[0].upper()
        entry = float(context.args[1])
        stop = float(context.args[2])
        target = float(context.args[3])
        shares = int(context.args[4])
    except:
        await update.message.reply_text("الاستخدام:\n/addtrade AMD 325 310 350 1")
        return

    trades = load_json(TRADES_FILE)
    new_id = max([t.get("id", 0) for t in trades], default=0) + 1

    trade = {
        "id": new_id,
        "user_id": user_id,
        "symbol": symbol,
        "entry": entry,
        "stop": stop,
        "target": target,
        "shares": shares,
        "reason": "Telegram Manual Entry",
        "status": "OPEN",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    trades.append(trade)
    save_json(TRADES_FILE, trades)

    await update.message.reply_text(
        f"✅ تم إضافة الصفقة #{new_id}\n\n"
        f"{symbol}\n"
        f"الدخول: {entry}\n"
        f"الوقف: {stop}\n"
        f"الهدف: {target}\n"
        f"الكمية: {shares}"
    )


async def trades_cmd(update, context):
    user_id = update.effective_user.id
    trades = load_json(TRADES_FILE)

    user_trades = [
        t for t in trades
        if t.get("user_id") == user_id and t.get("status") == "OPEN"
    ]

    if not user_trades:
        await update.message.reply_text("لا توجد صفقات مفتوحة.")
        return

    msg = "📌 صفقاتك المفتوحة:\n\n"
    for t in user_trades:
        msg += (
            f"#{t['id']} - {t['symbol']}\n"
            f"دخول: {t['entry']} | وقف: {t['stop']} | هدف: {t['target']}\n"
            f"كمية: {t['shares']}\n\n"
        )

    await update.message.reply_text(msg)


async def closetrade(update, context):
    user_id = update.effective_user.id

    try:
        trade_id = int(context.args[0])
    except:
        await update.message.reply_text("الاستخدام:\n/closetrade 1")
        return

    trades = load_json(TRADES_FILE)
    found = False

    for t in trades:
        if t.get("id") == trade_id and t.get("user_id") == user_id:
            t["status"] = "CLOSED"
            t["closed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            found = True
            break

    save_json(TRADES_FILE, trades)

    await update.message.reply_text(
        f"✅ تم إغلاق الصفقة #{trade_id}" if found else "❌ لم أجد الصفقة أو ليست لك."
    )


def format_alert(report):
    rules = "\n".join([f"- {r}" for r in report.get("rules", [])])
    news = report.get("news_analysis", {})
    news_summary = news.get("summary", "لا يوجد")

    return (
        f"🚨 تنبيه صفقة: {report['symbol']}\n\n"
        f"الإجراء: {report['action']}\n"
        f"السعر الحالي: {report['current']:.2f}\n"
        f"الدخول: {report['entry']:.2f}\n"
        f"الوقف: {report['stop']:.2f}\n"
        f"الوقف المقترح: {report['suggested_stop']:.2f}\n"
        f"الهدف: {report['target']:.2f}\n"
        f"الربح/الخسارة: {report['pnl_percent']:.2f}%\n"
        f"R: {report['r_multiple']:.2f}R\n\n"
        f"الأسباب:\n{rules}\n\n"
        f"الأخبار:\n{news_summary}"
    )

async def sector_cmd(update, context):
    user_id = update.effective_user.id

    if not is_user_active(user_id):
        await update.message.reply_text("❌ اشتراكك غير فعال.")
        return

    try:
        symbol = context.args[0].upper()
    except:
        await update.message.reply_text("الاستخدام:\n/sector AMD")
        return

    signal = await asyncio.to_thread(
        analyze_sector_rotation,
        symbol
    )

    if not signal:
        await update.message.reply_text("لا توجد بيانات كافية.")
        return

    await update.message.reply_text(
        format_sector_alert(signal)
    )

async def monitor_cmd(update, context):
    user_id = update.effective_user.id
    if not is_user_active(user_id):
        await update.message.reply_text("❌ اشتراكك غير فعال.")
        return

    trades = load_json(TRADES_FILE)
    user_trades = [
        t for t in trades
        if t.get("user_id") == user_id and t.get("status") == "OPEN"
    ]

    if not user_trades:
        await update.message.reply_text("لا توجد صفقات مفتوحة.")
        return

    for trade in user_trades:
        report = monitor_trade(trade)
        if not report:
            continue

        if should_alert(report):
            await update.message.reply_text(format_alert(report))
        else:
            await update.message.reply_text(
                f"✅ {trade['symbol']}: لا يوجد تغيير مهم.\n"
                f"السعر الحالي: {report['current']:.2f}\n"
                f"الحالة: HOLD"
            )


async def scan_cmd(update, context):
    user_id = update.effective_user.id

    if not is_user_active(user_id):
        await update.message.reply_text("❌ اشتراكك غير فعال.")
        return

    symbols = get_user_watchlist(user_id)

    await update.message.reply_text(
        "🚀 بدء فحص السوق الذكي...\n"
        f"قائمة المراقبة: {', '.join(symbols)}"
    )

    try:
        market = analyze_market()
        sentiment = analyze_sentiment()

        results = []

        for symbol in symbols:
            setup = build_trade_setup(symbol, market, sentiment)
            if setup:
                results.append(setup)

        # ترتيب
        results.sort(key=lambda x: x.trade_score, reverse=True)

        best = [s for s in results if "صفقة ممتازة" in s.decision]

        early = [s for s in results if s.early_watch_score >= 6]

        # 🔥 أفضل الفرص
        if best:
            for s in best[:3]:
                await update.message.reply_text(
                    f"🔥 فرصة قوية: {s.symbol}\n\n"
                    f"{s.arabic_summary}\n\n"
                    f"الدخول: {s.entry_low:.2f} - {s.entry_high:.2f}\n"
                    f"وقف الخسارة: {s.stop_loss:.2f}\n"
                    f"الهدف 1: {s.target_1:.2f}\n"
                    f"الهدف 2: {s.target_2:.2f}\n"
                    f"المخاطرة: {s.risk_level}\n"
                    f"الدرجة: {s.trade_score:.1f}/10"
                )
        else:
            await update.message.reply_text(
                "❌ لا توجد صفقة ممتازة الآن.\nالقرار الاحترافي: الانتظار."
            )

        # 👀 إنذار مبكر
        if early:
            msg = "👀 إنذارات مبكرة:\n\n"
            for s in early[:5]:
                msg += (
                    f"{s.symbol} | {s.early_watch}\n"
                    f"درجة: {s.early_watch_score:.1f}/10\n"
                    f"سعر تنبيه: {s.early_watch_trigger:.2f}\n\n"
                )
            await update.message.reply_text(msg)

    except Exception as e:
        await update.message.reply_text(f"❌ خطأ:\n{e}")

async def users_cmd(update, context):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ هذا الأمر للأدمن فقط")
        return

    users = load_json(USERS_FILE)
    if not users:
        await update.message.reply_text("لا يوجد مستخدمون.")
        return

    msg = "👥 المستخدمون:\n\n"
    for u in users:
        msg += f"{u['name']} | {u['telegram_id']} | ينتهي: {u['expires_at']}\n"

    await update.message.reply_text(msg)

async def menu_handler(update, context):
    text = update.message.text

    if text == "📊 فحص السوق":
        await scan_cmd(update, context)

    elif text == "👀 إنذار قطاعي":
        context.args = ["AMD"]
        await sector_cmd(update, context)

    elif text == "📋 قائمة المراقبة":
        await watchlist_cmd(update, context)

    elif text == "📈 صفقاتي":
        await trades_cmd(update, context)

    elif text == "🕒 وقت السوق":
        await markettime_cmd(update, context)

    elif text == "ℹ️ المساعدة":
        await help_cmd(update, context)

async def flow_cmd(update, context):
    user_id = update.effective_user.id

    if not is_user_active(user_id):
        await update.message.reply_text("❌ اشتراكك غير فعال.")
        return

    await update.message.reply_text("💰 جاري تحليل توجه رؤوس الأموال...")

    try:
        signals = await asyncio.to_thread(analyze_capital_flow)
        await update.message.reply_text(format_capital_flow_report(signals))
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ في تحليل السيولة:\n{e}")

def main():
    if not TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing in .env")

    app = Application.builder().token(TOKEN).build()

    # Auto scanner كل 30 دقيقة، يبدأ بعد 10 ثواني
    if app.job_queue:
        app.job_queue.run_repeating(auto_scanner, interval=1800, first=60)
    else:
        print("⚠️ JobQueue is not available. Install: pip install 'python-telegram-bot[job-queue]'")

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("create_code", create_code))
    app.add_handler(CommandHandler("login", login))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("users", users_cmd))
    app.add_handler(CommandHandler("markettime", markettime_cmd))
    app.add_handler(CommandHandler("sector", sector_cmd))
    app.add_handler(CommandHandler("flow", flow_cmd))

    app.add_handler(CommandHandler("watchlist", watchlist_cmd))
    app.add_handler(CommandHandler("addsymbol", addsymbol_cmd))
    app.add_handler(CommandHandler("removesymbol", removesymbol_cmd))

    app.add_handler(CommandHandler("scan", scan_cmd))
    app.add_handler(CommandHandler("addtrade", addtrade))
    app.add_handler(CommandHandler("trades", trades_cmd))
    app.add_handler(CommandHandler("closetrade", closetrade))
    app.add_handler(CommandHandler("monitor", monitor_cmd))

    print("Telegram trading bot started...")
    app.add_handler(
    MessageHandler(filters.TEXT & ~filters.COMMAND, menu_handler)
    )
    app.run_polling()


if __name__ == "__main__":
    asyncio.set_event_loop(asyncio.new_event_loop())
    main()
