"""
بوت اصطياد قاع الأسهم الأمريكية للاستثمار طويل المدى
استراتيجية Wiley + معايير القيمة من Benjamin Graham
"""
import yfinance as yf
import asyncio
import json
import os
import nest_asyncio
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from telegram import Update

nest_asyncio.apply()

TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
ET      = ZoneInfo("America/New_York")

# ═══════════════════════════════════════════════════════════
# قائمة المراقبة - شركات قوية معروفة (S&P 500 + شركات قيادية)
# تركيز على شركات ناضجة ذات أساسيات قوية محتمل تنخفض مؤقتاً
# ═══════════════════════════════════════════════════════════
WATCHLIST = [
    # Mega Cap Tech
    "AAPL","MSFT","GOOGL","AMZN","META","NVDA","TSLA","AVGO","ORCL","ADBE",
    "CRM","CSCO","INTC","AMD","QCOM","TXN","IBM","INTU","NOW","UBER",
    # Healthcare & Pharma (موقع ممتاز للاصطياد)
    "JNJ","LLY","UNH","ABBV","MRK","PFE","TMO","ABT","DHR","BMY",
    "AMGN","GILD","CVS","CI","HUM","ELV","ISRG","REGN","VRTX","BIIB",
    "MRNA","NVO","NVS","AZN","SNY","BSX","SYK","MDT","BDX","EW",
    # Financials
    "JPM","BAC","WFC","C","GS","MS","BLK","SCHW","AXP","V",
    "MA","SPGI","MCO","ICE","CME","COF","USB","PNC","TFC","BK",
    # Consumer
    "WMT","HD","COST","PG","KO","PEP","MCD","NKE","SBUX","LOW",
    "TGT","DG","DLTR","KHC","MDLZ","CL","KMB","EL","CHWY","LULU",
    # Industrial & Energy
    "XOM","CVX","COP","SLB","EOG","OXY","PSX","VLO","MPC","KMI",
    "CAT","DE","BA","RTX","HON","LMT","GE","UPS","FDX","UNP",
    # Real Estate & Utilities
    "PLD","AMT","EQIX","CCI","PSA","O","SPG","WELL","DLR","SBAC",
    "NEE","DUK","SO","D","AEP","XEL","SRE","EXC","PCG","ED",
    # Tech Growth (اصطياد قمم)
    "PYPL","SQ","SHOP","SNOW","NET","DDOG","CRWD","ZS","OKTA","TEAM",
    "MDB","ESTC","DOCU","ZM","TWLO","ROKU","PINS","SNAP","SPOT","RBLX",
    # Real Estate / Mortgage (مثل Rocket)
    "RKT","UWMC","COOP","PHM","DHI","LEN","NVR","TOL","KBH","TMHC",
    # Automotive
    "F","GM","RIVN","LCID","STLA","TM","HMC","NIO","XPEV","LI",
    # Materials
    "LIN","APD","SHW","ECL","NEM","FCX","DOW","DD","PPG","NUE",
    # Discretionary
    "DIS","NFLX","CMCSA","T","VZ","TMUS","CHTR","PARA","WBD","FOX",
    # Travel & Hospitality
    "BKNG","ABNB","MAR","HLT","CCL","RCL","NCLH","UAL","DAL","AAL",
    # Semiconductors
    "TSM","ASML","AMAT","LRCX","KLAC","MCHP","ADI","NXPI","MU","WDC",
]
WATCHLIST = list(dict.fromkeys(WATCHLIST))

# ═══════════════════════════════════════════════════════════
# معايير اصطياد القاع (Wiley Strategy + Value Criteria)
# ═══════════════════════════════════════════════════════════
NEAR_LOW_PCT      = 15.0    # ضمن 15% من قاع 52 أسبوع
MIN_DROP_PCT      = 20.0    # انخفاض 20%+ من القمة
MIN_MARKET_CAP    = 1e9     # 1 مليار دولار حد أدنى
MAX_PE            = 30.0    # P/E أقل من 30 (مرن للنمو)
MAX_DEBT_EQUITY   = 2.0     # نسبة الدين/الملكية
MIN_ROE           = 0.05    # ROE 5%+ (مخفف لاستيعاب أزمات مؤقتة)
MIN_PRICE         = 5.0     # تجنب الأسهم البخسة
TOP_RESULTS       = 15      # عدد أفضل الفرص

LOG_FILE = "long_term_watchlist.json"

# ═══════════════════════════════════════════════════════════
# جلب البيانات وتحليلها
# ═══════════════════════════════════════════════════════════

def analyze_stock(symbol):
    """تحليل سهم واحد وحساب نقاطه"""
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info

        # السعر الحالي
        current = info.get("currentPrice") or info.get("regularMarketPrice")
        if not current or current < MIN_PRICE:
            return None

        # قاع وقمة 52 أسبوع
        low_52w  = info.get("fiftyTwoWeekLow")
        high_52w = info.get("fiftyTwoWeekHigh")
        if not low_52w or not high_52w:
            return None

        # كم بعيد عن القاع وكم انخفض من القمة
        pct_from_low  = ((current - low_52w) / low_52w) * 100
        pct_from_high = ((high_52w - current) / high_52w) * 100

        # شرط: قريب من القاع ومنخفض من القمة
        if pct_from_low > NEAR_LOW_PCT:
            return None
        if pct_from_high < MIN_DROP_PCT:
            return None

        # معايير القيمة
        market_cap = info.get("marketCap", 0)
        if market_cap < MIN_MARKET_CAP:
            return None

        pe         = info.get("trailingPE") or info.get("forwardPE")
        debt_eq    = info.get("debtToEquity")
        roe        = info.get("returnOnEquity")
        eps        = info.get("trailingEps", 0)
        sector     = info.get("sector", "N/A")
        name       = info.get("shortName", symbol)
        dividend   = info.get("dividendYield", 0) or 0

        # نظام النقاط (0-100)
        score = 0

        # 1. القرب من القاع (30 نقطة) - كلما أقرب أفضل
        if pct_from_low <= 5:    score += 30
        elif pct_from_low <= 10: score += 20
        elif pct_from_low <= 15: score += 10

        # 2. عمق الانخفاض (20 نقطة) - معتدل أفضل من مفرط
        if 25 <= pct_from_high <= 50: score += 20
        elif 20 <= pct_from_high < 25: score += 15
        elif pct_from_high > 50: score += 10  # خطر أكبر

        # 3. P/E (15 نقطة)
        if pe and 0 < pe <= 15:    score += 15
        elif pe and pe <= 25:      score += 10
        elif pe and pe <= 30:      score += 5

        # 4. الديون (15 نقطة)
        if debt_eq is not None:
            de = debt_eq / 100 if debt_eq > 10 else debt_eq  # yfinance أحياناً تعطي %
            if de <= 0.5:   score += 15
            elif de <= 1.0: score += 10
            elif de <= 2.0: score += 5

        # 5. ROE (10 نقطة)
        if roe and roe >= 0.20:    score += 10
        elif roe and roe >= 0.10:  score += 7
        elif roe and roe >= 0.05:  score += 4

        # 6. ربحية (5 نقاط)
        if eps and eps > 0: score += 5

        # 7. توزيعات أرباح (5 نقاط) - مكافأة
        if dividend >= 0.04:   score += 5
        elif dividend >= 0.02: score += 3
        elif dividend > 0:     score += 1

        # تصفية إضافية
        if pe and pe > MAX_PE: return None
        if debt_eq is not None:
            de_check = debt_eq / 100 if debt_eq > 10 else debt_eq
            if de_check > MAX_DEBT_EQUITY: return None
        if roe is not None and roe < MIN_ROE: return None

        return {
            "symbol": symbol,
            "name": name[:30],
            "sector": sector,
            "price": round(current, 2),
            "low_52w": round(low_52w, 2),
            "high_52w": round(high_52w, 2),
            "pct_from_low": round(pct_from_low, 1),
            "pct_from_high": round(pct_from_high, 1),
            "market_cap_b": round(market_cap / 1e9, 1),
            "pe": round(pe, 1) if pe else None,
            "debt_eq": round(debt_eq, 2) if debt_eq is not None else None,
            "roe_pct": round(roe * 100, 1) if roe else None,
            "dividend_pct": round(dividend * 100, 2) if dividend else 0,
            "score": score,
        }

    except Exception as e:
        print(f"خطأ {symbol}: {e}")
        return None


def format_opportunity(s):
    """تنسيق فرصة استثمارية لرسالة تيليغرام"""
    pe_str  = f"{s['pe']}" if s['pe'] else "N/A"
    de_str  = f"{s['debt_eq']}" if s['debt_eq'] is not None else "N/A"
    roe_str = f"{s['roe_pct']}%" if s['roe_pct'] is not None else "N/A"
    div_str = f"{s['dividend_pct']}%" if s['dividend_pct'] > 0 else "لا يوجد"

    # رمز قوة الفرصة
    if s['score'] >= 80:   icon = "🔥"
    elif s['score'] >= 65: icon = "⭐"
    else:                  icon = "💎"

    return (
        f"{icon} *{s['symbol']}* — {s['name']}\n"
        f"📊 القطاع: {s['sector']}\n"
        f"💰 السعر: ${s['price']} | القاع: ${s['low_52w']} | القمة: ${s['high_52w']}\n"
        f"📉 من القاع: +{s['pct_from_low']}% | من القمة: -{s['pct_from_high']}%\n"
        f"🏢 القيمة السوقية: ${s['market_cap_b']}B\n"
        f"📈 P/E: {pe_str} | D/E: {de_str} | ROE: {roe_str}\n"
        f"💵 توزيعات: {div_str}\n"
        f"🎯 *النقاط: {s['score']}/100*\n"
    )


# ═══════════════════════════════════════════════════════════
# الفحص الرئيسي
# ═══════════════════════════════════════════════════════════

async def scan_market(bot, chat_id=None, manual=False):
    """فحص كل القائمة وإرجاع الفرص مرتبة"""
    target_chat = chat_id or CHAT_ID
    print(f"🔍 بدء الفحص لـ {len(WATCHLIST)} سهم...")

    if manual:
        await bot.send_message(
            chat_id=target_chat,
            text=f"🔍 جاري فحص {len(WATCHLIST)} سهم...\nالفحص يستغرق ~5 دقائق",
        )

    opportunities = []
    for i, symbol in enumerate(WATCHLIST):
        result = analyze_stock(symbol)
        if result:
            opportunities.append(result)
            print(f"  ✅ {symbol}: {result['score']} نقطة")
        await asyncio.sleep(0.3)  # تجنب rate limit

        if (i + 1) % 50 == 0:
            print(f"  تقدم: {i+1}/{len(WATCHLIST)}")

    # ترتيب حسب النقاط
    opportunities.sort(key=lambda x: x["score"], reverse=True)
    top = opportunities[:TOP_RESULTS]

    # حفظ في ملف log
    try:
        with open(LOG_FILE, "w") as f:
            json.dump({
                "scan_time": datetime.now(ET).isoformat(),
                "opportunities": top,
            }, f, indent=2)
    except Exception as e:
        print(f"خطأ الحفظ: {e}")

    # إرسال النتائج
    if not top:
        await bot.send_message(
            chat_id=target_chat,
            text="📭 لا توجد فرص تطابق المعايير حالياً.\nجرب لاحقاً أو خفف المعايير.",
        )
        return

    header = (
        f"🎯 *أفضل {len(top)} فرص اصطياد قاع*\n"
        f"📅 {datetime.now(ET).strftime('%Y-%m-%d %H:%M ET')}\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
    )
    await bot.send_message(chat_id=target_chat, text=header, parse_mode="Markdown")

    # إرسال كل فرصة في رسالة منفصلة لتجنب حد الطول
    for opp in top:
        await bot.send_message(
            chat_id=target_chat,
            text=format_opportunity(opp),
            parse_mode="Markdown",
        )
        await asyncio.sleep(0.3)

    summary = (
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"✅ تم فحص {len(WATCHLIST)} سهم\n"
        f"💎 وُجدت {len(opportunities)} فرصة\n"
        f"🏆 عُرضت أفضل {len(top)}\n\n"
        f"⚠️ *تذكر*: هذه ليست توصيات. ادرس كل سهم بنفسك قبل الاستثمار."
    )
    await bot.send_message(chat_id=target_chat, text=summary, parse_mode="Markdown")
    print(f"✅ انتهى - {len(opportunities)} فرصة")


# ═══════════════════════════════════════════════════════════
# أوامر البوت
# ═══════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🎯 *بوت اصطياد قاع الأسهم*\n\n"
        "أبحث عن أسهم قوية أساسياً تتداول قرب قاع 52 أسبوع.\n\n"
        "*الأوامر:*\n"
        "`/scan` - فحص كامل للسوق (~5 دقائق)\n"
        "`/top` - عرض آخر نتائج محفوظة\n"
        "`/stock SYMBOL` - تحليل سهم معين (مثل: /stock NVO)\n"
        "`/criteria` - عرض معايير الفلترة\n"
        "`/watchlist` - عدد الأسهم المراقبة\n\n"
        "📊 الفحص التلقائي يومياً 6 مساءً بتوقيت نيويورك\n"
        "(بعد إغلاق السوق)"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await scan_market(context.bot, chat_id=update.effective_chat.id, manual=True)


async def cmd_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض آخر فحص محفوظ"""
    try:
        with open(LOG_FILE, "r") as f:
            data = json.load(f)
        scan_time = datetime.fromisoformat(data["scan_time"])
        opps = data["opportunities"]

        if not opps:
            await update.message.reply_text("📭 لا توجد فرص في آخر فحص.")
            return

        header = (
            f"📊 *آخر فحص:* {scan_time.strftime('%Y-%m-%d %H:%M')}\n"
            f"🎯 *{len(opps)} فرصة*\n━━━━━━━━━━━━━━━━━━━\n"
        )
        await update.message.reply_text(header, parse_mode="Markdown")
        for opp in opps:
            await update.message.reply_text(format_opportunity(opp), parse_mode="Markdown")
            await asyncio.sleep(0.2)

    except FileNotFoundError:
        await update.message.reply_text(
            "📭 لا يوجد فحص سابق. شغّل `/scan` أولاً.",
            parse_mode="Markdown",
        )


async def cmd_stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تحليل سهم محدد"""
    if not context.args:
        await update.message.reply_text("الاستخدام: `/stock SYMBOL`\nمثال: `/stock NVO`", parse_mode="Markdown")
        return

    symbol = context.args[0].upper()
    await update.message.reply_text(f"🔍 تحليل {symbol}...")

    result = analyze_stock(symbol)
    if not result:
        # حتى لو ما طابق المعايير، نعرض البيانات الأساسية
        try:
            t = yf.Ticker(symbol)
            info = t.info
            price = info.get("currentPrice", "N/A")
            low   = info.get("fiftyTwoWeekLow", "N/A")
            high  = info.get("fiftyTwoWeekHigh", "N/A")
            mc    = info.get("marketCap", 0)
            pe    = info.get("trailingPE", "N/A")
            name  = info.get("shortName", symbol)

            msg = (
                f"⚠️ *{symbol}* لا يطابق معايير الاصطياد حالياً\n\n"
                f"📌 {name}\n"
                f"💰 السعر: ${price}\n"
                f"📉 قاع/قمة 52w: ${low} - ${high}\n"
                f"🏢 القيمة السوقية: ${round(mc/1e9, 1)}B\n"
                f"📈 P/E: {pe}\n\n"
                f"الأسباب المحتملة:\n"
                f"- بعيد عن القاع\n"
                f"- لم ينخفض 20%+ من القمة\n"
                f"- معايير القيمة لا تطابق"
            )
            await update.message.reply_text(msg, parse_mode="Markdown")
        except Exception as e:
            await update.message.reply_text(f"❌ خطأ: {e}")
    else:
        await update.message.reply_text(format_opportunity(result), parse_mode="Markdown")


async def cmd_criteria(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "*🎯 معايير اصطياد القاع:*\n\n"
        f"📉 السعر ضمن *{NEAR_LOW_PCT}%* من قاع 52 أسبوع\n"
        f"📊 انخفاض *{MIN_DROP_PCT}%+* من قمة 52 أسبوع\n"
        f"🏢 القيمة السوقية > *${MIN_MARKET_CAP/1e9:.0f}B*\n"
        f"📈 P/E < *{MAX_PE}*\n"
        f"💳 D/E < *{MAX_DEBT_EQUITY}*\n"
        f"💼 ROE > *{MIN_ROE*100:.0f}%*\n"
        f"💵 السعر > *${MIN_PRICE}*\n\n"
        "*نظام النقاط (100 max):*\n"
        "• القرب من القاع: 30\n"
        "• عمق الانخفاض: 20\n"
        "• P/E منخفض: 15\n"
        "• ديون منخفضة: 15\n"
        "• ROE مرتفع: 10\n"
        "• أرباح موجبة: 5\n"
        "• توزيعات: 5\n"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        f"📊 *قائمة المراقبة*\n\n"
        f"عدد الأسهم: *{len(WATCHLIST)}*\n\n"
        f"عينة: {', '.join(WATCHLIST[:10])}...\n\n"
        f"تشمل: Tech, Healthcare, Financials, Consumer, "
        f"Energy, Real Estate, Industrials, Auto"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


# ═══════════════════════════════════════════════════════════
# الجدولة - فحص يومي بعد إغلاق السوق
# ═══════════════════════════════════════════════════════════

async def daily_scheduler(bot):
    """يفحص يومياً 6 مساءً ET (بعد إغلاق السوق بساعتين)"""
    while True:
        now = datetime.now(ET)
        # 6 PM ET = 1 AM السعودية في اليوم التالي
        target = now.replace(hour=18, minute=0, second=0, microsecond=0)
        if now >= target:
            target = target.replace(day=target.day + 1)

        wait_seconds = (target - now).total_seconds()
        print(f"⏰ الفحص التالي بعد {wait_seconds/3600:.1f} ساعة")
        await asyncio.sleep(wait_seconds)

        # تحقق من يوم العمل (الإثنين-الجمعة)
        if datetime.now(ET).weekday() < 5:
            try:
                await scan_market(bot)
            except Exception as e:
                print(f"خطأ الفحص التلقائي: {e}")


async def post_init(app):
    asyncio.create_task(daily_scheduler(app.bot))
    print("✅ البوت جاهز - جدولة الفحص اليومي مفعّلة")


def main():
    if not TOKEN:
        raise SystemExit("❌ TELEGRAM_BOT_TOKEN غير موجود")
    app = ApplicationBuilder().token(TOKEN).build()
    app.post_init = post_init
    app.add_handler(CommandHandler("start",     cmd_start))
    app.add_handler(CommandHandler("scan",      cmd_scan))
    app.add_handler(CommandHandler("top",       cmd_top))
    app.add_handler(CommandHandler("stock",     cmd_stock))
    app.add_handler(CommandHandler("criteria",  cmd_criteria))
    app.add_handler(CommandHandler("watchlist", cmd_watchlist))
    print(f"🚀 البوت يعمل - يراقب {len(WATCHLIST)} سهم")
    app.run_polling()


if __name__ == "__main__":
    main()
