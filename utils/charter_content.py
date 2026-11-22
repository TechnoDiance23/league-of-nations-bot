"""Complete readable MC-KS-001 (Sürüm 2.0) League of Nations Charter split into Discord embed pages."""
import discord

CHARTER_PAGES: list[tuple[str, str, int]] = [
    (
        "📜 MİLLETLER CEMİYETİ KURULUŞ ŞARTI · ÖN SÖZ (MC-KS-001 v2.0)",
        (
            "**TEŞKİLAT TÜZÜĞÜ VE DAİMÎ İÇTÜZÜĞÜ**\n"
            "*ULUSLARARASI KURULUŞ ANTLAŞMASI · Kurucu Devletlerin Ortak İradesiyle*\n"
            "🇹🇷 **Türkçe** · 🇬🇧 **English** · 🇪🇸 **Español** · 🇮🇹 **Italiano**\n"
            "`Belge No: MC-KS-001` · `Sürüm: 2.0` · `Kurucu Konferans`\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "**ÖN SÖZ / PREAMBLE**\n\n"
            "İşbu Şart'a taraf olan devletler;\n"
            "• Uluslararası barışın, güvenliğin ve istikrarın korunmasının devletlerin ortak sorumluluğu olduğunu kabul ederek,\n"
            "• Devletlerin egemen eşitliğini, toprak bütünlüğünü ve siyasi bağımsızlığını tanıyarak,\n"
            "• Uluslararası ilişkilerde hukukun üstünlüğünü, karşılıklı saygıyı ve diplomatik sorumluluğu esas alarak,\n"
            "• Uluslararası uyuşmazlıkların savaş, tehdit ve zor kullanma yerine müzakere, diplomasi ve yargı yoluyla çözüme kavuşturulmasını temin etmek amacıyla,\n"
            "• Uluslararası anlaşmaların, taahhütlerin ve devletler arası yükümlülüklerin korunmasını sağlayacak etkili bir yargı sisteminin oluşturulmasını gerekli görerek,\n"
            "• Ekonomik, siyasi, askerî, sosyal ve kültürel alanlarda devletler arası iş birliğini geliştirmek üzere ortak bir uluslararası teşkilat oluşturmayı kararlaştırarak,\n"
            "• Devletlerin uluslararası düzene karşı sorumluluklarının yalnızca taraf oldukları anlaşmalarla sınırlı kalmaması gerektiğini, genel kabul görmüş uluslararası ilkelerin de dikkate alınmasını kabul ederek,\n\n"
            "**MİLLETLER CEMİYETİ'Nİ KURMAYA VE AŞAĞIDAKİ HÜKÜMLERİ KABUL ETMEYE KARAR VERMİŞLERDİR.**"
        ),
        0x1E3A8A,
    ),
    (
        "📘 BİRİNCİ BÖLÜM · Genel Hükümler ve Temel İlkeler (Madde 1–5)",
        (
            "**Madde 1 - Teşkilatın Kuruluşu**\n"
            "1. Milletler Cemiyeti, işbu Kuruluş Şartı’nın imzalanması ve yürürlüğe girmesiyle kurulan bağımsız bir uluslararası teşkilattır.\n"
            "2. Teşkilatın resmî adı Milletler Cemiyeti, kısa adı MC’dir.\n"
            "3. Milletler Cemiyeti’nin merkezi, Kurucu Genel Kurul tarafından belirlenir.\n"
            "4. Teşkilatın bütün faaliyetleri işbu Şart ve Genel Kurul tarafından kabul edilen düzenlemeler çerçevesinde yürütülür.\n"
            "5. Milletler Cemiyeti hiçbir devletin, ittifakın veya siyasi grubun hiyerarşik denetimi altında bulunamaz.\n"
            "6. Teşkilat, üye devletlerin ortak iradesini temsil eder ve uluslararası ilişkilerde bağımsız bir kurumsal kimliğe sahiptir.\n\n"
            "**Madde 2 - Teşkilatın Amaçları**\n"
            "1. Uluslararası barışın ve güvenliğin korunması.\n"
            "2. Devletler arasındaki savaşların, saldırganlıkların ve diplomatik krizlerin önlenmesi.\n"
            "3. Uluslararası uyuşmazlıkların barışçıl ve hukuki yollarla çözüme kavuşturulması.\n"
            "4. Devletlerin egemenliğinin, toprak bütünlüğünün ve siyasi bağımsızlığının korunması.\n"
            "5. Uluslararası anlaşmalara, taahhütlere ve yükümlülüklere uyumun sağlanması.\n"
            "6. Ekonomik, ticari, bilimsel, kültürel ve sosyal iş birliğinin geliştirilmesi.\n"
            "7. Uluslararası insani yardım ve kriz yönetimi faaliyetlerinin yürütülmesi.\n"
            "8. Devletler arasında diplomatik ilişkilerin kurumsallaştırılması.\n"
            "9. Uluslararası hukukun gelişmesine ve uygulanmasına katkı sağlanması.\n"
            "10. Uluslararası düzeni tehdit eden eylemlerin incelenmesi ve gerekli tedbirlerin belirlenmesi.\n"
            "11. Devletler arasında ortak güvenlik, karşılıklı güven ve iş birliği mekanizmalarının oluşturulması.\n\n"
            "**Madde 3 - Temel İlkeler**\n"
            "1. Bütün üye devletler egemenlik bakımından eşittir.\n"
            "2. Her üye devletin Genel Kurul’da bir oy hakkı vardır.\n"
            "3. Hiçbir devlet, başka bir devletin egemenlik haklarını zor kullanarak ortadan kaldıramaz.\n"
            "4. Üye devletler uluslararası uyuşmazlıklarını barışçıl yollarla çözmeyi kabul eder.\n"
            "5. Üye devletler, yetkili organlar tarafından alınan ve işbu Şart kapsamında bağlayıcı olan kararlara uymakla yükümlüdür.\n"
            "6. Hiçbir üye devlet, hakkında yürütülen hukuki incelemeyi veya soruşturmayı yalnızca siyasi gerekçelerle engelleyemez.\n"
            "7. Hiçbir devletin mevcut ittifak üyeliği Milletler Cemiyeti üyeliğine engel teşkil etmez.\n"
            "8. Üye devletler, teşkilatın amaç ve ilkelerine aykırı davranışlarda bulunmaktan kaçınır.\n\n"
            "**Madde 4 - Genel Yükümlülükler**\n"
            "Her üye devlet; Kuruluş Şartı hükümlerine uygun davranmayı; Genel Kurul’un resmî toplantılarına katılmayı; bağlayıcı kararları uygulamayı; uluslararası krizlerin çözümüne katkı sağlamayı; yetkili temsilcilerini ve diplomatik bilgilerini güncel tutmayı; diğer üye devletlerin diplomatik temsil haklarına saygı göstermeyi; uluslararası yargılama ve inceleme süreçlerine ilişkin yükümlülüklerini yerine getirmeyi ve Milletler Cemiyeti’nin kurumsal faaliyetlerini engellememeyi kabul eder.\n\n"
            "**Madde 5 - Resmî ve Evrensel Diller**\n"
            "1. Milletler Cemiyeti’nin resmî çalışma dilleri: **a) Türkçe, b) English, c) Español, d) Italiano**.\n"
            "2. Bu diller eşit statüye sahip resmî dillerdir. Kuruluş belgeleri, anlaşmalar, Genel Kurul/Divan kararları ve resmî duyurular dört dilde yayımlanır.\n"
            "3. Üye devletler resmî başvurularını ve toplantı konuşmalarını bu dillerden herhangi biriyle yapabilir; metinler eşit derecede geçerlidir *(nihai yorum yetkisi Genel Kurul'a aittir)*."
        ),
        0x2563EB,
    ),
    (
        "📗 İKİNCİ BÖLÜM · Üyelik ve Devletlerin Temsili (Madde 6–10)",
        (
            "**Madde 6 - Kurucu Üyelik**\n"
            "1. İşbu Şart’ı kuruluş aşamasında imzalayan ve onaylayan devletler kurucu üye kabul edilir.\n"
            "2. Kurucu devletler, Genel Kurul’un ilk oluşumunda eşit temsil ve oy hakkına sahiptir.\n"
            "3. Kurucu üyelik hiçbir devlete diğer devletler üzerinde üstünlük sağlamaz.\n"
            "4. Kurucu devletlerin isimleri Kurucu Devletler Sicili’ne kaydedilir.\n"
            "5. Kurucu üyeler, Milletler Cemiyeti’nin ilk kurumsal düzeninin oluşturulmasından sorumludur.\n\n"
            "**Madde 7 - Yeni Üyelerin Kabulü ve Katılım Usulü**\n"
            "1. Milletler Cemiyeti’ne üyelik, işbu Şart’ın hükümlerini kabul eden bütün devletlere açıktır.\n"
            "2. Kuruluş Anlaşması Genel Sekreterlik tarafından hazırlanır ve tarafların imzasına sunulur.\n"
            "3. Kuruluş Şartı’nı imzalayan her devlet, Genel Sekreterlik onayının verilmesiyle üye olarak katılabilir *(temsilcinin imzası ve yetkisi doğrulanır; ayrıca Genel Kurul oylaması zorunlu değildir)*.\n"
            "4. Onaylanan üyelik resmî bildirim tarihinde yürürlüğe girer; yeni üyeler kurucu üyelerle aynı hak ve yükümlülüklere sahip olup Üye Devletler Sicili'ne kaydedilir.\n\n"
            "**Madde 8 - Üyelik Hakları**\n"
            "Her üye devlet; Genel Kurul toplantılarına katılma, karar/önerge sunma, oy kullanma, kurumsal görevlere aday gösterme, uyuşmazlıkları yetkili organlara taşıma, Uluslararası Yargı Divanı’na başvurma, diplomatik arabuluculuk talep etme ve komisyonlarda görev alma hakkına sahiptir.\n\n"
            "**Madde 9 - Devletlerin Temsili**\n"
            "1. Her üye devlet bir asil temsilci ve ihtiyaç halinde bir veya daha fazla yedek temsilci belirler.\n"
            "2. Oy kullanma yetkisi kayıtlı asil temsilciye veya yokluğunda yetkilendirilmiş yedek temsilciye aittir; değişiklikler Genel Sekreterliğe bildirilir.\n"
            "3. Her devletin oy hakkı eşittir. Başka ittifaklara üyelik oy ağırlığını değiştirmez ve hiçbir ittifak ayrı devlet oyu kullanamaz.\n\n"
            "**Madde 10 - Üyeliğin Askıya Alınması ve Sona Ermesi**\n"
            "1. Kuruluş Şartı’nı ağır biçimde ihlal eden devletin üyelik hakları Genel Sekreterlik incelemesi, savunma hakkı ve Genel Kurul'un **2/3 çoğunluk** kararıyla askıya alınabilir veya ciddi/tekrarlanan ihlallerde üyelikten çıkarılabilir *(ilgili devlet kendi oylamasında oy kullanamaz)*.\n"
            "2. Her devlet Genel Sekreterliğe yazılı bildirimde bulunarak üyelikten çekilebilir; çekilme bildirimi **yedi gün** sonra yürürlüğe girer."
        ),
        0x10B981,
    ),
    (
        "🏛️ ÜÇÜNCÜ & DÖRDÜNCÜ BÖLÜM (1/2) · Organlar & Genel Kurul (Madde 11–14/B)",
        (
            "**Madde 11 - Ana Organların Oluşumu**\n"
            "1. **Genel Kurul:** En yüksek karar, yasama, politika belirleme ve denetim organıdır.\n"
            "2. **Genel Sekreterlik:** İdari, diplomatik ve kurumsal faaliyetleri yürüten yürütme organıdır.\n"
            "3. **Uluslararası Yargı Divanı:** Hukuki uyuşmazlıkları inceleyen bağımsız yargı organıdır.\n\n"
            "**Madde 12 - Genel Kurulun Üstün Yetkisi**\n"
            "Genel Kurul en yüksek karar organıdır; genel politikalar, yönetim düzeni, bütçe ve uluslararası faaliyetler Genel Kurulca belirlenir. Yetkileri başka bir organ tarafından devralınamaz veya sınırlandırılamaz.\n\n"
            "**Madde 13 - Genel Kurulun Oluşumu**\n"
            "Genel Kurul bütün üye devletlerden oluşur; her devletin yalnızca bir oy hakkı vardır. Genel Kurul Başkanı üye devletlerin oylarıyla **90 gün** süreyle seçilir ve yeniden seçilebilir; yokluğunda başkan vekili görev yapar.\n\n"
            "**Madde 14 - Genel Kurulun Yetkileri (A ve B)**\n"
            "**A. Siyasi ve diplomatik yetkiler:**\n"
            "a) Genel siyasi politikaları belirlemek · b) Uluslararası sorunları ve krizleri görüşmek · c) Resmî kararlar ve bildiriler yayımlamak · d) Diplomatik arabuluculuk mekanizmaları oluşturmak · e) Uluslararası konferans ve zirveler düzenlemek · f) Uluslararası anlaşmaların kabul süreçlerini yönetmek.\n"
            "**B. Barış ve güvenlik yetkileri:**\n"
            "g) Barışı tehdit eden durumları incelemek · h) Saldırganlık ve hukuk ihlalleri hakkında soruşturma başlatmak · ı) Ateşkes ve barış antlaşmalarını desteklemek · i) Uluslararası gözlem ve inceleme heyetleri oluşturmak · j) Diplomatik/kurumsal tedbirleri belirlemek · k) Krizlere ortak politikalar geliştirmek."
        ),
        0xD97706,
    ),
    (
        "🗳️ DÖRDÜNCÜ BÖLÜM (2/2) · Yetkiler, Kararlar, Toplantılar & Oylama (Madde 14/C–18)",
        (
            "**Madde 14 - Genel Kurulun Yetkileri (C ve D)**\n"
            "**C. Ekonomik ve sosyal yetkiler:** l) Ekonomik/ticari iş birliğini geliştirmek · m) Kalkınma, yatırım ve ticaret programları · n) Bilimsel, teknolojik ve kültürel projeler · o) Eğitim, sosyal gelişim ve insani yardım · ö) Ekonomik anlaşmazlıkları görüşmek · p) Özel komisyonlar kurmak.\n"
            "**D. Kurumsal ve idari yetkiler:** r) Genel Sekreteri seçmek ve denetlemek · s) Bütçeyi onaylamak · ş) Yargı Divanı yargıçlarını seçmek · t) Yeni düzenleme ve içtüzükler kabul etmek · u) Üyelik askıya alma/çıkarma · ü) Diğer organları denetlemek · v) Şart değişiklikleri · y) Uzmanlık komisyonları kurmak.\n\n"
            "**Madde 15 - Genel Kurul Kararları**\n"
            "• **Usul kararları:** Katılan ve oy kullanan devletlerin **salt çoğunluğu (1/2)**.\n"
            "• **Önemli kararlar (2/3 Çoğunluk):** a) Üyelikten çıkarma/askıya alma · b) Yaptırım ve ciddi tedbirler · c) Kuruluş Şartı değişiklikleri · d) Genel Sekreterin görevden alınması · e) Temel politikaları değiştiren kararlar · f) Özel yetki ve komisyonlar.\n"
            "• Oy kullanmayan devletler hesaba katılmaz; eşitlikte önerge reddedilir; kararlar karar siciline işlenir.\n\n"
            "**Madde 16 - Bağlayıcı Karar Yetkisi**\n"
            "Kurumsal yönetim, bütçe, üyelik yükümlülükleri, yaptırımlar ve idari işleyiş kararları bütün üye devletler için bağlayıcıdır. Tavsiye kararları açıkça belirtilmedikçe tavsiye niteliğindedir.\n\n"
            "**Madde 17 - Toplantılar ve Olağanüstü Oturumlar**\n"
            "Genel Kurul olağan olarak **30 günde bir** toplanır; üyelerin **1/3** talebi veya Genel Sekreterin kriz çağrısıyla olağanüstü toplanabilir. Yeter sayı üye devletlerin yarısından bir fazlasıdır. Oturumlar gizlilik kararı yoksa kamuya açıktır.\n\n"
            "**Madde 18 - Önerge ve Oylama Sistemi**\n"
            "Her üye devlet önerge sunabilir (önergeyi sunan devlet, konu, gerekçe, talep edilen karar, belgeler). Olağan oylama süresi **24 saat**, acil durum oylamaları **6 saat**tir."
        ),
        0xD97706,
    ),
    (
        "🛡️ BEŞİNCİ BÖLÜM · Genel Sekreterlik (Madde 19–22)",
        (
            "**Madde 19 - Genel Sekreterliğin Oluşumu**\n"
            "1. Genel Sekreterlik, Milletler Cemiyeti’nin başlıca idari ve yürütme organıdır ve Genel Sekreter tarafından yönetilir.\n"
            "2. Genel Sekreter, Genel Kurul tarafından **2/3 çoğunlukla**, **90 gün** süreyle seçilir ve yeniden seçilebilir.\n"
            "3. Görevini tarafsızlık ve bağımsızlık esaslarına göre yürütür.\n\n"
            "**Madde 20 - Genel Sekreterliğin Yetkileri**\n"
            "Günlük idareyi yürütmek · Toplantıları organize etmek · Kararların uygulanmasını takip etmek · Resmî başvuruları kaydetmek · Anlaşmaları ve karar sicilini arşivlemek · Resmî belgeleri yayımlamak · Krizleri gündeme taşımak · Diplomatik yazışmaları yönetmek · Yargılama başvurularını kaydetmek · Yıllık faaliyet raporunu hazırlamak · Madde 7 uyarınca üyelik başvurularını inceleyip onay vermek · İnceleme heyetlerinin idari işlerini yürütmek.\n\n"
            "**Madde 21 - Genel Sekreterliğin Tarafsızlığı**\n"
            "Genel Sekreter hiçbir devletin talimatıyla hareket edemez, ulusal çıkarını Cemiyetin önüne koyamaz, ayrımcılık yapamaz ve yetkilerini kişisel/ulusal çıkar için kullanamaz. Görevden alınması Genel Kurul’un **2/3 çoğunluk** kararıyla mümkündür.\n\n"
            "**Madde 22 - Genel Sekreterin Acil Durum Yetkileri**\n"
            "Ciddi barış tehditlerinde olağanüstü toplantı çağrısı yapabilir, acil diplomatik görüşmeler ve arabuluculuk teklif edebilir; ancak tek başına yaptırım, üyelikten çıkarma veya askerî müdahale kararı alamaz."
        ),
        0x1D4ED8,
    ),
    (
        "⚖️ ALTINCI BÖLÜM · Uluslararası Yargı Divanı (Madde 23–28)",
        (
            "**Madde 23 - Divanın Kuruluşu**\n"
            "Uluslararası Yargı Divanı, **5 bağımsız yargıçtan** oluşan bağımsız yargı organıdır. Yargıçlar Genel Kurul tarafından **2/3 çoğunlukla**, **90 gün** süreyle seçilir ve yeniden seçilebilir.\n\n"
            "**Madde 24 - Divanın Yetki Alanı**\n"
            "Kuruluş Şartı’nın yorumlanması · Üye devletler arası anlaşmazlıklar · Anlaşma ve taahhüt ihlalleri · Egemenlik ve toprak bütünlüğü uyuşmazlıkları · Saldırganlık ve barış ihlali iddiaları · Sorumluluk davaları · Genel Kurul sevkleri · Üye olmayan devletler hakkındaki uluslararası hukuki incelemeler.\n\n"
            "**Madde 25 - Anlaşmalara Taraf Olmayan Devletler Hakkında Yargılama**\n"
            "Divan, belirli bir anlaşmaya taraf olmayan veya yargı yetkisini kabul etmeyen devletler hakkında da genel uluslararası hukuk çerçevesinde açık inceleme ve değerlendirme oturumu düzenleyebilir. Taraf olunmayan anlaşmadan mahkûmiyet verilemez; rıza olmadıkça raporlar tavsiye/tespit niteliğindedir.\n\n"
            "**Madde 26 - Açık Uluslararası Yargılama Usulü**\n"
            "Bir üye devlet veya Genel Kurul başvurabilir *(ilgili devlet, iddia, olay/belgeler, ihlal edilen kural, talep)*. Divan ön inceleme sonrası resmî bildirim yapar; savunma/delil sunma hakkı tanır ve açık oturumda gerekçeli karar veya tespit raporu yayımlar.\n\n"
            "**Madde 27 & 28 - Yargılama Güvenceleri ve Uygulama**\n"
            "Tarafsızlık, eşitlik, somut delil zorunluluğu ve yargıçların çıkar çatışmasından çekilmesi esastır. Bağlayıcı kararlar Genel Sekreterlikçe takip edilir; karara uymayan üye devlet Genel Kurul’a bildirilir."
        ),
        0x6D28D9,
    ),
    (
        "🕊️ YEDİNCİ BÖLÜM · Uluslararası Barış ve Güvenlik (Madde 29–33)",
        (
            "**Madde 29 - Barışın Korunması**\n"
            "Barış ve güvenliğe ilişkin temel karar yetkisi Genel Kurul’a aittir; Genel Kurul inceleme komisyonları kurabilir, ateşkes ve arabuluculuk tavsiyelerinde bulunabilir.\n\n"
            "**Madde 30 - Uluslararası Saldırganlık ve İhlaller**\n"
            "Başka devletin toprak bütünlüğüne saldırı, egemenlik haklarının zorla ihlali, ateşkes/barış antlaşmalarının ağır ihlali, diplomatik ilişkilerin zorla değiştirilmesi ve uluslararası düzeni ciddi tehdit eden eylemler ciddi ihlal sayılır.\n\n"
            "**Madde 31 - Diplomatik Arabuluculuk**\n"
            "Uyuşmazlıkların öncelikle diplomatik yollarla çözülmesi esastır. Taraflar Genel Sekreterlikten arabuluculuk talep edebilir; sonuç alınamazsa uyuşmazlık Genel Kurul’a sunulur.\n\n"
            "**Madde 32 - Uluslararası Tedbirler ve Yaptırımlar**\n"
            "Genel Kurul ağır ihlallerde savunma hakkı tanıdıktan sonra şu tedbirleri alabilir:\n"
            "a) Resmî uyarı · b) Diplomatik kınama · c) Uluslararası soruşturma · d) Belirli temsil/görev haklarının kısıtlanması · e) Diplomatik ve ekonomik iş birliğinin sınırlandırılması · f) Üyelik haklarının geçici askıya alınması · g) Üyelikten çıkarılma süreci.\n"
            "Teşkilat kararları üye olmayan devletlerin egemenliğini tek başına ortadan kaldırmaz.\n\n"
            "**Madde 33 - Barış Gözlem ve İnceleme Heyetleri**\n"
            "Genel Kurul ateşkesleri ve mutabakatları izlemek üzere gözlem heyetleri kurabilir; bu heyetler devletlerin silahlı kuvvetleri üzerinde emir verme yetkisi oluşturmaz."
        ),
        0xDC2626,
    ),
    (
        "📂 SEKİZİNCİ & DOKUZUNCU BÖLÜM + EK I · Arşiv, Değişiklik & Usuller (Md. 34–39)",
        (
            "**Madde 34 - Resmî Arşiv**\n"
            "Bütün kuruluş belgeleri, katılım kayıtları, anlaşmalar, Genel Kurul ve Divan kararları kamuya açık resmî arşivde tutulur. İmzacı devletlerin temsilci isimleri, görevleri, imza tarihleri ve onay durumları kaydedilir.\n\n"
            "**Madde 35 - Şeffaflık ve Hesap Verebilirlik**\n"
            "Toplantılar, oylama sonuçları, karar gerekçeleri ve Divan kararları kamuya açıktır. Resmî belgelerin tahrifi veya kasıtlı değiştirilmesi ağır kurumsal ihlal sayılır.\n\n"
            "**Madde 36–39 - Şart Değişikliği, Yürürlük ve Dijital İmza**\n"
            "• **Değişiklik (Md. 36):** Kullanılan oyların ve üye devletlerin en az **2/3 onayı** ile yapılır.\n"
            "• **Üstünlük & Yürürlük (Md. 37–38):** İşbu Şart temel kurucu belgedir; en az **3 kurucu devletin** imzası ve onayıyla yürürlüğe girer.\n"
            "• **Dijital İmza (Md. 39):** Elektronik/dijital imza yöntemiyle imzalanabilir; her imzacı devlet için temsilcinin adı, unvanı, devleti ve imza tarihi resmî arşivde yayımlanır.\n\n"
            "**EK I - DAİMÎ TOPLANTI VE KARAR USULLERİ**\n"
            "• **Oturumlar:** 30 günde bir olağan; krizlerde olağanüstü · Her devlet 1 eşit oy.\n"
            "• **Oylama Yeter Sayıları:** Usul kararları **Salt Çoğunluk (1/2)** · Önemli kararlar, Genel Sekreter/Yargıç seçimi, askıya alma ve Şart değişikliği **Üçte İki Çoğunluk (2/3)**.\n"
            "• **Yargılama & Disiplin:** Yazılı dilekçe -> Ön inceleme -> Resmî bildirim -> Savunma -> Açık oturum -> Gerekçeli karar -> Arşiv. Hiçbir temsilci siyasi görüşü sebebiyle dışlanamaz."
        ),
        0x0F766E,
    ),
]


def build_charter_page_embeds() -> list[discord.Embed]:
    """Build ordered readable Charter chapter embeds from MC-KS-001 v2.0."""
    embeds: list[discord.Embed] = []
    total = len(CHARTER_PAGES)
    for idx, (title, body, color) in enumerate(CHARTER_PAGES, start=1):
        emb = discord.Embed(title=title, description=body, colour=color)
        emb.set_footer(text=f"MC-KS-001 · Sürüm 2.0 · Sayfa / Page {idx}/{total}")
        embeds.append(emb)
    return embeds
