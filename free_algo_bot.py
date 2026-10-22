import time
import requests
import pandas as pd
import ta
from datetime import datetime
from delta_rest_client import DeltaRestClient, OrderType, TimeInForce

# Delta Exchange API Credentials
DELTA_API_KEY = "GGPvNGxYK3TvoHHnwioKSWTsM2vvwF"
DELTA_API_SECRET = "FlIBOohE73EOwDGeRAVRSXdfBAbTR2FxMyHyogJhv1VmJlUMydsnmT04RlwD"
DELTA_BASE_URL = "https://demo.delta.exchange"  # Testnet / Demo URL

delta_client = DeltaRestClient(
    base_url=DELTA_BASE_URL,
    api_key=DELTA_API_KEY,
    api_secret=DELTA_API_SECRET
)

SYMBOL = "ETHUSDT"
PRODUCT_ID = 27  # ETHUSDT product ID on Delta
INTERVAL = "15m"
LAST_SIGNAL = None
ACTIVE_SL = None

def fetch_candles(symbol, interval, limit=100):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    response = requests.get(url)
    data = response.json()
    df = pd.DataFrame(data, columns=[
        'timestamp', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_av', 'trades', 'tb_base_av', 'tb_quote_av', 'ignore'
    ])
    df['close'] = df['close'].astype(float)
    df['low'] = df['low'].astype(float)
    df['high'] = df['high'].astype(float)
    return df

def get_account_balance():
    """Delta Exchange se live account balance fetch karta hai"""
    try:
        balances = delta_client.get_balances()
        for b in balances:
            if b['asset_symbol'] == 'USDT':
                return float(b['balance'])
    except Exception as e:
        print(f"⚠️ Error fetching balance: {e}. Using default fallback balance.")
    return 1000.0  # Fallback balance agar API error aaye

def calculate_dynamic_lot_size(current_price, stop_loss_price):
    """
    Risk Management ke basis par Lot Size calculate karta hai:
    - Account Balance ka 2% risk liya jata hai per trade.
    - Stop loss distance ke hisaab se exact contract quantity milti hai.
    """
    account_balance = get_account_balance()
    risk_percentage = 0.02  # 2% Risk per trade
    max_risk_amount = account_balance * risk_percentage
    
    # Entry price aur Stop Loss ke beech ka gap (Risk per unit)
    risk_per_unit = abs(current_price - stop_loss_price)
    
    if risk_per_unit == 0:
        return 0.01  # Minimum safe size
        
    # Quantity formula = Total Risk Amount / Risk Per Unit
    raw_size = max_risk_amount / risk_per_unit
    
    # Delta exchange ke rules ke mutabiq size ko round off (e.g., 2 decimal places) karenge
    lot_size = round(raw_size, 3)
    
    # Minimum size safeguard (jaise 0.01 contract minimum)
    if lot_size < 0.01:
        lot_size = 0.01
        
    print(f"💰 Account Balance: ${account_balance:.2f} | Risk Amount: ${max_risk_amount:.2f} | Calculated Lot Size: {lot_size}")
    return lot_size

def execute_delta_order(order_type, size, stop_loss_price=None):
    try:
        if order_type == "BUY":
            response = delta_client.place_order(
                product_id=PRODUCT_ID,
                size=size,
                side="buy",
                order_type=OrderType.MARKET,
                time_in_force=TimeInForce.GTC
            )
            print(f"✅ BUY ORDER EXECUTED | Size: {size} | SL Target: ${stop_loss_price:.2f}")

        elif order_type == "SELL":
            response = delta_client.place_order(
                product_id=PRODUCT_ID,
                size=size,
                side="sell",
                order_type=OrderType.MARKET,
                time_in_force=TimeInForce.GTC
            )
            print(f"✅ SELL ORDER EXECUTED | Size: {size} | SL Target: ${stop_loss_price:.2f}")

    except Exception as e:
        print(f"❌ Error executing order: {e}")

def run_trading_bot():
    global LAST_SIGNAL, ACTIVE_SL
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Checking Market Data...")
    df = fetch_candles(SYMBOL, INTERVAL)
    
    df['ema9'] = ta.trend.ema_indicator(df['close'], window=9)
    df['ema21'] = ta.trend.ema_indicator(df['close'], window=21)
    
    prev_ema9, prev_ema21 = df['ema9'].iloc[-3], df['ema21'].iloc[-3]
    curr_ema9, curr_ema21 = df['ema9'].iloc[-2], df['ema21'].iloc[-2]
    current_price = df['close'].iloc[-1]

    # Stop Loss Hit Check
    if LAST_SIGNAL == "BUY" and ACTIVE_SL and current_price <= ACTIVE_SL:
        print(f"🛑 STOP LOSS HIT! Price (${current_price:.2f}) dropped below SL (${ACTIVE_SL:.2f}). Exiting Buy Position...")
        execute_delta_order("SELL", size=0.1, stop_loss_price=0)  # Close position size
        LAST_SIGNAL = "SL_HIT"
        ACTIVE_SL = None
        return

    elif LAST_SIGNAL == "SELL" and ACTIVE_SL and current_price >= ACTIVE_SL:
        print(f"🛑 STOP LOSS HIT! Price (${current_price:.2f}) rose above SL (${ACTIVE_SL:.2f}). Exiting Sell Position...")
        execute_delta_order("BUY", size=0.1, stop_loss_price=0)  # Close position size
        LAST_SIGNAL = "SL_HIT"
        ACTIVE_SL = None
        return

    # 1. Bullish Crossover (BUY)
    if prev_ema9 <= prev_ema21 and curr_ema9 > curr_ema21:
        if LAST_SIGNAL != "BUY":
            swing_low = df['low'].iloc[-6:-1].min()
            
            # Dynamic Lot Size calculation based on balance & SL distance
            lot_size = calculate_dynamic_lot_size(current_price, swing_low)
            
            print("🚀 Bullish Crossover Detected!")
            execute_delta_order("BUY", size=lot_size, stop_loss_profile=swing_low)
            
            LAST_SIGNAL = "BUY"
            ACTIVE_SL = swing_low

    # 2. Bearish Crossover (SELL / Short)
    elif prev_ema9 >= prev_ema21 and curr_ema9 < curr_ema21:
        if LAST_SIGNAL != "SELL":
            swing_high = df['high'].iloc[-6:-1].max()
            
            # Dynamic Lot Size calculation based on balance & SL distance
            lot_size = calculate_dynamic_lot_size(current_price, swing_high)
            
            print("🔻 Bearish Crossover Detected!")
            execute_delta_order("SELL", size=lot_size, stop_loss_price=swing_high)
            
            LAST_SIGNAL = "SELL"
            ACTIVE_SL = swing_high
    else:
        print(f"Status: Waiting... Price: ${current_price:.2f} | 9 EMA: {curr_ema9:.2f} | 21 EMA: {curr_ema21:.2f}")

if __name__ == "__main__":
    print("=== DELTA EXCHANGE ADVANCED RISK-MANAGED ALGO BOT STARTED ===")
    while True:
        try:
            run_trading_bot()
        except Exception as e:
            print(f"Error: {e}")
        time.sleep(60*15)
