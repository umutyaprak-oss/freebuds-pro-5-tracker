# Huawei FreeBuds Pro 5 - 7/24 Fiyat Takip Botu

Public (herkese açık) GitHub deposu üzerinde **GitHub Actions** ile kota harcamadan her 15 dakikada bir çalışan Python fiyat takip ve Gmail bildirim projesi.

## Özellikler
- **Takip Edilen Kaynaklar:** Akakçe ve Vatan Bilgisayar
- **Bot Koruması:** `curl_cffi` (Chrome TLS impersonation) + opsiyonel Playwright fallback
- **Akıllı İlan Filtresi:** `Ambalaj hasarlı`, `teşhir`, `ikinci el`, `tek kulaklık`, `şarj kutusu`, `kılıf` içeren ilanları ve stok dışı/ikinci el ürünleri otomatik eler
- **İki Kademeli Alarm:**
  - `< 7.500 TL` → `[İYİ İNDİRİM] FreeBuds Pro 5`
  - `< 7.000 TL` → `[ACİL - 7000 TL ALTI!] FreeBuds Pro 5`
- **Spam Koruması:** Son bildirilen en düşük fiyat `last_price.json` dosyasında tutulur; yalnızca fiyat ilk kez eşiğin altına indiğinde veya yeni bir dip yaptığında e-posta gönderilir.

## GitHub Kurulumu (Public Depo)
1. GitHub'da **Public** yeni bir depo oluşturun ve bu klasördeki dosyaları pushlayın (`.env` dosyası `.gitignore` içinde olduğu için asla yüklenmez).
2. Depoda **Settings → Secrets and variables → Actions → New repository secret** yolunu izleyin ve şu iki sırrı ekleyin:
   - `GMAIL_USER`: Gmail adresiniz
   - `GMAIL_APP_PASSWORD`: Google Hesabınızdan oluşturduğunuz 16 haneli **Uygulama Şifresi (App Password)**
   - *(İsteğe bağlı)* `GMAIL_TO`: Bildirimin gideceği farklı bir e-posta adresi (boş bırakılırsa `GMAIL_USER` adresine gönderilir)
3. **Actions** sekmesinden `FreeBuds Pro 5 Fiyat Takip` iş akışını manuel olarak (**Run workflow**) test edebilirsiniz. Mail bağlantısını doğrulamak için açılan pencerede **`send_test_mail`** kutucuğunu işaretleyin (`last_price.json` dosyasını bozmadan test e-postası gönderir).

## Yerel Çalıştırma
```bash
pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env  # GMAIL_USER ve GMAIL_APP_PASSWORD değerlerini doldurun
python check_price.py
# Tek tıkla test e-postası göndermek için:
python check_price.py --test-mail
```
