import yfinance as yf
import asyncio
import time
import json
import os
from datetime import datetime
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import os
TOKEN = os.environ.get("7699448302:AAFn54m6G-AeL8_KsqufEqdkOQqWSRAOX90")
CHAT_ID = os.environ.get("8242776558")

WATCHLIST     = ["AAPL", "NVDA", "TSLA", "MSFT", "AMD", "META"]
LOOKBACK      = 20
VOLUME_MULT   = 1.5
COOLDOWN      = 1800
LOG_FILE      = "signals_log.json"
TARGET_PCT    = 1.5
STOP_PCT      = 0.75
TIMEOUT_MINS  = 120   # ساعتان بدل ساعة

last_signals = {}

# ─── ملف التسجيل ──────────────────────────────────────────

def load_log():
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r") as f:
            return json.load(f)
    return []

def save_log(log):
    with open(LOG_FILE, "w") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)

def log_signal(signal):
    log = load_log()
    entry = {
        "id":           int(time.time()),
        "time":         datetime.now().strftime("%Y-%m-%d %H:%M"),
        "symbol":       signal["symbol"],
        "entry_price":  signal["price"],
        "volume_ratio": signal["volume_ratio"],
        "target":       round(signal["price"] * (1 + TARGET_PCT / 100), 2),
        "stop":         round(signal["price"] * (1 - STOP_PCT  / 100), 2),
        "result":       "pending",
        "exit_price":   None,
        "pnl_pct":      None,
        "entry_ts":     time.time(),
    }
    log.append(entry)
    save_log(log)
    return entry

# ─── التقييم التلقائي (شمعة بشمعة) ───────────────────────

async def evaluate_pending(bot):
    log     = load_log()
    updated = False
    now     = time.time()

    for entry in log:
        if entry["result"] != "pending":
            continue

        symbol      = entry["symbol"]
        entry_price = entry["entry_price"]
        target      = entry["target"]
        stop        = entry["stop"]
        entry_ts    = entry["entry_ts"]
        elapsed_min = (now - entry_ts) / 60

        try:
            df = yf.Ticker(symbol).history(period="1d", interval="1m")
            if df is None or df.empty:
                continue

            # ✅ نقطة الدخول الزمنية — نتجاهل الشموع قبل الإشارة
            entry_time = datetime.fromtimestamp(entry_ts)
            df.index   = df.index.tz_localize(None) if df.index.tzinfo else df.index
            df_after   = df[df.index >= entry_time]

            if df_after.empty:
                continue

            result     = None
            exit_price = None

            # ✅ محاكاة شمعة بشمعة — بدون lookahead bias
            for i in range(len(df_after)):
                candle_high = df_after["High"].iloc[i]
                candle_low  = df_after["Low"].iloc[i]

                if candle_high >= target:
                    result     = "win"
                    exit_price = target
                    break
                elif candle_low <= stop:
                    result     = "loss"
                    exit_price = stop
                    break

            # timeout بعد ساعتين
            if result is None:
                if elapsed_min >= TIMEOUT_MINS:
                    result     = "timeout"
                    exit_price = round(df_after["Close"].iloc[-1], 2)
                else:
                    continue  # لا زالت مفتوحة

            pnl = round((exit_price - entry_price) / entry_price * 100, 2)

            entry["result"]     = result
            entry["exit_price"] = exit_price
            entry["pnl_pct"]    = pnl
            updated             = True

            icon = "✅" if result == "win" else ("❌" if result == "loss" else "⏱")
            await bot.send_message(chat_id=CHAT_ID, text=(
                f"{icon} نتيجة {symbol}\n\n"
                f"النتيجة:      {result.upper()}\n"
                f"دخول:        ${entry_price}\n"
                f"خروج:        ${exit_price}\n"
                f"ربح/خسارة:   {pnl}%"
            ))

        except Exception as e:
            print(f"خطأ في تقييم {symbol}: {e}")

    if updated:
        save_log(log)

# ─── فلتر السوق ───────────────────────────────────────────

def market_is_bullish():
    try:
        spy = yf.Ticker("SPY").history(period="5d", interval="5m")
        if spy.empty or len(spy) < 20:
            return True
        return spy["Close"].iloc[-1] > spy["Close"].ewm(span=20).mean().iloc[-1]
    except:
        return True

# ─── فحص الإشارة ──────────────────────────────────────────

def check_signal(symbol):
    if symbol in last_signals:
        if time.time() - last_signals[symbol] < COOLDOWN:
            return None
    try:
        df = yf.Ticker(symbol).history(period="5d", interval="5m")
        if df is None or df.empty or len(df) < LOOKBACK + 1:
            return None

        df["EMA20"]    = df["Close"].ewm(span=20).mean()
        current_close  = df["Close"].iloc[-1]
        current_volume = df["Volume"].iloc[-1]
        prev           = df.iloc[-(LOOKBACK + 1):-1]
        highest        = prev["High"].max()
        avg_vol        = prev["Volume"].mean()

        if avg_vol == 0:
            return None

        if (current_close > highest and
            current_volume > avg_vol * VOLUME_MULT and
            current_close > df["EMA20"].iloc[-1]):

            last_signals[symbol] = time.time()
            return {
                "symbol":       symbol,
                "price":        round(current_close, 2),
                "volume_ratio": round(current_volume / avg_vol, 1),
            }
    except Exception as e:
        print(f"خطأ في {symbol}: {e}")
    return None

# ─── الرسائل ──────────────────────────────────────────────

def build_message(s, bullish):
    target = round(s["price"] * (1 + TARGET_PCT / 100), 2)
    stop   = round(s["price"] * (1 - STOP_PCT  / 100), 2)
    market = "✅ السوق صاعد" if bullish else "⚠️ السوق هابط — احذر"
    return (
        f"🚨 إشارة شراء محتملة\n\n"
        f"السهم:  {s['symbol']}\n"
        f"السعر:  ${s['price']}\n"
        f"🎯 الهدف: ${target} (+{TARGET_PCT}%)\n"
        f"🛑 الوقف: ${stop} (-{STOP_PCT}%)\n"
        f"الحجم:  {s['volume_ratio']}x المتوسط\n\n"
        f"✅ كسر أعلى مستوى\n"
        f"✅ حجم مرتفع\n"
        f"✅ فوق EMA20\n"
        f"{market}\n\n"
        f"⏱ تقييم تلقائي — شمعة بشمعة"
    )

# ─── الإحصاء ──────────────────────────────────────────────

async def cmd_stats(update, context: ContextTypes.DEFAULT_TYPE):
    log  = load_log()
    done = [e for e in log if e["result"] != "pending"]

    if not done:
        await update.message.reply_text("لا توجد نتائج بعد")
        return

    wins     = [e for e in done if e["result"] == "win"]
    losses   = [e for e in done if e["result"] == "loss"]
    timeouts = [e for e in done if e["result"] == "timeout"]
    win_rate = round(len(wins) / len(done) * 100, 1)
    avg_pnl  = round(sum(e["pnl_pct"] for e in done) / len(done), 2)

    # حساب أقصى سلسلة خسائر متتالية
    max_loss_streak = streak = 0
    for e in done:
        if e["result"] == "loss":
            streak += 1
            max_loss_streak = max(max_loss_streak, streak)
        else:
            streak = 0

    await update.message.reply_text(
        f"📊 تحليل الأداء\n\n"
        f"الإجمالي:        {len(done)}\n"
        f"✅ نجاح:         {len(wins)}\n"
        f"❌ خسارة:        {len(losses)}\n"
        f"⏱ timeout:       {len(timeouts)}\n\n"
        f"🎯 نسبة النجاح:  {win_rate}%\n"
        f"📈 متوسط PnL:    {avg_pnl}%\n"
        f"📉 أطول خسائر:   {max_loss_streak} متتالية\n\n"
        f"{'✅ النظام مربح' if avg_pnl > 0 else '❌ يحتاج تعديل'}"
    )

async def cmd_pending(update, context: ContextTypes.DEFAULT_TYPE):
    log     = load_log()
    pending = [e for e in log if e["result"] == "pending"]
    if not pending:
        await update.message.reply_text("لا توجد إشارات مفتوحة")
        return
    lines = [f"⏳ مفتوحة ({len(pending)})\n"]
    for e in pending:
        lines.append(f"• {e['symbol']} @ ${e['entry_price']} — {e['time']}")
    await update.message.reply_text("\n".join(lines))

# ─── الجدولة ──────────────────────────────────────────────

async def scan(bot):
    print("🔍 فحص...")
    bullish = market_is_bullish()
    for symbol in WATCHLIST:
        signal = check_signal(symbol)
        if signal:
            log_signal(signal)
            await bot.send_message(chat_id=CHAT_ID, text=build_message(signal, bullish))

async def run_scheduler(bot):
    while True:
        await scan(bot)
        await evaluate_pending(bot)
        await asyncio.sleep(300)

# ─── الأوامر ──────────────────────────────────────────────

async def start(update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "البوت يعمل ✅\n\n"
        "/scan    — فحص فوري\n"
        "/pending — إشارات مفتوحة\n"
        "/stats   — تحليل الأداء"
    )

async def manual_scan(update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 جاري الفحص...")
    await scan(context.bot)
    await evaluate_pending(context.bot)
    await update.message.reply_text("✅ انتهى")

async def post_init(app):
    asyncio.create_task(run_scheduler(app.bot))

def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.post_init = post_init
    app.add_handler(CommandHandler("start",   start))
    app.add_handler(CommandHandler("scan",    manual_scan))
    app.add_handler(CommandHandler("stats",   cmd_stats))
    app.add_handler(CommandHandler("pending", cmd_pending))
    app.run_polling()

if __name__ == "__main__":
    main()
