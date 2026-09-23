"""
Adaptive crypto paper-trading bot.

Regime detection (ADX) picks the strategy:
- Trending market (ADX > threshold): SMA fast/slow crossover, gated by a
  longer-term SMA filter (don't buy against the macro trend) and, for live
  sessions, a higher-timeframe EMA confirmation.
- Ranging market (ADX <= threshold): Bollinger Band + RSI mean-reversion
  (buy near the lower band while oversold, sell near the upper band while
  overbought) -- the "adaptive" half of the strategy for sideways markets.

Every new entry also has to clear a volatility floor (ATR/price) and a
minimum reward-vs-cost bar (target must be worth several times the round
trip fee+slippage) before it's taken. Exits use an ATR-based stop and
take-profit with breakeven trailing, and a stop-loss exit triggers a
cooldown before the next entry.

Paper trading only: no real orders are sent, trades are simulated (with fee
and slippage modeled) against live public market data pulled via ccxt.
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
LONG_MA_PERIOD = 50      # macro trend filter: only buy when price also above this longer average
RSI_PERIOD = 14
RSI_OVERSOLD, RSI_OVERBOUGHT = 40, 60
ADX_PERIOD = 14
ADX_TREND_THRESHOLD = 20
STRONG_ADX = 30           # ADX above this (+ volume confirmation) = "strong" signal -> full size
ATR_PERIOD = 14
BB_PERIOD, BB_MULT = 20, 2.0
MIN_ATR_PCT = 0.0015      # skip new entries if ATR/price is below this -- market too quiet to bother

HIGHER_TIMEFRAME = "1h"          # confirmation timeframe for live sessions (point A3)
HIGHER_TF_EMA_PERIOD = 50
HIGHER_TF_REFRESH_TICKS = 30     # only refetch every N ticks -- don't hammer the exchange

FEE_PCT = 0.001            # 0.1% per side
SLIPPAGE_PCT = 0.0003       # ~0.03% per side
MIN_TARGET_MULTIPLE = 3     # required reward vs round-trip cost before taking a trade
ATR_STOP_MULT = 1.2         # stop = entry - ATR_STOP_MULT * ATR
TP_RR_MULTIPLE = 2.0        # take-profit = TP_RR_MULTIPLE * stop distance
TRAIL_TRIGGER_RR = 1.0      # once profit reaches this multiple of risk, trail stop to breakeven
COOLDOWN_CANDLES = 5        # candles to wait after a stop-loss exit before allowing a new entry

RISK_PER_TRADE_PCT = 0.01   # risk 1% of equity per trade for position sizing
WEAK_SIZE_MULT = 0.5        # half size when the signal isn't "strong"

INITIAL_BALANCE_USDT = 1000.0
TRADE_LOG = os.path.join(os.path.dirname(__file__), "trades.csv")
MAX_LOG_LINES = 8  # recent trades shown in the dashboard


# ---- indicators (pure python, no numpy/pandas needed) ------------------
def sma(values, period):
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def ema(values, period):
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    e = sum(values[:period]) / period
    for v in values[period:]:
        e = v * k + e * (1 - k)
    return e


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


def _tr_dm(highs, lows, closes, period):
    """Shared true-range/directional-movement arrays used by both adx() and atr()."""
    n = period + 1
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
    return plus_dm, minus_dm, tr


def adx(highs, lows, closes, period=14):
    n = period + 1
    if len(closes) < n + 1:
        return None
    plus_dm, minus_dm, tr = _tr_dm(highs, lows, closes, period)
    atr_val = sum(tr[-period:]) / period
    if atr_val == 0:
        return 0.0
    plus_di = 100 * (sum(plus_dm[-period:]) / period) / atr_val
    minus_di = 100 * (sum(minus_dm[-period:]) / period) / atr_val
    denom = plus_di + minus_di
    if denom == 0:
        return 0.0
    return 100 * abs(plus_di - minus_di) / denom


def atr(highs, lows, closes, period=14):
    n = period + 1
    if len(closes) < n + 1:
        return None
    _, _, tr = _tr_dm(highs, lows, closes, period)
    return sum(tr[-period:]) / period


def bollinger(closes, period=20, mult=2.0):
    if len(closes) < period:
        return None
    window = closes[-period:]
    mid = sum(window) / period
    variance = sum((c - mid) ** 2 for c in window) / period
    stdev = variance ** 0.5
    return mid, mid + mult * stdev, mid - mult * stdev  # mid, upper, lower


def _volume_confirmed(volumes, lookback=20):
    """True if the latest volume is above its recent average (or not enough data to judge)."""
    if len(volumes) < lookback + 1:
        return True
    recent = volumes[-1]
    avg = sum(volumes[-lookback - 1:-1]) / lookback
    return avg == 0 or recent > avg


# ---- signal generation --------------------------------------------------
def generate_signal(highs, lows, closes, volumes, has_position=False, higher_tf_ok=True):
    """Returns (signal, regime, reason, meta).

    reason is a plain-language explanation for the GUI's running commentary.
    meta carries the indicator snapshot at decision time (for logging/audit,
    point F) plus "strength" ("strong"/"weak"/None) used for position sizing.

    Gates applied before any new BUY is allowed (exits are never blocked):
      - enough history to compute indicators
      - ATR/price above MIN_ATR_PCT (skip dead-quiet markets)
      - price above the long-term SMA filter (don't buy into a macro downtrend)
      - higher-timeframe EMA confirmation (live sessions only)
      - the ATR-based target is worth >= MIN_TARGET_MULTIPLE times round-trip
        fee+slippage cost (don't trade for less than the cost of trading)
    """
    adx_val = adx(highs, lows, closes, ADX_PERIOD)
    atr_val = atr(highs, lows, closes, ATR_PERIOD)
    if adx_val is None or atr_val is None:
        return "hold", "warming_up", "Masih mengumpulkan data candle awal, belum bisa menilai pasar.", {}

    rsi_val = rsi(closes, RSI_PERIOD)
    long_ma = sma(closes, LONG_MA_PERIOD)
    bb = bollinger(closes, BB_PERIOD, BB_MULT)
    price = closes[-1]
    strength = "strong" if (adx_val >= STRONG_ADX and _volume_confirmed(volumes)) else "weak"

    meta = dict(adx=adx_val, atr=atr_val, rsi=rsi_val, long_ma=long_ma, strength=strength)

    macro_ok = long_ma is None or price > long_ma
    atr_pct = atr_val / price if price else 0
    volatility_ok = atr_pct >= MIN_ATR_PCT
    stop_distance = ATR_STOP_MULT * atr_val
    target_pct = (TP_RR_MULTIPLE * stop_distance) / price if price else 0
    round_trip_cost_pct = 2 * (FEE_PCT + SLIPPAGE_PCT)
    worth_it = target_pct >= MIN_TARGET_MULTIPLE * round_trip_cost_pct

    def entry_gate_reason():
        """First failing entry gate, or None if a new buy is allowed."""
        if not volatility_ok:
            return (f"Volatilitas terlalu rendah (ATR {atr_pct*100:.2f}% dari harga) -- "
                    f"pasar terlalu sepi, skip dulu.")
        if not macro_ok:
            return f"Harga masih di bawah SMA{LONG_MA_PERIOD} -- tren jangka panjang masih turun, hindari beli."
        if not higher_tf_ok:
            return f"Timeframe besar ({HIGHER_TIMEFRAME} EMA{HIGHER_TF_EMA_PERIOD}) belum konfirmasi tren naik."
        if not worth_it:
            return (f"Target realistis ({target_pct*100:.2f}%) belum {MIN_TARGET_MULTIPLE}x lebih besar dari "
                    f"biaya round-trip ({MIN_TARGET_MULTIPLE*round_trip_cost_pct*100:.2f}%) -- skip.")
        return None

    regime = "trending" if adx_val > ADX_TREND_THRESHOLD else "ranging"

    if regime == "trending":
        fast, slow = sma(closes, SMA_FAST), sma(closes, SMA_SLOW)
        if fast is None or slow is None:
            return "hold", "trending", "Data SMA belum cukup untuk menilai arah tren.", meta
        arah = "naik" if fast > slow else "turun"
        if fast > slow and not has_position:
            blocked = entry_gate_reason()
            if blocked:
                return "hold", "trending", f"Sinyal jangka pendek naik (ADX {adx_val:.0f}) tapi {blocked}", meta
            return ("buy", "trending",
                    f"Tren kuat naik (ADX {adx_val:.0f}), SMA9 di atas SMA21, target {target_pct*100:.2f}% "
                    f"(sinyal {strength}) -- masuk posisi.", meta)
        if fast < slow and has_position:
            return "sell", "trending", f"Tren berbalik turun (ADX {adx_val:.0f}), SMA9 di bawah SMA21 -- keluar.", meta
        if has_position:
            return "hold", "trending", f"Masih pegang posisi, tren {arah} (ADX {adx_val:.0f}) belum berbalik.", meta
        return "hold", "trending", f"Tren {arah} (ADX {adx_val:.0f}) tapi kondisi belum pas untuk masuk.", meta

    # ranging: Bollinger Band + RSI mean-reversion -- buy near the range bottom,
    # sell near the range top (the "adaptive" half of the strategy)
    if rsi_val is None or bb is None:
        return "hold", "ranging", "Data RSI/Bollinger Band belum cukup untuk menilai pasar.", meta
    _, upper, lower = bb
    if price <= lower and rsi_val < RSI_OVERSOLD and not has_position:
        blocked = entry_gate_reason()
        if blocked:
            return "hold", "ranging", f"Harga dekat batas bawah range, RSI {rsi_val:.0f} oversold tapi {blocked}", meta
        return ("buy", "ranging",
                f"Pasar sideways, harga di batas bawah range & RSI {rsi_val:.0f} oversold, "
                f"target {target_pct*100:.2f}% (sinyal {strength}) -- ambil peluang beli.", meta)
    if price >= upper and rsi_val > RSI_OVERBOUGHT and has_position:
        return "sell", "ranging", f"Harga di batas atas range & RSI {rsi_val:.0f} overbought -- ambil untung.", meta
    if has_position:
        return "hold", "ranging", f"Pasar sideways, RSI {rsi_val:.0f} -- tahan posisi dulu.", meta
    return "hold", "ranging", f"Pasar sideways, RSI {rsi_val:.0f} -- menunggu harga dekat batas range.", meta


# ---- paper trading engine -----------------------------------------------
class PaperAccount:
    """Fee + slippage are modeled on every fill so paper results aren't
    flattering compared to what a real order would actually cost."""

    def __init__(self, cash):
        self.cash = cash
        self.position = 0.0  # base asset qty held
        self.entry_price = None
        self.stop_price = None
        self.target_price = None

    def equity(self, price):
        return self.cash + self.position * price

    def buy(self, price, qty):
        if qty <= 0 or self.cash <= 0:
            return False
        fill_price = price * (1 + SLIPPAGE_PCT)
        max_qty = self.cash / (fill_price * (1 + FEE_PCT))
        qty = min(qty, max_qty)
        if qty <= 0:
            return False
        cost = qty * fill_price
        fee = cost * FEE_PCT
        self.cash -= cost + fee
        self.position += qty
        self.entry_price = fill_price
        return True

    def sell(self, price):
        if self.position <= 0:
            return False
        fill_price = price * (1 - SLIPPAGE_PCT)
        proceeds = self.position * fill_price
        fee = proceeds * FEE_PCT
        self.cash += proceeds - fee
        self.position = 0.0
        self.entry_price = None
        self.stop_price = None
        self.target_price = None
        return True


def log_trade(side, price, account, info=None):
    info = info or {}
    is_new = not os.path.exists(TRADE_LOG)
    with open(TRADE_LOG, "a", newline="") as f:
        w = csv.writer(f)
        if is_new:
            w.writerow(["timestamp", "side", "price", "cash", "position", "equity",
                        "rsi", "adx", "atr", "reason"])
        w.writerow([
            datetime.now(timezone.utc).isoformat(), side, price,
            round(account.cash, 2), round(account.position, 8),
            round(account.equity(price), 2),
            info.get("rsi"), info.get("adx"), info.get("atr"), info.get("reason", ""),
        ])


def analyze_trades(trades, equity_curve=None):
    """Performance metrics from this session's own trades (point F):
    win rate, expectancy, profit factor, max drawdown, avg win/loss."""
    buys = [t for t in trades if t[1] == "buy"]
    sells = [t for t in trades if t[1] == "sell"]
    round_trips = min(len(buys), len(sells))
    pnls = [sells[i][5] - buys[i][5] for i in range(round_trips)]  # equity delta per round trip
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_profit, gross_loss = sum(wins), abs(sum(losses))

    curve = equity_curve if equity_curve else [t[5] for t in trades]
    peak = curve[0] if curve else 0.0
    max_dd = 0.0
    for e in curve:
        peak = max(peak, e)
        if peak > 0:
            max_dd = max(max_dd, (peak - e) / peak)

    return dict(
        total=len(trades), buys=len(buys), sells=len(sells), round_trips=round_trips,
        wins=len(wins), losses=len(losses),
        win_rate=(len(wins) / round_trips * 100) if round_trips else 0.0,
        avg_win=(gross_profit / len(wins)) if wins else 0.0,
        avg_loss=(sum(losses) / len(losses)) if losses else 0.0,
        expectancy=(sum(pnls) / len(pnls)) if pnls else 0.0,
        profit_factor=(gross_profit / gross_loss) if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0),
        max_drawdown_pct=max_dd * 100,
    )


class MockExchange:
    """Random-walk OHLCV generator: lets the bot/UI run with no exchange access."""

    def __init__(self, start_price=65000.0, seed=None):
        self.price = start_price
        self.rng = random.Random(seed)

    def fetch_ohlcv(self, symbol, timeframe, limit=1):
        # ponytail: amplitude tuned to roughly match a real 5m BTC candle's
        # typical range so the new ATR/target-vs-cost gates aren't starved
        # of volatility in demo mode; real exchange data needs no tuning.
        candles = []
        for _ in range(limit):
            o = self.price
            self.price *= 1 + self.rng.uniform(-0.012, 0.012)
            h, l, c = max(o, self.price), min(o, self.price), self.price
            volume = self.rng.uniform(0.5, 8.0)
            candles.append([0, o, h, l, c, volume])
        return candles


class ReplayExchange:
    """Feeds pre-fetched historical candles to Session.tick() one bar at a
    time, for backtest.py -- reuses the exact same strategy/execution code
    path as live/paper trading instead of a separate backtest engine."""

    def __init__(self, candles):
        self.candles = candles
        self.cursor = 0

    def fetch_ohlcv(self, symbol, timeframe, limit=1):
        self.cursor += 1
        if self.cursor > len(self.candles):
            raise StopIteration("no more historical candles")
        return self.candles[max(0, self.cursor - limit):self.cursor]


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


def render(exchange_id, price, regime, signal, reason, account):
    bar = "=" * 52
    clear_screen()
    print(bar)
    print(f" ADAPTIVE PAPER TRADER   [{exchange_id}]  {SYMBOL} {TIMEFRAME}")
    print(bar)
    print(f" time     : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f" price    : {price:.2f}")
    print(f" regime   : {regime}")
    print(f" signal   : {signal.upper()}")
    print(f" alasan   : {reason}")
    print(f" cash     : {account.cash:.2f} USDT")
    print(f" position : {account.position:.8f} BTC")
    if account.stop_price:
        print(f" stop/tp  : {account.stop_price:.2f} / {account.target_price:.2f}")
    print(f" equity   : {account.equity(price):.2f} USDT")
    print(bar)
    print(f" last {MAX_LOG_LINES} trades:")
    rows = recent_trade_lines(MAX_LOG_LINES)
    if not rows:
        print("   (none yet)")
    for row in rows:
        ts, side, px, cash, pos, eq = row[:6]
        print(f"   {ts[:19]}  {side:<4}  px={float(px):.2f}  equity={float(eq):.2f}")
    print(bar)
    print(" Ctrl+C to stop")


class Session:
    """One trading session: holds exchange connection, account and OHLCV history.
    tick() advances one step -- shared by the CLI loop, the GUI's poll loop,
    and backtest.py (via a ReplayExchange instead of a live/mock one)."""

    def __init__(self, mock=False, balance=INITIAL_BALANCE_USDT, exchange=None, exchange_id=None):
        self.mock = mock
        if exchange is not None:
            self.exchange, self.exchange_id = exchange, exchange_id or "custom"
        elif mock:
            self.exchange, self.exchange_id = MockExchange(), "mock (random walk)"
        else:
            self.exchange, self.exchange_id = connect_exchange()

        if mock:
            self.poll_seconds = 1
            self.history = self.exchange.fetch_ohlcv(SYMBOL, TIMEFRAME, limit=CANDLE_LIMIT)
        else:
            self.poll_seconds = POLL_SECONDS
            self.history = []

        self.account = PaperAccount(balance)
        self.trades = []  # this session's own trades only -- trades.csv accumulates across runs
        self.cooldown_remaining = 0
        self.higher_tf_bullish = True  # unknown yet -> don't block until we've actually checked
        self._higher_tf_tick = 0

    def _refresh_higher_timeframe(self):
        """Point A3: confirm the entry timeframe against a coarser one. Only
        meaningful with a real exchange -- mock/replay sessions keep the
        default (unblocked) since there's no independent higher-timeframe data."""
        if self.mock or isinstance(self.exchange, ReplayExchange):
            return
        try:
            candles = self.exchange.fetch_ohlcv(SYMBOL, HIGHER_TIMEFRAME, limit=HIGHER_TF_EMA_PERIOD + 5)
            closes_htf = [c[4] for c in candles]
            e = ema(closes_htf, HIGHER_TF_EMA_PERIOD)
            if e is not None:
                self.higher_tf_bullish = closes_htf[-1] > e
        except Exception:
            pass  # keep the last known reading on a transient failure

    def tick(self):
        """Fetch latest data, trade on signal. Returns (price, regime, signal, reason) or raises."""
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
        volumes = [c[5] for c in ohlcv]
        price = closes[-1]

        self._higher_tf_tick += 1
        if self._higher_tf_tick == 1 or self._higher_tf_tick % HIGHER_TF_REFRESH_TICKS == 0:
            self._refresh_higher_timeframe()

        has_position = self.account.position > 0
        signal, regime, reason, meta = generate_signal(
            highs, lows, closes, volumes, has_position=has_position, higher_tf_ok=self.higher_tf_bullish)

        # cooldown after a stop-loss exit -- point D: don't immediately re-enter
        if self.cooldown_remaining > 0:
            if signal == "buy":
                signal = "hold"
                reason = (f"Cooldown aktif setelah kena stop-loss ({self.cooldown_remaining} candle lagi) "
                          f"-- tunggu sinyal baru yang valid.")
            self.cooldown_remaining -= 1

        stopped_out = False
        if has_position and self.account.entry_price:
            # trailing stop to breakeven once profit reaches TRAIL_TRIGGER_RR x risk (point C)
            risk = self.account.entry_price - (self.account.stop_price or self.account.entry_price)
            if risk > 0 and price >= self.account.entry_price + TRAIL_TRIGGER_RR * risk:
                self.account.stop_price = max(self.account.stop_price, self.account.entry_price)
            if self.account.target_price is not None and price >= self.account.target_price:
                signal = "sell"
                reason = f"Target profit tercapai di {price:,.2f} (target {self.account.target_price:,.2f}) -- ambil untung."
            elif self.account.stop_price is not None and price <= self.account.stop_price:
                signal = "sell"
                reason = f"Stop-loss (berbasis ATR) kena di {price:,.2f} (stop {self.account.stop_price:,.2f}) -- cut loss."
                stopped_out = True

        info = {**meta, "reason": reason}

        if signal == "buy" and not has_position:
            atr_val = meta.get("atr") or price * 0.01
            stop_distance = ATR_STOP_MULT * atr_val
            risk_amount = self.account.equity(price) * RISK_PER_TRADE_PCT
            qty = risk_amount / stop_distance if stop_distance > 0 else 0
            if meta.get("strength") == "weak":
                qty *= WEAK_SIZE_MULT
            if qty > 0 and self.account.buy(price, qty):
                self.account.stop_price = price - stop_distance
                self.account.target_price = price + TP_RR_MULTIPLE * stop_distance
                log_trade("buy", price, self.account, info)
                self.trades.append((
                    datetime.now(timezone.utc).isoformat(), "buy", price,
                    self.account.cash, self.account.position, self.account.equity(price), info,
                ))
        elif signal == "sell" and has_position:
            if self.account.sell(price):
                if stopped_out:
                    self.cooldown_remaining = COOLDOWN_CANDLES
                log_trade("sell", price, self.account, info)
                self.trades.append((
                    datetime.now(timezone.utc).isoformat(), "sell", price,
                    self.account.cash, self.account.position, self.account.equity(price), info,
                ))

        return price, regime, signal, reason


def run(mock=False):
    session = Session(mock)
    while True:
        try:
            price, regime, signal, reason = session.tick()
            render(session.exchange_id, price, regime, signal, reason, session.account)
        except ccxt.NetworkError as e:
            print(f"network error, retrying: {e}")
        except Exception as e:
            print(f"error: {e}")

        time.sleep(session.poll_seconds)


def demo():
    """Self-check: indicators, signal logic, fee/slippage, ATR stop/target, cooldown."""
    closes = list(range(1, 60))  # steady uptrend
    highs = [c + 1 for c in closes]
    lows = [c - 1 for c in closes]
    volumes = [10.0] * len(closes)

    assert sma(closes, 5) == sum(closes[-5:]) / 5
    assert sma([1, 2], 5) is None
    assert ema(closes, 5) is not None

    r = rsi(closes, 14)
    assert r == 100.0, f"pure uptrend should be RSI 100, got {r}"

    trend = adx(highs, lows, closes, 14)
    assert trend is not None and trend > 25, f"steady uptrend should register as trending, got {trend}"

    a = atr(highs, lows, closes, 14)
    assert a is not None and a > 0

    bb = bollinger(closes, 20, 2.0)
    assert bb is not None and bb[1] > bb[0] > bb[2]  # upper > mid > lower

    signal, regime, reason, meta = generate_signal(highs, lows, closes, volumes, has_position=False)
    assert regime == "trending"
    assert signal == "buy", f"flat account in a strong, cheap-to-trade uptrend should get a buy signal, got {signal} ({reason})"
    assert meta["strength"] in ("strong", "weak")

    signal, regime, reason, meta = generate_signal(highs, lows, closes, volumes, has_position=True)
    assert signal == "hold", f"already holding in an uptrend should hold, got {signal}"

    # volatility floor: a dead-flat market should never buy even if "trending" by ADX quirk
    flat = [100.0] * 40
    signal, regime, reason, meta = generate_signal(flat, flat, flat, volumes[:40], has_position=False)
    assert signal == "hold"

    # fee + slippage: buying should cost slightly more than the raw price*qty
    acct = PaperAccount(1000.0)
    assert acct.buy(100.0, 5.0) is True
    expected_cost = 5.0 * 100.0 * (1 + SLIPPAGE_PCT) * (1 + FEE_PCT)
    assert abs((1000.0 - acct.cash) - expected_cost) < 1e-6, "buy should charge fee+slippage on top of price*qty"
    assert acct.position == 5.0
    cash_before = acct.cash
    assert acct.sell(120.0) is True
    expected_proceeds = 5.0 * 120.0 * (1 - SLIPPAGE_PCT) * (1 - FEE_PCT)
    assert abs((acct.cash - cash_before) - expected_proceeds) < 1e-6, "sell should lose fee+slippage off proceeds"

    # buy sizing should never exceed available cash even if requested qty is huge
    acct2 = PaperAccount(100.0)
    assert acct2.buy(10.0, 1000.0) is True
    assert acct2.cash >= -1e-9

    # ATR stop/target + cooldown: Session.tick() should force-sell and start a cooldown
    s = Session(mock=True)
    s.account.cash, s.account.position = 0.0, 1.0
    s.account.entry_price, s.account.stop_price, s.account.target_price = 100.0, 95.0, 110.0
    s.exchange.price = 90.0  # next candle continues the random walk below the stop
    _, _, signal, reason = s.tick()
    assert signal == "sell", f"price below the ATR stop should force-sell, got {signal}"
    assert "stop-loss" in reason.lower()
    assert s.account.position == 0.0
    assert s.cooldown_remaining == COOLDOWN_CANDLES, "a stop-loss exit should start the cooldown"

    s2 = Session(mock=True)
    s2.cooldown_remaining = 3
    # force a buy-favorable setup, then confirm cooldown holds it anyway
    s2.history = [[0, c, c + 1, c - 1, c, 10.0] for c in range(1, 60)]
    _, _, signal, reason = s2.tick()
    assert signal != "buy" or s2.cooldown_remaining == 0, "cooldown should block new buys until it runs out"

    # analyze_trades: a simple win + a simple loss round trip
    trades = [
        ("t1", "buy", 100.0, 0, 1, 1000.0, {}),
        ("t2", "sell", 110.0, 0, 0, 1100.0, {}),
        ("t3", "buy", 100.0, 0, 1, 1100.0, {}),
        ("t4", "sell", 90.0, 0, 0, 900.0, {}),
    ]
    stats = analyze_trades(trades)
    assert stats["round_trips"] == 2
    assert stats["wins"] == 1 and stats["losses"] == 1
    assert stats["win_rate"] == 50.0
    assert stats["profit_factor"] > 0

    print("demo(): all self-checks passed")


if __name__ == "__main__":
    import sys
    if "--test" in sys.argv:
        demo()
    else:
        run(mock="--mock" in sys.argv)
