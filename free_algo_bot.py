import os
import sys
import time
import hmac
import hashlib
import json
import pandas as pd
import ta
import requests

# GitHub Secrets se API Keys fetch karna
API_KEY = os.getenv("DELTA_API_KEY")
API_SECRET = os.getenv("DELTA_API_SECRET")

BASE_URL = "https://api.india.delta.exchange"  # Delta India Base URL
SYMBOL = "ETHUSDT"
RISK_PERCENT = 0.02  # 2% Risk per trade
RISK_REWARD_RATIO = 2.0  # 1:2 Risk to Reward (TP = 2x Risk)

def generate_signature(secret, method, path, payload="", timestamp=""):
    """Delta Exchange API Signature generator"""
    signature_data = method + timestamp + path + payload
    return hmac.new(
        secret.encode('utf-8'),
        signature_data.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

def get_auth_headers(method, path, payload=""):
    """Authenticated requests ke liye headers prepare karta hai"""
    timestamp = str(int(time.time()))
    signature = generate_signature(API_SECRET, method, path, payload, timestamp)
    return {
        "api-key": API_KEY,
        "signature": signature,
        "timestamp": timestamp,
        "Content-Type": "application/json"
    }

def fetch_eth_candles():
    """Delta Exchange se 15m candles fetch karta hai"""
    try:
        url = f"{BASE_URL}/v2/history/candles"
        params = {"resolution": "15m", "symbol": SYMBOL, "limit": 100}
        response = requests.get(url, params=params)
        data = response.json()
        
        if not data.get("success") or not data.get("result"):
            print(f"❌ Candle fetch failed: {data}")
            return None
            
        candles = data["result"]
        df = pd.DataFrame(candles, columns=["time", "open", "high", "low", "close", "volume"])
        df["close"] = df["close"].astype(float)
        df["high"] = df["high"].astype(float)
        df["low"] = df["low"].astype(float)
        return df
    except Exception as e:
        print(f"❌ Exception in fetching candles: {e}")
        return None

def get_wallet_balance():
    """Account balance (USDT) fetch karta hai"""
    try:
        path = "/v2/wallet/balances"
        headers = get_auth_headers("GET", path)
        res = requests.get(f"{BASE_URL}{path}", headers=headers)
        data = res.json()
        if data.get("success"):
            for balance in data.get("result", []):
                if balance.get("asset_symbol") == "USDT":
                    return float(balance.get("available_balance", 0))
        return 100.0  # Fallback balance test ke liye
    except Exception as e:
        print(f"⚠️ Balance fetch error, using default: {e}")
        return 100.0

def calculate_lot_size(balance, entry_price, sl_price):
    """Dynamic 2% Risk Lot Calculator"""
    risk_amount = balance * RISK_PERCENT
    price_risk = abs(entry_price - sl_price)
    
    if price_risk == 0:
        return 1
        
    lot_qty = int(risk_amount / price_risk)
    return max(1, lot_qty)  # Minimum 1 lot

def place_bracket_order(side, price, sl_price, tp_price, qty):
    """Delta Exchange par Bracket Order (Market Entry + SL + TP) place karta hai"""
    path = "/v2/orders"
    payload_dict = {
        "product_symbol": SYMBOL,
        "size": qty,
        "side": side.lower(),  # 'buy' or 'sell'
        "order_type": "market_order",
        "stop_loss_price": str(round(sl_price, 2)),
        "take_profit_price": str(round(tp_price, 2))
    }
    
    payload_str = json.dumps(payload_dict)
    headers = get_auth_headers("POST", path, payload_str)
    
    try:
        res = requests.post(f"{BASE_URL}{path}", data=payload_str, headers=headers)
        res_data = res.json()
        if res_data.get("success"):
            print(f"✅ Order Placed Successfully! ID: {res_data['result']['id']}")
        else:
            print(f"❌ Order Placement Failed: {res_data}")
    except Exception as e:
        print(f"❌ Error placing order: {e}")

def check_and_execute_trade():
    if not API_KEY or not API_SECRET:
        print("❌ Error: Delta API Keys missing!")
        sys.exit(1)
        
    print("🚀 Running 9/21 EMA Crossover Strategy Check...")
    
    df = fetch_eth_candles()
    if df is None or len(df) < 25:
        print("❌ Insufficient candle data.")
        return

    # Calculate Indicators
    df['ema_9'] = ta.trend.ema_indicator(df['close'], window=9)
    df['ema_21'] = ta.trend.ema_indicator(df['close'], window=21)
    
    # Recent Closed Candles
    curr_close = df['close'].iloc[-2]
    curr_ema_9 = df['ema_9'].iloc[-2]
    curr_ema_21 = df['ema_21'].iloc[-2]
    
    prev_ema_9 = df['ema_9'].iloc[-3]
    prev_ema_21 = df['ema_21'].iloc[-3]
    
    # Recent Swing High/Low for Dynamic Stop Loss
    swing_low = df['low'].iloc[-4:-1].min()
    swing_high = df['high'].iloc[-4:-1].max()

    print(f"📍 ETH Price: ${curr_close:.2f} | EMA 9: {curr_ema_9:.2f} | EMA 21: {curr_ema_21:.2f}")

    signal = None
    sl_price = 0.0
    tp_price = 0.0

    # 1. Bullish Crossover (BUY Signal)
    if prev_ema_9 <= prev_ema_21 and curr_ema_9 > curr_ema_21:
        signal = "buy"
        sl_price = swing_low  # SL recent Swing Low par
        risk = curr_close - sl_price
        tp_price = curr_close + (risk * RISK_REWARD_RATIO)  # 1:2 Risk-Reward TP

    # 2. Bearish Crossover (SELL Signal)
    elif prev_ema_9 >= prev_ema_21 and curr_ema_9 < curr_ema_21:
        signal = "sell"
        sl_price = swing_high  # SL recent Swing High par
        risk = sl_price - curr_close
        tp_price = curr_close - (risk * RISK_REWARD_RATIO)  # 1:2 Risk-Reward TP

    # Signal Logic Execution
    if signal:
        balance = get_wallet_balance()
        qty = calculate_lot_size(balance, curr_close, sl_price)
        
        print(f"🔥 Signal: {signal.upper()} | Entry: ${curr_close:.2f} | SL: ${sl_price:.2f} | TP: ${tp_price:.2f}")
        print(f"🧮 Balance: ${balance:.2f} | Dynamic Qty: {qty} Lots (2% Risk)")
        
        place_bracket_order(signal, curr_close, sl_price, tp_price, qty)
    else:
        print("⏳ No crossover signal found in this 15m cycle.")

    print("✅ Single-cycle execution finished cleanly.")

if __name__ == "__main__":
    try:
        check_and_execute_trade()
    except Exception as e:
        print(f"❌ Execution Exception: {e}")
        sys.exit(1)
