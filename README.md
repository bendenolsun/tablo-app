# 📦 Tablo Sipariş Yönetim Sistemi

Kişiselleştirilmiş tablo siparişlerini otomatikleştiren web uygulaması.

---

## 🚀 Kurulum (Adım Adım)

### 1. Python Kurulumu
Bilgisayarınızda Python 3.10+ kurulu olmalı.
- İndirin: https://www.python.org/downloads/
- Kurulum sırasında "Add Python to PATH" seçeneğini işaretleyin.

### 2. Dosyaları Hazırlayın
İndirdiğiniz `tablo-app` klasörünü masaüstüne koyun.

### 3. Terminali Açın
- **Windows:** Klasöre girin → Adres çubuğuna `cmd` yazıp Enter
- **Mac:** Terminal'de `cd ~/Desktop/tablo-app` yazın

### 4. Bağımlılıkları Yükleyin
```bash
pip install -r requirements.txt
```

### 5. Fontları İndirin
```bash
python download_fonts.py
```

### 6. Uygulamayı Başlatın
```bash
python app.py
```

Tarayıcınızda şu adresi açın: **http://localhost:5000/admin**

---

## 🔐 Giriş Bilgileri

- **URL:** http://localhost:5000/admin/login
- **Şifre:** `tablo2024`

> Şifreyi değiştirmek için `app.py` dosyasında `ADMIN_PASSWORD = 'tablo2024'` satırını bulup değiştirin.

---

## 📋 Kullanım Kılavuzu

### Şablon Ekleme
1. Panelde **"+ Yeni Şablon"** butonuna tıklayın.
2. Şablon adını girin (örn: `9 Fotoğraflı Kolaj`)
3. Ebatı girin (örn: `21x30 CM`)
4. **Canva'dan hazırladığınız boş şablon görselini** PNG olarak yükleyin.
   - Canva'da şablonu açın
   - Fotoğraf ve metin alanlarını **boş bırakın** (sadece çerçeveler, arka plan kalsın)
   - "İndir → PNG" olarak dışa aktarın
5. **Devam Et** butonuna tıklayın.
6. Açılan editörde:
   - **📷 Fotoğraf** butonuna tıklayın → şablon üzerinde fotoğraf kutusunu sürükleyerek çizin
   - Her fotoğraf alanı için tekrarlayın (Fotoğraf 1, Fotoğraf 2, ...)
   - **✏️ Metin** butonuna tıklayın → metin alanlarını çizin, font ve renk seçin
7. **Kaydet** butonuna basın.

### Form Linkini Müşteriye Gönderme
1. Panele gidin.
2. İlgili şablonun yanındaki **"Kopyala"** butonuna tıklayın.
3. Kopyalanan linki WhatsApp'tan müşteriye gönderin.

### Siparişleri Takip Etme
- **Siparişler** menüsünden tüm siparişleri görüntüleyin.
- Her siparişin yanındaki **⬇ JPG** butonu ile tek tek indirin.
- Panel'deki **⬇ ZIP İndir** butonu ile o günkü tüm hazır siparişleri tek seferde indirin.

---

## 🌐 İnternette Yayınlama (Railway)

Uygulamayı sadece kendi bilgisayarınızda değil, internette de çalıştırabilirsiniz.
Böylece müşteriler her zaman forma erişebilir.

### Adımlar:
1. https://railway.app adresine gidin ve ücretsiz hesap açın.
2. https://github.com adresine gidin ve ücretsiz hesap açın.
3. Bu klasörü GitHub'a yükleyin (GitHub Desktop uygulamasıyla kolayca yapılabilir).
4. Railway'de "New Project → Deploy from GitHub Repo" seçin.
5. Repo'nuzu seçin, Railway otomatik olarak başlatır.
6. Railway size bir URL verir (örn: `https://tablo-app.up.railway.app`).
7. Artık `/admin` ve `/form/...` linkleri internet üzerinden çalışır.

**Maliyet:** Aylık ~3-5 dolar.

---

## 📁 Dosya Yapısı

```
tablo-app/
├── app.py              ← Ana uygulama
├── requirements.txt    ← Python paketleri
├── download_fonts.py   ← Font indirici
├── Procfile            ← Railway için
├── data/
│   ├── templates.json  ← Şablon verileri
│   └── orders.json     ← Sipariş verileri
├── static/
│   ├── fonts/          ← İndirilen fontlar
│   ├── uploads/        ← Yüklenen görseller
│   └── outputs/        ← Üretilen JPG'ler
└── templates/          ← HTML sayfaları
```

---

## ❓ Sık Sorulan Sorular

**Şifremi unuttum?**
`app.py` dosyasını not defteri ile açın, `ADMIN_PASSWORD` satırını bulun ve değiştirin.

**Şablon görseli çok büyük/küçük görünüyor?**
Bu görünüm sadece editör içindir, asıl boyut korunur. Endişelenmeyin.

**Tasarım üretilirken hata aldım?**
"Siparişler" sayfasında ilgili siparişte "Hata" yazıyorsa, fotoğrafın bozuk olma ihtimali var. Müşteriden tekrar istenebilir.
