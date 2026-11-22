# 🏛️ International Diplomatic Bot · Milletler Cemiyeti (League of Nations) Hard-RP

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Discord.py](https://img.shields.io/badge/discord.py-2.3%2B-blueviolet.svg)](https://discordpy.readthedocs.io/)
[![Charter](https://img.shields.io/badge/Charter-MC--KS--001%20v2.0-amber.svg)]()
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

> 🌐 **parlamentum.org ve Milletler Cemiyeti (League of Nations / `MC-KS-001` Sürüm 2.0) Hard-RP sunucuları için eksiksiz diplomasi, akreditasyon, oylama, resmi gazete, antlaşma, yargı ve ticket yönetim botu.**

---

## 🌟 Temel Özellikler / Key Features

### 📜 1. Dijital İmza & Akreditasyon Sistemi (`MC-KS-001` Madde 6, 7, 9, 39 & EK II)
* **Şart İmza & Katılım Masası (`/akreditasyon-masasi-kur`):** Ülkeler Kurucu Üye veya Katılan Üye başvurusu yapar; başvurular gizli `#🛡️│divan-onay` kanalında **Genel Sekreterlik & Divan** onayına düşer.
* **Resmi Makam & Temsilci Rolleri:** **Asil Temsilci (`Asil`)**, **Yedek Temsilci (`Yedek`)**, `GKB` (Genel Kurul Başkanı), `GKB-V`, `SG` (Genel Sekreter), `DSG`, `UYD` (Uluslararası Yargı Divanı Yargıcı), `Spokesperson`, Büyükelçi ve Konsolosluk rolleri otomatik atanır, takma adlar standardize edilir.
* **Ülke Sicili & Karnesi:** `/sicil-listesi` (EK II Kurucu & Üye Devlet Sicili), `/ulke-karnesi`, `/ulke-statu` ve `/delege-bilgi`.

### 🗳️ 2. Genel Kurul Eşit Oylama Motoru (Madde 13 & 15)
* **1 Üye Devlet = 1 Eşit Oy:** P5 veya tekil veto ayrıcalığı yoktur. Aynı devletten birden fazla temsilci varsa **Asil Temsilci** oyu önceliklidir; yoksa **Yedek Temsilci** oy kullanabilir (Madde 9.3).
* **Çoğunluk Kuralları:** Usul kararlarında **Basit Çoğunluk**, önemli kararlarda **2/3 Nitelikli Çoğunluk** uygulanır; çekimser oylar hesaba katılmaz ve eşitlik durumunda tasarı reddedilir.
* **Esnek Süreler & Kütük:** Acil (`6 saat`), Olağan (`24 saat`) veya Başkan kapanışı; kabul edilen kararlar `MC-KARAR` numarasıyla arşivlenir (`/kararlar-listesi`, `/karar-goruntule`).

### 📰 3. Çok Dilli Resmi Gazete (`TR · EN · ES · IT · RU` + `FR` Şiar)
* `MİLLETLER CEMİYETİ RESMİ GAZETESİ · MC-RG-YYYY/XXXX` başlığıyla kararlar, antlaşmalar, Divan hükümleri, büyükelçilik duyuruları ve özel kriz bültenleri yayınlanır (`/resmi-gazete-yayinla`, `/resmi-gazete-kanali`).

### 🏛️ 4. Diplomatik Temsilcilikler, Büyükelçilikler, Konsolosluklar & Notalar
* `/temsilcilik-ac`: **Cemiyet Daimi Temsilciliği**, **İkili Büyükelçilik** (kapatma butonlu ve Resmi Gazete ilanlı) veya isteğe bağlı serbest RP **Konsolosluk** açar.
* `/nota-gonder`: Açık *(Resmi Gazete'de yayınlanan)* veya Kriptolu *(özel/gizli)* diplomatik nota gönderir.

### ⚖️ 5. Uluslararası Antlaşmalar & Uluslararası Yargı Divanı (Madde 23-28)
* `/antlasma-sun`: Devletlerin **İmza**, **Onay (Ratifikasyon)** ve **Çekilme** butonlarıyla katılabildiği kalıcı antlaşma masası.
* `/dava-ac`: Başka bir devlete karşı Uluslararası Yargı Divanı'nda dava açar; akredite `UYD` yargıçları ve yöneticiler hüküm verir.

### 📢 6. Duyurular, Kürsü & 🎫 Ticket (Başvuru) Sistemi
* **Duyuru Yönetimi:** `/duyuru-kanali`, `/duyuru-yap`, `/duyuru-duzenle`, `/duyuru-paneli-kur`.
* **Meclis Kürsüsü:** `/konusmaci-paneli-kur` ve `/mola-onergesi`.
* **Ticket Masası (`/ticket-paneli-kur`):** 4 kategorili açılır menü (*Genel Sekreterlik*, *Diplomatik Kriz & Arabuluculuk*, *Yargı Divanı Ön Başvurusu*, *Yönetim/OOC Destek*), özel kanal izinleri, `Üstlen / Üye Ekle / Kapat` butonları ve kapatılışta `#📋│ticket-log` kanalına otomatik **UTF-8 `.txt` transkript** yükleme.

---

## 🚀 Kurulum ve Çalıştırma / Setup & Installation

### 1. Depoyu Klonlayın / Clone Repository
```bash
git clone https://github.com/TechnoDiance23/International-Diplomatic-Bot.git
cd International-Diplomatic-Bot
```

### 2. Sanal Ortam Oluşturun ve Paketleri Kurun
```bash
python -m venv venv
# Windows:
venv\Scripts\activate
# Linux / macOS:
# source venv/bin/activate

pip install -r requirements.txt
```

### 3. Discord Developer Portal Ayarları
1. [Discord Developer Portal](https://discord.com/developers/applications) üzerinden uygulamanızı açın.
2. **Bot -> Privileged Gateway Intents** bölümünden şu iki ayarı **AÇIK** yapın:
   * ✅ **Server Members Intent**
   * ✅ **Message Content Intent**
3. **OAuth2 -> URL Generator** altında `bot` ve `applications.commands` scope'larını seçip bota `Administrator` (veya Kanal/Rol/Mesaj Yönetimi + Üye Takma Adı Yönetimi) yetkisi vererek sunucunuza davet edin.

### 4. Ortam Değişkenlerini Ayarlayın (`.env`)
`.env.example` dosyasını kopyalayarak `.env` oluşturun ve bot token'ınızı girin:
```bash
cp .env.example .env
```
```env
DISCORD_TOKEN=your_discord_bot_token_here
COMMAND_PREFIX=!
DEFAULT_LANG=bilingual
```

### 5. Botu Başlatın / Run the Bot
```bash
python main.py
```

### 6. Discord Sunucusu İlk Kurulumu (Gerçek Tek Komut)
Bot sunucuya girdikten sonra yetkili bir yönetici **yalnızca tek komut** çalıştırmalıdır:
* **`/otomatik-kurulum`**:
  * **4 düzenli kategori** (`📢 Genel & Resmî`, `🏛️ Genel Kurul & Diplomasi`, `🎙️ Diplomatik Ses Odaları`, `🛡️ Yönetim & Genel Sekreterlik`) oluşturur,
  * Tüm **13 `MC-KS-001` rolünü**, **14 metin kanalını**, **6 ses kanalını** (Genel Kurul Kürsüsü, Yargı Divanı Duruşma Salonu, İkili Kulis 1-2, Gizli Sekreterlik Odası, Delege Dinlenme) ve tüm izinleri (`PermissionOverwrite`) otomatik kurar,
  * **Tüzük özetini (`MC-KS-001`) + 7 interaktif paneli** (Akreditasyon, Ticket, Konuşmacı Kürsüsü, Genel Kurul Oylama, Antlaşmalar, Uluslararası Yargı Divanı, Sekreterlik Duyuru Paneli) kanallara otomatik yerleştirir,
  * Önceki boş varsayılan/eski şablon kanallarını kontrol edip temizler (mesaj içeren kanalları ve aktif elçilik/ticket odalarını korur).

---

## 📌 Tüm Slash Komutları / Slash Commands Reference

| Kategori | Komut | Yetki | Açıklama |
| :--- | :--- | :---: | :--- |
| **Kurulum & Yardım** | `/yardim` | Herkes | Tüm komutları ve kullanım kılavuzunu gösterir. |
| | `/otomatik-kurulum` | Yönetici | Tüm kanalları, rolleri ve izinleri otomatik kurar. |
| **Akreditasyon & Sicil** | `/akreditasyon-masasi-kur` | Yönetici | `MC-KS-001` Dijital İmza ve Akreditasyon masasını kurar. |
| | `/akreditasyon-ver` | Yetkili | Bir üyeye doğrudan Devlet + Makam (`Asil`/`Yedek`/`SG`/`UYD` vb.) verir. |
| | `/sicil-listesi` | Herkes | EK II — Şart'ı imzalayan Kurucu ve Üye Devletler sicilini listeler. |
| | `/ulke-statu` | Yetkili | Ülke statüsünü (`Kurucu Üye`, `Üye Devlet`, `Gözlemci`, `Askıda`) günceller. |
| | `/ulke-karnesi` | Herkes | Bir ülkenin sicil, oy, antlaşma, nota ve divan karnesini gösterir. |
| | `/delege-bilgi` | Herkes | Bir temsilcinin devlet, makam ve statü dosyasını görüntüler. |
| **Oylama & Kararlar** | `/oylama` | Delege | Genel Kurul (1 Devlet = 1 Oy) veya Bireysel oylama başlatır. |
| | `/oylama-paneli-kur` | Yönetici | Kanala kalıcı oylama başlatma paneli kurar. |
| | `/kararlar-listesi` | Herkes | Kabul edilen `MC-KARAR` kararlarını listeler. |
| | `/karar-goruntule` | Herkes | Belirli bir kararın tam metnini görüntüler. |
| **Kürsü** | `/konusmaci-paneli-kur` | Yönetici | Konuşmacı kürsüsü ve söz sırası panelini kurar. |
| | `/mola-onergesi` | Delege | Mecliste lobi/mola oturumu önergesi açar. |
| **Misyonlar & Notalar** | `/temsilcilik-ac` | Delege | Daimi Temsilcilik, İkili Büyükelçilik veya Konsolosluk açar. |
| | `/nota-gonder` | Delege | Açık *(Resmi Gazete)* veya Kriptolu *(Gizli)* nota gönderir. |
| | `/temsilcilikler` | Herkes | Açık temsilcilik, büyükelçilik ve konsoloslukları listeler. |
| **Antlaşma, Divan & Gazete** | `/antlasma-sun` & `/antlasma-paneli-kur` | Delege / Yön. | Bretton Woods usulü butonlu Antlaşma İmza Masası ve İmza/Onay/Çekilme kartları. |
| | `/dava-ac` & `/divan-paneli-kur` | Delege / Yön. | Uluslararası Yargı Divanı paneli ve dava açma sistemi. |
| | `/resmi-gazete-yayinla` | Sözcü / Yetkili | Resmi Gazete'de özel bülten veya kriz bildirisi yayınlar. |
| | `/resmi-gazete-kanali` | Yönetici | Resmi Gazete yayın kanalını belirler. |
| **Duyurular** | `/duyuru-kanali` | Yönetici | Resmi duyuru kanalını belirler. |
| | `/duyuru-yap` | Sözcü / Yetkili | Seçilen kanala ve etiket moduyla duyuru yayınlar. |
| | `/duyuru-duzenle` | Sözcü / Yetkili | Botun yayınladığı bir duyuruyu mesaj ID'siyle düzenler. |
| | `/duyuru-paneli-kur` | Yönetici | Tek tıkla duyuru yayınlama paneli kurar. |
| **Ticket Sistemi** | `/ticket-paneli-kur` | Yetkili | 4 kategorili kalıcı Ticket Masası kurar. |
| | `/ticket-log-kanali` | Yetkili | Kapatılan ticket transkript log kanalını belirler. |
| | `/ticket-ekle` | Yetkili | Aktif ticket kanalına üye/temsilci ekler. |
| | `/ticket-cikar` | Yetkili | Aktif ticket kanalından üyeyi çıkarır. |
| | `/ticket-kapat` | Sahip / Yetkili | Ticket'ı kapatır, `.txt` transkriptini loglar ve kanalı siler. |

---

## 🏛️ Proje Mimarisi / Project Architecture

```text
international-diplomatic-bot/
├── main.py                     # Bot giriş noktası, otomatik kurulum & /yardim
├── requirements.txt            # Python bağımlılıkları
├── .env.example                # Ortam değişkeni şablonu
├── .gitignore                  # Git hariç tutma kuralları (data/*.json, .env)
├── README.md                   # Kurulum ve kullanım dokümantasyonu
├── data/                       # Yerel JSON veri deposu (.gitkeep)
├── cogs/
│   ├── delegation.py           # MC-KS-001 Dijital İmza, Akreditasyon & Ülke Karnesi
│   ├── voting.py               # Genel Kurul eşit oylama motoru (Madde 9, 13, 15)
│   ├── assembly.py             # Konuşmacı kürsüsü & caucus yönetimi
│   ├── resolutions.py          # Kabul edilen kararlar kütüğü (MC-KARAR)
│   ├── embassies.py            # Temsilcilikler, Büyükelçilikler, Konsolosluklar & Notalar
│   ├── treaties_icj.py         # Antlaşmalar, Uluslararası Yargı Divanı & Resmi Gazete
│   ├── announcements.py        # Duyuru yönetimi ve paneli
│   └── tickets.py              # 4 kategorili Ticket & transkript log sistemi
└── utils/
    ├── embed_factory.py        # Diplomatik embedler ve oylama çubukları
    ├── server_setup.py         # Tek-komut metin+ses odası, rol, panel kurulum & temizlik motoru
    ├── un_gazette.py           # 5 dilli Resmi Gazete & ülke sicil yardımcısı
    └── storage.py              # Thread-safe JSON kalıcı depolama yöneticisi
```

---

## 📄 Lisans / License
**MIT License** — Uluslararası diplomasi, `parlamentum.org` ve Milletler Cemiyeti Hard-RP toplulukları için geliştirilmiştir.
