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
import random
import time
from datetime import datetime, timezone

import ccxt

# ---- config -----------------------------------------------------------
EXCHANGES = ["binance", "okx", "bybit", "kraken"]  # tried in order, first reachable wins
SYMBOL = "BTC/USDT"
TIMEFRAME = "5m"
POLL_SECONDS = 1  # candles are 5m, but poll faster so price/ticker feel live
CANDLE_LIMIT = 100
SMA_FAST, SMA_SLOW = 9, 21
RSI_PERIOD = 14
ADX_PERIOD = 14
ADX_TREND_THRESHOLD = 25
POSITION_SIZE_PCT = 0.5   # use only half of available cash per buy, keep the rest as reserve
STOP_LOSS_PCT = 0.03      # force-sell if price drops 3% below entry, regardless of signal
INITIAL_BALANCE_USDT = 1000.0
TRADE_LOG = os.path.join(os.path.dirname(__file__), "trades.csv")
MAX_LOG_LINES = 8  # recent trades shown in the dashboard


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
def generate_signal(highs, lows, closes, has_position=False):
    """Returns 'buy', 'sell', or 'hold', plus the regime used.

    State-aware, not edge-triggered: buys when conditions favor entry and
    we're flat, sells when they reverse and we're holding. An edge-triggered
    version (fire only on the exact crossover tick) misses most entries here
    because ADX confirms a trend *after* the SMA cross already happened."""
    trend_strength = adx(highs, lows, closes, ADX_PERIOD)
    if trend_strength is None:
        return "hold", "warming_up"

    if trend_strength > ADX_TREND_THRESHOLD:
        fast, slow = sma(closes, SMA_FAST), sma(closes, SMA_SLOW)
        if fast is None or slow is None:
            return "hold", "trending"
        if fast > slow and not has_position:
            return "buy", "trending"
        if fast < slow and has_position:
            return "sell", "trending"
        return "hold", "trending"

    rsi_val = rsi(closes, RSI_PERIOD)
    if rsi_val is None:
        return "hold", "ranging"
    if rsi_val < 30 and not has_position:
        return "buy", "ranging"
    if rsi_val > 70 and has_position:
        return "sell", "ranging"
    return "hold", "ranging"


# ---- paper trading engine -----------------------------------------------
class PaperAccount:
    def __init__(self, cash):
        self.cash = cash
        self.position = 0.0  # base asset qty held
        self.entry_price = None

    def equity(self, price):
        return self.cash + self.position * price

    def execute(self, side, price):
        if side == "buy" and self.cash > 0:
            spend = self.cash * POSITION_SIZE_PCT
            self.position += spend / price
            self.cash -= spend
            self.entry_price = price
        elif side == "sell" and self.position > 0:
            self.cash += self.position * price
            self.position = 0.0
            self.entry_price = None
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


class MockExchange:
    """Random-walk OHLCV generator: lets the bot/UI run with no exchange access."""

    def __init__(self, start_price=65000.0, seed=None):
        self.price = start_price
        self.rng = random.Random(seed)

    def fetch_ohlcv(self, symbol, timeframe, limit=1):
        candles = []
        for _ in range(limit):
            o = self.price
            self.price *= 1 + self.rng.uniform(-0.004, 0.004)
            h, l, c = max(o, self.price), min(o, self.price), self.price
            volume = self.rng.uniform(0.5, 8.0)
            candles.append([0, o, h, l, c, volume])
        return candles


def connect_exchange():
    """Try each configured exchange until one answers (handles regional blocks)."""
    for ex_id in EXCHANGES:
        exchange = getattr(ccxt, ex_id)()
        try:
            exchange.fetch_ohlcv(SYMBOL, TIMEFRAME, limit=1)
            return exchange, ex_id
        except Exception:
            continue
    raise RuntimeError(f"none of {EXCHANGES} reachable from this network")


def recent_trade_lines(n):
    if not os.path.exists(TRADE_LOG):
        return []
    with open(TRADE_LOG) as f:
        rows = list(csv.reader(f))[1:]  # skip header
    return rows[-n:]


def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def render(exchange_id, price, regime, signal, account):
    bar = "=" * 52
    clear_screen()
    print(bar)
    print(f" ADAPTIVE PAPER TRADER   [{exchange_id}]  {SYMBOL} {TIMEFRAME}")
    print(bar)
    print(f" time     : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f" price    : {price:.2f}")
    print(f" regime   : {regime}")
    print(f" signal   : {signal.upper()}")
    print(f" cash     : {account.cash:.2f} USDT")
    print(f" position : {account.position:.8f} BTC")
    print(f" equity   : {account.equity(price):.2f} USDT")
    print(bar)
    print(f" last {MAX_LOG_LINES} trades:")
    rows = recent_trade_lines(MAX_LOG_LINES)
    if not rows:
        print("   (none yet)")
    for ts, side, px, cash, pos, eq in rows:
        print(f"   {ts[:19]}  {side:<4}  px={float(px):.2f}  equity={float(eq):.2f}")
    print(bar)
    print(" Ctrl+C to stop")


class Session:
    """One trading session: holds exchange connection, account and OHLCV history.
    tick() advances one step -- shared by the CLI loop and the GUI's poll loop."""

    def __init__(self, mock=False, balance=INITIAL_BALANCE_USDT):
        self.mock = mock
        if mock:
            self.exchange, self.exchange_id = MockExchange(), "mock (random walk)"
            self.poll_seconds = 1
            self.history = self.exchange.fetch_ohlcv(SYMBOL, TIMEFRAME, limit=CANDLE_LIMIT)
        else:
            self.exchange, self.exchange_id = connect_exchange()
            self.poll_seconds = POLL_SECONDS
            self.history = []
        self.account = PaperAccount(balance)

    def tick(self):
        """Fetch latest data, trade on signal. Returns (price, regime, signal) or raises."""
        if self.mock:
            self.history += self.exchange.fetch_ohlcv(SYMBOL, TIMEFRAME, limit=1)
            self.history = self.history[-CANDLE_LIMIT:]
            ohlcv = self.history
        else:
            ohlcv = self.exchange.fetch_ohlcv(SYMBOL, TIMEFRAME, limit=CANDLE_LIMIT)
            self.history = ohlcv
        highs = [c[2] for c in ohlcv]
        lows = [c[3] for c in ohlcv]
        closes = [c[4] for c in ohlcv]
        price = closes[-1]

        signal, regime = generate_signal(highs, lows, closes, has_position=self.account.position > 0)

        # stop-loss overrides the strategy signal -- risk control comes first
        entry = self.account.entry_price
        if entry and price <= entry * (1 - STOP_LOSS_PCT):
            signal = "sell"

        if signal in ("buy", "sell") and self.account.execute(signal, price):
            log_trade(signal, price, self.account)

        return price, regime, signal


def run(mock=False):
    session = Session(mock)
    while True:
        try:
            price, regime, signal = session.tick()
            render(session.exchange_id, price, regime, signal, session.account)
        except ccxt.NetworkError as e:
            print(f"network error, retrying: {e}")
        except Exception as e:
            print(f"error: {e}")

        time.sleep(session.poll_seconds)


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

    signal, regime = generate_signal(highs, lows, closes, has_position=False)
    assert regime == "trending"
    assert signal == "buy", f"flat account in an uptrend should get a buy signal, got {signal}"

    signal, regime = generate_signal(highs, lows, closes, has_position=True)
    assert signal == "hold", f"already holding in an uptrend should hold, got {signal}"

    acct = PaperAccount(100.0)
    assert acct.execute("buy", 10.0) is True
    assert acct.cash == 50.0 and acct.position == 5.0, "buy should only spend POSITION_SIZE_PCT of cash"
    assert acct.entry_price == 10.0
    assert acct.execute("sell", 20.0) is True
    assert acct.cash == 150.0 and acct.position == 0.0 and acct.entry_price is None

    # stop-loss: Session.tick() should force-sell once price drops far enough below entry
    s = Session(mock=True)
    s.account.cash, s.account.position, s.account.entry_price = 0.0, 1.0, 100.0
    s.exchange.price = 90.0  # next fetched candle continues the random walk from here (10% below entry)
    _, _, signal = s.tick()
    assert signal == "sell", f"a 10% drop below entry should trigger stop-loss, got {signal}"
    assert s.account.position == 0.0, "stop-loss should have closed the position"

    print("demo(): all self-checks passed")


if __name__ == "__main__":
    import sys
    if "--test" in sys.argv:
        demo()
    else:
        run(mock="--mock" in sys.argv)
