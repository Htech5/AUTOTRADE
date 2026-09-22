"""
GUI shell for bot.py styled like a professional trading terminal: dark
background, neon accents, bordered CLI-style panels, candlestick chart with
right-side price axis + volume, live indicator gauges, equity sparkline,
multi-coin ticker strip, trade stats, and plain-language account summary.
Uses tkinter (Python stdlib) -- no extra dependency (no matplotlib).

Run:
    python gui.py
Then pick starting balance and mode (simulated data / live market data) in
the window that opens. All trading here is PAPER TRADING -- no real orders,
no real money, ever.
"""
import csv
import os
import threading
import time
import tkinter as tk
from datetime import datetime, timezone

import bot

BG = "#05070a"
PANEL = "#0a0d13"
FG = "#c9d1d9"
NEON_CYAN = "#00e5ff"
NEON_MAGENTA = "#b967ff"
GREEN = "#26d07c"
GREEN_DIM = "#123a2b"
RED = "#ff4d5e"
RED_DIM = "#3a1218"
YELLOW = "#e8c547"
GRID = "#1b2129"
DIM = "#5a6472"
FONT = ("Consolas", 12)
FONT_SM = ("Consolas", 10)
FONT_BIG = ("Consolas", 26, "bold")
FONT_MED = ("Consolas", 14, "bold")
CHART_CANDLES = 70
EQUITY_POINTS = 120
EXTRA_SYMBOLS = ["ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT"]
TICKER_REFRESH_EVERY = 5  # ticks

REGIME_LABEL = {
    "trending": "Pasar lagi TREN kuat",
    "ranging": "Pasar SIDEWAYS (naik-turun kecil)",
    "warming_up": "Mengumpulkan data awal...",
}
SIGNAL_LABEL = {"buy": "BELI", "sell": "JUAL", "hold": "TAHAN"}
SIGNAL_COLOR = {"buy": GREEN, "sell": RED, "hold": YELLOW}


def panel(parent, title, **pack_opts):
    """Bordered section with a small neon header -- terminal-dashboard look."""
    outer = tk.Frame(parent, bg=BG)
    outer.pack(**pack_opts)
    head = tk.Frame(outer, bg=BG)
    head.pack(fill="x")
    tk.Label(head, text="▍", bg=BG, fg=NEON_CYAN, font=FONT_MED).pack(side="left")
    tk.Label(head, text=title, bg=BG, fg=NEON_CYAN, font=("Consolas", 10, "bold")).pack(side="left")
    body = tk.Frame(outer, bg=PANEL, highlightbackground="#1c2430", highlightthickness=1)
    body.pack(fill="both", expand=True, pady=(2, 0))
    return body


def trade_stats():
    """Win rate + counts from the full trade log (paired buy->sell round trips)."""
    path = bot.TRADE_LOG
    if not os.path.exists(path):
        return dict(total=0, buys=0, sells=0, wins=0, round_trips=0)
    with open(path) as f:
        rows = list(csv.reader(f))[1:]
    buys = [r for r in rows if r[1] == "buy"]
    sells = [r for r in rows if r[1] == "sell"]
    wins = 0
    for i in range(min(len(buys), len(sells))):
        if float(sells[i][2]) > float(buys[i][2]):
            wins += 1
    round_trips = min(len(buys), len(sells))
    return dict(total=len(rows), buys=len(buys), sells=len(sells),
                wins=wins, round_trips=round_trips)


class MockTicker:
    """Independent random-walk price for the decorative ticker strip in mock mode."""
    def __init__(self, start_price, seed):
        self._ex = bot.MockExchange(start_price=start_price, seed=seed)
        self.price = start_price

    def quote(self):
        prev = self.price
        self.price = self._ex.fetch_ohlcv(None, None, limit=1)[0][4]
        change = (self.price - prev) / prev * 100 if prev else 0
        return self.price, change


class TerminalGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Adaptive Paper Trader")
        self.configure(bg=BG)
        self.geometry("1180x860")
        self.session = None
        self.equity_history = []
        self.tick_count = 0
        self.started_at = None
        self.live_on = True
        self.build_start_screen()

    # ---- start screen: ask deposit + mode, plain language ----------------
    def build_start_screen(self):
        self.start_frame = tk.Frame(self, bg=BG)
        self.start_frame.pack(fill="both", expand=True)

        tk.Label(self.start_frame, text="ADAPTIVE PAPER TRADER", bg=BG, fg=NEON_CYAN,
                 font=("Consolas", 22, "bold")).pack(pady=(60, 4))
        tk.Label(self.start_frame, text="Simulasi trading -- TIDAK ADA uang asli yang dipakai",
                 bg=BG, fg=DIM, font=FONT).pack(pady=(0, 40))

        row = tk.Frame(self.start_frame, bg=BG)
        row.pack(pady=10)
        tk.Label(row, text="Modal awal (USDT):", bg=BG, fg=FG, font=FONT_MED).pack(side="left", padx=8)
        self.balance_entry = tk.Entry(row, font=FONT_MED, width=12, bg=PANEL, fg=NEON_CYAN,
                                       insertbackground=NEON_CYAN, relief="flat",
                                       justify="center")
        self.balance_entry.insert(0, "1000")
        self.balance_entry.pack(side="left", padx=8)

        self.mode = tk.StringVar(value="mock")
        mode_frame = tk.Frame(self.start_frame, bg=BG)
        mode_frame.pack(pady=24)
        tk.Radiobutton(mode_frame, text="Data simulasi (cepat, tidak perlu internet ke exchange)",
                       variable=self.mode, value="mock", bg=BG, fg=FG, selectcolor=PANEL,
                       activebackground=BG, activeforeground=NEON_CYAN, font=FONT).pack(anchor="w", pady=4)
        tk.Radiobutton(mode_frame, text="Harga pasar sungguhan (real-time), order tetap simulasi",
                       variable=self.mode, value="live", bg=BG, fg=FG, selectcolor=PANEL,
                       activebackground=BG, activeforeground=NEON_CYAN, font=FONT).pack(anchor="w", pady=4)

        self.start_error = tk.Label(self.start_frame, text="", bg=BG, fg=RED, font=FONT)
        self.start_error.pack(pady=6)

        start_btn = tk.Button(self.start_frame, text="  MULAI SIMULASI  ", command=self.on_start,
                               bg=NEON_CYAN, fg="#05070a", font=FONT_MED, relief="flat",
                               activebackground=NEON_MAGENTA, cursor="hand2")
        start_btn.pack(pady=20, ipady=6)

    def on_start(self):
        raw = self.balance_entry.get().strip().replace(",", "")
        try:
            balance = float(raw)
            if balance <= 0:
                raise ValueError
        except ValueError:
            self.start_error.config(text="Masukkan angka modal yang valid (contoh: 1000)")
            return

        self.start_error.config(text="Menghubungkan...")
        self.update_idletasks()
        mock = self.mode.get() == "mock"
        try:
            self.session = bot.Session(mock=mock, balance=balance)
        except Exception as e:
            self.start_error.config(text=f"Gagal konek: {e}")
            return

        self.deposit = balance
        self.mock = mock
        self.extra_tickers = (
            {sym: MockTicker(start_price=p, seed=i) for i, (sym, p) in
             enumerate(zip(EXTRA_SYMBOLS, [3200, 165, 610, 0.58]))}
            if mock else {}
        )
        self.started_at = time.time()
        self.start_frame.destroy()
        self.build_dashboard()
        self.after(200, self.tick)
        self.after(500, self.blink_live)

    # ---- main dashboard ----------------------------------------------------
    def build_dashboard(self):
        strip_body = panel(self, "MARKET WATCH", fill="x", padx=14, pady=(12, 4))
        self.strip_labels = {}
        strip_row = tk.Frame(strip_body, bg=PANEL)
        strip_row.pack(fill="x", padx=10, pady=8)
        for sym in [bot.SYMBOL] + EXTRA_SYMBOLS:
            cell = tk.Frame(strip_row, bg=PANEL)
            cell.pack(side="left", padx=18)
            tk.Label(cell, text=sym, bg=PANEL, fg=DIM, font=FONT_SM).pack(anchor="w")
            lbl = tk.Label(cell, text="--", bg=PANEL, fg=FG, font=("Consolas", 12, "bold"))
            lbl.pack(anchor="w")
            self.strip_labels[sym] = lbl

        self.live_dot = tk.Label(strip_row, text="● LIVE", bg=PANEL, fg=GREEN, font=FONT_SM)
        self.live_dot.pack(side="right", padx=10)
        self.uptime_lbl = tk.Label(strip_row, text="uptime 00:00:00", bg=PANEL, fg=DIM, font=FONT_SM)
        self.uptime_lbl.pack(side="right", padx=10)

        acc_body = panel(self, f"AKUN -- {bot.SYMBOL}", fill="x", padx=14, pady=4)
        top = tk.Frame(acc_body, bg=PANEL)
        top.pack(fill="x", padx=14, pady=10)

        self.lbl_deposit = tk.Label(top, text="MODAL AWAL\n$0", bg=PANEL, fg=DIM,
                                     font=FONT_MED, justify="left")
        self.lbl_deposit.pack(side="left", padx=(0, 30))

        self.lbl_equity = tk.Label(top, text="SEKARANG JADI\n$0", bg=PANEL, fg=NEON_CYAN,
                                    font=FONT_BIG, justify="left")
        self.lbl_equity.pack(side="left", padx=(0, 30))

        self.lbl_pnl = tk.Label(top, text="+$0 (0%)", bg=PANEL, fg=GREEN, font=FONT_MED)
        self.lbl_pnl.pack(side="left")

        signal_row = tk.Frame(acc_body, bg=PANEL)
        signal_row.pack(fill="x", padx=14, pady=(0, 10))
        self.lbl_regime = tk.Label(signal_row, text="", bg=PANEL, fg=FG, font=FONT)
        self.lbl_regime.pack(side="left")
        self.lbl_signal = tk.Label(signal_row, text="", bg=PANEL, fg=YELLOW, font=("Consolas", 16, "bold"))
        self.lbl_signal.pack(side="right")

        # --- middle area: chart (left, wide) + indicators/stats (right) -----
        mid = tk.Frame(self, bg=BG)
        mid.pack(fill="both", expand=True, padx=14, pady=4)

        chart_col = tk.Frame(mid, bg=BG)
        chart_col.pack(side="left", fill="both", expand=True)
        chart_body = panel(chart_col, "CHART -- HARGA & VOLUME", fill="both", expand=True)
        self.ticker = tk.Label(chart_body, text="", bg=PANEL, fg=FG, font=("Consolas", 15, "bold"),
                                anchor="w")
        self.ticker.pack(fill="x", padx=10, pady=(8, 0))
        self.chart = tk.Canvas(chart_body, bg=PANEL, height=260, highlightthickness=0)
        self.chart.pack(fill="both", expand=True, padx=6, pady=4)
        self.volume = tk.Canvas(chart_body, bg=PANEL, height=50, highlightthickness=0)
        self.volume.pack(fill="x", padx=6, pady=(0, 6))

        side_col = tk.Frame(mid, bg=BG, width=280)
        side_col.pack(side="left", fill="y", padx=(10, 0))
        side_col.pack_propagate(False)

        ind_body = panel(side_col, "INDIKATOR", fill="x")
        self.gauge_rsi = self._build_gauge(ind_body, "RSI (14)")
        self.gauge_adx = self._build_gauge(ind_body, "ADX (14)")
        self.lbl_sma = tk.Label(ind_body, text="SMA9 vs SMA21: --", bg=PANEL, fg=FG,
                                 font=FONT_SM, anchor="w", justify="left")
        self.lbl_sma.pack(fill="x", padx=10, pady=(4, 10))

        eq_body = panel(side_col, "GRAFIK MODAL (EQUITY)", fill="x", pady=(8, 0))
        self.eq_canvas = tk.Canvas(eq_body, bg=PANEL, height=90, highlightthickness=0)
        self.eq_canvas.pack(fill="x", padx=6, pady=6)

        stats_body = panel(side_col, "STATISTIK", fill="x", pady=(8, 0))
        self.lbl_stats = tk.Label(stats_body, text="", bg=PANEL, fg=FG, font=FONT_SM,
                                   justify="left", anchor="w")
        self.lbl_stats.pack(fill="x", padx=10, pady=8)

        log_body = panel(self, "RIWAYAT TRANSAKSI", fill="both", expand=True, padx=14, pady=(4, 6))
        self.log = tk.Text(log_body, bg=PANEL, fg=FG, font=FONT, height=8, borderwidth=0,
                            highlightthickness=0, state="disabled")
        self.log.pack(fill="both", expand=True, padx=10, pady=8)
        for tag, color in (("buy", GREEN), ("sell", RED), ("dim", DIM)):
            self.log.tag_configure(tag, foreground=color)

        self.status = tk.Label(self, text="", bg=BG, fg=DIM, font=FONT_SM, anchor="w")
        self.status.pack(fill="x", padx=14, pady=(2, 10))

    def _build_gauge(self, parent, label):
        tk.Label(parent, text=label, bg=PANEL, fg=DIM, font=FONT_SM, anchor="w").pack(
            fill="x", padx=10, pady=(8, 0))
        c = tk.Canvas(parent, bg=PANEL, height=22, highlightthickness=0)
        c.pack(fill="x", padx=10, pady=(2, 4))
        return c

    def blink_live(self):
        if self.session:
            self.live_on = not self.live_on
            self.live_dot.config(fg=GREEN if self.live_on else "#0d3a24")
            elapsed = int(time.time() - self.started_at)
            h, rem = divmod(elapsed, 3600)
            m, s = divmod(rem, 60)
            self.uptime_lbl.config(text=f"uptime {h:02d}:{m:02d}:{s:02d}")
        self.after(500, self.blink_live)

    def tick(self):
        """Kick off a fetch on a background thread so the GUI never freezes
        while waiting on the exchange's network response."""
        self.status.config(text="mengambil data...")
        threading.Thread(target=self._fetch, daemon=True).start()

    def _fetch(self):
        try:
            result = self.session.tick()
            self.after(0, self._on_tick_ok, result)
        except Exception as e:
            self.after(0, self._on_tick_err, e)

    def _on_tick_ok(self, result):
        price, regime, signal = result
        self.tick_count += 1
        self.render(price, regime, signal)
        self.status.config(
            text=f"sumber: {self.session.exchange_id}  |  {bot.SYMBOL}  |  update tiap {self.session.poll_seconds}s")
        self.after(self.session.poll_seconds * 1000, self.tick)

    def _on_tick_err(self, e):
        self.status.config(text=f"error: {e}")
        self.after(self.session.poll_seconds * 1000, self.tick)

    # ---- chart: price axis on the right, dashed last-price line, volume ---
    def draw_chart(self):
        c = self.chart
        c.delete("all")
        c.update_idletasks()
        w, h = c.winfo_width(), c.winfo_height()
        candles = self.session.history[-CHART_CANDLES:]
        if len(candles) < 2 or w < 10:
            return

        axis_w = 62
        pad_top, pad_bot = 14, 10
        plot_w = w - axis_w
        highs = [k[2] for k in candles]
        lows = [k[3] for k in candles]
        lo, hi = min(lows), max(highs)
        if hi == lo:
            hi = lo + 1
        margin = (hi - lo) * 0.08
        lo, hi = lo - margin, hi + margin
        span = hi - lo

        def y(v):
            return pad_top + (hi - v) / span * (h - pad_top - pad_bot)

        for i in range(5):
            frac = i / 4
            gy = pad_top + frac * (h - pad_top - pad_bot)
            price_at = hi - frac * span
            c.create_line(0, gy, plot_w, gy, fill=GRID)
            c.create_text(plot_w + 6, gy, text=f"{price_at:,.0f}", fill=DIM,
                          font=FONT_SM, anchor="w")

        n = len(candles)
        cw = plot_w / n
        body_w = max(cw * 0.62, 2)
        for i, k in enumerate(candles):
            o, hi_, lo_, cl = k[1], k[2], k[3], k[4]
            x = i * cw + cw / 2
            up = cl >= o
            color = GREEN if up else RED
            c.create_line(x, y(hi_), x, y(lo_), fill=color, width=1)
            top, bot = y(max(o, cl)), y(min(o, cl))
            c.create_rectangle(x - body_w / 2, top, x + body_w / 2, max(bot, top + 1),
                                fill=color, outline=color)

        last_price = candles[-1][4]
        ly = y(last_price)
        up = candles[-1][4] >= candles[-1][1]
        last_color = GREEN if up else RED
        c.create_line(0, ly, plot_w, ly, fill=last_color, dash=(3, 3))
        c.create_rectangle(plot_w, ly - 8, w, ly + 8, fill=last_color, outline=last_color)
        c.create_text(plot_w + axis_w / 2, ly, text=f"{last_price:,.0f}", fill="#05070a",
                      font=("Consolas", 9, "bold"))

        # volume sub-panel, aligned to same x-axis
        vc = self.volume
        vc.delete("all")
        vc.update_idletasks()
        vw, vh = vc.winfo_width(), vc.winfo_height()
        vol_plot_w = vw - axis_w
        vols = [k[5] for k in candles]
        vmax = max(vols) or 1
        vcw = vol_plot_w / n
        vbody = max(vcw * 0.62, 2)
        for i, k in enumerate(candles):
            up = k[4] >= k[1]
            color = GREEN_DIM if up else RED_DIM
            x = i * vcw + vcw / 2
            bar_h = (k[5] / vmax) * (vh - 4)
            vc.create_rectangle(x - vbody / 2, vh - bar_h, x + vbody / 2, vh,
                                fill=color, outline=color)
        vc.create_text(vol_plot_w + 6, vh / 2, text="VOL", fill=DIM, font=FONT_SM, anchor="w")

    def draw_gauge(self, canvas, value, vmax, low, high):
        canvas.delete("all")
        canvas.update_idletasks()
        w, h = canvas.winfo_width(), canvas.winfo_height()
        if w < 10:
            return
        canvas.create_rectangle(0, 0, w, h, fill="#151b24", outline="")
        frac = max(0, min(1, value / vmax))
        color = GREEN if value < low else RED if value > high else YELLOW
        canvas.create_rectangle(0, 0, w * frac, h, fill=color, outline="")
        canvas.create_text(w - 4, h / 2, text=f"{value:.1f}", fill="#05070a" if frac > 0.4 else FG,
                           font=("Consolas", 9, "bold"), anchor="e")

    def draw_equity_curve(self):
        c = self.eq_canvas
        c.delete("all")
        c.update_idletasks()
        w, h = c.winfo_width(), c.winfo_height()
        pts = self.equity_history[-EQUITY_POINTS:]
        if len(pts) < 2 or w < 10:
            return
        lo, hi = min(pts), max(pts)
        if hi == lo:
            hi = lo + 1
        c.create_line(0, h / 2, w, h / 2, fill=GRID, dash=(2, 2))
        coords = []
        for i, v in enumerate(pts):
            x = i / (len(pts) - 1) * w
            yv = h - (v - lo) / (hi - lo) * (h - 6) - 3
            coords += [x, yv]
        color = GREEN if pts[-1] >= pts[0] else RED
        c.create_line(*coords, fill=color, width=2, smooth=True)

    def render(self, price, regime, signal):
        account = self.session.account
        equity = account.equity(price)
        deposit = self.deposit
        pnl = equity - deposit
        pnl_pct = (pnl / deposit * 100) if deposit else 0
        pnl_color = GREEN if pnl >= 0 else RED
        arrow = "▲" if pnl >= 0 else "▼"
        self.equity_history.append(equity)

        closes = [k[4] for k in self.session.history]
        highs = [k[2] for k in self.session.history]
        lows = [k[3] for k in self.session.history]
        change_pct = 0.0
        if len(closes) >= 2 and closes[-2]:
            change_pct = (closes[-1] - closes[-2]) / closes[-2] * 100
        change_color = GREEN if change_pct >= 0 else RED
        change_arrow = "▲" if change_pct >= 0 else "▼"

        self.draw_chart()
        self.draw_equity_curve()

        rsi_val = bot.rsi(closes, bot.RSI_PERIOD) or 50.0
        adx_val = bot.adx(highs, lows, closes, bot.ADX_PERIOD) or 0.0
        self.draw_gauge(self.gauge_rsi, rsi_val, 100, 30, 70)
        self.draw_gauge(self.gauge_adx, adx_val, 50, 100, bot.ADX_TREND_THRESHOLD)
        sma_f, sma_s = bot.sma(closes, bot.SMA_FAST), bot.sma(closes, bot.SMA_SLOW)
        if sma_f and sma_s:
            trend = "BULLISH" if sma_f > sma_s else "BEARISH"
            self.lbl_sma.config(text=f"SMA9 {sma_f:,.1f}  vs  SMA21 {sma_s:,.1f}\n-> {trend}",
                                 fg=GREEN if trend == "BULLISH" else RED)

        stats = trade_stats()
        wr = (stats["wins"] / stats["round_trips"] * 100) if stats["round_trips"] else 0.0
        self.lbl_stats.config(text=(
            f"Total transaksi : {stats['total']}\n"
            f"Beli / Jual      : {stats['buys']} / {stats['sells']}\n"
            f"Round-trip       : {stats['round_trips']}\n"
            f"Win rate         : {wr:.0f}%\n"
            f"Jumlah tick      : {self.tick_count}"
        ))

        if self.mock and self.extra_tickers:
            for sym, mt in self.extra_tickers.items():
                p, chg = mt.quote()
                lbl = self.strip_labels[sym]
                lbl.config(text=f"{p:,.2f}  {'▲' if chg>=0 else '▼'}{chg:+.2f}%",
                          fg=GREEN if chg >= 0 else RED)
        elif not self.mock and self.tick_count % TICKER_REFRESH_EVERY == 0:
            threading.Thread(target=self._refresh_ticker_strip, daemon=True).start()

        self.strip_labels[bot.SYMBOL].config(
            text=f"{price:,.2f}  {change_arrow}{change_pct:+.2f}%", fg=change_color)

        self.ticker.config(
            text=f"{bot.SYMBOL}   {price:,.2f} USDT   {change_arrow} {change_pct:+.2f}%",
            fg=change_color)

        self.lbl_deposit.config(text=f"MODAL AWAL\n${deposit:,.2f}")
        self.lbl_equity.config(text=f"SEKARANG JADI\n${equity:,.2f}", fg=pnl_color if pnl != 0 else NEON_CYAN)
        self.lbl_pnl.config(text=f"{arrow} ${pnl:,.2f}  ({pnl_pct:+.2f}%)", fg=pnl_color)

        self.lbl_regime.config(text=REGIME_LABEL.get(regime, regime))
        self.lbl_signal.config(text=SIGNAL_LABEL.get(signal, signal), fg=SIGNAL_COLOR.get(signal, FG))

        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        rows = bot.recent_trade_lines(bot.MAX_LOG_LINES)
        if not rows:
            self.log.insert("end", "  (belum ada transaksi)\n", "dim")
        for ts, side, px, cash, pos, eq in rows:
            label = "BELI" if side == "buy" else "JUAL"
            self.log.insert("end", f"  {ts[:19]}  {label:<5}  harga=${float(px):,.2f}  saldo jadi=${float(eq):,.2f}\n",
                            "buy" if side == "buy" else "sell")
        self.log.config(state="disabled")

    def _refresh_ticker_strip(self):
        for sym in EXTRA_SYMBOLS:
            try:
                t = self.session.exchange.fetch_ticker(sym)
                price, chg = t["last"], t.get("percentage") or 0.0
                self.after(0, self._update_strip_label, sym, price, chg)
            except Exception:
                continue

    def _update_strip_label(self, sym, price, chg):
        lbl = self.strip_labels.get(sym)
        if lbl:
            lbl.config(text=f"{price:,.2f}  {'▲' if chg>=0 else '▼'}{chg:+.2f}%",
                      fg=GREEN if chg >= 0 else RED)


if __name__ == "__main__":
    TerminalGUI().mainloop()
