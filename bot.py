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

# S&P 500 كامل
WATCHLIST = [
    "AAPL","MSFT","NVDA","AMZN","GOOGL","GOOG","META","TSLA","BRK-B","AVGO",
    "JPM","LLY","UNH","XOM","V","MA","COST","HD","PG","ABBV","MRK","CVX","NFLX",
    "CRM","BAC","KO","PEP","TMO","ACN","MCD","CSCO","ABT","ADBE","WMT","TXN",
    "PM","NKE","DHR","NEE","ORCL","RTX","HON","AMGN","LOW","UPS","QCOM","IBM",
    "CAT","GS","INTU","SPGI","BLK","ISRG","ELV","MDT","AXP","T","DE","GILD",
    "NOW","SYK","MMC","VRTX","ZTS","BMY","C","MO","CL","DUK","SO","PLD","AMT",
    "CI","CB","AON","TJX","USB","PNC","REGN","HUM","ITW","CME","ETN","APD",
    "GD","NSC","FDX","EMR","MCO","PSA","F","GM","SHW","EOG","SLB","OXY",
    "KMB","CCI","WM","CARR","OTIS","CTAS","PAYX","ADP","MSCI","ICE","NXPI",
    "KLAC","LRCX","AMAT","MCHP","ADI","SNPS","CDNS","PH","ROK","AME",
    "IDXX","BIIB","MRNA","DXCM","BSX","EW","RMD","BAX",
    "AMD","INTC","MU","WDC","HPQ","HPE","DELL",
    "UBER","LYFT","ABNB","DASH","RBLX","HOOD","COIN","PYPL","AFRM","SOFI",
    "NET","SNOW","MDB","TEAM","HUBS","CRWD","DDOG","ZS","OKTA","BILL",
    "DIS","WBD","AMC","IMAX","FOX","FOXA",
    "LEN","PHM","TOL","NVR","DHI","KBH",
    "BLDR","TREX","AZEK","MAS","OC","LPX",
    "ARCC","MAIN","HTGC","PSEC",
    "JPM","WFC","BAC","C","GS","MS","SCHW","BK","STT","FITB","KEY","RF","CFG",
    "TFC","MTB","HBAN","ZION","CMA","SIVB","ALLY","COF","DFS","SYF","AXP",
    "MET","PRU","AFL","ALL","TRV","HIG","AIG","L","LNC","UNM","PFG","VOYA",
    "AMG","IVZ","BEN","TROW","STT","NTRS","SF","RJF","LM",
    "CVS","WBA","MCK","ABC","CAH","HCA","THC","UHS","CYH","ENSG",
    "ZBH","STE","VAR","HOLX","NVCR","MASI","NVST","CNMD","LMAT","MMSI",
    "XOM","CVX","COP","EOG","SLB","HAL","BKR","OXY","MPC","VLO","PSX",
    "PXD","DVN","FANG","HES","APA","MRO","OVV","SM","RRC","AR","SWN","COG",
    "NEE","DUK","SO","AEP","EXC","SRE","PEG","ED","ES","XEL","WEC","DTE",
    "ETR","FE","EIX","PPL","CMS","NI","AES","PNW","OGE","NWE","AVA","IDA",
    "AMT","PLD","CCI","EQIX","PSA","EXR","WELL","VTR","PEAK","HR","DOC",
    "SPG","MAC","TCO","PEI","CBL","WPG","SRG","PREIT","BRX","KIM","REG",
    "FRT","EQR","AVB","UDR","CPT","MAA","ESS","NVR","IRT","NXRT","AIRC",
    "ARE","BXP","SLG","VNO","KRC","OFC","CUZ","PDM","HIW","CLI","EQC",
    "CAT","DE","EMR","ETN","HON","GE","MMM","ROK","PH","ITW","ROP","CARR",
    "OTIS","TT","JCI","IR","AME","GNRC","XYL","FBHS","MAS","SWK","SNA",
    "WAB","TDY","TXT","HEI","AXON","TDG","SPR","HXL","CW","KTOS","MRCY",
    "BA","LMT","RTX","NOC","GD","L3H","DRS","LDOS","CACI","SAIC","BAH",
    "AMZN","EBAY","ETSY","W","OSTK","CHWY","PRTS","FLXS","DRVN","IAC",
    "GOOGL","META","SNAP","PINS","TWTR","MTCH","BMBL","ANGI","YELP","ZG",
    "NFLX","DIS","WBD","PARA","AMCX","SIRI","LSXMA","LGF-A","MSGM",
    "EA","ATVI","TTWO","RBLX","U","DKNG","PENN","CZR","MGM","LVS","WYNN",
    "MCD","SBUX","YUM","QSR","DPZ","WEN","JACK","SONC","TXRH","DENN",
    "CMG","SHAK","PZZA","FAT","NATH","LOCO","TACO","HABT","FRSH","CAVA"
]

WATCHLIST = list(dict.fromkeys(WATCHLIST))

ET = pytz.timezone("America/New_York")

LOOKBACK     = 20
VOLUME_MULT  = 1.5
COOLDOWN     = 1800
LOG_FILE     = "signals_log.json"
TARGET_PCT   = 1.5
STOP_PCT     = 0.75
TIMEOUT_MINS = 120
SCAN_BATCH   = 50
MIN_PRICE    = 5.0
MIN_VOLUME   = 1_000_000
MAX_RSI      = 75

last_signals = {}

# ─── السوق ────────────────────────────────────────────────

def market_is_open():
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return False
    open_t  = now.replace(hour=9,  minute=30, second=0, microsecond=0)
    close_t = now.replace(hour=16, minute=0,  second=0, microsecond=0)
    return open_t <= now <= close_t

def time_until_open():
    now = datetime.now(ET)
    next_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    if now >= next_open:
        from datetime import timedelta
        next_open += timedelta(days=1)
    while next_open.weekday() >= 5:
        from datetime import timedelta
        next_open += timedelta(days=1)
    mins = int((next_open - now).total_seconds() / 60)
    return mins // 60, mins % 60

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
        "time":         datetime.now(ET).strftime("%Y-%m-%d %H:%M ET"),
        "symbol":       signal["symbol"],
        "strategy":     signal["strategy"],
        "entry_price":  signal["price"],
        "volume_ratio": signal["volume_ratio"],
        "rsi":          signal["rsi"],
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

# ─── RSI ──────────────────────────────────────────────────

def calc_rsi(series, period=14):
    delta = series.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss
    return 100 - (100 / (1 + rs))

# ─── فحص الإشارة ──────────────────────────────────────────

def check_signal(symbol):
    if symbol in last_signals:
        if time.time() - last_signals[symbol] < COOLDOWN:
            return None
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period="5d", interval="5m")
        if df is None or df.empty or len(df) < LOOKBACK + 1:
            return None

        price  = df["Close"].iloc[-1]
        vol    = df["Volume"].iloc[-1]

        if price < MIN_PRICE:
            return None

        daily = ticker.history(period="5d", interval="1d")
        if daily.empty or daily["Volume"].mean() < MIN_VOLUME:
            return None

        rsi_s  = calc_rsi(df["Close"])
        rsi    = round(rsi_s.iloc[-1], 1)
        rsi_prev = round(rsi_s.iloc[-2], 1)

        df["EMA20"] = df["Close"].ewm(span=20).mean()
        df["EMA9"]  = df["Close"].ewm(span=9).mean()
        df["VWAP"]  = (df["Close"] * df["Volume"]).cumsum() / df["Volume"].cumsum()

        prev     = df.iloc[-(LOOKBACK + 1):-1]
        highest  = prev["High"].max()
        lowest   = prev["Low"].min()
        avg_vol  = prev["Volume"].mean()
        if avg_vol == 0:
            return None

        vol_ratio = round(vol / avg_vol, 1)
        ema20     = df["EMA20"].iloc[-1]
        ema9      = df["EMA9"].iloc[-1]
        vwap      = df["VWAP"].iloc[-1]
        prev_close = df["Close"].iloc[-2]
        prev_vwap  = df["VWAP"].iloc[-2]
        candle_green = df["Close"].iloc[-1] > df["Open"].iloc[-1]

        # ── 1. Breakout 🚀 ──────────────────────────────────
        if (rsi <= MAX_RSI and
            price > highest and
            vol_ratio > VOLUME_MULT and
            price > ema20):
            last_signals[symbol] = time.time()
            return {"symbol": symbol, "price": round(price, 2),
                    "volume_ratio": vol_ratio, "rsi": rsi,
                    "strategy": "Breakout 🚀"}

        # ── 2. VWAP Bounce 📊 ───────────────────────────────
        if (rsi <= MAX_RSI and
            prev_close < prev_vwap and
            price > vwap and
            vol_ratio > 1.2 and
            price > ema9 and rsi > 40):
            last_signals[symbol] = time.time()
            return {"symbol": symbol, "price": round(price, 2),
                    "volume_ratio": vol_ratio, "rsi": rsi,
                    "strategy": "VWAP Bounce 📊"}

        # ── 3. Gap & Go ⚡ ──────────────────────────────────
        if len(daily) >= 2:
            prev_day_close = daily["Close"].iloc[-2]
            today_open     = daily["Open"].iloc[-1]
            gap_pct = (today_open - prev_day_close) / prev_day_close * 100
            if (gap_pct > 1.5 and
                price > today_open and
                vol_ratio > 2.0 and
                rsi <= MAX_RSI):
                last_signals[symbol] = time.time()
                return {"symbol": symbol, "price": round(price, 2),
                        "volume_ratio": vol_ratio, "rsi": rsi,
                        "strategy": f"Gap & Go ⚡ (+{round(gap_pct,1)}%)"}

        # ── 4. Reversal (ارتداد من قاع) 🔄 ─────────────────
        if (rsi_prev < 45 and          # كان منخفض (مخفف من 35)
            rsi > rsi_prev and         # RSI ارتد للأعلى
            rsi > rsi_prev + 1 and     # ارتداد حقيقي مش عشوائي
            price > lowest and         # ارتد من القاع
            vol_ratio > 1.5 and        # حجم مقبول (مخفف من 2.0)
            candle_green):             # شمعة خضراء
            last_signals[symbol] = time.time()
            return {"symbol": symbol, "price": round(price, 2),
                    "volume_ratio": vol_ratio, "rsi": rsi,
                    "strategy": f"Reversal 🔄 (RSI: {rsi_prev}→{rsi})"}

    except Exception as e:
        print(f"خطأ {symbol}: {e}")
    return None

# ─── بناء الرسالة ─────────────────────────────────────────

def build_message(s):
    target  = round(s["price"] * (1 + TARGET_PCT / 100), 2)
    stop    = round(s["price"] * (1 - STOP_PCT  / 100), 2)
    now_et  = datetime.now(ET).strftime("%H:%M ET")
    return (
        f"🚨 إشارة شراء — {s['strategy']}\n\n"
        f"السهم:   {s['symbol']}\n"
        f"السعر:   ${s['price']}\n"
        f"RSI:     {s['rsi']}\n"
        f"الحجم:   {s['volume_ratio']}x المتوسط\n\n"
        f"🎯 الهدف: ${target} (+{TARGET_PCT}%)\n"
        f"🛑 الوقف: ${stop} (-{STOP_PCT}%)\n\n"
        f"🕐 {now_et}"
    )

# ─── التقييم التلقائي ──────────────────────────────────────

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
                    exit_price = round(df_after["Close"].iloc[-1], 2)
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

# ─── الأوامر ──────────────────────────────────────────────

async def start(update, context: ContextTypes.DEFAULT_TYPE):
    status = "✅ السوق مفتوح" if market_is_open() else "🔴 السوق مغلق"
    await update.message.reply_text(
        f"البوت يعمل ✅\n\n"
        f"يراقب {len(WATCHLIST)} سهم\n"
        f"فلتر السعر: >${MIN_PRICE}\n"
        f"فلتر الحجم: >{MIN_VOLUME:,}/يوم\n"
        f"فلتر RSI: <{MAX_RSI}\n\n"
        f"الاستراتيجيات:\n"
        f"• Breakout 🚀\n"
        f"• VWAP Bounce 📊\n"
        f"• Gap & Go ⚡\n"
        f"• Reversal 🔄\n\n"
        f"{status}\n"
        f"الفحص: كل 60 ثانية خلال السوق\n\n"
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
        lines.append(f"• {e['symbol']} ({e.get('strategy','').split()[0]}) @ ${e['entry_price']} — {e['time']}")
    await update.message.reply_text("\n".join(lines))

async def cmd_watchlist(update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"📋 قائمة المراقبة\n\n"
        f"عدد الأسهم: {len(WATCHLIST)}\n"
        f"فلتر السعر: >${MIN_PRICE}\n"
        f"فلتر الحجم: >{MIN_VOLUME:,}/يوم\n"
        f"فلتر RSI:   <{MAX_RSI}\n\n"
        f"أول 20 سهم:\n" +
        "\n".join(f"• {s}" for s in WATCHLIST[:20]) +
        f"\n\n... و {len(WATCHLIST)-20} سهم آخر"
    )

# ─── الفحص والجدولة ───────────────────────────────────────

async def scan(bot):
    print(f"🔍 فحص {len(WATCHLIST)} سهم... {datetime.now(ET).strftime('%H:%M ET')}")
    for i in range(0, len(WATCHLIST), SCAN_BATCH):
        batch = WATCHLIST[i:i+SCAN_BATCH]
        for symbol in batch:
            signal = check_signal(symbol)
            if signal:
                log_signal(signal)
                await bot.send_message(chat_id=CHAT_ID, text=build_message(signal))
        await asyncio.sleep(3)

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

