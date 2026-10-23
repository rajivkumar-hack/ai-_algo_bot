import os
import sys
import time
import hmac
import hashlib
import json
import pandas as pd
import ta
import requests

API_KEY = os.getenv("DELTA_API_KEY")
API_SECRET = os.getenv("DELTA_API_SECRET")

BASE_URL = "https://api.india.delta.exchange"
SYMBOL = "ETHUSDT"
RISK_PERCENT = 0.02

def generate_signature(secret, method, path, payload="", timestamp=""):
    signature_data = method + timestamp + path + payload
    return hmac.new(
        secret.encode('utf-8'),
        signature_data.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

def get_auth_headers(method, path, payload=""):
    timestamp = str(int(time.time()))
    signature = generate_signature(API_SECRET, method, path, payload, timestamp)
    return {
        "api-key": API_KEY,
        "signature": signature,
        "timestamp": timestamp,
        "Content-Type": "application/json"
    }

def fetch_candles(resolution, limit):
    try:
        url = f"{BASE_URL}/v2/history/candles"
        params = {"resolution": resolution, "symbol": SYMBOL, "limit": limit}
        response = requests.get(url, params=params)
        data = response.json()
        if not data.get("success") or not data.get("result"):
            return None
        candles = data["result"]
        df = pd.DataFrame(candles, columns=["time", "open", "high", "low", "close", "volume"])
        df["close"] = df["close"].astype(float)
        df["high"] = df["high"].astype(float)
        df["low"] = df["low"].astype(float)
        return df
    except Exception as e:
        print(f"❌ Exception fetching candles: {e}")
        return None

def get_wallet_balance():
    try:
        path = "/v2/wallet/balances"
        headers = get_auth_headers("GET", path)
        res = requests.get(f"{BASE_URL}{path}", headers=headers)
        data = res.json()
        if data.get("success"):
            for balance in data.get("result", []):
                if balance.get("asset_symbol") == "USDT":
                    return float(balance.get("available_balance", 0))
        return 100.0
    except Exception:
        return 100.0

def get_open_positions():
    """Check karta hai ki currently koi position open hai ya nahi"""
    try:
        path = "/v2/positions"
        headers = get_auth_headers("GET", path)
        res = requests.get(f"{BASE_URL}{path}", headers=headers)
        data = res.json()
        if data.get("success"):
            for pos in data.get("result", []):
                if pos.get("product_symbol") == SYMBOL and float(pos.get("size", 0)) != 0:
                    return pos 
        return None
    except Exception as e:
        print(f"❌ Error fetching positions: {e}")
        return None

def close_position(side, size):
    """Opposite crossover par purani position ko market order se cut/close karta hai"""
    path = "/v2/orders"
    close_side = "sell" if side.lower() == "buy" else "buy"
    payload_dict = {
        "product_symbol": SYMBOL,
        "size": abs(int(size)),
        "side": close_side,
        "order_type": "market_order",
        "reduce_only": True  
    }
    payload_str = json.dumps(payload_dict)
    headers = get_auth_headers("POST", path, payload_str)
    try:
        res = requests.post(f"{BASE_URL}{path}", data=payload_str, headers=headers)
        res_data = res.json()
        if res_data.get("success"):
            print(f"🔄 Existing position successfully closed via opposite crossover!")
        else:
            print(f"❌ Failed to close position: {res_data}")
    except Exception as e:
        print(f"❌ Error closing position: {e}")

def calculate_lot_size(balance, entry_price, sl_price):
    risk_amount = balance * RISK_PERCENT
    price_risk = abs(entry_price - sl_price)
    if price_risk == 0:
        return 1
    lot_qty = int(risk_amount / price_risk)
    return max(1, lot_qty)

def place_order(side, price, sl_price, qty):
    path = "/v2/orders"
    payload_dict = {
        "product_symbol": SYMBOL,
        "size": qty,
        "side": side.lower(),
        "order_type": "market_order",
        "stop_loss_price": str(round(sl_price, 2))
    }
    payload_str = json.dumps(payload_dict)
    headers = get_auth_headers("POST", path, payload_str)
    try:
        res = requests.post(f"{BASE_URL}{path}", data=payload_str, headers=headers)
        res_data = res.json()
        if res_data.get("success"):
            print(f"✅ New {side.upper()} Order Placed! ID: {res_data['result']['id']}")
        else:
            print(f"❌ Order Placement Failed: {res_data}")
    except Exception as e:
        print(f"❌ Error placing order: {e}")

def run_bot_cycle():
    if not API_KEY or not API_SECRET:
        print("❌ Error: Delta API Keys missing!")
        sys.exit(1)
        
    print("🚀 Running 15m EMA Crossover Strategy with 4h SL Check...")
    
    df_15m = fetch_candles("15m", 100)
    df_4h = fetch_candles("4h", 20)
    
    if df_15m is None or len(df_15m) < 25 or df_4h is None or len(df_4h) < 5:
        print("❌ Insufficient candle data.")
        return

    df_15m['ema_9'] = ta.trend.ema_indicator(df_15m['close'], window=9)
    df_15m['ema_21'] = ta.trend.ema_indicator(df_15m['close'], window=21)
    
    curr_close = df_15m['close'].iloc[-2]
    curr_ema_9 = df_15m['ema_9'].iloc[-2]
    curr_ema_21 = df_15m['ema_21'].iloc[-2]
    
    prev_ema_9 = df_15m['ema_9'].iloc[-3]
    prev_ema_21 = df_15m['ema_21'].iloc[-3]
    
    prev_4h_low = df_4h['low'].iloc[-2]
    prev_4h_high = df_4h['high'].iloc[-2]

    print(f"📍 ETH Price: ${curr_close:.2f} | 15m EMA 9: {curr_ema_9:.2f} | EMA 21: {curr_ema_21:.2f}")

    signal = None
    sl_price = 0.0

    if prev_ema_9 <= prev_ema_21 and curr_ema_9 > curr_ema_21:
        signal = "buy"
        sl_price = prev_4h_low
    elif prev_ema_9 >= prev_ema_21 and curr_ema_9 < curr_ema_21:
        signal = "sell"
        sl_price = prev_4h_high

    open_pos = get_open_positions()

    if signal:
        if open_pos:
            pos_size = float(open_pos.get("size", 0))
            pos_side = "buy" if pos_size > 0 else "sell"
            
            if pos_side != signal:
                print(f"🔄 Opposite crossover detected! Closing existing {pos_side.upper()} position...")
                close_position(pos_side, pos_size)
                time.sleep(2) 
                
                balance = get_wallet_balance()
                qty = calculate_lot_size(balance, curr_close, sl_price)
                print(f"🔥 Taking new {signal.upper()} trade | SL: ${sl_price:.2f} | Qty: {qty}")
                place_order(signal, curr_close, sl_price, qty)
            else:
                print(f"ℹ️ Already in a {pos_side.upper()} position matching the trend. Holding.")
        else:
            balance = get_wallet_balance()
            qty = calculate_lot_size(balance, curr_close, sl_price)
            print(f"🔥 Fresh Signal: {signal.upper()} | Entry: ${curr_close:.2f} | 4h SL: ${sl_price:.2f}")
            place_order(signal, curr_close, sl_price, qty)
    else:
        print("⏳ No crossover signal found in this 15m cycle. Holding position (if any).")

    print("✅ Single-cycle execution finished cleanly.")

if __name__ == "__main__":
    try:
        run_bot_cycle()
    except Exception as e:
        print(f"❌ Execution Exception: {e}")
        sys.exit(1)
