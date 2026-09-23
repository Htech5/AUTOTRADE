"""
Backtest the adaptive strategy on real historical data, split into an
in-sample half and an out-of-sample half.

This is NOT automatic walk-forward optimization -- there's no parameter
search here, just the same fixed strategy/config run on two different
periods so you can see whether performance holds up or was just a fluke of
one period (a first, cheap way to catch overfitting). A real optimizer
would need a parameter grid and a lot more runtime; ask for it if you get
to that stage.

Reuses bot.Session/generate_signal/PaperAccount exactly as live trading
does (via ReplayExchange), so backtest results reflect the same code path
that actually runs -- no separate "backtest engine" to drift out of sync.

Usage:
    python backtest.py --days 60
    python backtest.py --days 90 --symbol ETH/USDT --timeframe 1h
"""
import argparse
import time

import ccxt

import bot


def fetch_historical(exchange_id, symbol, timeframe, days):
    exchange = getattr(ccxt, exchange_id)()
    since = exchange.milliseconds() - days * 24 * 60 * 60 * 1000
    candles = []
    while True:
        batch = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
        if not batch:
            break
        candles += batch
        if len(batch) < 1000:
            break
        since = batch[-1][0] + 1
        time.sleep(exchange.rateLimit / 1000)
    return candles


def run_backtest(candles, balance):
    session = bot.Session(mock=False, balance=balance,
                          exchange=bot.ReplayExchange(candles), exchange_id="backtest")
    equity_curve = []
    while True:
        try:
            price, regime, signal, reason = session.tick()
            equity_curve.append(session.account.equity(price))
        except StopIteration:
            break
    return session.trades, equity_curve


def print_report(label, trades, equity_curve, balance):
    stats = bot.analyze_trades(trades, equity_curve=equity_curve)
    final_equity = equity_curve[-1] if equity_curve else balance
    pf = "inf" if stats["profit_factor"] == float("inf") else f"{stats['profit_factor']:.2f}"
    print(f"\n--- {label} ---")
    print(f"  modal awal         : ${balance:,.2f}")
    print(f"  equity akhir       : ${final_equity:,.2f}  ({(final_equity-balance)/balance*100:+.2f}%)")
    print(f"  total transaksi    : {stats['total']}  (beli {stats['buys']} / jual {stats['sells']})")
    print(f"  round-trip         : {stats['round_trips']}")
    print(f"  win rate           : {stats['win_rate']:.1f}%")
    print(f"  expectancy/trade   : ${stats['expectancy']:,.2f}")
    print(f"  profit factor      : {pf}")
    print(f"  rata2 menang/kalah : ${stats['avg_win']:,.2f} / ${stats['avg_loss']:,.2f}")
    print(f"  max drawdown       : {stats['max_drawdown_pct']:.1f}%")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, default=60, help="total historical days to fetch (split in half)")
    parser.add_argument("--symbol", default=bot.SYMBOL)
    parser.add_argument("--timeframe", default=bot.TIMEFRAME)
    parser.add_argument("--exchange", default="binance")
    parser.add_argument("--balance", type=float, default=bot.INITIAL_BALANCE_USDT)
    args = parser.parse_args()

    print(f"Mengambil {args.days} hari data historis {args.symbol} ({args.timeframe}) dari {args.exchange}...")
    candles = fetch_historical(args.exchange, args.symbol, args.timeframe, args.days)
    print(f"Total {len(candles)} candle terkumpul.")
    if len(candles) < 200:
        print("Data terlalu sedikit untuk backtest yang berarti (butuh >= 200 candle).")
        return

    mid = len(candles) // 2
    trades_in, curve_in = run_backtest(candles[:mid], args.balance)
    print_report("IN-SAMPLE (paruh pertama)", trades_in, curve_in, args.balance)

    trades_out, curve_out = run_backtest(candles[mid:], args.balance)
    print_report("OUT-OF-SAMPLE (paruh kedua)", trades_out, curve_out, args.balance)

    print("\nCatatan: strategi & parameter yang sama dijalankan di kedua periode ini,")
    print("bukan hasil optimasi otomatis. Kalau hasil paruh pertama jauh lebih bagus dari")
    print("paruh kedua, itu tanda strategi/parameter overfit ke satu kondisi pasar saja.")


def demo():
    """Self-check: ReplayExchange behavior + a full backtest run on synthetic data, no network."""
    candles = [[i, 100 + i * 0.1, 100 + i * 0.1 + 1, 100 + i * 0.1 - 1, 100 + i * 0.1, 5.0]
               for i in range(300)]

    ex = bot.ReplayExchange(candles)
    first = ex.fetch_ohlcv("BTC/USDT", "5m", limit=10)
    assert first == candles[0:1], "before the window fills, replay should return what little history exists"
    for _ in range(20):
        ex.fetch_ohlcv("BTC/USDT", "5m", limit=10)
    window = ex.fetch_ohlcv("BTC/USDT", "5m", limit=10)
    assert len(window) == 10 and window[-1] == candles[ex.cursor - 1]

    ex2 = bot.ReplayExchange(candles[:5])
    for _ in range(5):
        ex2.fetch_ohlcv("BTC/USDT", "5m", limit=1)
    try:
        ex2.fetch_ohlcv("BTC/USDT", "5m", limit=1)
        raise AssertionError("should have run out of candles")
    except StopIteration:
        pass

    trades, curve = run_backtest(candles, 1000.0)
    assert isinstance(trades, list)
    assert len(curve) <= len(candles)
    stats = bot.analyze_trades(trades, equity_curve=curve)
    assert "win_rate" in stats and "max_drawdown_pct" in stats

    print("demo(): all self-checks passed")


if __name__ == "__main__":
    import sys
    if "--test" in sys.argv:
        demo()
    else:
        main()
