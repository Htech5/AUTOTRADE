# Adaptive Paper Trader

Bot trading crypto adaptif: mendeteksi kondisi pasar (tren kuat vs sideways)
lalu otomatis ganti strategi -- SMA crossover saat tren, RSI mean-reversion
saat sideways. **Semua mode di repo ini adalah paper trading / simulasi.
Tidak ada order asli yang dikirim ke exchange, tidak ada uang asli yang
dipakai**, walaupun harga yang ditampilkan bisa harga pasar sungguhan.

## Isi project

| File | Fungsi |
|---|---|
| `bot.py` | Logic trading (indikator, deteksi regime, risk management, akun simulasi) + versi CLI |
| `gui.py` | Tampilan GUI ala terminal trading (chart candlestick, angka modal/untung-rugi, export laporan) |
| `backtest.py` | Uji strategi di data historis asli (in-sample vs out-of-sample) |
| `requirements.txt` | Dependency (`ccxt`, untuk ambil data harga dari exchange) |
| `trades.csv` | Log semua transaksi simulasi (dibuat otomatis saat pertama kali ada transaksi) |

## 1. Install

```bash
pip install -r requirements.txt
```

## 2. Coba Simulasi

### Cara termudah: GUI

```bash
python gui.py
```

Akan muncul layar awal untuk:
1. **Modal awal (USDT)** -- masukkan angka bebas, misal `1000`. Ini cuma
   angka simulasi, bukan uang asli.
2. **Mode data**:
   - *Data simulasi* -- harga dibuat acak (random walk), tidak perlu koneksi
     ke exchange sama sekali. Cocok buat cepat lihat tampilan.
   - *Harga pasar sungguhan* -- bot ambil harga BTC/USDT real-time dari
     Binance/OKX/Bybit/Kraken (dicoba berurutan), tapi order tetap disimulasikan
     secara lokal.

Setelah klik **MULAI SIMULASI**, dashboard menampilkan:
- **MODAL AWAL** vs **SEKARANG JADI** vs **UNTUNG/RUGI** (nominal + persen)
- Chart candlestick harga
- Status pasar ("lagi TREN" / "SIDEWAYS") dan sinyal (BELI/JUAL/TAHAN)
- Riwayat transaksi

### Alternatif: CLI (tanpa GUI)

```bash
python bot.py --mock   # data simulasi acak, update tiap 1 detik
python bot.py          # data harga live, order tetap simulasi
```

### Cek hasil

Semua transaksi simulasi otomatis tercatat di `trades.csv` (kolom:
waktu, beli/jual, harga, sisa cash, posisi, total equity). Buka file ini di
Excel/Notepad untuk lihat riwayat lengkap dan hitung performa strategi
sebelum mempertimbangkan uang asli.

### Jalankan self-test (opsional, buat yang mau modif kode)

```bash
python bot.py --test
```

## 3. Kalau nanti mau coba pakai uang asli (live trading)

**Baca ini dulu:** kode di repo ini SAAT INI belum bisa kirim order asli --
itu keputusan yang disengaja. Live trading = risiko kehilangan uang, jadi
jangan buru-buru. Tahapannya kalau kamu memang sudah yakin:

### A. Buat akun exchange

1. Daftar di exchange yang didukung, contoh [Binance](https://www.binance.com)
   atau [OKX](https://www.okx.com).
2. Selesaikan verifikasi identitas (KYC) -- wajib di exchange besar sebelum
   bisa deposit/trading dalam jumlah signifikan.
3. Aktifkan **2FA** (Google Authenticator, bukan cuma SMS) untuk keamanan akun.

### B. Deposit dana

1. Transfer Rupiah lewat exchange lokal (Indodax, Pintu, dll) lalu kirim
   crypto ke exchange internasional, **atau**
2. Pakai fitur deposit langsung (kartu/bank transfer) kalau exchange-nya
   mendukung untuk region Indonesia.
3. **Mulai dengan nominal kecil** yang siap kamu anggap hilang sepenuhnya --
   bot trading, sepintar apapun, tetap bisa rugi.

### C. Buat API Key (supaya bot bisa "melihat" dan/atau "bertransaksi" atas namamu)

1. Login ke exchange -> menu **API Management** (di Binance: Profile ->
   API Management).
2. Klik **Create API**, kasih nama label (misal `paper-trader-bot`).
3. Atur permission dengan hati-hati:
   - ✅ **Enable Reading** -- wajib, untuk ambil harga & saldo.
   - ✅ **Enable Spot Trading** -- hanya aktifkan ini kalau memang mau bot
     eksekusi order.
   - ❌ **Enable Withdrawals** -- **JANGAN PERNAH** diaktifkan untuk API key
     yang dipakai bot. Kalau key bocor, penyerang tidak akan bisa menarik
     dananya keluar.
4. Kalau exchange menyediakan **IP whitelist**, aktifkan dan isi dengan IP
   komputer/server yang menjalankan bot -- ini mengunci key supaya tidak
   bisa dipakai dari perangkat lain sama sekali.
5. Simpan **API Key** dan **Secret Key** yang muncul (Secret biasanya cuma
   ditampilkan sekali). Simpan di password manager, jangan di teks biasa.

### D. Simpan API key dengan aman (jangan taruh di kode)

Set sebagai environment variable, contoh di PowerShell:

```powershell
$env:EXCHANGE_API_KEY = "isi-api-key-kamu"
$env:EXCHANGE_API_SECRET = "isi-secret-kamu"
```

Kode bot nantinya membaca dari `os.environ`, bukan ditulis langsung di file
`.py` -- supaya key tidak ikut ter-commit ke git atau ter-share tanpa sengaja.

### E. Perubahan kode yang masih perlu dibuat sebelum live (belum ada di repo ini)

Ini bagian yang sengaja belum diimplementasikan otomatis karena menyangkut
uang asli -- minta dibuatkan kalau kamu sudah sampai tahap ini:

1. Autentikasi `ccxt` pakai API key/secret dari environment variable.
2. Ganti `PaperAccount.buy()`/`.sell()` (simulasi lokal) dengan
   `exchange.create_market_buy_order(...)` / `create_market_sell_order(...)`
   (order asli) -- stop-loss/take-profit ATR-nya bisa jadi `create_order`
   bertipe stop/limit di exchange, bukan cuma dicek tiap tick di kode.
3. Handle minimum order size (lot size) exchange -- fee & slippage sudah
   disimulasikan, tapi realita exchange bisa beda persis.
4. Risk control tambahan: batas kerugian harian (daily loss limit) di atas
   yang sudah ada (risk per trade, cooldown).
5. Logging & alert kalau bot error atau koneksi putus di tengah posisi terbuka.

### F. Sebelum benar-benar live

1. **Test di testnet dulu**: Binance punya
   [testnet.binance.vision](https://testnet.binance.vision) -- order beneran
   diproses exchange tapi pakai saldo palsu. Jalankan bot di sini dulu.
2. Jalankan versi paper trading di repo ini selama beberapa hari/minggu,
   bandingkan hasilnya dengan ekspektasi kamu.
3. Kalau lanjut ke mainnet, mulai dari modal terkecil yang exchange izinkan,
   naikkan bertahap kalau performanya konsisten.

## Catatan strategi

**Deteksi kondisi pasar**
- **Trending** (ADX > 20): sinyal BELI saat SMA9 di atas SMA21, JUAL saat sebaliknya.
- **Sideways** (ADX <= 20): mean-reversion pakai Bollinger Band -- BELI saat
  harga di batas bawah band **dan** RSI < 40 (oversold), JUAL saat harga di
  batas atas band **dan** RSI > 60 (overbought).

**Filter sebelum entry** (BELI diblokir kalau salah satu gagal, JUAL/keluar posisi tidak pernah diblokir):
- Volatilitas: ATR harus >= `MIN_ATR_PCT` dari harga -- skip kalau pasar terlalu sepi.
- Tren jangka panjang: harga harus di atas SMA50 (macro filter).
- Timeframe lebih besar (mode live saja): harga 1h harus di atas EMA50 1h,
  dicek ulang tiap ~30 tick supaya tidak membanjiri API exchange.
- Target realistis: target profit (dari ATR) harus >= 3x biaya round-trip
  (fee + slippage kedua sisi) -- kalau tidak, potensi untungnya habis kena biaya.

**Exit & risk management**
- Stop-loss berbasis ATR (`entry - 1.2x ATR`), take-profit `2x` jarak stop.
- Begitu profit mencapai 1x risiko, stop otomatis digeser ke breakeven (trailing).
- Ukuran posisi dihitung dari risiko: `(equity x 1%) / jarak_stop` -- sinyal
  "lemah" (ADX di bawah 30 atau volume tidak terkonfirmasi) dapat setengah ukuran.
- Fee 0.1% dan slippage ~0.03% disimulasikan di setiap beli/jual.
- Setelah kena stop-loss, ada cooldown 5 candle sebelum boleh entry baru lagi.

Parameter-parameter ini (`ADX_TREND_THRESHOLD`, `ATR_STOP_MULT`,
`RISK_PER_TRADE_PCT`, `COOLDOWN_CANDLES`, dll) ada di bagian atas `bot.py`
kalau mau dieksperimenkan.

## 4. Mengukur performa & akurasi bot

### Laporan dari GUI

Tombol **⬇ EXPORT LAPORAN** (di panel Market Watch) menyimpan laporan HTML
berisi grafik equity, win rate, **expectancy per trade**, **profit factor**,
**max drawdown**, rata-rata untung/rugi, dan riwayat transaksi lengkap
beserta alasan tiap entry -- semuanya dari sesi yang sedang berjalan.

### Backtest di data historis asli

```bash
python backtest.py --days 60                          # BTC/USDT, timeframe default
python backtest.py --days 90 --symbol ETH/USDT --timeframe 1h
python backtest.py --test                              # self-check tanpa jaringan
```

Data historis diambil dari exchange, dibagi dua: paruh pertama (in-sample)
dan paruh kedua (out-of-sample), lalu strategi yang SAMA dijalankan di
kedua paruh dan hasilnya dibandingkan. Ini **bukan** walk-forward
optimization otomatis (tidak ada pencarian parameter) -- kalau kamu mau
optimasi parameter otomatis, itu fitur terpisah yang lebih besar, minta
dibuatkan kalau sudah sampai tahap itu.

Kalau hasil paruh pertama jauh lebih bagus dari paruh kedua, itu tanda
strategi/parameter kemungkinan overfit ke satu kondisi pasar saja -- jangan
langsung percaya angka backtest dari satu periode pendek.
