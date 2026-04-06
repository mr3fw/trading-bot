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
    "AAPL","MSFT","NVDA","AMZN","GOOGL","GOOG","META","TSLA","BRK-B","AVGO",
    "JPM","LLY","UNH","XOM","V","MA","COST","HD","PG","ABBV","MRK","CVX","NFLX",
    "CRM","BAC","KO","PEP","TMO","ACN","MCD","CSCO","ABT","ADBE","WMT","TXN",
    "PM","NKE","DHR","NEE","ORCL","RTX","HON","AMGN","LOW","UPS","QCOM","IBM",
    "CAT","GS","INTU","SPGI","BLK","ISRG","ELV","MDT","AXP","T","DE","GILD",
    "NOW","SYK","VRTX","ZTS","BMY","C","MO","CL","DUK","SO","PLD","AMT",
    "CI","CB","TJX","USB","PNC","REGN","HUM","ITW","CME","ETN","APD",
    "GD","NSC","FDX","EMR","MCO","PSA","F","GM","SHW","EOG","SLB","OXY",
    "KMB","CCI","WM","CARR","OTIS","CTAS","PAYX","ADP","MSCI","ICE",
    "KLAC","LRCX","AMAT","MCHP","ADI","SNPS","CDNS","PH","ROK","AME",
    "BIIB","MRNA","DXCM","BSX","EW","RMD","BAX",
    "AMD","INTC","MU","WDC","HPQ","HPE","DELL",
    "UBER","LYFT","ABNB","COIN","PYPL","AFRM","SOFI",
    "NET","SNOW","MDB","TEAM","HUBS","CRWD","DDOG","ZS","OKTA",
    "DIS","WBD","AMC","FOX","FOXA",
    "LEN","PHM","DHI","KBH",
    "BLDR","TREX","MAS","OC",
    "ARCC","MAIN","HTGC",
    "WFC","MS","SCHW","COF","DFS","SYF",
    "MET","PRU","AFL","ALL","TRV","AIG",
    "CVS","WBA","HCA",
    "ZBH","HOLX","NVST",
    "COP","MPC","VLO","PSX","PXD","DVN","HES","APA","MRO",
    "AEP","EXC","SRE","PEG","ED","XEL","WEC","DTE",
    "EQIX","EXR","WELL","VTR","SPG",
    "MMM","GE","TT","JCI","IR","GNRC","XYL","SWK",
    "BA","LMT","NOC","L3H","LDOS","CACI","SAIC","BAH",
    "EBAY","ETSY","CHWY",
    "SNAP","PINS","MTCH","BMBL",
    "EA","TTWO","RBLX","DKNG","PENN","MGM","LVS","WYNN",
    "SBUX","YUM","QSR","DPZ","CMG","CAVA",
    "SBAC","AMT","CCI"
]

WATCHLIST = list(dict.fromkeys(WATCHLIST))

ET = pytz.timezone("America/New_York")

COOLDOWN     = 1800
LOG_FILE     = "signals_log.json"
TARGET_PCT   = 1.5
STOP_PCT     = 0.75
TIMEOUT_MINS = 120
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
    from datetime import timedelta
    now = datetime.now(ET)
    next_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    if now >= next_open:
        next_open += timedelta(days=1)
    while next_open.weekday() >= 5:
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
        "stars":        signal["stars"],
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

# ─── قوة الإشارة ──────────────────────────────────────────

def calc_stars(vol_ratio, rsi, price_change_pct, strategy):
    stars = 1
    if vol_ratio >= 3.0:
        stars += 1
    if strategy == "Reversal" and rsi < 35:
        stars += 1
    elif strategy == "Breakout" and price_change_pct > 1.5:
        stars += 1
    elif strategy == "Gap&Go" and price_change_pct > 3.0:
        stars += 1
    return min(stars, 3)

# ─── الفحص السريع (batch) ─────────────────────────────────

def fetch_batch(symbols):
    """جلب بيانات دفعة واحدة — أسرع بـ 10x"""
    try:
        data = yf.download(
            symbols,
            period="5d",
            interval="5m",
            group_by="ticker",
            auto_adjust=True,
            progress=False,
            threads=True
        )
        return data
    except Exception as e:
        print(f"خطأ batch: {e}")
        return None

# ─── فحص سهم واحد ─────────────────────────────────────────

def check_signal(symbol, df5, daily):
    if symbol in last_signals:
        if time.time() - last_signals[symbol] < COOLDOWN:
            return None
    try:
        if df5 is None or df5.empty or len(df5) < 21:
            return None

        price  = df5["Close"].iloc[-1]
        vol    = df5["Volume"].iloc[-1]

        if price < MIN_PRICE:
            return None
        if daily is None or daily.empty or daily["Volume"].mean() < MIN_VOLUME:
            return None

        rsi_s    = calc_rsi(df5["Close"])
        rsi      = round(rsi_s.iloc[-1], 1)
        rsi_prev = round(rsi_s.iloc[-2], 1)

        if rsi > MAX_RSI:
            return None

        df5["EMA20"] = df5["Close"].ewm(span=20).mean()
        df5["EMA9"]  = df5["Close"].ewm(span=9).mean()
        df5["VWAP"]  = (df5["Close"] * df5["Volume"]).cumsum() / df5["Volume"].cumsum()

        prev      = df5.iloc[-21:-1]
        highest   = prev["High"].max()
        lowest    = prev["Low"].min()
        avg_vol   = prev["Volume"].mean()

        if avg_vol == 0:
            return None

        vol_ratio    = round(vol / avg_vol, 1)
        ema20        = df5["EMA20"].iloc[-1]
        ema9         = df5["EMA9"].iloc[-1]
        vwap         = df5["VWAP"].iloc[-1]
        prev_close   = df5["Close"].iloc[-2]
        prev_vwap    = df5["VWAP"].iloc[-2]
        candle_green = df5["Close"].iloc[-1] > df5["Open"].iloc[-1]

        # حساب تغير السعر في آخر 30 دقيقة (6 شمعات)
        price_30m_ago   = df5["Close"].iloc[-7] if len(df5) >= 7 else df5["Close"].iloc[0]
        price_change_pct = round((price - price_30m_ago) / price_30m_ago * 100, 2)

        # ── 1. Breakout 🚀 ──────────────────────────────────
        if (price > highest and vol_ratio > 1.5 and price > ema20):
            stars = calc_stars(vol_ratio, rsi, price_change_pct, "Breakout")
            last_signals[symbol] = time.time()
            return {"symbol": symbol, "price": round(price, 2),
                    "volume_ratio": vol_ratio, "rsi": rsi, "stars": stars,
                    "price_change": price_change_pct,
                    "support": round(lowest, 2), "resistance": round(highest, 2),
                    "strategy": "Breakout 🚀"}

        # ── 2. VWAP Bounce 📊 ───────────────────────────────
        if (prev_close < prev_vwap and price > vwap and
                vol_ratio > 1.2 and price > ema9 and rsi > 40):
            stars = calc_stars(vol_ratio, rsi, price_change_pct, "VWAP")
            last_signals[symbol] = time.time()
            return {"symbol": symbol, "price": round(price, 2),
                    "volume_ratio": vol_ratio, "rsi": rsi, "stars": stars,
                    "price_change": price_change_pct,
                    "support": round(vwap, 2), "resistance": round(highest, 2),
                    "strategy": "VWAP Bounce 📊"}

        # ── 3. Gap & Go ⚡ ──────────────────────────────────
        if len(daily) >= 2:
            prev_day_close = daily["Close"].iloc[-2]
            today_open     = daily["Open"].iloc[-1]
            gap_pct = (today_open - prev_day_close) / prev_day_close * 100
            if (gap_pct > 1.5 and price > today_open and vol_ratio > 2.0):
                stars = calc_stars(vol_ratio, rsi, gap_pct, "Gap&Go")
                last_signals[symbol] = time.time()
                return {"symbol": symbol, "price": round(price, 2),
                        "volume_ratio": vol_ratio, "rsi": rsi, "stars": stars,
                        "price_change": round(gap_pct, 2),
                        "support": round(today_open, 2), "resistance": round(highest, 2),
                        "strategy": f"Gap & Go ⚡ (+{round(gap_pct,1)}%)"}

        # ── 4. Reversal 🔄 ──────────────────────────────────
        if (rsi_prev < 45 and rsi > rsi_prev + 1 and
                price > lowest and vol_ratio > 1.5 and candle_green):
            stars = calc_stars(vol_ratio, rsi, price_change_pct, "Reversal")
            last_signals[symbol] = time.time()
            return {"symbol": symbol, "price": round(price, 2),
                    "volume_ratio": vol_ratio, "rsi": rsi, "stars": stars,
                    "price_change": price_change_pct,
                    "support": round(lowest, 2), "resistance": round(highest, 2),
                    "strategy": f"Reversal 🔄 (RSI {rsi_prev}→{rsi})"}

    except Exception as e:
        print(f"خطأ {symbol}: {e}")
    return None

# ─── بناء الرسالة ─────────────────────────────────────────

def build_message(s):
    target   = round(s["price"] * (1 + TARGET_PCT / 100), 2)
    stop     = round(s["price"] * (1 - STOP_PCT  / 100), 2)
    now_et   = datetime.now(ET).strftime("%H:%M ET")
    stars    = "⭐" * s["stars"]
    change   = f"+{s['price_change']}%" if s['price_change'] > 0 else f"{s['price_change']}%"
    return (
        f"🚨 {stars} إشارة — {s['strategy']}\n\n"
        f"السهم:      {s['symbol']}\n"
        f"السعر:      ${s['price']} ({change})\n"
        f"RSI:        {s['rsi']}\n"
        f"الحجم:      {s['volume_ratio']}x المتوسط\n\n"
        f"📊 دعم:     ${s['support']}\n"
        f"📊 مقاومة: ${s['resistance']}\n\n"
        f"🎯 الهدف:   ${target} (+{TARGET_PCT}%)\n"
        f"🛑 الوقف:   ${stop} (-{STOP_PCT}%)\n\n"
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
        f"قوة الإشارة: ⭐ إلى ⭐⭐⭐\n\n"
        f"{status}\n"
        f"الفحص: كل 60 ثانية — batch mode (سريع)\n\n"
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
    await update.message.reply_text(f"🔍 جاري فحص {len(WATCHLIST)} سهم (batch mode)...")
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
        f"فلتر الحجم: >{MIN_VOLUME:,}/يوم\n"
        f"فلتر RSI:   <{MAX_RSI}\n\n"
        f"أول 20 سهم:\n" +
        "\n".join(f"• {s}" for s in WATCHLIST[:20]) +
        f"\n\n... و {len(WATCHLIST)-20} سهم آخر"
    )

# ─── الفحص السريع ─────────────────────────────────────────

async def scan(bot):
    now_et = datetime.now(ET).strftime("%H:%M ET")
    print(f"🔍 فحص {len(WATCHLIST)} سهم... {now_et}")

    signals_found = 0
    batch_size    = 100  # نجلب 100 سهم دفعة واحدة

    for i in range(0, len(WATCHLIST), batch_size):
        batch = WATCHLIST[i:i+batch_size]

        try:
            # جلب بيانات 5 دقائق — batch
            raw = yf.download(
                batch, period="5d", interval="5m",
                group_by="ticker", auto_adjust=True,
                progress=False, threads=True
            )

            # جلب بيانات يومية — batch
            raw_d = yf.download(
                batch, period="5d", interval="1d",
                group_by="ticker", auto_adjust=True,
                progress=False, threads=True
            )

            for symbol in batch:
                try:
                    # استخراج بيانات السهم من batch
                    if len(batch) == 1:
                        df5   = raw.copy()   if raw is not None and not raw.empty else None
                        daily = raw_d.copy() if raw_d is not None and not raw_d.empty else None
                    else:
                        try:
                            df5 = raw.xs(symbol, axis=1, level=1).copy() if raw is not None and not raw.empty else None
                        except:
                            df5 = None
                        try:
                            daily = raw_d.xs(symbol, axis=1, level=1).copy() if raw_d is not None and not raw_d.empty else None
                        except:
                            daily = None

                    signal = check_signal(symbol, df5, daily)
                    if signal:
                        log_signal(signal)
                        await bot.send_message(chat_id=CHAT_ID, text=build_message(signal))
                        signals_found += 1

                except Exception as e:
                    print(f"خطأ {symbol}: {e}")

        except Exception as e:
            print(f"خطأ batch {i}: {e}")

        await asyncio.sleep(2)  # استراحة بين الدفعات

    print(f"✅ انتهى — {signals_found} إشارة")

# ─── الجدولة ──────────────────────────────────────────────

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

