import yfinance as yf
import asyncio
import time
import json
import os
import nest_asyncio
from datetime import datetime
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

nest_asyncio.apply()

TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# S&P 500 كامل
WATCHLIST = [
    "AAPL","MSFT","NVDA","AMZN","GOOGL","META","TSLA","BRK-B","AVGO","JPM",
    "LLY","UNH","XOM","V","MA","COST","HD","PG","ABBV","MRK","CVX","NFLX",
    "CRM","BAC","KO","PEP","TMO","ACN","MCD","CSCO","ABT","ADBE","WMT","TXN",
    "PM","NKE","DHR","NEE","ORCL","RTX","HON","AMGN","LOW","UPS","QCOM","IBM",
    "CAT","GS","INTU","SPGI","BLK","ISRG","ELV","MDT","AXP","T","DE","GILD",
    "NOW","SYK","MMC","VRTX","ZTS","BMY","C","MO","CL","DUK","SO","PLD","AMT",
    "CI","CB","AON","TJX","USB","PNC","REGN","HUM","ITW","CME","ETN","APD",
    "GD","NSC","FDX","EMR","MCO","PSA","F","GM","SHW","EOG","SLB","OXY",
    "KMB","CCI","WM","CARR","OTIS","CTAS","PAYX","ADP","MSCI","ICE","NXPI",
    "KLAC","LRCX","AMAT","MCHP","ADI","SNPS","CDNS","ANSS","PH","ROK","AME",
    "FTV","IDXX","ILMN","BIIB","MRNA","DXCM","ALGN","HOLX","BAX","BSX","EW",
    "HSIC","RMD","COO","VAR","XRAY","PODD","TFX","STE","ABMD","INCY","TECH",
    "VTRS","CTLT","PKI","DGX","LH","MTD","WAT","A","BIO","IQV","CRL","WST",
    "EPAM","OKTA","ZS","CRWD","DDOG","NET","SNOW","MDB","TEAM","HUBS","COUP",
    "BILL","FIVN","PCTY","PAYC","RNG","SMAR","APPN","ESTC","SUMO","PING",
    "AMD","INTC","MU","WDC","STX","NTAP","HPQ","HPE","DELL","PSTG","NTNX",
    "UBER","LYFT","ABNB","DASH","RBLX","U","HOOD","COIN","SQ","PYPL","AFRM",
    "SOFI","LC","UPST","OPEN","OFFERPAD","OPENDOOR","Z","ZILLOW","REDFIN",
    "W","ETSY","CHWY","CHEWY","BARK","PETS","WOOF","FRPT","HRMY","RXRX",
    "XNCR","PRAX","ARWR","BEAM","EDIT","NTLA","CRSP","FATE","BLUE","SGEN",
    "RCUS","IMVT","KRTX","PTGX","ACAD","SAGE","AXSM","INVA","PTCT","SRPT",
    "FOLD","RARE","AGEN","ADMA","ACHC","ENSG","AMED","AMEDISYS","LHCG",
    "PDCO","HSIC","PRGO","PBH","PRESTIGE","ENR","SPB","SPECTRUM","CHD",
    "CLOROX","CLX","CENT","CENTRAL","REYN","REYNOLDS","BRBR","BELLRING",
    "SMPL","SIMPLY","NOMD","NOMAD","TWNK","HOSTESS","NWSA","FOX","FOXA",
    "DIS","PARA","WBD","NFLX","LGF-A","AMC","CNK","IMAX","MDC","LEN","PHM",
    "TOL","NVR","DHI","KBH","MHO","SMITH","LGIH","SKY","CAVCO","PATK",
    "BECN","BLDR","IBP","TREX","AZEK","FBHS","MAS","OC","AWI","TILE",
    "LPX","UFPI","UFP","WEST","DOOR","JELD","PGTI","WMS","APOG","CSWC",
    "MAIN","GAIN","GLAD","HTGC","ARCC","PSEC","SLRC","NMFC","TPVG","GSBD",
    "BBDC","TCPC","KCAP","TICC","OXSQ","PFLT","PNNT","TRIN","CSWC","WHF",
    "HRZN","GECC","MRCC","BCSF","BKCC","CGBD","FDUS","GBDC","KCAP","NEWT",
    "ORCC","RWAY","SCM","SLRC","SSSS","TPVG","TRIN","CGBD","FCRD","FDUS"
]

# إزالة المكررات
WATCHLIST = list(dict.fromkeys(WATCHLIST))

LOOKBACK      = 20
VOLUME_MULT   = 1.5
COOLDOWN      = 1800
LOG_FILE      = "signals_log.json"
TARGET_PCT    = 1.5
STOP_PCT      = 0.75
TIMEOUT_MINS  = 120
SCAN_BATCH    = 50  # عدد الأسهم في كل دفعة

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

            result     = None
            exit_price = None

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

            if result is None:
                if elapsed_min >= TIMEOUT_MINS:
                    result     = "timeout"
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

# ─── الأوامر ──────────────────────────────────────────────

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

async def cmd_watchlist(update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"📋 قائمة المراقبة\n\n"
        f"عدد الأسهم: {len(WATCHLIST)}\n\n"
        f"أول 20 سهم:\n" +
        "\n".join(f"• {s}" for s in WATCHLIST[:20]) +
        f"\n\n... و {len(WATCHLIST)-20} سهم آخر"
    )

async def start(update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"البوت يعمل ✅\n\n"
        f"يراقب {len(WATCHLIST)} سهم\n\n"
        f"/scan      — فحص فوري\n"
        f"/pending   — إشارات مفتوحة\n"
        f"/stats     — تحليل الأداء\n"
        f"/watchlist — قائمة الأسهم"
    )

async def manual_scan(update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"🔍 جاري فحص {len(WATCHLIST)} سهم...")
    await scan(context.bot)
    await evaluate_pending(context.bot)
    await update.message.reply_text("✅ انتهى الفحص")

# ─── الجدولة ──────────────────────────────────────────────

async def scan(bot):
    print(f"🔍 فحص {len(WATCHLIST)} سهم...")
    bullish = market_is_bullish()

    # مسح على دفعات لتفادي الحظر
    for i in range(0, len(WATCHLIST), SCAN_BATCH):
        batch = WATCHLIST[i:i+SCAN_BATCH]
        for symbol in batch:
            signal = check_signal(symbol)
            if signal:
                log_signal(signal)
                await bot.send_message(chat_id=CHAT_ID, text=build_message(signal, bullish))
        await asyncio.sleep(2)  # استراحة بين الدفعات

async def run_scheduler(bot):
    while True:
        await scan(bot)
        await evaluate_pending(bot)
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
    app.run_polling()

if __name__ == "__main__":
    main()
