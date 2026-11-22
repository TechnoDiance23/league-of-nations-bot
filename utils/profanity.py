"""Multilingual (TR / EN / RU / IT / ES / DE) profanity & slur detector with false-positive guards."""

import re

# Unambiguous root stems where suffixed/inflected forms are also profanity
_ROOT_PATTERNS = (
    # Turkish severe profanity & insults
    r"siktir\w*",
    r"sikik\w*",
    r"sikim\w*",
    r"sikeyim\w*",
    r"sikerim\w*",
    r"siktim\w*",
    r"sikis\w*",
    r"sikiş\w*",
    r"sikmek\w*",
    r"orospu\w*",
    r"oç",
    r"amc[iı]k\w*",
    r"yarr?a[kğg]\w*",
    r"götveren\w*",
    r"gotveren\w*",
    r"götün\w*",
    r"gotunu\w*",
    r"pezevenk\w*",
    r"kahpe\w*",
    r"kaltak\w*",
    r"puşt\w*",
    r"ibne\w*",
    r"gavat\w*",
    r"dalyarak\w*",
    r"taşak\w*",
    r"taşşak\w*",
    r"tassak\w*",
    r"sürtük\w*",
    r"surtuk\w*",
    r"fahişe\w*",
    r"yavşak\w*",
    r"yavsak\w*",
    r"gerizekal[iı]\w*",
    r"anan[ıi]\s*(?:sik\w*|sikeyim|avrad\w*)",
    r"am[ıi]na\s*(?:koy\w*|k[o0]y[ıi]m|kodum\w*|sok\w*|skm)",
    r"mk\s*koy\w*",
    # English severe profanity & slurs
    r"motherfuck\w*",
    r"fuck(?:ing|ed|er|ers|s|off)?",
    r"bitch\w*",
    r"asshole\w*",
    r"bullshit\w*",
    r"dumbass\w*",
    r"cocksuck\w*",
    r"blowjob\w*",
    r"nigg(?:er|a)\w*",
    r"faggot\w*",
    r"retard\w*",
    r"whore\w*",
    r"slut\w*",
    r"cunt\w*",
    # Russian / Italian / Spanish common severe profanity
    r"бляд\w*",
    r"блять",
    r"сука\b",
    r"пизд\w*",
    r"хуй\w*",
    r"ебат\w*",
    r"ёбан\w*",
    r"ебан\w*",
    r"vaffanculo\w*",
    r"cazzo\b",
    r"puttana\w*",
    r"stronzo\w*",
    r"puta\b",
    r"cabron\w*",
    r"cabrón\w*",
    r"gilipollas\w*",
    r"maric[oó]n\w*",
    # German common severe profanity & slurs
    r"schei[ßs]{1,2}e\w*",
    r"arschloch\w*",
    r"hurensohn\w*",
    r"wichser\w*",
    r"fick(?:en|t|dich)\w*",
    r"fotze\w*",
    r"missgeburt\w*",
    r"schlampe\w*",
)

# Standalone short abbreviations / words matched strictly on word boundaries
_EXACT_WORDS = (
    r"amk",
    r"amq",
    r"aq",
    r"a\.m\.k",
    r"a\.q",
    r"o\.ç",
    r"piç\w*",
    r"göt",
    r"göte",
    r"götü",
    r"shit",
    r"shitty",
    r"dick",
    r"dickhead",
    r"cock",
    r"pussy",
    r"mierda",
    r"joder",
    r"blyat",
    r"cyka",
    r"suka",
    r"nahuy",
    r"naxuy",
)

# Common spaced evasion targets normalized without separators (longer unambiguous terms only)
_COLLAPSED_TERMS = (
    "siktir",
    "orospu",
    "pezevenk",
    "dalyarak",
    "yavşak",
    "yavsak",
    "amcık",
    "amcik",
    "yarrak",
    "götveren",
    "gotveren",
    "motherfucker",
    "asshole",
    "bullshit",
    "vaffanculo",
    "arschloch",
    "hurensohn",
)

_ROOT_RE = re.compile(
    r"(?<![a-zA-ZçğıöşüÇĞİÖŞÜßа-яА-ЯёЁáéíóúñÁÉÍÓÚÑàèìòùÀÈÌÒÙ])(?:"
    + "|".join(_ROOT_PATTERNS + _EXACT_WORDS)
    + r")(?![a-zA-ZçğıöşüÇĞİÖŞÜßа-яА-ЯёЁáéíóúñÁÉÍÓÚÑàèìòùÀÈÌÒÙ])",
    re.IGNORECASE,
)

# Short spaced tokens like "a m k", "o ç", "a q"
_SPACED_SHORT_RE = re.compile(
    r"(?<![a-zA-ZçğıöşüÇĞİÖŞÜ])(?:a[\s._*-]+m[\s._*-]+k|a[\s._*-]+q|o[\s._*-]+ç|p[\s._*-]+i[\s._*-]+ç|s[\s._*-]+i[\s._*-]+k[\s._*-]+t[\s._*-]+i[\s._*-]+r)(?![a-zA-ZçğıöşüÇĞİÖŞÜ])",
    re.IGNORECASE,
)

_ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\ufeff\u00ad]")
_LEET_MAP = str.maketrans({
    "@": "a",
    "4": "a",
    "3": "e",
    "1": "i",
    "!": "i",
    "0": "o",
    "$": "s",
    "5": "s",
    "İ": "i",
    "I": "ı",
})


def _prepare_text(text: str) -> str:
    cleaned = _ZERO_WIDTH_RE.sub("", text or "")
    # Collapse 3+ repeated identical characters (e.g. "siktiirrrr" -> "siktiir") while keeping doubles valid
    cleaned = re.sub(r"(.)\1{2,}", r"\1\1", cleaned)
    return cleaned.translate(_LEET_MAP).lower()


def mask_word(word: str) -> str:
    w = (word or "").strip()
    if len(w) <= 2:
        return "*" * max(1, len(w))
    return f"{w[0]}{'*' * (len(w) - 2)}{w[-1]}"


def mask_profane_text(text: str) -> str:
    """Replace matched profanity spans inside text with masked characters for audit/court views."""
    if not text:
        return ""
    out = _ROOT_RE.sub(lambda m: mask_word(m.group(0)), text)
    out = _SPACED_SHORT_RE.sub(lambda m: mask_word(re.sub(r"[\s._*-]+", "", m.group(0))), out)

    # Catch leet / repeated-char tokens (e.g. "b1tch", "siktiirrrr") on whitespace-delimited words
    parts = re.split(r"(\s+)", out)
    changed = False
    for idx, tok in enumerate(parts):
        if tok and not tok.isspace() and _ROOT_RE.search(_prepare_text(tok)):
            parts[idx] = mask_word(tok)
            changed = True
    if changed:
        out = "".join(parts)

    # Catch obfuscated multi-token spacing (e.g. "o r o s p u") if any remains
    rem = detect_profanity(out)
    if rem:
        pattern = r"[\s._*-]*".join(re.escape(ch) for ch in rem["matched"])
        masked_out = re.sub(pattern, rem["masked"], out, flags=re.IGNORECASE)
        out = masked_out if masked_out != out else f"[filtrelendi: {rem['masked']}]"
    return out


def detect_profanity(text: str) -> dict | None:
    """Return match metadata if text contains profanity, otherwise None."""
    if not text or not text.strip():
        return None

    prep = _prepare_text(text)
    m = _ROOT_RE.search(prep)
    if m:
        matched = m.group(0).strip()
        return {"matched": matched, "masked": mask_word(matched)}

    ms = _SPACED_SHORT_RE.search(prep)
    if ms:
        matched = re.sub(r"[\s._*-]+", "", ms.group(0))
        return {"matched": matched, "masked": mask_word(matched)}

    # Check obfuscated letter-spaced long insults (e.g. "o.r.o.s.p.u", "s i k t i r")
    run: list[str] = []
    for tok in re.split(r"[^a-zçğıöşüа-яё]+", prep):
        if len(tok) == 1:
            run.append(tok)
        else:
            if len(run) >= 3:
                joined = "".join(run)
                for term in _COLLAPSED_TERMS:
                    if term in joined:
                        return {"matched": term, "masked": mask_word(term)}
            run = []
    if len(run) >= 3:
        joined = "".join(run)
        for term in _COLLAPSED_TERMS:
            if term in joined:
                return {"matched": term, "masked": mask_word(term)}

    # Also check dot/dash/underscore glued variants like "s.i.k.t.i.r" or "o_r_o_s_p_u"
    glued_hits = re.findall(r"(?:[a-zçğıöşüа-яё][._*-]){2,}[a-zçğıöşüа-яё]+", prep)
    for g in glued_hits:
        collapsed = re.sub(r"[._*-]+", "", g)
        for term in _COLLAPSED_TERMS:
            if term in collapsed:
                return {"matched": term, "masked": mask_word(term)}

    return None
