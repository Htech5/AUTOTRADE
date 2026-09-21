"""
Adaptive crypto paper-trading bot.

Regime detection (ADX) picks the strategy:
- Trending market (ADX > 25): SMA fast/slow crossover
- Ranging market (ADX <= 25): RSI mean-reversion (buy <30, sell >70)

Paper trading only: no real orders are sent, trades are simulated against
live public market data pulled from Binance via ccxt.
"""
import csv
import os
import time
from datetime import datetime, timezone

import ccxt

# ---- config -----------------------------------------------------------
SYMBOL = "BTC/USDT"
TIMEFRAME = "5m"
POLL_SECONDS = 60
CANDLE_LIMIT = 100
SMA_FAST, SMA_SLOW = 9, 21
RSI_PERIOD = 14
ADX_PERIOD = 14
ADX_TREND_THRESHOLD = 25
INITIAL_BALANCE_USDT = 1000.0
TRADE_LOG = os.path.join(os.path.dirname(__file__), "trades.csv")


# ---- indicators (pure python, no numpy/pandas needed) ------------------
def sma(values, period):
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(-period, 0):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def adx(highs, lows, closes, period=14):
    n = period + 1
    if len(closes) < n + 1:
        return None
    plus_dm, minus_dm, tr = [], [], []
    for i in range(-n, 0):
        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]
        plus_dm.append(up_move if (up_move > down_move and up_move > 0) else 0)
        minus_dm.append(down_move if (down_move > up_move and down_move > 0) else 0)
        tr.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        ))
    atr = sum(tr[-period:]) / period
    if atr == 0:
        return 0.0
    plus_di = 100 * (sum(plus_dm[-period:]) / period) / atr
    minus_di = 100 * (sum(minus_dm[-period:]) / period) / atr
    denom = plus_di + minus_di
    if denom == 0:
        return 0.0
    return 100 * abs(plus_di - minus_di) / denom


# ---- signal generation --------------------------------------------------
def generate_signal(highs, lows, closes):
    """Returns 'buy', 'sell', or 'hold', plus the regime used."""
    trend_strength = adx(highs, lows, closes, ADX_PERIOD)
    if trend_strength is None:
        return "hold", "warming_up"

    if trend_strength > ADX_TREND_THRESHOLD:
        fast, slow = sma(closes, SMA_FAST), sma(closes, SMA_SLOW)
        prev_fast, prev_slow = sma(closes[:-1], SMA_FAST), sma(closes[:-1], SMA_SLOW)
        if None in (fast, slow, prev_fast, prev_slow):
            return "hold", "trending"
        if prev_fast <= prev_slow and fast > slow:
            return "buy", "trending"
        if prev_fast >= prev_slow and fast < slow:
            return "sell", "trending"
        return "hold", "trending"

    rsi_val = rsi(closes, RSI_PERIOD)
    if rsi_val is None:
        return "hold", "ranging"
    if rsi_val < 30:
        return "buy", "ranging"
    if rsi_val > 70:
        return "sell", "ranging"
    return "hold", "ranging"


# ---- paper trading engine -----------------------------------------------
class PaperAccount:
    def __init__(self, cash):
        self.cash = cash
        self.position = 0.0  # base asset qty held

    def equity(self, price):
        return self.cash + self.position * price

    def execute(self, side, price):
        if side == "buy" and self.cash > 0:
            self.position += self.cash / price
            self.cash = 0.0
        elif side == "sell" and self.position > 0:
            self.cash += self.position * price
            self.position = 0.0
        else:
            return False
        return True


def log_trade(side, price, account):
    is_new = not os.path.exists(TRADE_LOG)
    with open(TRADE_LOG, "a", newline="") as f:
        w = csv.writer(f)
        if is_new:
            w.writerow(["timestamp", "side", "price", "cash", "position", "equity"])
        w.writerow([
            datetime.now(timezone.utc).isoformat(), side, price,
            round(account.cash, 2), round(account.position, 8),
            round(account.equity(price), 2),
        ])


def run():
    exchange = ccxt.binance()
    account = PaperAccount(INITIAL_BALANCE_USDT)
    print(f"Starting paper trading on {SYMBOL} ({TIMEFRAME}) with {INITIAL_BALANCE_USDT} USDT")

    while True:
        try:
            ohlcv = exchange.fetch_ohlcv(SYMBOL, TIMEFRAME, limit=CANDLE_LIMIT)
            highs = [c[2] for c in ohlcv]
            lows = [c[3] for c in ohlcv]
            closes = [c[4] for c in ohlcv]
            price = closes[-1]

            signal, regime = generate_signal(highs, lows, closes)
            print(f"[{datetime.now(timezone.utc).isoformat()}] price={price:.2f} "
                  f"regime={regime} signal={signal} equity={account.equity(price):.2f}")

            if signal in ("buy", "sell") and account.execute(signal, price):
                log_trade(signal, price, account)
                print(f"  -> executed {signal} @ {price:.2f}")

        except ccxt.NetworkError as e:
            print(f"network error, retrying: {e}")
        except Exception as e:
            print(f"error: {e}")

        time.sleep(POLL_SECONDS)


def demo():
    """Self-check: indicators and signal logic on a synthetic dataset."""
    closes = list(range(1, 60))  # steady uptrend
    highs = [c + 1 for c in closes]
    lows = [c - 1 for c in closes]

    assert sma(closes, 5) == sum(closes[-5:]) / 5
    assert sma([1, 2], 5) is None

    r = rsi(closes, 14)
    assert r == 100.0, f"pure uptrend should be RSI 100, got {r}"

    trend = adx(highs, lows, closes, 14)
    assert trend is not None and trend > 25, f"steady uptrend should register as trending, got {trend}"

    signal, regime = generate_signal(highs, lows, closes)
    assert regime == "trending"
    assert signal in ("buy", "sell", "hold")

    acct = PaperAccount(100.0)
    assert acct.execute("buy", 10.0) is True
    assert acct.cash == 0.0 and acct.position == 10.0
    assert acct.execute("buy", 10.0) is False  # no cash left
    assert acct.execute("sell", 20.0) is True
    assert acct.cash == 200.0 and acct.position == 0.0

    print("demo(): all self-checks passed")


if __name__ == "__main__":
    import sys
    if "--test" in sys.argv:
        demo()
    else:
        run()
