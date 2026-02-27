import time
import requests
import pandas as pd
from datetime import datetime, timedelta, timezone

# ================= CONFIG =================
TELEGRAM_TOKEN = "8773685370:AAHGiajKzoDFup_lBQf2LekQ3TZERnim42E"
CHAT_ID = "7434243701"
TWELVE_DATA_API_KEY = "1f86473561f94006a6b46f5fc2875c3d"
NEWS_API_KEY = "c7f1bceb70744202a4b7b3524fc34b6f"

CRYPTO_SYMBOLS = ["BTCUSDT", "ETHUSDT"]
FOREX_METALS = ["EUR/USD", "XAU/USD", "XAG/USD"]

CHECK_INTERVAL = 1800  # 30 minutes
MIN_SCORE = 4
COOLDOWN_MINUTES = 120
RISK_REWARD = 3
TIMEFRAMES = ["1m","1h","4h"]

bot_running = True
last_signal_time = {}
LOG_FILE = "bot_messages.log"
last_update_id = None

# ================= INDICATORS =================
def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def atr(df, period=14):
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = ranges.max(axis=1)
    return true_range.rolling(period).mean()

# ================= NEWS FILTER =================
def high_impact_news():
    try:
        url = "https://newsapi.org/v2/everything?q=forex OR gold OR crypto&language=en&sortBy=publishedAt&apiKey=1f86473561f94006a6b46f5fc2875c3d"
        res = requests.get(url).json()
        now = datetime.now(timezone.utc)
        for article in res.get("articles", [])[:5]:
            published = datetime.fromisoformat(article["publishedAt"].replace("Z","")).replace(tzinfo=timezone.utc)
            if now - published < timedelta(hours=1):
                return True
        return False
    except:
        return False

# ================= GET DATA =================
def get_crypto(symbol, interval):
    try:
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit=250"
        data = requests.get(url).json()
        df = pd.DataFrame(data, columns=range(len(data[0])))
        df = df.iloc[:, :6]
        df.columns = ["time","open","high","low","close","volume"]
        df = df.astype(float)
        df["RSI"] = rsi(df["close"])
        df["EMA200"] = ema(df["close"],200)
        df["ATR"] = atr(df)
        return df.iloc[-1]
    except:
        return None

def get_forex(symbol, interval):
    try:
        url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval={interval}&outputsize=250&apikey={TWELVE_DATA_API_KEY}"
        res = requests.get(url).json()
        if "values" not in res: return None
        df = pd.DataFrame(res["values"]).astype(float)[::-1]
        df["RSI"] = rsi(df["close"])
        df["EMA200"] = ema(df["close"],200)
        df["ATR"] = atr(df)
        return df.iloc[-1]
    except:
        return None

# ================= BUILD SIGNAL =================
def build_signal(rows,symbol,market_type):
    score = 0
    directions = []

    for tf,row in rows.items():
        price = row["close"]
        ema_val = row["EMA200"]
        rsi_val = row["RSI"]
        atr_val = row["ATR"]
        if pd.isna(rsi_val) or pd.isna(ema_val) or pd.isna(atr_val):
            continue
        trend = "UP" if price > ema_val else "DOWN"
        dir_tf = None
        if rsi_val < 30: dir_tf="BUY"
        elif rsi_val > 70: dir_tf="SELL"
        if dir_tf: directions.append(dir_tf)
        if trend=="UP" and dir_tf=="BUY": score+=1
        if trend=="DOWN" and dir_tf=="SELL": score+=1
        if atr_val/price>0.002: score+=1

    if len(set(directions))>1 or len(directions)==0: return None
    direction = directions[0]
    if score<MIN_SCORE: return None

    now = datetime.now(timezone.utc)
    last_time = last_signal_time.get(symbol)
    if last_time and now - last_time < timedelta(minutes=COOLDOWN_MINUTES):
        return None
    last_signal_time[symbol]=now

    row_main = rows.get("1h", next(iter(rows.values())))
    price = row_main["close"]
    atr_val = row_main["ATR"]

    if direction=="BUY":
        sl = price - atr_val*1.5
        tp = price + atr_val*RISK_REWARD
        emoji="🟢"
    else:
        sl = price + atr_val*1.5
        tp = price - atr_val*RISK_REWARD
        emoji="🔴"

    msg = (
        f"{emoji} **{direction} SIGNAL ({market_type})**\n"
        f"Asset: {symbol}\n"
        f"Price: {price:.5f}\n"
        f"Trend: {direction} confirmed on {len(rows)} TFs\n"
        f"SL: {sl:.5f}\n"
        f"TP: {tp:.5f}\n"
        f"Score: {score}/5"
    )
    return msg

# ================= TELEGRAM =================
def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url,data={"chat_id":CHAT_ID,"text":msg,"parse_mode":"Markdown"})
        with open(LOG_FILE,"a",encoding="utf-8") as f:
            f.write(f"{datetime.now(timezone.utc)} | {msg}\n\n")
    except:
        print("Telegram send failed")

def check_commands():
    global bot_running, last_update_id
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
        if last_update_id:
            url += f"?offset={last_update_id+1}"
        res = requests.get(url).json()
        for update in res.get("result", []):
            last_update_id = update["update_id"]
            if "message" not in update: continue
            text = update["message"].get("text","").lower()
            chat_id = update["message"]["chat"]["id"]
            if chat_id != int(CHAT_ID): continue
            if text=="/status":
                send_telegram("✅ Bot is running" if bot_running else "⛔ Bot is stopped")
            elif text=="/startbot":
                bot_running=True
                send_telegram("🚀 Bot manually started")
            elif text=="/stopbot":
                bot_running=False
                send_telegram("⛔ Bot manually stopped")
            elif text=="/history":
                try:
                    with open(LOG_FILE,"r",encoding="utf-8") as f:
                        history = f.read()[-4000:]
                    send_telegram(f"📜 Bot History (latest):\n{history}" if history else "📜 No messages yet.")
                except:
                    send_telegram("⚠️ Failed to read history.")
    except Exception as e:
        print("Command check error:", e)

# ================= SCAN LOOP =================
def scan_loop():
    last_scan_time = datetime.now() - timedelta(seconds=CHECK_INTERVAL)
    print("🚀 Android PRO Bot Live with Instant Commands")
    while True:
        try:
            check_commands()  # commands checked every 5 sec

            now = datetime.now()
            if bot_running and (now - last_scan_time).total_seconds() >= CHECK_INTERVAL:
                last_scan_time = now

                if high_impact_news():
                    print("⚠️ High impact news — skipping scan")
                else:
                    # Crypto
                    for s in CRYPTO_SYMBOLS:
                        rows={}
                        for tf in TIMEFRAMES:
                            row = get_crypto(s, tf)
                            if row is not None: rows[tf]=row
                        if rows:
                            msg = build_signal(rows,s,"Crypto")
                            if msg: send_telegram(msg)
                    # Forex/Metals
                    for s in FOREX_METALS:
                        rows={}
                        for tf in TIMEFRAMES:
                            row = get_forex(s, tf)
                            if row is not None: rows[tf]=row
                        if rows:
                            msg = build_signal(rows,s,"Forex/Metal")
                            if msg: send_telegram(msg)
            else:
                if not bot_running:
                    print("⛔ Bot is stopped manually")
        except Exception as e:
            print("Scan error:", e)
        time.sleep(5)

# ================= RUN BOT =================
if __name__=="__main__":
    scan_loop()
