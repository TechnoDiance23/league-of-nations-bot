import datetime
from difflib import SequenceMatcher
import re
import discord
from utils.storage import gazette_storage, countries_storage, signatories_storage
from utils.constants import STATUS_LABELS  # re-exported for backward compatibility

_YTD_COUNTRY_KEY = "yuce tanri daglari"
_COUNTRY_KEY_ALIASES = {
    "yuce tanri daglari cumhuriyeti devleti": _YTD_COUNTRY_KEY,
    "yuce tanri daglari cumhuriyeti": _YTD_COUNTRY_KEY,
    "yuce tanri daglari devleti": _YTD_COUNTRY_KEY,
    "ytd": _YTD_COUNTRY_KEY,
    "ytdc": _YTD_COUNTRY_KEY,
    "ydt": _YTD_COUNTRY_KEY,
}


def _fold_tr(text: str) -> str:
    map_tr = {
        "İ": "i", "I": "i", "ı": "i",
        "Ç": "c", "ç": "c",
        "Ğ": "g", "ğ": "g",
        "Ö": "o", "ö": "o",
        "Ş": "s", "ş": "s",
        "Ü": "u", "ü": "u",
        "Ə": "e", "ə": "e",
    }
    out = "".join(map_tr.get(ch, ch) for ch in text)
    return out.replace("\u0307", "").lower()


def _raw_country_key(name: str) -> str:
    return " ".join(_fold_tr(name).strip().split())


def normalize_country_key(name: str) -> str:
    """Normalize country name into a consistent case/diacritic-insensitive storage key."""
    folded = _raw_country_key(name)
    return _COUNTRY_KEY_ALIASES.get(folded, folded)


def canonical_sovereign_stem(name: str) -> str:
    """Canonical stem to stop the same state from signing twice (e.g. Türkiye vs Türkiye Cumhuriyeti)."""
    folded = re.sub(r"[^a-z0-9\s]", " ", _fold_tr(name))
    tokens = [t for t in folded.split() if t]
    generic = {
        "cumhuriyeti", "cumhuriyet", "cumhuriyyeti", "cumhuriyyet",
        "republic", "kralligi", "krallik", "kingdom",
        "devleti", "devlet", "state", "states", "federal", "federated",
        "federasyonu", "federation", "konfederasyonu", "confederation",
        "imparatorlugu", "empire", "prensligi", "principality",
        "birligi", "union", "of", "the",
    }
    stripped = [t for t in tokens if t not in generic]
    return " ".join(stripped) if stripped else " ".join(tokens)


def get_country_record(nation: str) -> dict:
    """Retrieve or initialize a sovereign country's dossier record."""
    key = normalize_country_key(nation)
    data = countries_storage.read()
    if key not in data:
        return {
            "key": key,
            "name": nation.strip(),
            "short": generate_country_short(nation),
            "status": "MEMBER",  # P5, E10, MEMBER, OBSERVER, SUSPENDED
            "sanction_note": None,
            "stats": {
                "proposals": 0,
                "passed_proposals": 0,
                "yes": 0,
                "no": 0,
                "abstain": 0,
                "veto": 0,
                "treaties_ratified": [],
                "icj_cases": 0,
                "notes_sent": 0,
                "notes_received": 0,
            },
        }
    return data[key]


def save_country_record(record: dict):
    countries_storage.set(record["key"], record)


def ensure_country_record(nation: str) -> dict:
    key = normalize_country_key(nation)
    data = countries_storage.read()
    if key not in data:
        rec = get_country_record(nation)
        countries_storage.set(key, rec)
        return rec
    rec = data[key]
    expected_short = generate_country_short(rec.get("name") or nation)
    if expected_short and rec.get("short") != expected_short:
        rec["short"] = expected_short
        countries_storage.set(key, rec)
    return rec


_SHORT_LOWER_PARTICLES = {
    "of", "and", "or", "the", "for", "in", "on", "to", "a", "an",
    "de", "du", "des", "la", "le", "les", "da", "do", "dos", "das",
    "von", "van", "der", "den", "ve", "ile", "ya",
}


def generate_country_short(country_clean: str) -> str:
    """Build a max-4-char country tag from word initials (1 word->3 chars, 2+ words->up to 4 initials)."""
    raw = (country_clean or "").strip()
    if not raw:
        return ""
    if _COUNTRY_KEY_ALIASES.get(_raw_country_key(raw)) == _YTD_COUNTRY_KEY:
        return "YTD"
    words = [
        "".join(ch for ch in part if ch.isalpha())
        for part in re.split(r"[\s\-–—/'’]+", raw)
    ]
    words = [w for w in words if w]
    if not words:
        letters = "".join(ch for ch in raw if ch.isalpha())
        return (letters[:3] or raw[:3]).upper()
    if len(words) == 1:
        return words[0][:3].upper()
    selected = words[:4]
    initials: list[str] = []
    for idx, word in enumerate(selected):
        if 0 < idx < len(selected) - 1 and word.lower() in _SHORT_LOWER_PARTICLES:
            initials.append(word[0].lower())
        else:
            initials.append(word[0].upper())
    return "".join(initials)[:4]


def find_country_matches(country_clean: str, guild: discord.Guild | None = None) -> dict:
    """Find exact or similar registered countries from storage and 🏳️ guild roles."""
    candidates: dict[str, dict] = {}
    for rec in countries_storage.read().values():
        if not isinstance(rec, dict) or not rec.get("name"):
            continue
        name = rec["name"].strip()
        key = normalize_country_key(name)
        if key and key not in candidates:
            candidates[key] = {
                "key": key,
                "name": name,
                "short": generate_country_short(name),
                "status": rec.get("status"),
            }
    for sig in signatories_storage.read().values():
        if not isinstance(sig, dict) or not sig.get("nation"):
            continue
        name = sig["nation"].strip()
        key = normalize_country_key(name)
        if key and key not in candidates:
            candidates[key] = {
                "key": key,
                "name": name,
                "short": generate_country_short(name),
                "status": sig.get("status"),
            }
    if guild is not None:
        for role in getattr(guild, "roles", []):
            rname = getattr(role, "name", "")
            if rname.startswith("🏳️"):
                cname = rname.removeprefix("🏳️").strip()
                key = normalize_country_key(cname)
                if key and key not in candidates:
                    candidates[key] = {
                        "key": key,
                        "name": cname,
                        "short": generate_country_short(cname),
                        "status": None,
                    }

    typed_key = normalize_country_key(country_clean)
    if typed_key in candidates:
        return {"exact": candidates[typed_key], "similar": []}

    typed_stem = canonical_sovereign_stem(country_clean)
    typed_tokens = {t for t in typed_stem.split() if len(t) >= 2}
    scored: list[tuple[float, dict]] = []
    for cand_key, info in candidates.items():
        cand_stem = canonical_sovereign_stem(info["name"])
        cand_tokens = {t for t in cand_stem.split() if len(t) >= 2}
        score = max(
            SequenceMatcher(None, typed_key, cand_key).ratio(),
            SequenceMatcher(None, typed_stem, cand_stem).ratio() if (typed_stem and cand_stem) else 0.0,
        )
        if typed_stem and cand_stem and typed_stem == cand_stem:
            score = max(score, 0.98)
        elif (
            len(typed_stem) >= 3
            and cand_stem
            and (typed_stem in cand_stem or cand_stem in typed_stem)
        ) or (len(typed_key) >= 4 and (typed_key in cand_key or cand_key in typed_key)):
            score = max(score, 0.90)
        elif typed_tokens and cand_tokens and (typed_tokens <= cand_tokens or cand_tokens <= typed_tokens):
            score = max(score, 0.88)
        elif typed_key and info["short"] and typed_key == _fold_tr(info["short"]):
            score = max(score, 0.86)

        if score >= 0.68:
            scored.append((score, info))

    scored.sort(key=lambda item: (-item[0], item[1]["name"]))
    return {"exact": None, "similar": [item[1] for item in scored[:3]]}


def sync_all_country_shorts_storage() -> list[tuple[str, str, str]]:
    """Recompute and persist country short codes for all recorded nations."""
    updated: list[tuple[str, str, str]] = []
    data = countries_storage.read()
    for key, rec in data.items():
        if not isinstance(rec, dict) or not rec.get("name"):
            continue
        old_short = rec.get("short", "")
        new_short = generate_country_short(rec["name"])
        if new_short and old_short != new_short:
            rec["short"] = new_short
            countries_storage.set(key, rec)
            updated.append((rec["name"], old_short, new_short))
    return updated


def find_gazette_channel(guild: discord.Guild) -> discord.TextChannel | None:
    """Locate the League of Nations Official Gazette channel in a guild."""
    if not guild:
        return None
    cfg = gazette_storage.get(f"config_{guild.id}", {})
    ch_id = cfg.get("channel_id")
    if ch_id:
        ch = guild.get_channel(int(ch_id))
        if ch:
            return ch
    for ch in guild.text_channels:
        low = ch.name.lower()
        if "resmi-gazete" in low or "official-gazette" in low or "mc-gazete" in low:
            return ch
    return None


async def publish_to_gazette(
    bot: discord.Client,
    guild_id: int | None,
    category_tag: str,
    title: str,
    summary: str,
    fields: list[tuple[str, str, bool]] | None = None,
    color: int = 0x1E3A8A,
    issuer: str = "Genel Sekreterlik · Secretariat",
) -> str | None:
    """Publish a numbered entry to the League of Nations Official Gazette and archive it."""
    if not guild_id:
        return None
    guild = bot.get_guild(int(guild_id))
    if not guild:
        return None

    data = gazette_storage.read()
    counter = data.get("counter", 0) + 1
    year = datetime.datetime.utcnow().year
    issue_no = f"MC-RG-{year}/{counter:04d}"

    entry = {
        "issue_no": issue_no,
        "guild_id": guild_id,
        "category": category_tag,
        "title": title,
        "summary": summary,
        "issuer": issuer,
        "timestamp": datetime.datetime.utcnow().strftime("%d.%m.%Y %H:%M UTC"),
    }
    entries = data.get("entries", {})
    entries[issue_no] = entry
    data["counter"] = counter
    data["entries"] = entries
    gazette_storage.write(data)

    ch = find_gazette_channel(guild)
    if not ch:
        return issue_no

    embed = discord.Embed(
        title=f"📰 RESMÎ GAZETE · OFFICIAL GAZETTE ({issue_no})",
        description=(
            f"*Milletler Cemiyeti · League of Nations · Société des Nations · Лига Наций*\n\n"
            f"**[{category_tag}] — {title}**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n{summary}"
        ),
        colour=color,
    )
    if fields:
        for fname, fval, finline in fields:
            embed.add_field(name=fname, value=fval[:1024], inline=finline)

    embed.set_footer(text=f"{issuer} | Tarih / Date: {entry['timestamp']}")
    try:
        await ch.send(embed=embed)
    except Exception:
        pass
    return issue_no


async def upsert_panel_message(
    channel: discord.TextChannel,
    title_prefixes: list[str] | str,
    embed: discord.Embed,
    view: discord.ui.View | None = None,
    pin: bool = True,
) -> str:
    """Edit an existing matching bot panel embed in-place or send and pin a fresh one."""
    prefixes = [title_prefixes] if isinstance(title_prefixes, str) else list(title_prefixes)
    try:
        async for msg in channel.history(limit=25):
            if msg.author.id == channel.guild.me.id and msg.embeds:
                etitle = msg.embeds[0].title or ""
                if any(etitle.startswith(p) for p in prefixes):
                    await msg.edit(embed=embed, view=view)
                    return "updated"
        sent = await channel.send(embed=embed, view=view)
        if pin:
            try:
                await sent.pin()
            except Exception:
                pass
        return "created"
    except Exception:
        return "failed"


async def move_court_verdict_to_archive(
    guild: discord.Guild | None,
    verdict_embed: discord.Embed,
    case: dict,
    *,
    source_message: discord.Message | None = None,
    view: discord.ui.View | None = None,
) -> discord.Message | None:
    """Publish a finalized court verdict into #yargi-arsivi-icj-archive and remove the active court message."""
    from utils.constants import resolve_panel_channel

    if not guild:
        return None
    archive_ch = resolve_panel_channel(guild, None, "yargi-arsivi-icj-archive", None)
    if not archive_ch:
        return None
    arch_msg = await archive_ch.send(embed=verdict_embed, view=view)
    case["archived"] = True
    case["archive_channel_id"] = archive_ch.id
    case["archive_message_id"] = arch_msg.id

    msg_to_delete = source_message
    if msg_to_delete is None and case.get("court_channel_id") and case.get("court_message_id"):
        src_ch = guild.get_channel(int(case["court_channel_id"]))
        if isinstance(src_ch, discord.TextChannel):
            try:
                msg_to_delete = await src_ch.fetch_message(int(case["court_message_id"]))
            except Exception:
                msg_to_delete = None
    if msg_to_delete is not None:
        try:
            await msg_to_delete.delete()
        except Exception:
            pass
    return arch_msg
