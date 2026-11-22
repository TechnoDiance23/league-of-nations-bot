"""Shared constants, bilingual labels, role definitions, and free-text duty classifier."""

import re

# (new_name, color, legacy_aliases)
ROLE_SPECS = [
    ("🏛️ Genel Sekreter · Secretary-General", 0xD97706, ["🏛️ MC Genel Sekreteri"]),
    ("🏛️ Cemiyet Yönetimi · Staff & Council", 0x1E3A8A, ["🏛️ Cemiyet Yönetimi & Divan"]),
    ("⚖️ Divan Yargıcı · ICJ Judge", 0x6D28D9, ["⚖️ Uluslararası Yargı Divanı Yargıcı"]),
    ("🌟 Kurucu Üye · Founding State", 0xF59E0B, ["🌟 Kurucu Üye Devlet (Madde 6)"]),
    ("🏛️ Üye Devlet · Member State", 0x10B981, ["🏛️ Cemiyet Üye Devleti"]),
    ("👑 Devlet Başkanı · Head of State", 0xEAB308, []),
    ("🌐 Dışişleri Bakanı · Foreign Minister", 0x0284C7, []),
    ("🏛️ İçişleri Bakanı · Interior Minister", 0x0F766E, []),
    ("💰 Ekonomi Bakanı · Economy Minister", 0x15803D, []),
    ("🛡️ Savunma Bakanı · Defense Minister", 0x991B1B, []),
    ("🏛️ Bakan / Kabine · Cabinet Minister", 0x1D4ED8, []),
    ("🏛️ Ulus Temsilcisi · MP / Parliament", 0x4F46E5, ["🏛️ Milletvekili · MP / Parliament", "Milletvekili", "MP"]),
    ("🏛️ Asil Temsilci · Primary Rep", 0x2563EB, ["🏛️ Asil Temsilci (Madde 9)"]),
    ("🏛️ Yedek Temsilci · Alternate Rep", 0x3B82F6, ["🏛️ Yedek Temsilci (Madde 9)"]),
    ("🏛️ Büyükelçi · Ambassador", 0x1E3A8A, ["🏛️ Büyükelçi / Diplomatik Misyon"]),
    ("🛂 Konsolosluk · Consulate", 0x0EA5E9, ["🛂 Başkonsolos / Konsolosluk"]),
    ("📰 Cemiyet Sözcüsü · Spokesperson", 0xF59E0B, ["📰 Cemiyet Sözcüsü"]),
    ("👁️ Gözlemci · Observer", 0x6B7280, ["👁️ Gözlemci"]),
    ("📰 Akredite Basın · Press", 0x9CA3AF, ["📰 Akredite Basın"]),
    ("🪪 Vatandaş · Citizen (No Office)", 0x64748B, []),
    ("⛔ Askıya Alınmış · Suspended", 0xEF4444, ["⛔ Askıya Alınmış Üye (Madde 10)"]),
    ("✅ Kayıtlı Üye · Verified", 0x10B981, []),
    ("🪑 Genel Kurul Başkanı · Assembly President", 0xD97706, []),
    ("🏛️ Şûra Başkanı · Council Chair", 0xB45309, []),
    ("⚖️ Divan Başkanı · ICJ President", 0x7C3AED, []),
    ("🎖️ Delege Başkanı · Head of Delegation", 0x0284C7, []),
    ("🛡️ Moderatör · Moderator", 0x0EA5E9, ["🛡️ Moderatör", "Moderatör", "Yetkili", "Moderator"]),
    ("⚖️ Davacı Taraf · Claimant", 0x0284C7, ["Davacı Taraf", "Davacı"]),
    ("🚨 Davalı Taraf · Respondent", 0xDC2626, ["Davalı Taraf", "Davalı"]),
    ("⚖️ Cemiyet Başsavcısı · Chief Prosecutor", 0x991B1B, ["⚖️ Başsavcı", "Başsavcı", "Chief Prosecutor"]),
    ("📜 Cemiyet Savcısı · Prosecutor", 0xDC2626, ["📜 Savcı", "Savcı", "Prosecutor"]),
]

ROLE_SG = ROLE_SPECS[0][0]
ROLE_ADMIN = ROLE_SPECS[1][0]
ROLE_JUDGE = ROLE_SPECS[2][0]
ROLE_FOUNDING = ROLE_SPECS[3][0]
ROLE_MEMBER = ROLE_SPECS[4][0]
ROLE_PRESIDENT = ROLE_SPECS[5][0]
ROLE_FM = ROLE_SPECS[6][0]
ROLE_IM = ROLE_SPECS[7][0]
ROLE_EM = ROLE_SPECS[8][0]
ROLE_DM = ROLE_SPECS[9][0]
ROLE_CABINET = ROLE_SPECS[10][0]
ROLE_MP = ROLE_SPECS[11][0]
ROLE_PRIMARY = ROLE_SPECS[12][0]
ROLE_ALTERNATE = ROLE_SPECS[13][0]
ROLE_AMBASSADOR = ROLE_SPECS[14][0]
ROLE_CONSULATE = ROLE_SPECS[15][0]
ROLE_SPOKESPERSON = ROLE_SPECS[16][0]
ROLE_OBSERVER = ROLE_SPECS[17][0]
ROLE_PRESS = ROLE_SPECS[18][0]
ROLE_CITIZEN = ROLE_SPECS[19][0]
ROLE_SUSPENDED = ROLE_SPECS[20][0]
ROLE_VERIFIED = ROLE_SPECS[21][0]
ROLE_GA_PRESIDENT = ROLE_SPECS[22][0]
ROLE_COUNCIL_CHAIR = ROLE_SPECS[23][0]
ROLE_ICJ_PRESIDENT = ROLE_SPECS[24][0]
ROLE_HEAD_OF_DELEGATION = ROLE_SPECS[25][0]
ROLE_MODERATOR = ROLE_SPECS[26][0]
ROLE_SUMMIT_CLAIMANT = ROLE_SPECS[27][0]
ROLE_SUMMIT_RESPONDENT = ROLE_SPECS[28][0]
ROLE_CHIEF_PROSECUTOR = ROLE_SPECS[29][0]
ROLE_PROSECUTOR = ROLE_SPECS[30][0]
SUMMIT_STANCE_ROLE_NAMES = {ROLE_SUMMIT_CLAIMANT, ROLE_SUMMIT_RESPONDENT}

# Top-to-bottom sidebar display priority & hoist configuration ("üyelerden ayrı göster")
ROLE_DISPLAY_ORDER = [
    ROLE_SG,
    ROLE_GA_PRESIDENT,
    ROLE_COUNCIL_CHAIR,
    ROLE_ADMIN,
    "Admin",
    ROLE_MODERATOR,
    ROLE_ICJ_PRESIDENT,
    ROLE_JUDGE,
    ROLE_CHIEF_PROSECUTOR,
    ROLE_PROSECUTOR,
    ROLE_SUSPENDED,
    ROLE_PRESIDENT,
    ROLE_FM,
    ROLE_IM,
    ROLE_EM,
    ROLE_DM,
    ROLE_CABINET,
    ROLE_HEAD_OF_DELEGATION,
    ROLE_MP,
    ROLE_PRIMARY,
    ROLE_ALTERNATE,
    ROLE_AMBASSADOR,
    ROLE_CONSULATE,
    ROLE_SPOKESPERSON,
    ROLE_PRESS,
    ROLE_OBSERVER,
    ROLE_SUMMIT_CLAIMANT,
    ROLE_SUMMIT_RESPONDENT,
    ROLE_FOUNDING,
    ROLE_MEMBER,
    ROLE_CITIZEN,
    ROLE_VERIFIED,
    "Botlar",
]
HOISTED_ROLE_NAMES = set(ROLE_DISPLAY_ORDER)
CHAIR_ROLE_NAMES = {ROLE_GA_PRESIDENT, ROLE_COUNCIL_CHAIR, ROLE_ICJ_PRESIDENT, ROLE_CHIEF_PROSECUTOR}

# Authority boundaries for State Signing vs Cabinet/Rep Assembly Voting
SIGNATORY_OFFICE_CODES = {"Pres.", "FM"}
SIGNATORY_ROLE_NAMES = {ROLE_PRESIDENT, ROLE_FM}

VOTING_AUTHORIZED_OFFICE_CODES = {"Pres.", "FM", "IM", "EM", "DM", "Kabine", "Del.B.", "Asil", "Yedek"}
VOTING_AUTHORIZED_ROLE_NAMES = {
    ROLE_PRESIDENT,
    ROLE_FM,
    ROLE_IM,
    ROLE_EM,
    ROLE_DM,
    ROLE_CABINET,
    ROLE_HEAD_OF_DELEGATION,
    ROLE_PRIMARY,
    ROLE_ALTERNATE,
}
PARLIAMENTARY_SPEAKER_CODES = {
    "SG",
    "DSG",
    "GKB",
    "GKB-V",
    "ŞB",
    "DB",
    "BSV",
    "SVC",
    "Pres.",
    "FM",
    "IM",
    "EM",
    "DM",
    "Kabine",
    "Del.B.",
    "MP",
    "Asil",
    "Yedek",
    "Amb.",
}
DIPLOMATIC_REPRESENTATIVE_CODES = VOTING_AUTHORIZED_OFFICE_CODES | {"Amb.", "SG", "DSG"}
DIPLOMATIC_REPRESENTATIVE_ROLE_NAMES = VOTING_AUTHORIZED_ROLE_NAMES | {
    ROLE_AMBASSADOR,
    ROLE_SG,
    ROLE_ADMIN,
}

# Native-script nationality, origin & language self-selection roles:
# (code, role_name, color, legacy_aliases, select_description)
IDENTITY_ROLE_SPECS = [
    ("tr", "🇹🇷 Türk", 0x970A0C, ["Türk", "🇹🇷 Türkçe"], "Türkçe · Türkiye"),
    ("az", "🇦🇿 Azərbaycan Türkü", 0x00B5E2, ["Azərbaycan", "Azərbaycanlı", "Azerbaycan Türkü"], "Azərbaycan dili · Azərbaycan"),
    ("kz", "🇰🇿 Қазақ", 0x00AFCA, ["Қазақ", "Kazak", "Qazaq"], "Қазақ тілі · Қазақстан (Kazak)"),
    ("kg", "🇰🇬 Кыргыз", 0xE8112D, ["Кыргыз", "Kırgız", "Kyrgyz"], "Кыргыз тили · Кыргызстан (Kırgız)"),
    ("uz", "🇺🇿 Oʻzbek", 0x1EB53A, ["Oʻzbek", "Özbek", "Uzbek"], "Oʻzbek tili · Oʻzbekiston (Özbek)"),
    ("tm", "🇹🇲 Türkmen", 0x00843D, ["Türkmen", "Turkmen"], "Türkmençe · Türkmenistan"),
    ("gb", "🇬🇧 British · English", 0x1E3A8A, ["British", "English", "İngiliz"], "English · United Kingdom (İngiliz)"),
    ("us", "🇺🇸 American", 0x3C3B6E, ["American", "Amerikan"], "English · United States (Amerikan)"),
    ("it", "🇮🇹 Italiano", 0x008C45, ["Italiano", "İtalyan"], "Italiano · Italia (İtalyan)"),
    ("ru", "🇷🇺 Русский", 0x0039A6, ["Русский", "Rus"], "Русский язык · Россия (Rus)"),
    ("fr", "🇫🇷 Français", 0x0055A4, ["Français", "Fransız"], "Français · France (Fransız)"),
    ("cn", "🇨🇳 中国人 · 中文", 0xDE2910, ["中国人", "中文", "Çinli"], "中文 / Mandarin · 中国 (Çinli)"),
    ("de", "🇩🇪 Deutsch", 0xFFCC00, ["Deutsch", "Alman"], "Deutsch · Deutschland (Alman)"),
    ("es", "🇪🇸 Español", 0xAA151B, ["Español", "İspanyol"], "Español · España / LATAM"),
    ("ua", "🇺🇦 Український", 0x005BBB, ["Український", "Ukraynalı"], "Українська мова · Україна"),
    ("jp", "🇯🇵 日本人 · 日本語", 0xBC002D, ["日本人", "日本語", "Japon"], "日本語 · 日本 (Japon)"),
    ("kr", "🇰🇷 한국인 · 한국어", 0x0047A0, ["한국인", "한국어", "Koreli"], "한국어 · 대한민국 (Koreli)"),
    ("ar", "🇸🇦 عربي · العربية", 0x006C35, ["عربي", "العربية", "Arap"], "اللغة العربية · Arapça"),
    ("ir", "🇮🇷 فارسی", 0x239F40, ["فارسی", "Fars", "İranlı"], "زبان فارسی · ایران (Farsça)"),
    ("pl", "🇵🇱 Polski", 0xDC143C, ["Polski", "Polonyalı"], "Język polski · Polska"),
    ("gr", "🇬🇷 Έλληνας", 0x0D5EAF, ["Έλληνας", "Ελληνικά", "Yunan"], "Ελληνικά · Ελλάδα (Yunan)"),
    ("pt", "🇵🇹 Português", 0x006600, ["Português", "Portekizce"], "Português · Portugal / Brasil"),
    ("nl", "🇳🇱 Nederlands", 0xAE1C28, ["Nederlands", "Hollandalı"], "Nederlands · Nederland"),
    ("in", "🇮🇳 भारतीय · हिन्दी", 0xFF9933, ["भारतीय", "हिन्दी", "Hint"], "हिन्दी / Indian · भारत (Hint)"),
    ("other", "🌍 Diğer · Other", 0x64748B, ["Diğer", "Other"], "Başka bir milliyet / dil · Other origin"),
]

IDENTITY_BY_CODE = {
    code: (rname, col, aliases, desc)
    for code, rname, col, aliases, desc in IDENTITY_ROLE_SPECS
}
IDENTITY_ROLE_NAMES = {rname for _, rname, _, _, _ in IDENTITY_ROLE_SPECS}

# Language-specific text/voice room access mapping -> native identity codes
LANG_CHANNEL_IDENTITY_CODES = {
    "tr": ["tr", "az", "kz", "kg", "uz", "tm"],
    "en": ["gb", "us"],
    "fr": ["fr"],
    "it": ["it"],
    "es": ["es"],
    "ru": ["ru", "ua"],
    "de": ["de"],
}


def format_identity_summary(codes: list[str]) -> str:
    names = [IDENTITY_BY_CODE[c][0] for c in (codes or []) if c in IDENTITY_BY_CODE]
    return ", ".join(names) if names else "Belirtilmedi"

LEADERSHIP_ROLES = [ROLE_SG, ROLE_GA_PRESIDENT, ROLE_COUNCIL_CHAIR, ROLE_ADMIN]
SPEAKER_ROLES = LEADERSHIP_ROLES + [
    ROLE_ICJ_PRESIDENT,
    ROLE_CHIEF_PROSECUTOR,
    ROLE_PROSECUTOR,
    ROLE_PRESIDENT,
    ROLE_FM,
    ROLE_IM,
    ROLE_EM,
    ROLE_DM,
    ROLE_CABINET,
    ROLE_HEAD_OF_DELEGATION,
    ROLE_MP,
    ROLE_PRIMARY,
    ROLE_ALTERNATE,
    ROLE_AMBASSADOR,
]
ALL_ACCREDITED_ROLES = SPEAKER_ROLES + [
    ROLE_MODERATOR,
    ROLE_SPOKESPERSON,
    ROLE_FOUNDING,
    ROLE_MEMBER,
    ROLE_CONSULATE,
    ROLE_PRESS,
    ROLE_OBSERVER,
    ROLE_CITIZEN,
]

# Roles stripped on office resignation / role switch without stripping country/membership status
MANAGED_OFFICE_ROLES = {
    ROLE_PRESIDENT,
    ROLE_FM,
    ROLE_IM,
    ROLE_EM,
    ROLE_DM,
    ROLE_CABINET,
    ROLE_HEAD_OF_DELEGATION,
    ROLE_MP,
    ROLE_PRIMARY,
    ROLE_ALTERNATE,
    ROLE_AMBASSADOR,
    ROLE_CONSULATE,
    ROLE_SPOKESPERSON,
    ROLE_PRESS,
    ROLE_CITIZEN,
    "🏭 Ticaret Ataşesi · Trade Attaché",
    "🛡️ Askeri Ataşe · Military Attaché",
    "🕊️ Özel Temsilci · Special Envoy",
}

STATUS_LABELS = {
    "FOUNDING": "🌟 Kurucu Üye / Founding State (Md./Art. 6 · 1 Oy/Vote)",
    "MEMBER": "🏛️ Üye Devlet / Member State (Md./Art. 7 · 1 Oy/Vote)",
    "OBSERVER": "👁️ Gözlemci / Observer (Oy Yok / No Vote)",
    "SUSPENDED": "⛔ Askıda / Suspended (Md./Art. 10)",
}

STATUS_ROLE_NAMES = {
    "FOUNDING": ROLE_FOUNDING,
    "MEMBER": ROLE_MEMBER,
    "OBSERVER": ROLE_OBSERVER,
    "SUSPENDED": ROLE_SUSPENDED,
}


def _fold_ascii(text: str) -> str:
    m = {
        "İ": "i", "I": "i", "ı": "i", "Î": "i", "î": "i",
        "Ç": "c", "ç": "c",
        "Ğ": "g", "ğ": "g",
        "Ö": "o", "ö": "o",
        "Ş": "s", "ş": "s",
        "Ü": "u", "ü": "u", "Û": "u", "û": "u",
        "Â": "a", "â": "a",
        "Ə": "e", "ə": "e",
    }
    return "".join(m.get(ch, ch) for ch in text).replace("\u0307", "").lower().strip()


# Ordered keyword table -> (keywords, office_code, role_name, bilingual_label, is_elevated)
_DUTY_RULES: list[tuple[tuple[str, ...], str, str, str, bool]] = [
    (("gorevim yok", "gorev yok", "yok", "none", "no role", "no office", "vatandas", "sivil", "citizen"), "Vatandaş", ROLE_CITIZEN, "🪪 Görevi Yok / Citizen", False),
    (("genel sekreter", "secretary general", "secretary-general"), "SG", ROLE_SG, "🏛️ Genel Sekreter · Secretary-General", True),
    (("genel kurul baskan", "assembly president", "meclis baskan", "speaker of assembly"), "GKB", ROLE_GA_PRESIDENT, "🪑 Genel Kurul Başkanı · Assembly President", True),
    (("sura baskan", "yuksek sura", "konsey baskan", "council chair", "council president"), "ŞB", ROLE_COUNCIL_CHAIR, "🏛️ Şûra Başkanı · Council Chair", True),
    (("divan baskan", "yargi divani baskan", "icj president", "court president", "basyargic", "bas yargic"), "DB", ROLE_ICJ_PRESIDENT, "⚖️ Divan Başkanı · ICJ President", True),
    (("bassavci", "bas savci", "chief prosecutor"), "BSV", ROLE_CHIEF_PROSECUTOR, "⚖️ Cemiyet Başsavcısı · Chief Prosecutor", True),
    (("savci", "prosecutor"), "SVC", ROLE_PROSECUTOR, "📜 Cemiyet Savcısı · Prosecutor", True),
    (("delege baskan", "delegasyon baskan", "heyet baskan", "head of delegation", "chief delegate", "delegation chair"), "Del.B.", ROLE_HEAD_OF_DELEGATION, "🎖️ Delege Başkanı · Head of Delegation", False),
    (("yargic", "hakim", "judge", "icj"), "UYD", ROLE_JUDGE, "⚖️ Divan Yargıcı · ICJ Judge", True),
    (("disisleri", "dis isleri", "dis-isleri", "foreign minister", "foreign affairs", "dis iliskiler"), "FM", ROLE_FM, "🌐 Dışişleri Bakanı · Foreign Minister", False),
    (("icisleri", "ic isleri", "ic-isleri", "interior minister", "home affairs", "internal affairs"), "IM", ROLE_IM, "🏛️ İçişleri Bakanı · Interior Minister", False),
    (("ekonomi", "maliye", "hazine", "ticaret", "economy", "finance", "treasury", "trade minister"), "EM", ROLE_EM, "💰 Ekonomi Bakanı · Economy Minister", False),
    (("savunma", "milli savunma", "genelkurmay", "defense", "defence"), "DM", ROLE_DM, "🛡️ Savunma Bakanı · Defense Minister", False),
    (("ulus temsilcisi", "ulustemsilcisi", "milletvekili", "mebus", "senator", "parlamenter", "parliament", "congress", "deputy"), "MP", ROLE_MP, "🏛️ Ulus Temsilcisi · MP", False),
    (("cumhurbaskan", "devlet baskan", "basbakan", "baskan", "kral", "kralice", "imparator", "padisah", "sansolye", "president", "prime minister", "head of state", "chancellor", "monarch", "premier"), "Pres.", ROLE_PRESIDENT, "👑 Devlet Başkanı · Head of State", False),
    (("asil temsilci", "daimi temsilci", "primary rep", "permanent rep"), "Asil", ROLE_PRIMARY, "🏛️ Asil Temsilci · Primary Rep", False),
    (("yedek temsilci", "alternate"), "Yedek", ROLE_ALTERNATE, "🏛️ Yedek Temsilci · Alternate Rep", False),
    (("buyukelci", "sefir", "ambassador"), "Amb.", ROLE_AMBASSADOR, "🏛️ Büyükelçi · Ambassador", False),
    (("konsolos", "consul"), "CG", ROLE_CONSULATE, "🛂 Konsolosluk · Consulate", False),
    (("sozcu", "spokesperson"), "Spokesperson", ROLE_SPOKESPERSON, "📰 Sözcü · Spokesperson", False),
    (("gozlemci", "observer"), "Obs.", ROLE_OBSERVER, "👁️ Gözlemci · Observer", False),
    (("basin", "gazeteci", "press", "journalist", "media"), "Press", ROLE_PRESS, "📰 Basın · Press", False),
    (("bakan", "minister", "kabine", "cabinet"), "Kabine", ROLE_CABINET, "🏛️ Bakan / Kabine · Cabinet Minister", False),
    (("temsilci", "delege", "delegate", "representative", "atache", "atase", "elci"), "Asil", ROLE_PRIMARY, "🏛️ Temsilci · Delegate", False),
]


def classify_office_role(duty_text: str) -> dict:
    """Map free-text official duty input (TR/EN) to canonical office_code and Discord role."""
    norm = _fold_ascii(duty_text or "")
    for keywords, code, role_name, label, elevated in _DUTY_RULES:
        if any(kw in norm for kw in keywords):
            return {
                "code": code,
                "role_name": role_name,
                "display_label": label,
                "elevated": elevated,
                "raw_title": duty_text.strip() or "Görevim Yok",
            }
    return {
        "code": "Vatandaş",
        "role_name": ROLE_CITIZEN,
        "display_label": "🪪 Vatandaş / Delege · Citizen",
        "elevated": False,
        "raw_title": duty_text.strip() or "Görevim Yok",
    }


CHANNEL_ALIASES = {
    "duyurular-announcements": ["duyurular-announcements", "cemiyet-duyuru"],
    "resmi-gazete-official-gazette": ["resmi-gazete-official-gazette", "mc-resmi-gazete", "resmi-gazete", "official-gazette"],
    "tuzuk-charter": ["tuzuk-charter", "charter-tuzuk"],
    "imzaci-devletler-signatories": ["imzaci-devletler-signatories", "imzaci-devletler", "signatories"],
    "akreditasyon-accreditation": ["akreditasyon-accreditation", "kayit-masasi-register", "akreditasyon-masasi", "accreditation-desk-kayit"],
    "destek-masasi-tickets": ["destek-masasi-tickets", "destek-basvuru-masasi"],
    "basvuru-odasi-applications": ["basvuru-odasi-applications", "basvuru-odasi", "yetkili-basvuru", "applications"],
    "genel-kurul-assembly": ["genel-kurul-assembly", "cemiyet-genel-kurulu"],
    "konusmaci-kursusu-speakers": ["konusmaci-kursusu-speakers", "konusmaci-kursusu", "speakers-floor-kursu"],
    "onergeler-motions-votes": ["onergeler-motions-votes", "onerge-ve-oylamalar", "motions-and-votes-oylamalar"],
    "kararlar-resolutions": ["kararlar-resolutions", "kabul-edilen-kararlar", "adopted-resolutions-kararlar"],
    "antlasmalar-treaties": ["antlasmalar-treaties", "antlasmalar"],
    "yargi-divani-icj-court": ["yargi-divani-icj-court", "uluslararasi-yargi-divani", "daimi-adalet-divani"],
    "yargi-arsivi-icj-archive": ["yargi-arsivi-icj-archive", "yargi-arsivi", "dava-arsivi", "icj-archive"],
    "divan-makami-chambers": ["divan-makami-chambers", "divan-ofisi", "yargic-ofisi", "court-chambers"],
    "savcilik-makami-prosecution": ["savcilik-makami-prosecution", "savcilik-ofisi", "prosecution-office", "savcilik-burosu"],
    "kurumsal-atama-appointments": ["kurumsal-atama-appointments", "genel-sekreter-atama", "kurumsal-atama", "appointments-desk"],
    "onay-odasi-approvals": ["onay-odasi-approvals", "divan-onay"],
    "yayin-paneli-broadcast": ["yayin-paneli-broadcast", "sekreterlik-yayin-paneli"],
    "ticket-log-archive": ["ticket-log-archive", "ticket-log"],
    "denetim-kaydi-audit-log": ["denetim-kaydi-audit-log", "denetim-kaydi", "audit-log", "mod-log"],
    "sohbet-sifirlama-kayit": ["sohbet-sifirlama-kayit", "sohbet-kayit", "chat-reset-log", "mesaj-kayit"],
    "uluslar-nations": ["uluslar-nations", "milletler-nations", "milletler"],
}


def can_member_sign_for_state(member, rec: dict | None = None) -> bool:
    """Return True if member holds Head of State or Foreign Minister signing authority."""
    if isinstance(rec, dict) and rec.get("office_code") in SIGNATORY_OFFICE_CODES:
        return True
    roles = getattr(member, "roles", None) or ()
    return bool({getattr(r, "name", str(r)) for r in roles} & SIGNATORY_ROLE_NAMES)


def can_member_vote_for_state(member, rec: dict | None = None) -> bool:
    """Return True if member holds Cabinet, Head of Delegation, or Primary/Alternate Rep voting authority."""
    if isinstance(rec, dict) and rec.get("office_code") in VOTING_AUTHORIZED_OFFICE_CODES:
        return True
    roles = getattr(member, "roles", None) or ()
    return bool({getattr(r, "name", str(r)) for r in roles} & VOTING_AUTHORIZED_ROLE_NAMES)


def can_member_appoint_head_of_delegation(member, rec: dict | None = None) -> bool:
    """Return True if member is Head of State (Devlet Başkanı) or MP (Ulus Temsilcisi) for delegate appointments."""
    if isinstance(rec, dict) and rec.get("office_code") in {"Pres.", "MP"}:
        return True
    roles = getattr(member, "roles", None) or ()
    return bool({ROLE_PRESIDENT, ROLE_MP} & {getattr(r, "name", str(r)) for r in roles})


def can_member_speak_in_assembly(member, rec: dict | None = None) -> bool:
    """Return True if member holds an accredited Assembly/diplomatic speaker office (excludes Citizen-only & country tier badges)."""
    if isinstance(rec, dict) and rec.get("office_code") in PARLIAMENTARY_SPEAKER_CODES:
        return True
    roles = getattr(member, "roles", None) or ()
    return bool({getattr(r, "name", str(r)) for r in roles} & set(SPEAKER_ROLES))


def can_member_represent_state_diplomacy(
    member,
    rec: dict | None = None,
    *,
    allow_consulate: bool = False,
    allow_mp_icj: bool = False,
) -> bool:
    """Return True if member holds official state diplomatic authority (excludes Citizen-only even with Founding/Member badge)."""
    allowed_codes = set(DIPLOMATIC_REPRESENTATIVE_CODES)
    allowed_roles = set(DIPLOMATIC_REPRESENTATIVE_ROLE_NAMES)
    if allow_consulate:
        allowed_codes.add("CG")
        allowed_roles.add(ROLE_CONSULATE)
    if allow_mp_icj:
        allowed_codes.update({"MP", "DB", "UYD"})
        allowed_roles.update({ROLE_MP, ROLE_ICJ_PRESIDENT, ROLE_JUDGE})
    if isinstance(rec, dict) and rec.get("office_code") in allowed_codes:
        return True
    roles = getattr(member, "roles", None) or ()
    return bool({getattr(r, "name", str(r)) for r in roles} & allowed_roles)


_INVALID_PLACEHOLDERS = {
    "yok", "yoktur", "none", "null", "na", "n a", "bilmiyorum", "belirtilmedi",
    "bagimsiz", "test", "deneme", "asdf", "asdfg", "qwe", "qwert", "qwerty",
    "abc", "abcd", "xxx", "ulke", "devlet", "country", "state", "ad", "isim",
    "soyad", "name", "oyuncu", "player", "nick", "nickname",
}


def validate_country_name(raw: str) -> tuple[bool, str, str]:
    """Validate mandatory country name written cleanly using alphabet letters only."""
    cleaned = " ".join((raw or "").strip().split())
    if len(cleaned) < 3:
        return (
            False,
            cleaned,
            "❌ Ülke adını eksiksiz ve yalnızca alfabeyle (en az 3 harf) yazmalısınız.\n"
            "*Please write a valid full country name using letters only (min 3 characters).*",
        )
    if any(ch.isdigit() for ch in cleaned):
        return (
            False,
            cleaned,
            "❌ Ülke adında rakam kullanılamaz; lütfen ülkenizi yalnızca alfabeyle net bir şekilde yazın (örn: `Türkiye Cumhuriyeti`).\n"
            "*Country names cannot contain digits; use alphabet letters only.*",
        )
    allowed_punct = {" ", "-", "'", "’"}
    if any(not (ch.isalpha() or ch in allowed_punct) for ch in cleaned):
        return (
            False,
            cleaned,
            "❌ Ülke adında yalnızca harf (alfabe), boşluk veya tire kullanın (noktalama/sembol/emoji yasaktır).\n"
            "*Country names may contain only alphabet letters, spaces, or hyphens.*",
        )
    letters = [ch for ch in cleaned if ch.isalpha()]
    folded_words = re.sub(r"[^a-z0-9\s]", " ", _fold_ascii(cleaned)).strip()
    if len(letters) < 3 or len({_fold_ascii(ch) for ch in letters}) < 2 or folded_words in _INVALID_PLACEHOLDERS:
        return (
            False,
            cleaned,
            "❌ Geçerli bir devlet tam adı yazın (`yok`, `test`, tek harf tekrarı vb. kabul edilmez; örn: `Türkiye Cumhuriyeti`).\n"
            "*Please enter a real sovereign state name.*",
        )
    return True, cleaned, ""


def validate_ingame_name(raw: str) -> tuple[bool, str, str]:
    """Validate mandatory full in-game name."""
    cleaned = " ".join((raw or "").strip().split())
    letters = [ch for ch in cleaned if ch.isalpha()]
    allowed_punct = {" ", "-", "_", ".", "'", "’"}
    if len(cleaned) < 3 or len(letters) < 3:
        return (
            False,
            cleaned,
            "❌ Oyun içi tam adınızı eksiksiz yazmalısınız (en az 3 harf).\n"
            "*Please enter your full in-game name (at least 3 letters).*",
        )
    if any(not (ch.isalnum() or ch in allowed_punct) for ch in cleaned):
        return (
            False,
            cleaned,
            "❌ Oyun içi adınızda geçersiz sembol kullanmayın; lütfen net oyun içi adınızı yazın.\n"
            "*Please avoid special symbols in your in-game name.*",
        )
    folded = re.sub(r"[^a-z0-9\s]", " ", _fold_ascii(cleaned)).strip()
    if len({_fold_ascii(ch) for ch in letters}) < 2 or folded in _INVALID_PLACEHOLDERS:
        return (
            False,
            cleaned,
            "❌ Geçerli bir oyun içi tam ad girin (`yok`, `test`, anlamsız harf tekrarı kabul edilmez).\n"
            "*Please enter a valid full in-game name.*",
        )
    return True, cleaned, ""


def slug_match(channel_name: str, aliases: list[str]) -> bool:
    """Case/separator-insensitive substring match against canonical channel aliases."""
    low = channel_name.lower()
    norm = re.sub(r"[-_\s]+", " ", low)
    return any(
        alias.lower() in low or re.sub(r"[-_\s]+", " ", alias.lower()) in norm
        for alias in aliases
    )


def resolve_panel_channel(guild, explicit_channel, panel_key: str, fallback):
    """Return explicit_channel if chosen, else canonical League channel for panel_key, else fallback."""
    if explicit_channel is not None:
        return explicit_channel
    aliases = CHANNEL_ALIASES.get(panel_key, [panel_key])
    if guild is not None:
        for ch in getattr(guild, "text_channels", []):
            if slug_match(ch.name, aliases):
                return ch
    return fallback
