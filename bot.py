import yfinance as yf
import asyncio
import time
import json
import os
import nest_asyncio
from datetime import datetime
import pytz
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

nest_asyncio.apply()

TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

WATCHLIST = [
    "AAPL","MSFT","NVDA","AMZN","GOOGL","META","TSLA","AVGO",
    "JPM","LLY","UNH","XOM","V","MA","COST","HD","PG","ABBV","MRK","CVX","NFLX",
    "CRM","BAC","KO","PEP","TMO","ACN","MCD","CSCO","ABT","ADBE","WMT","TXN",
    "PM","NKE","DHR","NEE","ORCL","RTX","HON","AMGN","LOW","UPS","QCOM","IBM",
    "CAT","GS","INTU","SPGI","BLK","ISRG","MDT","AXP","T","DE","GILD",
    "NOW","SYK","VRTX","ZTS","BMY","C","MO","CL","DUK","SO","PLD","AMT",
    "CI","CB","TJX","USB","PNC","REGN","HUM","ITW","CME","ETN","APD",
    "GD","NSC","FDX","EMR","MCO","PSA","F","GM","SHW","EOG","SLB","OXY",
    "KLAC","LRCX","AMAT","MCHP","ADI","SNPS","CDNS","PH","ROK","AME",
    "BIIB","MRNA","DXCM","BSX","EW","RMD",
    "AMD","INTC","MU","DELL",
    "UBER","ABNB","COIN","PYPL","SOFI",
    "NET","SNOW","MDB","TEAM","HUBS","CRWD","DDOG","ZS","OKTA",
    "DIS","WBD","AMC",
    "LEN","PHM","DHI",
    "WFC","MS","SCHW","COF",
    "COP","MPC","VLO","DVN",
    "EQIX","SPG",
    "MMM","GE","BA","LMT",
    "SBUX","CMG","CAVA",
    "SNAP","EA","TTWO","RBLX","DKNG",
]
WATCHLIST = list(dict.fromkeys(WATCHLIST))

ET = pytz.timezone("America/New_York")

COOLDOWN     = 1800
LOG_FILE     = "signals_log.json"
TARGET_PCT   = 1.5   # سيُعاد حسابه بناءً على ATR
STOP_PCT     = 0.75  # سيُعاد حسابه بناءً على ATR
TIMEOUT_MINS = 120
MIN_PRICE    = 5.0
MIN_VOLUME   = 1_000_000

last_signals = {}

# ─── السوق ───────────────────────────────────────────────

def market_is_open():
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return False
    open_t  = now.replace(hour=9,  minute=30, second=0, microsecond=0)
    close_t = now.replace(hour=16, minute=0,  second=0, microsecond=0)
    return open_t <= now <= close_t

def time_until_open():
    from datetime import timedelta
    now = datetime.now(ET)
    next_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    if now >= next_open:
        next_open += timedelta(days=1)
    while next_open.weekday() >= 5:
        next_open += timedelta(days=1)
    mins = int((next_open - now).total_seconds() / 60)
    return mins // 60, mins % 60

# ─── Log ─────────────────────────────────────────────────

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
        "time":         datetime.now(ET).strftime("%Y-%m-%d %H:%M ET"),
        "symbol":       signal["symbol"],
        "strategy":     signal["strategy"],
        "stars":        signal["stars"],
        "entry_price":  signal["price"],
        "volume_ratio": signal["volume_ratio"],
        "rsi":          signal["rsi"],
        "atr_pct":      signal.get("atr_pct", 0),
        "target":       signal["target"],
        "stop":         signal["stop"],
        "result":       "pending",
        "exit_price":   None,
        "pnl_pct":      None,
        "entry_ts":     time.time(),
    }
    log.append(entry)
    save_log(log)
    return entry

# ─── RSI ─────────────────────────────────────────────────

def calc_rsi(series, period=14):
    delta = series.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss
    return 100 - (100 / (1 + rs))

# ─── ATR — الهدف والوقف حسب تقلب السهم ──────────────────

def calc_atr(df, period=14):
    """ATR كنسبة مئوية من السعر"""
    high  = df["High"]
    low   = df["Low"]
    close = df["Close"].shift(1)
    tr    = (high - low).combine(
        (high - close).abs(), max
    ).combine(
        (low  - close).abs(), max
    )
    atr     = tr.rolling(period).mean().iloc[-1]
    price   = df["Close"].iloc[-1]
    atr_pct = round(atr / price * 100, 2)
    return atr_pct

def calc_targets(price, atr_pct):
    """
    الهدف = 1.5x ATR
    الوقف = 0.75x ATR
    حد أدنى 0.5% وحد أقصى 4%
    """
    target_pct = max(0.5, min(4.0, atr_pct * 1.5))
    stop_pct   = max(0.3, min(2.0, atr_pct * 0.75))
    target     = round(price * (1 + target_pct / 100), 2)
    stop       = round(price * (1 - stop_pct   / 100), 2)
    return target, stop, round(target_pct, 2), round(stop_pct, 2)

# ─── Stars ───────────────────────────────────────────────

def calc_stars(vol_ratio, rsi, price_change_pct, strategy):
    stars = 1
    if vol_ratio >= 3.0:
        stars += 1
    if strategy == "Reversal" and rsi < 25:
        stars += 1
    elif strategy == "Breakout" and price_change_pct > 2.0:
        stars += 1
    elif strategy == "Gap&Go" and price_change_pct > 3.0:
        stars += 1
    elif strategy == "VWAP" and vol_ratio >= 2.0:
        stars += 1
    return min(stars, 3)

# ─── فحص سهم واحد ────────────────────────────────────────

def check_symbol(symbol):
    if symbol in last_signals:
        if time.time() - last_signals[symbol] < COOLDOWN:
            return None
    try:
        ticker = yf.Ticker(symbol)

        df5 = ticker.history(period="2d", interval="5m", auto_adjust=True)
        if df5 is None or df5.empty or len(df5) < 21:
            return None

        daily = ticker.history(period="10d", interval="1d", auto_adjust=True)
        if daily is None or daily.empty:
            return None

        price = float(df5["Close"].iloc[-1])
        vol   = float(df5["Volume"].iloc[-1])

        # فلاتر أساسية
        if price < MIN_PRICE:
            return None
        avg_daily_vol = float(daily["Volume"].mean())
        if avg_daily_vol < MIN_VOLUME:
            return None

        # ATR-based targets
        atr_pct = calc_atr(df5)
        if atr_pct == 0 or atr_pct != atr_pct:  # NaN
            return None
        target, stop, target_pct, stop_pct = calc_targets(price, atr_pct)

        # مؤشرات
        rsi_s    = calc_rsi(df5["Close"])
        rsi      = round(float(rsi_s.iloc[-1]), 1)
        rsi_prev = round(float(rsi_s.iloc[-2]), 1)
        if rsi != rsi:  # NaN
            return None

        df5["EMA20"] = df5["Close"].ewm(span=20).mean()
        df5["EMA9"]  = df5["Close"].ewm(span=9).mean()
        df5["VWAP"]  = (df5["Close"] * df5["Volume"]).cumsum() / df5["Volume"].cumsum()

        prev     = df5.iloc[-21:-1]
        highest  = float(prev["High"].max())
        lowest   = float(prev["Low"].min())
        avg_vol  = float(prev["Volume"].mean())
        if avg_vol == 0:
            return None

        vol_ratio    = round(vol / avg_vol, 1)
        ema20        = float(df5["EMA20"].iloc[-1])
        ema9         = float(df5["EMA9"].iloc[-1])
        vwap         = float(df5["VWAP"].iloc[-1])
        prev_close   = float(df5["Close"].iloc[-2])
        prev_vwap    = float(df5["VWAP"].iloc[-2])
        candle_green = float(df5["Close"].iloc[-1]) > float(df5["Open"].iloc[-1])

        price_30m_ago    = float(df5["Close"].iloc[-7]) if len(df5) >= 7 else float(df5["Close"].iloc[0])
        price_change_pct = round((price - price_30m_ago) / price_30m_ago * 100, 2)

        def make_signal(strategy_name, strategy_key):
            last_signals[symbol] = time.time()
            stars = calc_stars(vol_ratio, rsi, price_change_pct, strategy_key)
            return dict(
                symbol=symbol, price=round(price, 2),
                volume_ratio=vol_ratio, rsi=rsi, stars=stars,
                price_change=price_change_pct,
                support=round(lowest, 2), resistance=round(highest, 2),
                strategy=strategy_name,
                target=target, stop=stop,
                target_pct=target_pct, stop_pct=stop_pct,
                atr_pct=atr_pct,
            )

        # ── 1. Breakout 🚀 ──────────────────────────────
        # شرط الحجم رُفع لـ 2x (كان 1.5x) — Breakout حقيقي
        if price > highest and vol_ratio >= 2.0 and price > ema20:
            return make_signal("Breakout 🚀", "Breakout")

        # ── 2. VWAP Bounce 📊 ───────────────────────────
        # RSI بين 45-65 — Bounce صحيح ليس سهم ضعيف
        if (prev_close < prev_vwap and price > vwap and
                vol_ratio > 1.2 and price > ema9 and 45 <= rsi <= 65):
            return make_signal("VWAP Bounce 📊", "VWAP")

        # ── 3. Gap & Go ⚡ ──────────────────────────────
        if len(daily) >= 2:
            prev_day_close = float(daily["Close"].iloc[-2])
            today_open     = float(daily["Open"].iloc[-1])
            gap_pct = (today_open - prev_day_close) / prev_day_close * 100
            if gap_pct > 1.5 and price > today_open and vol_ratio > 2.0:
                last_signals[symbol] = time.time()
                stars = calc_stars(vol_ratio, rsi, gap_pct, "Gap&Go")
                return dict(
                    symbol=symbol, price=round(price, 2),
                    volume_ratio=vol_ratio, rsi=rsi, stars=stars,
                    price_change=round(gap_pct, 2),
                    support=round(today_open, 2), resistance=round(highest, 2),
                    strategy=f"Gap & Go ⚡ (+{round(gap_pct,1)}%)",
                    target=target, stop=stop,
                    target_pct=target_pct, stop_pct=stop_pct,
                    atr_pct=atr_pct,
                )

        # ── 4. Reversal 🔄 ──────────────────────────────
        # RSI < 30 فقط (كان < 45) — Oversold حقيقي
        if (rsi_prev < 30 and rsi > rsi_prev + 2 and
                price > lowest and vol_ratio > 1.5 and candle_green):
            return make_signal(f"Reversal 🔄 (RSI {rsi_prev}→{rsi})", "Reversal")

    except Exception as e:
        print(f"خطأ {symbol}: {e}")
    return None

# ─── رسالة ───────────────────────────────────────────────

def build_message(s):
    now_et = datetime.now(ET).strftime("%H:%M ET")
    stars  = "⭐" * s["stars"]
    change = f"+{s['price_change']}%" if s['price_change'] > 0 else f"{s['price_change']}%"
    return (
        f"🚨 {stars} إشارة — {s['strategy']}\n\n"
        f"السهم:      {s['symbol']}\n"
        f"السعر:      ${s['price']} ({change})\n"
        f"RSI:        {s['rsi']}\n"
        f"الحجم:      {s['volume_ratio']}x المتوسط\n"
        f"ATR:        {s['atr_pct']}%\n\n"
        f"📊 دعم:     ${s['support']}\n"
        f"📊 مقاومة: ${s['resistance']}\n\n"
        f"🎯 الهدف:   ${s['target']} (+{s['target_pct']}%)\n"
        f"🛑 الوقف:   ${s['stop']} (-{s['stop_pct']}%)\n\n"
        f"🕐 {now_et}"
    )

# ─── تقييم الإشارات المفتوحة ──────────────────────────────

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
            entry_time = datetime.fromtimestamp(entry_ts)
            df.index   = df.index.tz_localize(None) if df.index.tzinfo else df.index
            df_after   = df[df.index >= entry_time]
            if df_after.empty:
                continue
            result = exit_price = None
            for i in range(len(df_after)):
                if df_after["High"].iloc[i] >= target:
                    result = "win"; exit_price = target; break
                elif df_after["Low"].iloc[i] <= stop:
                    result = "loss"; exit_price = stop; break
            if result is None:
                if elapsed_min >= TIMEOUT_MINS:
                    result = "timeout"
                    exit_price = round(float(df_after["Close"].iloc[-1]), 2)
                else:
                    continue
            pnl = round((exit_price - entry_price) / entry_price * 100, 2)
            entry["result"]     = result
            entry["exit_price"] = exit_price
            entry["pnl_pct"]    = pnl
            updated             = True
            icon = "✅" if result == "win" else ("❌" if result == "loss" else "⏱")
            await bot.send_message(chat_id=CHAT_ID, text=(
                f"{icon} نتيجة {symbol}\n\n"
                f"الاستراتيجية: {entry.get('strategy','')}\n"
                f"النتيجة:    {result.upper()}\n"
                f"دخول:      ${entry_price}\n"
                f"خروج:      ${exit_price}\n"
                f"ربح/خسارة: {pnl}%"
            ))
        except Exception as e:
            print(f"خطأ تقييم {symbol}: {e}")
    if updated:
        save_log(log)

# ─── أوامر ───────────────────────────────────────────────

async def start(update, context: ContextTypes.DEFAULT_TYPE):
    status = "✅ السوق مفتوح" if market_is_open() else "🔴 السوق مغلق"
    await update.message.reply_text(
        f"البوت يعمل ✅\n\n"
        f"يراقب {len(WATCHLIST)} سهم\n"
        f"فلتر السعر:  >${MIN_PRICE}\n"
        f"فلتر الحجم:  >{MIN_VOLUME:,}/يوم\n\n"
        f"الاستراتيجيات:\n"
        f"• Breakout 🚀  (vol ≥ 2x)\n"
        f"• VWAP Bounce 📊  (RSI 45-65)\n"
        f"• Gap & Go ⚡\n"
        f"• Reversal 🔄  (RSI < 30)\n\n"
        f"الهدف/الوقف: مبني على ATR السهم\n"
        f"قوة الإشارة: ⭐ إلى ⭐⭐⭐\n\n"
        f"{status}\n\n"
        f"/scan      — فحص فوري\n"
        f"/pending   — إشارات مفتوحة\n"
        f"/stats     — تحليل الأداء\n"
        f"/watchlist — قائمة الأسهم\n"
        f"/status    — حالة السوق"
    )

async def cmd_status(update, context: ContextTypes.DEFAULT_TYPE):
    now_et = datetime.now(ET).strftime("%Y-%m-%d %H:%M ET")
    if market_is_open():
        await update.message.reply_text(f"✅ السوق مفتوح\n🕐 {now_et}\nالفحص كل 60 ثانية")
    else:
        h, m = time_until_open()
        await update.message.reply_text(
            f"🔴 السوق مغلق\n🕐 {now_et}\n⏳ يفتح بعد {h} ساعة و{m} دقيقة"
        )

async def manual_scan(update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"🔍 جاري فحص {len(WATCHLIST)} سهم...")
    await scan(context.bot)
    await evaluate_pending(context.bot)
    await update.message.reply_text("✅ انتهى الفحص")

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
    strategies = {}
    for e in done:
        s = e.get("strategy", "Unknown").split()[0]
        if s not in strategies:
            strategies[s] = {"win": 0, "total": 0}
        strategies[s]["total"] += 1
        if e["result"] == "win":
            strategies[s]["win"] += 1
    strat_lines = ""
    for s, d in strategies.items():
        sr = round(d["win"] / d["total"] * 100, 1)
        strat_lines += f"  {s}: {sr}% ({d['total']} إشارة)\n"
    await update.message.reply_text(
        f"📊 تحليل الأداء\n\n"
        f"الإجمالي:       {len(done)}\n"
        f"✅ نجاح:        {len(wins)}\n"
        f"❌ خسارة:       {len(losses)}\n"
        f"⏱ timeout:      {len(timeouts)}\n\n"
        f"🎯 نسبة النجاح: {win_rate}%\n"
        f"📈 متوسط PnL:   {avg_pnl}%\n\n"
        f"حسب الاستراتيجية:\n{strat_lines}\n"
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
        stars = "⭐" * e.get("stars", 1)
        lines.append(f"• {e['symbol']} {stars} @ ${e['entry_price']} — {e['time']}")
    await update.message.reply_text("\n".join(lines))

async def cmd_watchlist(update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"📋 قائمة المراقبة\n\n"
        f"عدد الأسهم: {len(WATCHLIST)}\n"
        f"فلتر السعر: >${MIN_PRICE}\n"
        f"فلتر الحجم: >{MIN_VOLUME:,}/يوم\n\n"
        f"أول 20 سهم:\n" +
        "\n".join(f"• {s}" for s in WATCHLIST[:20]) +
        f"\n\n... و {len(WATCHLIST)-20} سهم آخر"
    )

# ─── الفحص الرئيسي ───────────────────────────────────────

async def scan(bot):
    now_et = datetime.now(ET).strftime("%H:%M ET")
    print(f"🔍 فحص {len(WATCHLIST)} سهم... {now_et}")
    signals_found = 0

    for symbol in WATCHLIST:
        try:
            signal = check_symbol(symbol)
            if signal:
                log_signal(signal)
                await bot.send_message(chat_id=CHAT_ID, text=build_message(signal))
                signals_found += 1
                print(f"✅ إشارة: {signal['symbol']} — {signal['strategy']}")
        except Exception as e:
            print(f"خطأ {symbol}: {e}")
        await asyncio.sleep(0.5)

    print(f"✅ انتهى — {signals_found} إشارة")

# ─── الجدولة ─────────────────────────────────────────────

async def run_scheduler(bot):
    while True:
        if market_is_open():
            await scan(bot)
            await evaluate_pending(bot)
            await asyncio.sleep(60)
        else:
            print(f"⏸ السوق مغلق {datetime.now(ET).strftime('%H:%M ET')}")
            await asyncio.sleep(300)

async def post_init(app):
    asyncio.create_task(run_scheduler(app.bot))

def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.post_init = post_init
    app.add_handler(CommandHandler("start",     start))
    app.add_handler(CommandHandler("scan",      manual_scan))
    app.add_handler(CommandHandler("stats",     cmd_stats))
    app.add_handler(CommandHandler("pending",   cmd_pending))
    app.add_handler(CommandHandler("watchlist", cmd_watchlist))
    app.add_handler(CommandHandler("status",    cmd_status))
    app.run_polling()

if __name__ == "__main__":
    main()
