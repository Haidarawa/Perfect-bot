# ============================================
# 🚀 Multi-Market Telegram Bot (Crypto + Forex/Metals)
# Version: 2026-02-27 TIMEOUT + SERIES FIX
# ============================================

import time
import requests
import pandas as pd
from datetime import datetime, timezone, timedelta

# ================= CONFIG =================
TELEGRAM_TOKEN = "8602431042:AAHnJ3z9qAtbwtrLpeggSoqXx4HhPdC7dJU"
CHAT_ID = "7434243701"
TWELVE_DATA_API_KEY = "ac37a1fd975846dbba1536512e7de631"
NEWS_API_KEY = "c7f1bceb70744202a4b7b3524fc34b6f"

CRYPTO_SYMBOLS = ["BTCUSDT", "ETHUSDT"]
FOREX_METALS = ["EUR/USD", "GBP/USD", "GBP/JPY", "XAG/USD", "XAU/USD"]

TIMEFRAMES = ["15m", "1h", "4h"]
MIN_SCORE_CRYPTO = 1
MIN_SCORE_FOREX = 3
COOLDOWN_MINUTES = 60
RISK_REWARD = 3
DEBUG_MODE = True

last_signal_time = {}
LOG_FILE = "bot_messages.log"
last_update_id = None
bot_running = True

# ================= RETRY FUNCTION =================
def fetch_with_retry(url, params=None, headers=None, timeout=20, max_retries=3):
    attempt = 0
    while attempt < max_retries:
        try:
            response = requests.get(url, params=params, headers=headers, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except (requests.exceptions.ConnectTimeout, requests.exceptions.ReadTimeout):
            attempt += 1
            print(f"Timeout, retrying {attempt}/{max_retries} for {url}...")
            time.sleep(2)
        except requests.exceptions.RequestException as e:
            print(f"Request failed: {e}")
            break
    return None

# ================= TELEGRAM =================
def send_telegram(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"})
        with open(LOG_FILE,"a",encoding="utf-8") as f:
            f.write(f"{datetime.now(timezone.utc)} | {msg}\n\n")
    except Exception as e:
        print("Telegram send failed:", e)

# ================= INDICATORS =================
def add_indicators(df):
    df["ema"] = df["close"].ewm(span=200).mean()
    delta = df["close"].diff()
    gain = (delta.where(delta>0,0)).rolling(14).mean()
    loss = (-delta.where(delta<0,0)).rolling(14).mean()
    rs = gain/loss
    df["rsi"] = 100-(100/(1+rs))
    df["tr"] = df["high"]-df["low"]
    df["atr"] = df["tr"].rolling(14).mean()
    return df

# ================= CRYPTO FETCH =================
def get_crypto(symbol, interval):
    try:
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit=250"
        data = fetch_with_retry(url)
        if data is None:
            print(f"{symbol} data error: failed to fetch after retries")
            return None
        df = pd.DataFrame(data)
        df = df.iloc[:, :6]
        df.columns = ["time","open","high","low","close","volume"]
        df = df.astype(float)
        df = add_indicators(df)
        row = df.iloc[-1].copy()
        if row.isna().any(): return None
        return row
    except Exception as e:
        print(f"{symbol} data error:", e)
        return None

# ================= FOREX/METALS FETCH =================
def get_forex(symbol, interval):
    try:
        url = "https://api.twelvedata.com/time_series"
        params = {"symbol": symbol,"interval": interval,"outputsize": 200,"apikey": TWELVE_DATA_API_KEY}
        r = fetch_with_retry(url, params=params)
        if not r or "values" not in r or not r["values"]:
            print(f"{symbol} data error: values missing")
            return None
        df = pd.DataFrame(r["values"])[::-1].reset_index(drop=True)
        for col in ["open","high","low","close"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna()
        if len(df)<50:
            print(f"{symbol} skipped → insufficient data")
            return None
        df = add_indicators(df)
        row = df.iloc[-1].copy()
        if row.isna().any(): return None
        return row
    except Exception as e:
        print(f"{symbol} data error:", e)
        return None

# ================= NEWS FILTER =================
def high_impact_news():
    try:
        url = f"https://newsapi.org/v2/everything?q=forex OR gold OR crypto&language=en&sortBy=publishedAt&apiKey={NEWS_API_KEY}"
        res = fetch_with_retry(url)
        if res is None:
            print("News API error after retries")
            return False
        now = datetime.now(timezone.utc)
        for article in res.get("articles", [])[:5]:
            published = datetime.fromisoformat(article["publishedAt"].replace("Z","")).replace(tzinfo=timezone.utc)
            if now - published < timedelta(hours=1):
                if DEBUG_MODE: print("⚠️ High-impact news detected → skipping signals")
                return True
        return False
    except Exception as e:
        print("News API error:", e)
        return False

# ================= SIGNAL LOGIC =================
def build_signal(row, symbol, market_type):
    price = float(row["close"])
    ema = float(row["ema"])
    rsi = float(row["rsi"])
    atr = float(row["atr"])
    atr_percent = atr/price*100

    direction = None
    score = 0
    min_score = MIN_SCORE_CRYPTO if market_type=="Crypto" else MIN_SCORE_FOREX

    if market_type=="Crypto":
        if price>ema and rsi<45:
            direction="BUY"; score+=1
        elif price<ema and rsi>55:
            direction="SELL"; score+=1
    else:
        if price>ema and rsi<40:
            direction="BUY"; score+=2
        elif price<ema and rsi>60:
            direction="SELL"; score+=2
        if atr_percent>0.05: score+=1

    if not direction or score<min_score: 
        if DEBUG_MODE: print(f"🔎 {symbol} skipped → weak signal or RSI/EMA condition")
        return None

    now = datetime.now(timezone.utc)
    last_time = last_signal_time.get(symbol)
    if last_time and now-last_time < timedelta(minutes=COOLDOWN_MINUTES):
        return None
    last_signal_time[symbol]=now

    sl = price-atr*1.5 if direction=="BUY" else price+atr*1.5
    tp = price+atr*RISK_REWARD if direction=="BUY" else price-atr*RISK_REWARD
    emoji = "🟢" if direction=="BUY" else "🔴"
    price_fmt = f"{price:.5f}" if market_type=="Crypto" else f"{price:.5f}"

    msg = (
        f"{emoji} **{direction} SIGNAL ({market_type})**\n"
        f"Asset: {symbol}\n"
        f"Price: {price_fmt}\n"
        f"SL: {sl:.5f}\n"
        f"TP: {tp:.5f}\n"
        f"Score: {score}/{min_score}"
    )
    return msg

# ================= TELEGRAM COMMANDS =================
def check_commands():
    global bot_running, last_update_id
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
        if last_update_id:
            url += f"?offset={last_update_id+1}"
        res = fetch_with_retry(url)
        if not res: return
        for update in res.get("result", []):
            last_update_id = update["update_id"]
            if "message" not in update: continue
            text = update["message"].get("text","").lower()
            chat_id = update["message"]["chat"]["id"]
            if str(chat_id) != str(CHAT_ID): continue
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

# ================= MAIN LOOP =================
def scan_loop():
    print("🚀 Bot Live | Crypto + Forex/Metals active signals")
    while True:
        try:
            check_commands()
            if bot_running:
                if high_impact_news():
                    time.sleep(60)
                    continue

                for symbol in CRYPTO_SYMBOLS:
                    for tf in TIMEFRAMES:
                        row = get_crypto(symbol, tf)
                        if row is not None:  # FIXED: Series check
                            msg = build_signal(row, symbol, "Crypto")
                            if msg: send_telegram(msg)

                for symbol in FOREX_METALS:
                    daily_signals = 0
                    for tf in TIMEFRAMES:
                        if daily_signals >= 3: break
                        row = get_forex(symbol, tf)
                        if row is not None:  # FIXED: Series check
                            msg = build_signal(row, symbol, "Forex/Metal")
                            if msg:
                                send_telegram(msg)
                                daily_signals += 1

            time.sleep(15)
        except Exception as e:
            print("Scan error:", e)
            time.sleep(15)

# ================= START BOT =================
if __name__=="__main__":
    scan_loop()
