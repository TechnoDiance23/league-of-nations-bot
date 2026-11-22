import logging
from functools import lru_cache
from pathlib import Path


log = logging.getLogger("InternationalDiplomaticBot.Translation")
LANGUAGE_ID_MODEL = Path("/opt/league-translation/lid.176.ftz")

# Base languages in the TranslateGemma WMT24++ evaluation, plus English.
NATURAL_LANGUAGE_NAMES = {
    "ar": "Arabic",
    "bg": "Bulgarian",
    "bn": "Bengali",
    "ca": "Catalan",
    "cs": "Czech",
    "da": "Danish",
    "de": "German",
    "el": "Greek",
    "en": "English",
    "es": "Spanish",
    "et": "Estonian",
    "fa": "Persian",
    "fi": "Finnish",
    "fil": "Filipino",
    "fr": "French",
    "gu": "Gujarati",
    "he": "Hebrew",
    "hi": "Hindi",
    "hr": "Croatian",
    "hu": "Hungarian",
    "id": "Indonesian",
    "is": "Icelandic",
    "it": "Italian",
    "ja": "Japanese",
    "kn": "Kannada",
    "ko": "Korean",
    "lt": "Lithuanian",
    "lv": "Latvian",
    "ml": "Malayalam",
    "mr": "Marathi",
    "nl": "Dutch",
    "no": "Norwegian",
    "pa": "Punjabi",
    "pl": "Polish",
    "pt": "Portuguese",
    "ro": "Romanian",
    "ru": "Russian",
    "sk": "Slovak",
    "sl": "Slovenian",
    "sr": "Serbian",
    "sv": "Swedish",
    "sw": "Swahili",
    "ta": "Tamil",
    "te": "Telugu",
    "th": "Thai",
    "tr": "Turkish",
    "uk": "Ukrainian",
    "ur": "Urdu",
    "vi": "Vietnamese",
    "zh-Hans": "Simplified Chinese",
    "zu": "Zulu",
}

LANGUAGE_ALIASES = {
    "iw": "he",
    "nb": "no",
    "tl": "fil",
    "zh": "zh-Hans",
    "zh-cn": "zh-Hans",
    "zh-hans": "zh-Hans",
}


def normalize_language(code: str) -> str:
    code = code.strip().replace("_", "-").lower()
    return LANGUAGE_ALIASES.get(code, code)


@lru_cache(maxsize=1)
def _language_model():
    try:
        import fasttext

        return fasttext.load_model(str(LANGUAGE_ID_MODEL))
    except (ImportError, OSError, RuntimeError, ValueError):
        log.warning("Local fastText language detection unavailable")
        return None


def identify_language(text: str) -> str | None:
    """Return a clear fastText result, leaving uncertain text for the fallback detector."""
    text = " ".join(text.split())
    letters = sum(character.isalpha() for character in text)
    if letters < 2:
        return None

    model = _language_model()
    if model is None:
        return None
    labels, scores = model.predict(text, k=2)
    if not labels:
        return None

    confidence = float(scores[0])
    alternative = float(scores[1]) if len(scores) > 1 else 0.0
    short_text = letters < 12 or len(text.split()) < 3
    minimum_confidence, minimum_gap = (0.82, 0.35) if short_text else (0.60, 0.20)
    if confidence < minimum_confidence or confidence - alternative < minimum_gap:
        return None
    return normalize_language(labels[0].removeprefix("__label__"))
