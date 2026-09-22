# Adaptive Paper Trader

Bot trading crypto adaptif: mendeteksi kondisi pasar (tren kuat vs sideways)
lalu otomatis ganti strategi -- SMA crossover saat tren, RSI mean-reversion
saat sideways. **Semua mode di repo ini adalah paper trading / simulasi.
Tidak ada order asli yang dikirim ke exchange, tidak ada uang asli yang
dipakai**, walaupun harga yang ditampilkan bisa harga pasar sungguhan.

## Isi project

| File | Fungsi |
|---|---|
| `bot.py` | Logic trading (indikator, deteksi regime, akun simulasi) + versi CLI |
| `gui.py` | Tampilan GUI ala terminal trading (chart candlestick, angka modal/untung-rugi) |
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
2. Ganti `account.execute()` (simulasi lokal) dengan
   `exchange.create_market_buy_order(...)` / `create_market_sell_order(...)`
   (order asli).
3. Handle minimum order size, fee, dan slippage sesuai aturan exchange.
4. Risk control minimum: batas kerugian harian (daily loss limit), ukuran
   posisi maksimum per trade, dan stop-loss otomatis.
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

- **Trending** (ADX > 25): sinyal BELI saat SMA cepat (9) memotong ke atas
  SMA lambat (21), sinyal JUAL saat sebaliknya.
- **Sideways** (ADX <= 25): sinyal BELI saat RSI < 30 (oversold), JUAL saat
  RSI > 70 (overbought).
- Parameter (`SMA_FAST`, `SMA_SLOW`, `RSI_PERIOD`, `ADX_TREND_THRESHOLD`, dll)
  ada di bagian atas `bot.py` kalau mau dieksperimenkan.
