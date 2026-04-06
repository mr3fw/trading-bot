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

ET           = pytz.timezone("America/New_York")
COOLDOWN     = 1800
LOG_FILE     = "signals_log.json"
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
        "id":            int(time.time()),
        "time":          datetime.now(ET).strftime("%Y-%m-%d %H:%M ET"),
        "symbol":        signal["symbol"],
        "strategy":      signal["strategy"],
        "stars":         signal["stars"],
        "entry_price":   signal["price"],
        "volume_ratio":  signal["volume_ratio"],
        "rsi":           signal["rsi"],
        "atr_pct":       signal.get("atr_pct", 0),
        "target1":       signal["target1"],
        "target2":       signal["target2"],
        "stop":          signal["stop"],
        "target1_pct":   signal["target1_pct"],
        "target2_pct":   signal["target2_pct"],
        "stop_pct":      signal["stop_pct"],
        # نظام الهدف المتحرك
        "result":        "pending",
        "target1_hit":   False,
        "target2_hit":   False,
        "trailing_stop": None,   # يُفعَّل بعد هدف 2
        "highest_price": signal["price"],
        "exit_price":    None,
        "pnl_pct":       None,
        "entry_ts":      time.time(),
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
        (low  - close).abs(), max
    )
    atr     = float(tr.rolling(period).mean().iloc[-1])
    price   = float(df["Close"].iloc[-1])
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

# ─── Stars ───────────────────────────────────────────────

def calc_stars(vol_ratio, rsi, chg_pct, strategy):
    stars = 1
    if vol_ratio >= 3.0: stars += 1
    if strategy == "Reversal" and rsi < 25: stars += 1
    elif strategy == "Breakout" and chg_pct > 2.0: stars += 1
    elif strategy == "Gap&Go" and chg_pct > 3.0: stars += 1
    elif strategy == "VWAP" and vol_ratio >= 2.0: stars += 1
    return min(stars, 3)

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

        def make(name, key):
            last_signals[symbol] = time.time()
            return dict(
                symbol=symbol, price=round(price,2),
                volume_ratio=vol_ratio, rsi=rsi,
                stars=calc_stars(vol_ratio, rsi, chg_pct, key),
                price_change=chg_pct,
                support=round(lowest,2), resistance=round(highest,2),
                strategy=name, atr_pct=atr_pct,
                target1=target1, target2=target2, stop=stop,
                target1_pct=t1_pct, target2_pct=t2_pct, stop_pct=stop_pct,
            )

        if price > highest and vol_ratio >= 2.0 and price > ema20:
            return make("Breakout 🚀", "Breakout")
        if (prev_close < prev_vwap and price > vwap and
                vol_ratio > 1.2 and price > ema9 and 45 <= rsi <= 65):
            return make("VWAP Bounce 📊", "VWAP")
        if len(daily) >= 2:
            prev_day_close = float(daily["Close"].iloc[-2])
            today_open     = float(daily["Open"].iloc[-1])
            gap_pct = (today_open - prev_day_close) / prev_day_close * 100
            if gap_pct > 1.5 and price > today_open and vol_ratio > 2.0:
                last_signals[symbol] = time.time()
                return dict(
                    symbol=symbol, price=round(price,2),
                    volume_ratio=vol_ratio, rsi=rsi,
                    stars=calc_stars(vol_ratio, rsi, gap_pct, "Gap&Go"),
                    price_change=round(gap_pct,2),
                    support=round(today_open,2), resistance=round(highest,2),
                    strategy=f"Gap & Go ⚡ (+{round(gap_pct,1)}%)",
                    atr_pct=atr_pct,
                    target1=target1, target2=target2, stop=stop,
                    target1_pct=t1_pct, target2_pct=t2_pct, stop_pct=stop_pct,
                )
        if (rsi_prev < 30 and rsi > rsi_prev + 2 and
                price > lowest and vol_ratio > 1.5 and candle_green):
            return make(f"Reversal 🔄 (RSI {rsi_prev}→{rsi})", "Reversal")

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
        f"السهم:       {s['symbol']}\n"
        f"السعر:       ${s['price']} ({change})\n"
        f"RSI:         {s['rsi']}\n"
        f"الحجم:       {s['volume_ratio']}x المتوسط\n"
        f"ATR:         {s['atr_pct']}%\n\n"
        f"📊 دعم:      ${s['support']}\n"
        f"📊 مقاومة:  ${s['resistance']}\n\n"
        f"🎯 هدف 1:    ${s['target1']} (+{s['target1_pct']}%) — بيع 33%\n"
        f"🎯 هدف 2:    ${s['target2']} (+{s['target2_pct']}%) — بيع 33%\n"
        f"📈 هدف 3:    Trailing Stop يتبع السعر\n"
        f"🛑 الوقف:    ${s['stop']} (-{s['stop_pct']}%)\n\n"
        f"🕐 {now_et}"
    )

# ─── تقييم الإشارات (نظام 3 أهداف + Trailing) ────────────

async def evaluate_pending(bot):
    log     = load_log()
    updated = False
    now     = time.time()

    for entry in log:
        if entry["result"] not in ("pending", "partial", "partial2"):
            continue

        symbol       = entry["symbol"]
        entry_price  = entry["entry_price"]
        target1      = entry["target1"]
        target2      = entry["target2"]
        stop         = entry["stop"]
        atr_pct      = entry.get("atr_pct", 1.0)
        entry_ts     = entry["entry_ts"]
        elapsed_min  = (now - entry_ts) / 60
        t1_hit       = entry.get("target1_hit", False)
        t2_hit       = entry.get("target2_hit", False)
        trailing     = entry.get("trailing_stop", None)
        highest      = entry.get("highest_price", entry_price)

        try:
            df = yf.Ticker(symbol).history(period="1d", interval="1m")
            if df is None or df.empty:
                continue
            entry_time = datetime.fromtimestamp(entry_ts)
            df.index   = df.index.tz_localize(None) if df.index.tzinfo else df.index
            df_after   = df[df.index >= entry_time]
            if df_after.empty:
                continue

            done = False

            for i in range(len(df_after)):
                high_c = float(df_after["High"].iloc[i])
                low_c  = float(df_after["Low"].iloc[i])
                close_c = float(df_after["Close"].iloc[i])

                # تحديث أعلى سعر
                if high_c > highest:
                    highest = high_c
                    entry["highest_price"] = highest

                # ── مرحلة Trailing (بعد هدف 2) ──────────
                if t2_hit:
                    # الوقف المتحرك = أعلى سعر - ATR
                    new_trail = round(highest * (1 - atr_pct / 100), 2)
                    if trailing is None or new_trail > trailing:
                        trailing = new_trail
                        entry["trailing_stop"] = trailing

                    if low_c <= trailing:
                        # خروج بالوقف المتحرك
                        exit_p = trailing
                        pnl = round((
                            (target1 - entry_price) * 0.33 +
                            (target2 - entry_price) * 0.33 +
                            (exit_p  - entry_price) * 0.34
                        ) / entry_price * 100, 2)
                        entry["result"]     = "win"
                        entry["exit_price"] = exit_p
                        entry["pnl_pct"]    = pnl
                        updated = True
                        t1_pct  = round((target1 - entry_price) / entry_price * 100, 2)
                        t2_pct  = round((target2 - entry_price) / entry_price * 100, 2)
                        t3_pct  = round((exit_p  - entry_price) / entry_price * 100, 2)
                        await bot.send_message(chat_id=CHAT_ID, text=(
                            f"🏁 خروج نهائي — {symbol}\n\n"
                            f"هدف 1 ✅ +{t1_pct}% (33%)\n"
                            f"هدف 2 ✅ +{t2_pct}% (33%)\n"
                            f"هدف 3 📈 +{t3_pct}% (34%) — Trailing\n\n"
                            f"🏆 صافي PnL: +{pnl}%\n"
                            f"📈 أعلى سعر: ${round(highest,2)}"
                        ))
                        done = True
                        break
                    continue  # لا توجد أهداف ثابتة بعد هدف 2

                # ── هدف 1 ────────────────────────────────
                if not t1_hit and high_c >= target1:
                    t1_hit = True
                    entry["target1_hit"] = True
                    entry["result"]      = "partial"
                    entry["stop"]        = entry_price   # وقف للتعادل
                    stop                 = entry_price
                    updated = True
                    t1_pct = round((target1 - entry_price) / entry_price * 100, 2)
                    await bot.send_message(chat_id=CHAT_ID, text=(
                        f"✅ هدف 1 — {symbol}\n\n"
                        f"بيع 33% @ ${target1} (+{t1_pct}%)\n"
                        f"الوقف → ${entry_price} (تعادل)\n"
                        f"الباقي يستهدف ${target2}"
                    ))

                # ── هدف 2 ────────────────────────────────
                if t1_hit and not t2_hit and high_c >= target2:
                    t2_hit = True
                    entry["target2_hit"]   = True
                    entry["result"]        = "partial2"
                    entry["trailing_stop"] = round(target2 * (1 - atr_pct / 100), 2)
                    trailing               = entry["trailing_stop"]
                    entry["stop"]          = target1     # وقف عند هدف 1 مضمون
                    stop                   = target1
                    updated = True
                    t2_pct = round((target2 - entry_price) / entry_price * 100, 2)
                    await bot.send_message(chat_id=CHAT_ID, text=(
                        f"✅ هدف 2 — {symbol}\n\n"
                        f"بيع 33% @ ${target2} (+{t2_pct}%)\n"
                        f"الوقف → ${target1} (مضمون)\n"
                        f"📈 الباقي (34%) يتبعه Trailing Stop\n"
                        f"الوقف المتحرك الآن: ${trailing}"
                    ))

                # ── وقف الخسارة (قبل هدف 1) ─────────────
                if not t1_hit and low_c <= stop:
                    pnl = round((stop - entry_price) / entry_price * 100, 2)
                    entry["result"]     = "loss"
                    entry["exit_price"] = stop
                    entry["pnl_pct"]    = pnl
                    updated = True
                    await bot.send_message(chat_id=CHAT_ID, text=(
                        f"❌ وقف — {symbol}\n\n"
                        f"خروج @ ${stop}\nPnL: {pnl}%"
                    ))
                    done = True
                    break

                # ── وقف التعادل (بعد هدف 1، قبل هدف 2) ──
                if t1_hit and not t2_hit and low_c <= stop:
                    pnl = round(((target1 - entry_price) * 0.33) / entry_price * 100, 2)
                    entry["result"]     = "partial_exit"
                    entry["exit_price"] = stop
                    entry["pnl_pct"]    = pnl
                    updated = True
                    t1_pct = round((target1 - entry_price) / entry_price * 100, 2)
                    await bot.send_message(chat_id=CHAT_ID, text=(
                        f"⚪ خروج — {symbol}\n\n"
                        f"هدف 1 ✅ +{t1_pct}% (33%)\n"
                        f"الباقي خرج بالتعادل\n"
                        f"صافي PnL: +{pnl}%"
                    ))
                    done = True
                    break

            if done:
                continue

            # ── Timeout ───────────────────────────────────
            if elapsed_min >= TIMEOUT_MINS:
                exit_p = round(float(df_after["Close"].iloc[-1]), 2)
                if t2_hit:
                    pnl = round((
                        (target1 - entry_price) * 0.33 +
                        (target2 - entry_price) * 0.33 +
                        (exit_p  - entry_price) * 0.34
                    ) / entry_price * 100, 2)
                    msg = (f"⏱ Timeout {symbol}\n\n"
                           f"هدف 1 ✅ + هدف 2 ✅\n"
                           f"الباقي @ ${exit_p}\nصافي PnL: {pnl:+}%")
                elif t1_hit:
                    pnl = round(((target1-entry_price)*0.33 + (exit_p-entry_price)*0.67) / entry_price * 100, 2)
                    msg = (f"⏱ Timeout {symbol}\n\n"
                           f"هدف 1 ✅\nالباقي @ ${exit_p}\nصافي PnL: {pnl:+}%")
                else:
                    pnl = round((exit_p - entry_price) / entry_price * 100, 2)
                    msg = (f"⏱ Timeout {symbol}\n\nخروج @ ${exit_p}\nPnL: {pnl:+}%")
                entry["result"]     = "timeout"
                entry["exit_price"] = exit_p
                entry["pnl_pct"]    = pnl
                updated = True
                await bot.send_message(chat_id=CHAT_ID, text=msg)

        except Exception as e:
            print(f"خطأ تقييم {symbol}: {e}")

    if updated:
        save_log(log)

# ─── أوامر ───────────────────────────────────────────────

async def start(update, context: ContextTypes.DEFAULT_TYPE):
    status = "✅ السوق مفتوح" if market_is_open() else "🔴 السوق مغلق"
    await update.message.reply_text(
        f"البوت يعمل ✅\n\n"
        f"يراقب {len(WATCHLIST)} سهم\n\n"
        f"الاستراتيجيات:\n"
        f"• Breakout 🚀  (vol ≥ 2x)\n"
        f"• VWAP Bounce 📊  (RSI 45-65)\n"
        f"• Gap & Go ⚡\n"
        f"• Reversal 🔄  (RSI < 30)\n\n"
        f"نظام الأهداف الثلاثة:\n"
        f"🎯 هدف 1 = ATR×1.5 → بيع 33% + وقف للتعادل\n"
        f"🎯 هدف 2 = ATR×3.0 → بيع 33% + وقف لهدف 1\n"
        f"📈 هدف 3 = Trailing Stop يتبع السعر\n"
        f"🛑 وقف   = ATR×0.75\n\n"
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
    done = [e for e in log if e["result"] not in ("pending","partial","partial2")]
    if not done:
        await update.message.reply_text("لا توجد نتائج بعد")
        return
    wins     = [e for e in done if e["result"] in ("win","partial_exit")]
    losses   = [e for e in done if e["result"] == "loss"]
    timeouts = [e for e in done if e["result"] == "timeout"]
    win_rate = round(len(wins)/len(done)*100, 1)
    avg_pnl  = round(sum(e["pnl_pct"] for e in done if e["pnl_pct"])/len(done), 2)
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
        f"🏆 هدف 2+Trail:  {len([e for e in done if e['result']=='win'])}\n"
        f"⚪ هدف 1 فقط:   {len([e for e in done if e['result']=='partial_exit'])}\n"
        f"❌ خسارة:        {len(losses)}\n"
        f"⏱ timeout:       {len(timeouts)}\n\n"
        f"🎯 نسبة النجاح:  {win_rate}%\n"
        f"📈 متوسط PnL:    {avg_pnl}%\n\n"
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
        if e.get("target2_hit"):
            st = f"🏃 Trailing @ ${e.get('trailing_stop','')}"
        elif e.get("target1_hit"):
            st = "🎯 هدف 1 ✅"
        else:
            st = "⏳"
        lines.append(f"• {e['symbol']} {stars} @ ${e['entry_price']} {st} — {e['time']}")
    await update.message.reply_text("\n".join(lines))

async def cmd_watchlist(update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"📋 قائمة المراقبة\n\n"
        f"عدد الأسهم: {len(WATCHLIST)}\n\n"
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
