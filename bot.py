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

# ── S&P 500 + NASDAQ أبرز الأسهم ─────────────────────────
WATCHLIST = [
    # Mega Cap
    "AAPL","MSFT","NVDA","AMZN","GOOGL","GOOG","META","TSLA","AVGO","BRK-B",
    # Financials
    "JPM","BAC","WFC","GS","MS","C","BLK","SCHW","COF","AXP","V","MA","PYPL",
    # Health
    "LLY","UNH","JNJ","ABBV","MRK","PFE","TMO","ABT","BMY","AMGN","GILD",
    "ISRG","BSX","EW","RMD","DXCM","MRNA","BIIB","REGN","VRTX","HUM","CI","ELV",
    # Tech
    "ORCL","CRM","ADBE","INTU","NOW","SNPS","CDNS","AMAT","LRCX","KLAC",
    "MCHP","ADI","TXN","QCOM","INTC","AMD","MU","DELL","HPQ","IBM","CSCO",
    "NET","SNOW","MDB","TEAM","CRWD","DDOG","ZS","OKTA","HUBS","PANW",
    "COIN","UBER","ABNB","LYFT","SNAP","PINS","RBLX","EA","TTWO","DKNG",
    # Consumer
    "AMZN","WMT","COST","HD","TGT","LOW","MCD","SBUX","CMG","YUM","CAVA",
    "NKE","LULU","PG","KO","PEP","PM","MO","CL","KMB","EL","ULTA",
    # Industrial
    "GE","BA","LMT","RTX","NOC","GD","HON","MMM","CAT","DE","EMR",
    "ETN","PH","ROK","AME","ITW","CME","NSC","UPS","FDX","CARR","OTIS",
    # Energy
    "XOM","CVX","COP","EOG","SLB","OXY","MPC","VLO","DVN","HES","APA",
    # Real Estate & Utilities
    "AMT","PLD","EQIX","SPG","CCI","PSA","WELL","DUK","NEE","SO","AEP","EXC",
    # Communication
    "DIS","NFLX","WBD","PARA","T","VZ","TMUS","FOX","FOXA",
    # Materials & Others
    "LIN","APD","SHW","NEM","FCX","DOW","DD","PPG",
    # Small/Mid Cap نشط
    "SOFI","AFRM","HOOD","RIVN","LCID","JOBY","ACHR","WKHS",
    "SMCI","PLTR","IONQ","RGTI","QUBT","ARQQ","BBAI",
    "MSTR","RIOT","MARA","HUT","CLSK","CIFR",
    "GME","AMC","BBBY","EXPR","KOSS",
    "NKLA","HYLN","GOEV","FSR","MULN",
    "SPCE","RKT","CLOV","WISH","SKLZ",
]
WATCHLIST = list(dict.fromkeys(WATCHLIST))

ET           = pytz.timezone("America/New_York")
COOLDOWN     = 3600          # ساعة بين إشارتين لنفس السهم
LOG_FILE     = "signals_log.json"
TIMEOUT_MINS = 120
MIN_PRICE    = 2.0
MIN_VOLUME   = 500_000
DAILY_CAP    = 10            # أقصى إشارات يومياً
MIN_SCORE    = 70            # أقل نقاط للإرسال

last_signals  = {}
daily_count   = {"date": "", "count": 0}
signal_queue  = []           # قائمة انتظار مرتبة بالنقاط

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

def get_today():
    return datetime.now(ET).strftime("%Y-%m-%d")

def daily_count_reset():
    today = get_today()
    if daily_count["date"] != today:
        daily_count["date"]  = today
        daily_count["count"] = 0

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
        "score":        signal["score"],
        "stars":        signal["stars"],
        "entry_price":  signal["price"],
        "volume_ratio": signal["volume_ratio"],
        "rsi":          signal["rsi"],
        "atr_pct":      signal.get("atr_pct", 0),
        "target1":      signal["target1"],
        "target2":      signal["target2"],
        "stop":         signal["stop"],
        "target1_pct":  signal["target1_pct"],
        "target2_pct":  signal["target2_pct"],
        "stop_pct":     signal["stop_pct"],
        "result":       "pending",
        "target1_hit":  False,
        "target2_hit":  False,
        "trailing_stop": None,
        "highest_price": signal["price"],
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

# ─── ATR ─────────────────────────────────────────────────

def calc_atr(df, period=14):
    high  = df["High"]
    low   = df["Low"]
    close = df["Close"].shift(1)
    tr = (high - low).combine(
        (high - close).abs(), max
    ).combine(
        (low - close).abs(), max
    )
    atr   = float(tr.rolling(period).mean().iloc[-1])
    price = float(df["Close"].iloc[-1])
    return round(atr / price * 100, 2)

def calc_targets(price, atr_pct):
    t1_pct   = max(0.5, min(4.0, atr_pct * 1.5))
    t2_pct   = max(1.0, min(8.0, atr_pct * 3.0))
    stop_pct = max(0.3, min(2.0, atr_pct * 0.75))
    return (
        round(price * (1 + t1_pct   / 100), 2),
        round(price * (1 + t2_pct   / 100), 2),
        round(price * (1 - stop_pct / 100), 2),
        round(t1_pct, 2), round(t2_pct, 2), round(stop_pct, 2)
    )

# ─── نظام النقاط (0-100) ──────────────────────────────────

def calc_score(vol_ratio, rsi, atr_pct, price_change_pct, strategy, gap_pct=0):
    score = 0

    # الحجم (0-35 نقطة) — الأهم
    if vol_ratio >= 5.0:   score += 35
    elif vol_ratio >= 3.0: score += 25
    elif vol_ratio >= 2.0: score += 15
    else:                  score += 5

    # الاستراتيجية (0-25 نقطة)
    if "Gap" in strategy:
        score += 25 if gap_pct > 3.0 else 15
    elif "Breakout" in strategy:
        score += 20 if price_change_pct > 2.0 else 12
    elif "VWAP" in strategy:
        score += 15
    elif "Reversal" in strategy:
        score += 18 if rsi < 25 else 10

    # RSI (0-20 نقطة)
    if "Reversal" in strategy:
        score += 20 if rsi < 25 else 10
    elif "Breakout" in strategy or "Gap" in strategy:
        if 50 <= rsi <= 70: score += 20
        elif rsi < 50:      score += 10
        else:               score += 5
    else:
        if 45 <= rsi <= 65: score += 20
        else:               score += 8

    # ATR — التقلب المناسب (0-20 نقطة)
    if 1.5 <= atr_pct <= 4.0:   score += 20  # مثالي
    elif 0.8 <= atr_pct < 1.5:  score += 12  # هادئ
    elif 4.0 < atr_pct <= 6.0:  score += 10  # متقلب
    else:                        score += 3   # خطر

    return min(score, 100)

def score_to_stars(score):
    if score >= 85: return 3
    if score >= 70: return 2
    return 1

# ─── فحص سهم ─────────────────────────────────────────────

def check_symbol(symbol):
    if symbol in last_signals:
        if time.time() - last_signals[symbol] < COOLDOWN:
            return None
    try:
        ticker = yf.Ticker(symbol)
        df5    = ticker.history(period="2d", interval="5m", auto_adjust=True)
        if df5 is None or df5.empty or len(df5) < 21:
            return None
        daily = ticker.history(period="10d", interval="1d", auto_adjust=True)
        if daily is None or daily.empty:
            return None

        price = float(df5["Close"].iloc[-1])
        vol   = float(df5["Volume"].iloc[-1])
        if price < MIN_PRICE: return None
        if float(daily["Volume"].mean()) < MIN_VOLUME: return None

        atr_pct = calc_atr(df5)
        if not atr_pct or atr_pct != atr_pct: return None
        target1, target2, stop, t1_pct, t2_pct, stop_pct = calc_targets(price, atr_pct)

        rsi_s    = calc_rsi(df5["Close"])
        rsi      = round(float(rsi_s.iloc[-1]), 1)
        rsi_prev = round(float(rsi_s.iloc[-2]), 1)
        if rsi != rsi: return None

        df5["EMA20"] = df5["Close"].ewm(span=20).mean()
        df5["EMA9"]  = df5["Close"].ewm(span=9).mean()
        df5["VWAP"]  = (df5["Close"] * df5["Volume"]).cumsum() / df5["Volume"].cumsum()

        prev         = df5.iloc[-21:-1]
        highest      = float(prev["High"].max())
        lowest       = float(prev["Low"].min())
        avg_vol      = float(prev["Volume"].mean())
        if avg_vol == 0: return None

        vol_ratio    = round(vol / avg_vol, 1)
        ema20        = float(df5["EMA20"].iloc[-1])
        ema9         = float(df5["EMA9"].iloc[-1])
        vwap         = float(df5["VWAP"].iloc[-1])
        prev_close   = float(df5["Close"].iloc[-2])
        prev_vwap    = float(df5["VWAP"].iloc[-2])
        candle_green = price > float(df5["Open"].iloc[-1])
        price_30m    = float(df5["Close"].iloc[-7]) if len(df5) >= 7 else float(df5["Close"].iloc[0])
        chg_pct      = round((price - price_30m) / price_30m * 100, 2)

        strategy_name = None
        gap_pct       = 0

        # 1. Breakout
        if price > highest and vol_ratio >= 2.0 and price > ema20:
            strategy_name = "Breakout 🚀"

        # 2. VWAP Bounce
        elif (prev_close < prev_vwap and price > vwap and
              vol_ratio > 1.2 and price > ema9 and 45 <= rsi <= 65):
            strategy_name = "VWAP Bounce 📊"

        # 3. Gap & Go
        elif len(daily) >= 2:
            prev_day_close = float(daily["Close"].iloc[-2])
            today_open     = float(daily["Open"].iloc[-1])
            gap_pct = (today_open - prev_day_close) / prev_day_close * 100
            if gap_pct > 1.5 and price > today_open and vol_ratio > 2.0:
                strategy_name = f"Gap & Go ⚡ (+{round(gap_pct,1)}%)"

        # 4. Reversal
        elif (rsi_prev < 30 and rsi > rsi_prev + 2 and
              price > lowest and vol_ratio > 1.5 and candle_green):
            strategy_name = f"Reversal 🔄 (RSI {rsi_prev}→{rsi})"

        if not strategy_name:
            return None

        # حساب النقاط
        score = calc_score(vol_ratio, rsi, atr_pct, chg_pct, strategy_name, gap_pct)

        # رفض إذا النقاط أقل من الحد
        if score < MIN_SCORE:
            return None

        last_signals[symbol] = time.time()

        return dict(
            symbol=symbol, price=round(price, 2),
            volume_ratio=vol_ratio, rsi=rsi,
            score=score,
            stars=score_to_stars(score),
            price_change=chg_pct,
            support=round(lowest, 2), resistance=round(highest, 2),
            strategy=strategy_name, atr_pct=atr_pct,
            target1=target1, target2=target2, stop=stop,
            target1_pct=t1_pct, target2_pct=t2_pct, stop_pct=stop_pct,
        )

    except Exception as e:
        print(f"خطأ {symbol}: {e}")
    return None

# ─── رسالة ───────────────────────────────────────────────

def build_message(s, rank=None):
    now_et = datetime.now(ET).strftime("%H:%M ET")
    stars  = "⭐" * s["stars"]
    change = f"+{s['price_change']}%" if s['price_change'] > 0 else f"{s['price_change']}%"
    rank_line = f"🏆 الترتيب: #{rank}\n" if rank else ""
    return (
        f"🚨 {stars} إشارة — {s['strategy']}\n"
        f"{rank_line}"
        f"النقاط: {s['score']}/100\n\n"
        f"السهم:       {s['symbol']}\n"
        f"السعر:       ${s['price']} ({change})\n"
        f"RSI:         {s['rsi']}\n"
        f"الحجم:       {s['volume_ratio']}x المتوسط\n"
        f"ATR:         {s['atr_pct']}%\n\n"
        f"📊 دعم:      ${s['support']}\n"
        f"📊 مقاومة:  ${s['resistance']}\n\n"
        f"🎯 هدف 1:    ${s['target1']} (+{s['target1_pct']}%) — بيع 33%\n"
        f"🎯 هدف 2:    ${s['target2']} (+{s['target2_pct']}%) — بيع 33%\n"
        f"📈 هدف 3:    Trailing Stop\n"
        f"🛑 الوقف:    ${s['stop']} (-{s['stop_pct']}%)\n\n"
        f"🕐 {now_et}"
    )

# ─── تقييم الإشارات ───────────────────────────────────────

async def evaluate_pending(bot):
    log     = load_log()
    updated = False
    now     = time.time()

    for entry in log:
        if entry["result"] not in ("pending", "partial", "partial2"):
            continue

        symbol      = entry["symbol"]
        entry_price = entry["entry_price"]
        target1     = entry["target1"]
        target2     = entry["target2"]
        stop        = entry["stop"]
        atr_pct     = entry.get("atr_pct", 1.0)
        entry_ts    = entry["entry_ts"]
        elapsed_min = (now - entry_ts) / 60
        t1_hit      = entry.get("target1_hit", False)
        t2_hit      = entry.get("target2_hit", False)
        trailing    = entry.get("trailing_stop", None)
        highest     = entry.get("highest_price", entry_price)

        try:
            df = yf.Ticker(symbol).history(period="1d", interval="1m")
            if df is None or df.empty: continue
            entry_time = datetime.fromtimestamp(entry_ts)
            df.index   = df.index.tz_localize(None) if df.index.tzinfo else df.index
            df_after   = df[df.index >= entry_time]
            if df_after.empty: continue

            done = False

            for i in range(len(df_after)):
                high_c  = float(df_after["High"].iloc[i])
                low_c   = float(df_after["Low"].iloc[i])

                if high_c > highest:
                    highest = high_c
                    entry["highest_price"] = highest

                # Trailing بعد هدف 2
                if t2_hit:
                    new_trail = round(highest * (1 - atr_pct / 100), 2)
                    if trailing is None or new_trail > trailing:
                        trailing = new_trail
                        entry["trailing_stop"] = trailing
                    if low_c <= trailing:
                        pnl = round(((target1-entry_price)*0.33+(target2-entry_price)*0.33+(trailing-entry_price)*0.34)/entry_price*100, 2)
                        entry.update(result="win", exit_price=trailing, pnl_pct=pnl)
                        updated = True
                        await bot.send_message(chat_id=CHAT_ID, text=(
                            f"🏁 خروج نهائي — {symbol}\n\n"
                            f"هدف 1 ✅ +{round((target1-entry_price)/entry_price*100,2)}%\n"
                            f"هدف 2 ✅ +{round((target2-entry_price)/entry_price*100,2)}%\n"
                            f"هدف 3 📈 +{round((trailing-entry_price)/entry_price*100,2)}% — Trailing\n\n"
                            f"🏆 صافي PnL: +{pnl}%\n"
                            f"📈 أعلى سعر: ${round(highest,2)}"
                        ))
                        done = True
                        break
                    continue

                if not t1_hit and high_c >= target1:
                    t1_hit = True
                    entry.update(target1_hit=True, result="partial", stop=entry_price)
                    stop = entry_price
                    updated = True
                    await bot.send_message(chat_id=CHAT_ID, text=(
                        f"✅ هدف 1 — {symbol}\n"
                        f"بيع 33% @ ${target1} (+{round((target1-entry_price)/entry_price*100,2)}%)\n"
                        f"الوقف → تعادل ${entry_price}"
                    ))

                if t1_hit and not t2_hit and high_c >= target2:
                    t2_hit = True
                    new_trail = round(target2 * (1 - atr_pct / 100), 2)
                    entry.update(target2_hit=True, result="partial2",
                                 trailing_stop=new_trail, stop=target1)
                    trailing = new_trail
                    updated = True
                    await bot.send_message(chat_id=CHAT_ID, text=(
                        f"✅ هدف 2 — {symbol}\n"
                        f"بيع 33% @ ${target2} (+{round((target2-entry_price)/entry_price*100,2)}%)\n"
                        f"📈 Trailing Stop: ${new_trail}"
                    ))

                if not t1_hit and low_c <= stop:
                    pnl = round((stop-entry_price)/entry_price*100, 2)
                    entry.update(result="loss", exit_price=stop, pnl_pct=pnl)
                    updated = True
                    await bot.send_message(chat_id=CHAT_ID, text=f"❌ وقف — {symbol}\nPnL: {pnl}%")
                    done = True
                    break

                if t1_hit and not t2_hit and low_c <= stop:
                    pnl = round(((target1-entry_price)*0.33)/entry_price*100, 2)
                    entry.update(result="partial_exit", exit_price=stop, pnl_pct=pnl)
                    updated = True
                    await bot.send_message(chat_id=CHAT_ID, text=(
                        f"⚪ خروج — {symbol}\nهدف 1 ✅\nالباقي بالتعادل\nPnL: +{pnl}%"
                    ))
                    done = True
                    break

            if done: continue

            if elapsed_min >= TIMEOUT_MINS:
                exit_p = round(float(df_after["Close"].iloc[-1]), 2)
                if t2_hit:
                    pnl = round(((target1-entry_price)*0.33+(target2-entry_price)*0.33+(exit_p-entry_price)*0.34)/entry_price*100, 2)
                elif t1_hit:
                    pnl = round(((target1-entry_price)*0.33+(exit_p-entry_price)*0.67)/entry_price*100, 2)
                else:
                    pnl = round((exit_p-entry_price)/entry_price*100, 2)
                entry.update(result="timeout", exit_price=exit_p, pnl_pct=pnl)
                updated = True
                await bot.send_message(chat_id=CHAT_ID, text=f"⏱ Timeout {symbol}\nPnL: {pnl:+}%")

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
        f"الحد اليومي: {DAILY_CAP} إشارات\n"
        f"الحد الأدنى للنقاط: {MIN_SCORE}/100\n\n"
        f"الاستراتيجيات:\n"
        f"• Breakout 🚀\n"
        f"• VWAP Bounce 📊\n"
        f"• Gap & Go ⚡\n"
        f"• Reversal 🔄\n\n"
        f"نظام الأهداف:\n"
        f"🎯 هدف 1 = ATR×1.5 → 33%\n"
        f"🎯 هدف 2 = ATR×3.0 → 33%\n"
        f"📈 هدف 3 = Trailing Stop\n\n"
        f"{status}\n\n"
        f"/scan      — فحص فوري\n"
        f"/pending   — إشارات مفتوحة\n"
        f"/stats     — تحليل الأداء\n"
        f"/top       — أفضل إشارات اليوم\n"
        f"/status    — حالة السوق"
    )

async def cmd_status(update, context: ContextTypes.DEFAULT_TYPE):
    now_et = datetime.now(ET).strftime("%Y-%m-%d %H:%M ET")
    daily_count_reset()
    if market_is_open():
        await update.message.reply_text(
            f"✅ السوق مفتوح\n🕐 {now_et}\n"
            f"إشارات اليوم: {daily_count['count']}/{DAILY_CAP}"
        )
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

async def cmd_top(update, context: ContextTypes.DEFAULT_TYPE):
    """أفضل إشارات اليوم مرتبة بالنقاط"""
    log   = load_log()
    today = get_today()
    today_signals = [e for e in log if e["time"].startswith(today)]
    if not today_signals:
        await update.message.reply_text("لا توجد إشارات اليوم بعد")
        return
    sorted_signals = sorted(today_signals, key=lambda x: x.get("score", 0), reverse=True)
    lines = [f"🏆 أفضل إشارات اليوم ({len(today_signals)})\n"]
    for i, e in enumerate(sorted_signals[:10], 1):
        stars  = "⭐" * e.get("stars", 1)
        result = {"pending":"⏳","partial":"🎯","partial2":"🎯🎯","win":"✅","loss":"❌","timeout":"⏱","partial_exit":"⚪"}.get(e["result"],"")
        lines.append(f"{i}. {e['symbol']} {stars} — {e.get('score',0)}/100 {result}")
    await update.message.reply_text("\n".join(lines))

async def cmd_stats(update, context: ContextTypes.DEFAULT_TYPE):
    log  = load_log()
    done = [e for e in log if e["result"] not in ("pending","partial","partial2")]
    if not done:
        await update.message.reply_text("لا توجد نتائج بعد")
        return
    wins     = [e for e in done if e["result"] in ("win","partial_exit")]
    losses   = [e for e in done if e["result"] == "loss"]
    timeouts = [e for e in done if e["result"] == "timeout"]
    win_rate = round(len(wins)/len(done)*100, 1)
    avg_pnl  = round(sum(e["pnl_pct"] for e in done if e["pnl_pct"])/len(done), 2)
    # أداء حسب النقاط
    high_score = [e for e in done if e.get("score",0) >= 85]
    if high_score:
        hs_wins = len([e for e in high_score if e["result"] in ("win","partial_exit")])
        hs_rate = round(hs_wins/len(high_score)*100, 1)
        hs_line = f"⭐⭐⭐ (85+): {hs_rate}% ({len(high_score)} إشارة)\n"
    else:
        hs_line = ""
    strategies = {}
    for e in done:
        s = e.get("strategy","Unknown").split()[0]
        if s not in strategies:
            strategies[s] = {"win":0,"total":0}
        strategies[s]["total"] += 1
        if e["result"] in ("win","partial_exit"):
            strategies[s]["win"] += 1
    strat_lines = ""
    for s,d in strategies.items():
        sr = round(d["win"]/d["total"]*100,1)
        strat_lines += f"  {s}: {sr}% ({d['total']} إشارة)\n"
    await update.message.reply_text(
        f"📊 تحليل الأداء\n\n"
        f"الإجمالي:        {len(done)}\n"
        f"🏆 كامل:         {len([e for e in done if e['result']=='win'])}\n"
        f"⚪ جزئي:         {len([e for e in done if e['result']=='partial_exit'])}\n"
        f"❌ خسارة:        {len(losses)}\n"
        f"⏱ timeout:       {len(timeouts)}\n\n"
        f"🎯 نسبة النجاح:  {win_rate}%\n"
        f"📈 متوسط PnL:    {avg_pnl}%\n\n"
        f"حسب النقاط:\n{hs_line}"
        f"حسب الاستراتيجية:\n{strat_lines}\n"
        f"{'✅ النظام مربح' if avg_pnl > 0 else '❌ يحتاج تعديل'}"
    )

async def cmd_pending(update, context: ContextTypes.DEFAULT_TYPE):
    log     = load_log()
    pending = [e for e in log if e["result"] in ("pending","partial","partial2")]
    if not pending:
        await update.message.reply_text("لا توجد إشارات مفتوحة")
        return
    lines = [f"⏳ مفتوحة ({len(pending)})\n"]
    for e in pending:
        stars = "⭐" * e.get("stars",1)
        if e.get("target2_hit"):   st = f"🏃 Trail@${e.get('trailing_stop','')}"
        elif e.get("target1_hit"): st = "🎯 هدف1 ✅"
        else:                      st = "⏳"
        lines.append(f"• {e['symbol']} {stars} {e.get('score',0)}pts @ ${e['entry_price']} {st}")
    await update.message.reply_text("\n".join(lines))

# ─── الفحص الرئيسي ───────────────────────────────────────

async def scan(bot):
    daily_count_reset()
    if daily_count["count"] >= DAILY_CAP:
        print(f"⛔ وصلنا الحد اليومي {DAILY_CAP} إشارات")
        return

    now_et = datetime.now(ET).strftime("%H:%M ET")
    print(f"🔍 فحص {len(WATCHLIST)} سهم... {now_et}")

    candidates = []

    for symbol in WATCHLIST:
        try:
            signal = check_symbol(symbol)
            if signal:
                candidates.append(signal)
                print(f"📌 مرشح: {signal['symbol']} — {signal['score']}/100 — {signal['strategy']}")
        except Exception as e:
            print(f"خطأ {symbol}: {e}")
        await asyncio.sleep(0.3)

    # ترتيب من الأعلى نقاطاً
    candidates.sort(key=lambda x: x["score"], reverse=True)

    # إرسال أفضل الإشارات فقط
    remaining = DAILY_CAP - daily_count["count"]
    to_send   = candidates[:remaining]

    if to_send:
        print(f"📤 إرسال {len(to_send)} إشارة من {len(candidates)} مرشح")
        for rank, signal in enumerate(to_send, 1):
            log_signal(signal)
            await bot.send_message(chat_id=CHAT_ID, text=build_message(signal, rank))
            daily_count["count"] += 1
            await asyncio.sleep(0.5)
    else:
        print("لا توجد إشارات تجاوزت الحد الأدنى للنقاط")

    print(f"✅ انتهى — إشارات اليوم: {daily_count['count']}/{DAILY_CAP}")

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
    app.add_handler(CommandHandler("status",    cmd_status))
    app.add_handler(CommandHandler("top",       cmd_top))
    app.run_polling()

if __name__ == "__main__":
    main()
