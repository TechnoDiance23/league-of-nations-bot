"""Peace & Crisis Summits system for war mediation, ceasefire talks, and bilateral/multilateral diplomacy."""
from datetime import datetime, timezone
import io
import re
import discord
from discord import app_commands
from discord.ext import commands

from utils.storage import (
    countries_storage,
    delegates_storage,
    signatories_storage,
    sticky_roles_storage,
    summits_storage,
    tickets_storage,
    treaties_storage,
)
from utils.constants import (
    LEADERSHIP_ROLES,
    ROLE_ADMIN,
    ROLE_COUNCIL_CHAIR,
    ROLE_FOUNDING,
    ROLE_GA_PRESIDENT,
    ROLE_MODERATOR,
    ROLE_SG,
    ROLE_SUMMIT_CLAIMANT,
    ROLE_SUMMIT_RESPONDENT,
    ROLE_SUSPENDED,
    STATUS_LABELS,
    can_member_represent_state_diplomacy,
    can_member_sign_for_state,
    resolve_panel_channel,
    validate_country_name,
)
from utils.un_gazette import (
    canonical_sovereign_stem,
    ensure_country_record,
    generate_country_short,
    get_country_record,
    normalize_country_key,
    publish_to_gazette,
    save_country_record,
    upsert_panel_message,
)
from cogs.delegation import (
    _country_role_name,
    _find_existing_charter_signature,
    _nation_match_tokens,
    _nation_matches_tokens,
    _propagate_country_status_to_members,
    _update_sticky_status_snapshot,
    ensure_role,
    mark_bot_role_op,
    resolve_member,
    save_sticky_snapshot,
    sync_signatories_channel,
)
from cogs.treaties_icj import TreatyActionView, treaty_embed

SUMMIT_CATEGORY_NAME = "🕊️ BARIŞ & KRİZ ZİRVELERİ · PEACE SUMMITS"
SUMMIT_ARCHIVE_CATEGORY_NAME = "📂 ARŞİVLER · ARCHIVES"
SUMMIT_PANEL_TITLES = [
    "🕊️ BARIŞ & KRİZ ZİRVESİ MASASI",
    "🕊️ PEACE & CRISIS SUMMIT DESK",
]

SUMMIT_TYPES = {
    "peace": ("🕊️ Barış Zirvesi · Peace Summit", 0x10B981),
    "sanction": ("🚨 Yaptırım & Tahkim Zirvesi · Sanction Summit", 0xDC2626),
    "ceasefire": ("🛑 Acil Ateşkes Masası · Urgent Ceasefire", 0xEF4444),
    "mediation": ("⚖️ Cemiyet Arabuluculuğu · League Mediation", 0xD97706),
    "multilateral": ("🌐 Çok Taraflı Konferans · Multilateral Crisis", 0x0284C7),
}

SUMMIT_STANCE_SPECS = {
    "plaintiff": ("⚖️ Davacı Taraf · Claimant", ROLE_SUMMIT_CLAIMANT, 0x0284C7),
    "defendant": ("🚨 Davalı Taraf · Respondent", ROLE_SUMMIT_RESPONDENT, 0xDC2626),
    "neutral": ("🕊️ Müzakere / Barış Tarafı", None, 0x10B981),
}

SUMMIT_STATUS_LABELS = {
    "ACTIVE": "🟢 Görüşmeler Sürüyor · Active Talks",
    "PEACE_REACHED": "🕊️ Barış / Ateşkes Sağlandı · Peace Reached",
    "SUSPENDED": "⏸️ Uzlaşma Sağlanamadı · Talks Suspended",
    "CLOSED": "🔒 Kapatıldı & Arşivlendi · Closed",
}

SUMMIT_LEADERSHIP_NAMES = tuple(
    dict.fromkeys(
        list(LEADERSHIP_ROLES)
        + [
            ROLE_MODERATOR,
            "🏛️ MC Genel Sekreteri",
            "🏛️ Cemiyet Yönetimi & Divan",
            "Admin",
        ]
    )
)


def _slug(name: str) -> str:
    tr_map = str.maketrans("çğıöşüÇĞİÖŞÜ ", "cgiosuCGIOSU-")
    cleaned = "".join(c for c in name.strip().translate(tr_map).lower() if c.isalnum() or c == "-")
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")
    return cleaned[:22] or "taraf"


def is_summit_convener(member) -> bool:
    """Return True if member is Secretariat, Council/GA leadership, Moderator, or Guild Admin."""
    guild = getattr(member, "guild", None)
    if guild is None or not hasattr(member, "roles"):
        return False
    perms = getattr(member, "guild_permissions", None)
    if getattr(member, "id", None) == guild.owner_id or bool(
        getattr(perms, "administrator", False) or getattr(perms, "manage_channels", False)
    ):
        return True
    if {r.name for r in member.roles} & set(SUMMIT_LEADERSHIP_NAMES):
        return True
    rec = delegates_storage.get(str(member.id), {})
    return isinstance(rec, dict) and rec.get("office_code") in {"SG", "DSG", "GKB", "GKB-V", "ŞB"}


def _can_manage_existing_summit(member, summit: dict) -> bool:
    """Convener leadership, designated mediators, or original summit opener may manage participants/close."""
    if is_summit_convener(member):
        return True
    uid = str(getattr(member, "id", ""))
    if uid == str(summit.get("opened_by", "")):
        return True
    return uid in {str(x) for x in summit.get("mediator_user_ids", [])}


def _next_numbered_id(store_data: dict, prefix: str) -> str:
    year = datetime.utcnow().year
    tag = f"{prefix}-{year}/"
    max_seq = 0
    for key in store_data:
        if isinstance(key, str) and key.startswith(tag):
            tail = key[len(tag):]
            if tail.isdigit():
                max_seq = max(max_seq, int(tail))
    return f"{tag}{max_seq + 1:03d}"


def _find_summit_by_channel(guild_id: int | None, channel_id: int | None, code: str | None = None) -> tuple[str | None, dict | None]:
    data = summits_storage.read()
    if code:
        norm = code.strip().upper()
        rec = data.get(norm)
        if isinstance(rec, dict) and rec.get("guild_id") == guild_id:
            return norm, rec
        return None, None
    for sid, rec in data.items():
        if not isinstance(rec, dict) or rec.get("guild_id") != guild_id:
            continue
        if rec.get("text_channel_id") == channel_id or rec.get("voice_channel_id") == channel_id:
            return sid, rec
    return None, None


_ROLE_MENTION_RE = re.compile(r"^<@&(\d+)>$")
_USER_MENTION_RE = re.compile(r"^<@!?(\d+)>$")


def _list_known_guild_nations(guild: discord.Guild | None) -> list[str]:
    """Return deduplicated canonical nation names known in guild roles, charter signatories, and country dossiers."""
    by_stem: dict[str, str] = {}
    candidates: list[str] = []
    for s_rec in signatories_storage.read().values():
        if isinstance(s_rec, dict) and s_rec.get("nation"):
            candidates.append(s_rec["nation"])
    for c_rec in countries_storage.read().values():
        if isinstance(c_rec, dict) and c_rec.get("name"):
            candidates.append(c_rec["name"])
    if guild:
        for role in getattr(guild, "roles", ()):
            if role.name.startswith("🏳️ "):
                candidates.append(role.name[2:].strip())
    for name in candidates:
        clean = (name or "").strip()
        if not clean:
            continue
        stem = canonical_sovereign_stem(clean) or normalize_country_key(clean)
        if stem and stem not in by_stem:
            by_stem[stem] = clean
    return sorted(by_stem.values(), key=normalize_country_key)


async def _extract_country_query_from_input(guild: discord.Guild, raw: str) -> tuple[str | None, discord.Role | None, str]:
    """Accept plain country names, @Country role mentions (<@&id>), @Member mentions (<@id>), or @text."""
    text = (raw or "").strip()
    if not text:
        return None, None, "❌ Ülke adı boş olamaz."

    m_role = _ROLE_MENTION_RE.match(text)
    if m_role:
        rid = int(m_role.group(1))
        role = guild.get_role(rid) if hasattr(guild, "get_role") else discord.utils.get(guild.roles, id=rid)
        if not role:
            return None, None, "❌ Etiketlediğiniz rol sunucuda bulunamadı."
        r_clean = role.name[2:].strip() if role.name.startswith("🏳️") else role.name.strip()
        return r_clean, (role if role.name.startswith("🏳️ ") else None), ""

    m_user = _USER_MENTION_RE.match(text)
    if m_user:
        uid = int(m_user.group(1))
        member = await resolve_member(guild, uid)
        rec = delegates_storage.get(str(uid))
        if isinstance(rec, dict) and rec.get("nation"):
            return rec["nation"], None, ""
        if member:
            for r in getattr(member, "roles", ()):
                if r.name.startswith("🏳️ "):
                    return r.name[2:].strip(), r, ""
        return None, None, f"❌ <@{uid}> üyesinin kayıtlı bir ülkesi bulunamadı."

    cleaned = text.lstrip("@").strip()
    if cleaned.startswith("🏳️"):
        cleaned = cleaned[2:].strip()
    return cleaned, None, ""


def _fuzzy_match_existing_nation(query: str, pool: list[str]) -> str | None:
    """Match exact key, stem (e.g. Azerbaycan -> Azərbaycan Cümhuriyyəti), or unique token/prefix."""
    q_key = normalize_country_key(query)
    if not q_key:
        return None
    keys, stems = _nation_match_tokens(query)
    for cand in pool:
        if _nation_matches_tokens(cand, keys, stems):
            return cand
    if len(q_key) >= 3:
        prefix_hits = [
            cand
            for cand in pool
            if normalize_country_key(cand).startswith(q_key)
            or any(tok.startswith(q_key) for tok in normalize_country_key(cand).split())
        ]
        if len(prefix_hits) == 1:
            return prefix_hits[0]
    return None


async def _resolve_country_party(guild: discord.Guild, raw_name: str) -> tuple[bool, dict | None, str]:
    """Validate and resolve a country name (or @role / @member mention) to its canonical name, dossier, and 🏳️ role."""
    extracted, explicit_role, err_ext = await _extract_country_query_from_input(guild, raw_name)
    if err_ext or not extracted:
        return False, None, err_ext or "❌ Geçerli bir ülke adı veya ülke rolü etiketi girin."

    # Check known server nations/roles first so @mentions and fuzzy country names resolve immediately
    pool = _list_known_guild_nations(guild)
    matched_name = _fuzzy_match_existing_nation(extracted, pool)
    if matched_name:
        cleaned = matched_name
    elif explicit_role is not None:
        cleaned = extracted
    else:
        ok, cleaned, err = validate_country_name(extracted)
        if not ok:
            return False, None, err

    keys, stems = _nation_match_tokens(cleaned)

    # 1. Reuse canonical/matching 🏳️ role on Discord (preferring signatory spelling when multiple exist)
    matched_role = explicit_role
    sig_canonical = None
    for s_rec in signatories_storage.read().values():
        if isinstance(s_rec, dict) and s_rec.get("nation") and _nation_matches_tokens(s_rec["nation"], keys, stems):
            sig_canonical = s_rec["nation"]
            break

    role_matches: list[tuple[str, discord.Role]] = []
    for role in guild.roles:
        if role.name.startswith("🏳️ "):
            r_nation = role.name[2:].strip()
            if _nation_matches_tokens(r_nation, keys, stems):
                role_matches.append((r_nation, role))

    if role_matches:
        preferred = next((pair for pair in role_matches if sig_canonical and pair[0] == sig_canonical), role_matches[0])
        cleaned, matched_role = preferred
    elif sig_canonical:
        cleaned = sig_canonical

    crec = ensure_country_record(cleaned)
    canonical_nation = sig_canonical or crec.get("name") or cleaned
    short = crec.get("short") or generate_country_short(canonical_nation)
    if matched_role is None:
        matched_role = await ensure_role(guild, _country_role_name(canonical_nation), 0x0EA5E9)

    return True, {
        "nation": canonical_nation,
        "key": normalize_country_key(canonical_nation),
        "short": short,
        "role_id": matched_role.id if matched_role else None,
    }, ""


async def _find_party_in_summit(guild: discord.Guild, summit: dict, raw_query: str) -> dict | None:
    """Resolve a party entry inside summit using plain name, @role mention, @member mention, or fuzzy prefix."""
    extracted, explicit_role, err_ext = await _extract_country_query_from_input(guild, raw_query)
    parties = summit.get("parties") or []
    if explicit_role:
        for p in parties:
            if p.get("role_id") == explicit_role.id:
                return p
    if err_ext or not extracted:
        return None
    matched = _fuzzy_match_existing_nation(extracted, [p["nation"] for p in parties])
    if matched:
        for p in parties:
            if p["nation"] == matched:
                return p
    return None


def _collect_party_member_ids(nation: str) -> list[int]:
    """Return accredited/registered user IDs in delegates_storage matching nation."""
    keys, stems = _nation_match_tokens(nation)
    out: list[int] = []
    for uid, rec in delegates_storage.read().items():
        if not isinstance(rec, dict) or not str(uid).isdigit():
            continue
        if _nation_matches_tokens(rec.get("nation", ""), keys, stems):
            out.append(int(uid))
    return out


def _member_belongs_to_country(member, rec: dict | None, nation: str, key: str = "", role_id: int | None = None) -> bool:
    """Return True if member belongs to `nation` via delegate record or Discord country role."""
    candidate_nations: list[str] = []
    if isinstance(rec, dict) and rec.get("nation"):
        candidate_nations.append(rec["nation"])
    for role in getattr(member, "roles", ()):
        if role.name.startswith("🏳️ "):
            candidate_nations.append(role.name[2:].strip())
    p_keys, p_stems = _nation_match_tokens(nation)
    if key:
        p_keys.add(key)
    if any(_nation_matches_tokens(cn, p_keys, p_stems) for cn in candidate_nations):
        return True
    if role_id and any(getattr(r, "id", None) == role_id for r in getattr(member, "roles", ())):
        return True
    return False


def _member_matching_party(member, rec: dict | None, summit: dict) -> dict | None:
    """Return the party dict matching member's registered nation or Discord 🏳️ role."""
    for party in summit.get("parties") or []:
        if _member_belongs_to_country(
            member,
            rec,
            party["nation"],
            party.get("key", ""),
            party.get("role_id"),
        ):
            return party
    return None


async def _get_or_create_private_category(guild: discord.Guild, category_name: str) -> discord.CategoryChannel:
    """Return existing private leadership/summit category or create one with @everyone hidden."""
    cat = discord.utils.get(guild.categories, name=category_name)
    if cat:
        return cat
    everyone = getattr(guild, "default_role", None) or discord.utils.get(guild.roles, name="@everyone")
    overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {}
    if everyone:
        overwrites[everyone] = discord.PermissionOverwrite(view_channel=False)
    if guild.me:
        overwrites[guild.me] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            manage_channels=True,
            manage_messages=True,
            read_message_history=True,
            connect=True,
            speak=True,
            move_members=True,
        )
    for rname in SUMMIT_LEADERSHIP_NAMES:
        role = discord.utils.get(guild.roles, name=rname)
        if role:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                connect=True,
                speak=True,
            )
    return await guild.create_category(category_name, overwrites=overwrites)


async def _get_or_create_summit_category(guild: discord.Guild) -> discord.CategoryChannel:
    return await _get_or_create_private_category(guild, SUMMIT_CATEGORY_NAME)


async def _get_or_create_archive_category(guild: discord.Guild) -> discord.CategoryChannel:
    return await _get_or_create_private_category(guild, SUMMIT_ARCHIVE_CATEGORY_NAME)


async def archive_summit_channel(
    guild: discord.Guild,
    summit: dict,
    *,
    reason: str = "",
) -> discord.CategoryChannel | None:
    """Move a summit text channel to `📂 ARŞİVLER · ARCHIVES` with read-only party permissions."""
    archive_cat = await _get_or_create_archive_category(guild)
    text_ow, _ = await _build_summit_overwrites(guild, summit, read_only_parties=True)
    tch = guild.get_channel(int(summit["text_channel_id"])) if summit.get("text_channel_id") else None
    if tch and hasattr(tch, "edit"):
        try:
            await tch.edit(
                category=archive_cat,
                overwrites=text_ow,
                reason=reason or f"MC Zirve arşive alındı ({summit.get('id', '-')})",
            )
        except Exception:
            await _apply_summit_overwrites(guild, summit, read_only_parties=True)
    return archive_cat


async def _build_summit_overwrites(
    guild: discord.Guild,
    summit: dict,
    *,
    read_only_parties: bool = False,
) -> tuple[dict, dict]:
    """Return (text_overwrites, voice_overwrites) scoped strictly to leadership, mediators, and summit parties."""
    everyone = getattr(guild, "default_role", None) or discord.utils.get(guild.roles, name="@everyone")
    text_ow: dict = {}
    voice_ow: dict = {}

    if everyone:
        text_ow[everyone] = discord.PermissionOverwrite(view_channel=False, send_messages=False)
        voice_ow[everyone] = discord.PermissionOverwrite(view_channel=False, connect=False)

    if guild.me:
        text_ow[guild.me] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            manage_channels=True,
            manage_messages=True,
            read_message_history=True,
            attach_files=True,
            embed_links=True,
        )
        voice_ow[guild.me] = discord.PermissionOverwrite(
            view_channel=True,
            connect=True,
            speak=True,
            stream=True,
            mute_members=True,
            deafen_members=True,
            move_members=True,
            manage_channels=True,
        )

    for rname in SUMMIT_LEADERSHIP_NAMES:
        role = discord.utils.get(guild.roles, name=rname)
        if role:
            text_ow[role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                manage_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
            )
            voice_ow[role] = discord.PermissionOverwrite(
                view_channel=True,
                connect=True,
                speak=True,
                stream=True,
                mute_members=True,
                move_members=True,
            )

    party_text_ow = discord.PermissionOverwrite(
        view_channel=True,
        send_messages=not read_only_parties,
        read_message_history=True,
        attach_files=not read_only_parties,
        embed_links=not read_only_parties,
    )
    party_voice_ow = discord.PermissionOverwrite(
        view_channel=not read_only_parties,
        connect=not read_only_parties,
        speak=not read_only_parties,
        stream=not read_only_parties,
    )

    seen_party_role_ids: set[int] = set()
    for party in summit.get("parties", []):
        p_keys, p_stems = _nation_match_tokens(party["nation"])
        matched_roles: list[discord.Role] = []
        if party.get("role_id") and hasattr(guild, "get_role"):
            r_id_obj = guild.get_role(int(party["role_id"]))
            if r_id_obj:
                matched_roles.append(r_id_obj)
        for r in guild.roles:
            if r.name.startswith("🏳️ ") and _nation_matches_tokens(r.name[2:].strip(), p_keys, p_stems):
                if r not in matched_roles:
                    matched_roles.append(r)
        for role in matched_roles:
            seen_party_role_ids.add(role.id)
            text_ow[role] = party_text_ow
            voice_ow[role] = party_voice_ow

        # Ensure registered members of this country whose 🏳️ role may not be attached yet still have access
        for uid in _collect_party_member_ids(party["nation"])[:20]:
            member = await resolve_member(guild, uid)
            if member:
                has_role = any(getattr(r, "id", None) in seen_party_role_ids for r in getattr(member, "roles", ()))
                if not has_role:
                    text_ow[member] = party_text_ow
                    voice_ow[member] = party_voice_ow

    # Direct invitees (extra delegates / mediators / summit opener)
    direct_uids = {str(x) for x in summit.get("invited_user_ids", [])} | {
        str(x) for x in summit.get("mediator_user_ids", [])
    }
    if summit.get("opened_by"):
        direct_uids.add(str(summit["opened_by"]))

    for uid_str in direct_uids:
        if not str(uid_str).isdigit():
            continue
        member = await resolve_member(guild, int(uid_str))
        if not member:
            continue
        is_mediator = uid_str in {str(x) for x in summit.get("mediator_user_ids", [])} or uid_str == str(summit.get("opened_by"))
        text_ow[member] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True if is_mediator else (not read_only_parties),
            read_message_history=True,
            attach_files=True if is_mediator else (not read_only_parties),
            embed_links=True if is_mediator else (not read_only_parties),
        )
        voice_ow[member] = discord.PermissionOverwrite(
            view_channel=True,
            connect=True if is_mediator else (not read_only_parties),
            speak=True if is_mediator else (not read_only_parties),
            stream=True if is_mediator else (not read_only_parties),
        )

    return text_ow, voice_ow


async def _apply_summit_overwrites(guild: discord.Guild, summit: dict, *, read_only_parties: bool = False) -> None:
    text_ow, voice_ow = await _build_summit_overwrites(guild, summit, read_only_parties=read_only_parties)
    tch = guild.get_channel(int(summit["text_channel_id"])) if summit.get("text_channel_id") else None
    if tch and hasattr(tch, "edit"):
        try:
            await tch.edit(overwrites=text_ow, reason=f"MC Zirve izin senkronizasyonu ({summit['id']})")
        except Exception:
            pass
    vch = guild.get_channel(int(summit["voice_channel_id"])) if summit.get("voice_channel_id") else None
    if vch and hasattr(vch, "edit"):
        try:
            await vch.edit(overwrites=voice_ow, reason=f"MC Zirve ses izin senkronizasyonu ({summit['id']})")
        except Exception:
            pass


async def sync_guild_summit_stance_roles(guild: discord.Guild) -> dict:
    """Assign ⚖️ Davacı Taraf / 🚨 Davalı Taraf roles to all members of active party nations and clean up stale ones."""
    claimant_role: discord.Role | None = discord.utils.get(guild.roles, name=ROLE_SUMMIT_CLAIMANT)
    respondent_role: discord.Role | None = discord.utils.get(guild.roles, name=ROLE_SUMMIT_RESPONDENT)

    desired_by_uid: dict[int, set[str]] = {}
    tracked_uids: set[int] = set()

    # Include anyone currently holding either role (cache + role.members), sticky snapshot, or any guild summit party
    for role in (claimant_role, respondent_role):
        if role:
            for m in getattr(role, "members", ()):
                tracked_uids.add(m.id)
    for m in getattr(guild, "members", ()):
        if any(r.name in {ROLE_SUMMIT_CLAIMANT, ROLE_SUMMIT_RESPONDENT} for r in getattr(m, "roles", ())):
            tracked_uids.add(m.id)
    for srec in summits_storage.read().values():
        if isinstance(srec, dict) and srec.get("guild_id") == guild.id:
            for p in srec.get("parties", []):
                tracked_uids.update(_collect_party_member_ids(p.get("nation", "")))
    for key, snap in sticky_roles_storage.read().items():
        if not isinstance(snap, dict) or snap.get("guild_id") != guild.id:
            continue
        rnames = set(snap.get("role_names") or [])
        if (rnames & {ROLE_SUMMIT_CLAIMANT, ROLE_SUMMIT_RESPONDENT}) and str(snap.get("user_id", "")).isdigit():
            tracked_uids.add(int(snap["user_id"]))

    # Compute desired stance role(s) across every ACTIVE summit
    for srec in summits_storage.read().values():
        if not isinstance(srec, dict) or srec.get("guild_id") != guild.id or srec.get("status") != "ACTIVE":
            continue
        for p in srec.get("parties", []):
            stance = p.get("stance", "neutral")
            spec = SUMMIT_STANCE_SPECS.get(stance)
            role_name = spec[1] if spec else None
            p_nation = p.get("nation", "")
            p_keys, p_stems = _nation_match_tokens(p_nation)

            member_uids: set[int] = set(_collect_party_member_ids(p_nation))
            for r in guild.roles:
                if r.name.startswith("🏳️ ") and _nation_matches_tokens(r.name[2:].strip(), p_keys, p_stems):
                    for m in getattr(r, "members", ()):
                        member_uids.add(m.id)
            if p.get("role_id") and hasattr(guild, "get_role"):
                p_role = guild.get_role(int(p["role_id"]))
                if p_role:
                    for m in getattr(p_role, "members", ()):
                        member_uids.add(m.id)
            for snap in sticky_roles_storage.read().values():
                if not isinstance(snap, dict) or snap.get("guild_id") != guild.id or not str(snap.get("user_id", "")).isdigit():
                    continue
                if any(
                    rn.startswith("🏳️ ") and _nation_matches_tokens(rn[2:].strip(), p_keys, p_stems)
                    for rn in (snap.get("role_names") or [])
                ):
                    member_uids.add(int(snap["user_id"]))

            for uid in member_uids:
                tracked_uids.add(uid)
                bucket = desired_by_uid.setdefault(uid, set())
                if role_name:
                    bucket.add(role_name)

    if any(ROLE_SUMMIT_CLAIMANT in s for s in desired_by_uid.values()) or claimant_role is not None:
        claimant_role = await ensure_role(guild, ROLE_SUMMIT_CLAIMANT, 0x0284C7) or claimant_role
    if any(ROLE_SUMMIT_RESPONDENT in s for s in desired_by_uid.values()) or respondent_role is not None:
        respondent_role = await ensure_role(guild, ROLE_SUMMIT_RESPONDENT, 0xDC2626) or respondent_role

    founding_role = discord.utils.get(guild.roles, name=ROLE_FOUNDING)
    if founding_role and hasattr(guild, "me") and getattr(guild.me, "top_role", None):
        target_pos = min(founding_role.position + 1, max(1, guild.me.top_role.position - 1))
        for sr in (claimant_role, respondent_role):
            if sr is not None and hasattr(sr, "edit") and sr.position < target_pos:
                try:
                    await sr.edit(position=target_pos, reason="MC Zirve taraf rollerini görünür sıraya taşı")
                except Exception:
                    pass

    role_by_name = {
        ROLE_SUMMIT_CLAIMANT: claimant_role,
        ROLE_SUMMIT_RESPONDENT: respondent_role,
    }

    claimant_uids: list[str] = []
    respondent_uids: list[str] = []
    removed_uids: list[str] = []

    for uid in sorted(tracked_uids):
        want_names = desired_by_uid.get(uid, set())
        member = await resolve_member(guild, uid)
        if not member:
            if sticky_roles_storage.get(f"{guild.id}_{uid}"):
                off_rem = [r for rn, r in role_by_name.items() if r is not None and rn not in want_names]
                if off_rem:
                    _update_sticky_status_snapshot(guild, uid, add_role=None, remove_roles=off_rem, member=None)
            continue

        to_add = [r for rn, r in role_by_name.items() if r is not None and rn in want_names and r not in member.roles]
        to_rem = [r for rn, r in role_by_name.items() if r is not None and rn not in want_names and r in member.roles]
        removed_ids: set[int] = set()

        if to_rem:
            mark_bot_role_op(guild.id, uid)
            try:
                await member.remove_roles(*to_rem, reason="MC Zirve Taraf Statüsü Güncellemesi")
                removed_ids.update(r.id for r in to_rem)
                removed_uids.append(str(uid))
            except Exception:
                pass
        if to_add:
            mark_bot_role_op(guild.id, uid)
            try:
                await member.add_roles(*to_add, reason="MC Zirve Taraf Statüsü Ataması (Davacı/Davalı)")
            except Exception:
                pass
        if to_add or to_rem:
            try:
                refreshed = await guild.fetch_member(uid)
            except Exception:
                refreshed = await resolve_member(guild, uid) or member
            save_sticky_snapshot(refreshed, active=True, resigned_role_ids=removed_ids, update_locked=True)

        if ROLE_SUMMIT_CLAIMANT in want_names:
            claimant_uids.append(str(uid))
        if ROLE_SUMMIT_RESPONDENT in want_names:
            respondent_uids.append(str(uid))

    return {"claimants": claimant_uids, "respondents": respondent_uids, "removed": removed_uids}


def build_summit_header_embed(summit: dict) -> discord.Embed:
    type_label, type_col = SUMMIT_TYPES.get(summit.get("summit_type", "peace"), ("🕊️ Barış Zirvesi · Peace Summit", 0x10B981))
    status_label = SUMMIT_STATUS_LABELS.get(summit.get("status", "ACTIVE"), summit.get("status", "ACTIVE"))
    parties_lines: list[str] = []
    for p in summit.get("parties", []):
        st_code = p.get("stance", "neutral")
        st_label = SUMMIT_STANCE_SPECS.get(st_code, SUMMIT_STANCE_SPECS["neutral"])[0]
        st_role = SUMMIT_STANCE_SPECS.get(st_code, SUMMIT_STANCE_SPECS["neutral"])[1]
        badge = f" — **{st_label}**" if st_code != "neutral" or st_role else ""
        parties_lines.append(
            f"• 🏳️ **{p['nation']}** (`{p.get('short', '')}`)"
            + (f" (<@&{p['role_id']}>)" if p.get("role_id") else "")
            + badge
        )
    parties_text = "\n".join(parties_lines) or "*Yok*"
    mediators = [f"<@{u}>" for u in summit.get("mediator_user_ids", [])]
    invited = [f"<@{u}>" for u in summit.get("invited_user_ids", [])]
    voice_line = f"<#{summit['voice_channel_id']}>" if summit.get("voice_channel_id") else "*Açılmadı / Disabled*"

    color = 0x10B981 if summit.get("status") == "PEACE_REACHED" else (0x6B7280 if summit.get("status") in {"SUSPENDED", "CLOSED"} else type_col)
    embed = discord.Embed(
        title=f"🕊️ BARIŞ & KRİZ ZİRVESİ · {summit['id']}",
        description=(
            f"**Başlık / Title:** **{summit['title']}**\n"
            f"**Tür / Format:** {type_label}\n"
            f"**Durum / Status:** {status_label}\n"
            f"**Gündem / Sebep:** {summit['reason']}\n"
            f"**Toplantı Açan / Convened by:** <@{summit['opened_by']}>\n"
            f"**Sesli Zirve Salonu / Voice Hall:** {voice_line}\n"
            + (f"**Sonuç Notu / Outcome:** `{summit['outcome_note']}`\n" if summit.get("outcome_note") else "")
            + "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "*Bu özel konferans odası yalnızca taraf devlet delegasyonları, atanan arabulucular ve Cemiyet Divanı'na açıktır.*\n"
            "*Taraflar alttaki **`Ateşkes / Barış Sun`** butonuyla yazılı barış/ateşkes protokolü sunup imzalayabilir.*"
        ),
        colour=color,
    )
    embed.add_field(name=f"🏳️ Taraf Devletler · Participating States ({len(summit.get('parties', []))})", value=parties_text[:1024], inline=False)
    if mediators or invited:
        extra_bits = []
        if mediators:
            extra_bits.append("**Arabulucular / Mediators:** " + ", ".join(mediators))
        if invited:
            extra_bits.append("**Özel Davetliler / Invited:** " + ", ".join(invited))
        embed.add_field(name="🤝 Katılımcılar & Arabulucular · Envoys", value="\n".join(extra_bits)[:1024], inline=False)
    embed.set_footer(text=f"Milletler Cemiyeti Barış Konferansı · {summit['id']}")
    return embed


def build_accord_embed(summit: dict, acc: dict) -> discord.Embed:
    party_total = len(summit.get("parties", []))
    accepted_labels = acc.get("accepted_labels") or []
    accepted_keys = set(acc.get("accepted_keys") or [])
    pending_names = [p["nation"] for p in summit.get("parties", []) if p["key"] not in accepted_keys]
    rejected_labels = acc.get("rejected_labels") or []

    status = acc.get("status", "PROPOSED")
    if status == "SIGNED":
        st_text = "🕊️ **TÜM TARAFLARCA İMZALANDI / AGREED & RATIFIED**"
        col = 0x10B981
    elif status == "REJECTED":
        st_text = "❌ **REDDEDİLDİ / REVİZYON İSTENDİ**"
        col = 0xEF4444
    else:
        st_text = f"✍️ **İMZA BEKLİYOR ({len(accepted_keys)}/{party_total} Taraf)**"
        col = 0xD97706

    embed = discord.Embed(
        title=f"📜 ATEŞKES & BARIŞ TASLAĞI · {summit['id']} / {acc['accord_id']}",
        description=(
            f"**Başlık / Title:** **{acc['title']}**\n"
            f"**Sunan / Proposed by:** <@{acc['proposed_by_uid']}> (`{acc.get('proposed_by_nation', 'Cemiyet / Mediator')}`)\n"
            f"**Durum / Status:** {st_text}\n\n"
            f"**Şartlar & Maddeler / Peace Terms:**\n```text\n{acc['terms']}\n```\n"
            + (f"**📜 Resmî Antlaşma Kaydı:** `{acc['treaty_id']}` *(Antlaşmalar kütüğüne işlendi)*\n" if acc.get("treaty_id") else "")
            + "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ),
        colour=col,
    )
    embed.add_field(
        name=f"✅ Kabul & İmza Edenler ({len(accepted_labels)}/{party_total})",
        value="\n".join(f"• {lbl}" for lbl in accepted_labels)[:1024] or "*Henüz imzalanmadı / Pending*",
        inline=False,
    )
    if pending_names and status == "PROPOSED":
        embed.add_field(
            name="⏳ İmza Beklenen Taraflar · Pending Parties",
            value=", ".join(f"`{n}`" for n in pending_names)[:1024],
            inline=False,
        )
    if rejected_labels:
        embed.add_field(
            name="⚠️ Revizyon / Ret Bildirenler",
            value="\n".join(f"• {lbl}" for lbl in rejected_labels)[:1024],
            inline=False,
        )
    embed.set_footer(text=f"Milletler Cemiyeti Zirve Protokolü · {acc['accord_id']}")
    return embed


async def _refresh_summit_panel_message(guild: discord.Guild, summit: dict) -> None:
    tch = guild.get_channel(int(summit["text_channel_id"])) if summit.get("text_channel_id") else None
    msg_id = summit.get("panel_message_id")
    if not tch or not msg_id or not hasattr(tch, "fetch_message"):
        return
    try:
        msg = await tch.fetch_message(int(msg_id))
        view = SummitControlView(summit["id"])
        if summit.get("status") != "ACTIVE":
            for item in view.children:
                item.disabled = True
        await msg.edit(embed=build_summit_header_embed(summit), view=view)
    except Exception:
        pass


async def create_peace_summit(
    guild: discord.Guild,
    bot: commands.Bot | discord.Client,
    *,
    opener: discord.Member,
    raw_parties: list[str],
    reason: str,
    summit_type: str = "peace",
    party_stances: list[str] | None = None,
    mediator: discord.Member | None = None,
    open_voice: bool = True,
    custom_title: str | None = None,
) -> tuple[bool, dict | None, str]:
    """Validate parties, create private Summit text+voice channels, persist dossier, sync stance roles, and notify Gazette."""
    resolved_parties: list[dict] = []
    seen_keys: set[str] = set()
    stype = summit_type if summit_type in SUMMIT_TYPES else "peace"

    for idx, raw in enumerate(raw_parties):
        raw_clean = (raw or "").strip()
        if not raw_clean:
            continue
        ok, party, err = await _resolve_country_party(guild, raw_clean)
        if not ok or not party:
            return False, None, err
        if party["key"] in seen_keys:
            continue
        seen_keys.add(party["key"])
        explicit_stance = party_stances[idx] if party_stances and idx < len(party_stances) and party_stances[idx] in SUMMIT_STANCE_SPECS else None
        if explicit_stance:
            party["stance"] = explicit_stance
        elif stype == "sanction":
            party["stance"] = "plaintiff" if len(resolved_parties) == 0 else ("defendant" if len(resolved_parties) == 1 else "neutral")
        else:
            party["stance"] = "neutral"
        resolved_parties.append(party)

    if len(resolved_parties) < 2:
        return False, None, "❌ Barış/kriz zirvesi için **en az 2 farklı ülke** belirtmelisiniz."

    reason_clean = (reason or "").strip() or "Savaş sebebiyle ateşkes ve barış görüşmesi"
    suffix = "Yaptırım & Tahkim Zirvesi" if stype == "sanction" else "Barış Zirvesi"
    title_clean = (custom_title or "").strip() or (
        f"{' ↔ '.join(p['nation'] for p in resolved_parties[:3])}"
        + (f" (+{len(resolved_parties) - 3})" if len(resolved_parties) > 3 else "")
        + f" {suffix}"
    )

    summit_id = _next_numbered_id(summits_storage.read(), "ZRV")
    mediator_ids = [str(mediator.id)] if mediator and not getattr(mediator, "bot", False) else []

    summit_record: dict = {
        "id": summit_id,
        "guild_id": guild.id,
        "title": title_clean[:120],
        "reason": reason_clean[:500],
        "summit_type": summit_type if summit_type in SUMMIT_TYPES else "peace",
        "text_channel_id": None,
        "voice_channel_id": None,
        "panel_message_id": None,
        "parties": resolved_parties,
        "invited_user_ids": [],
        "mediator_user_ids": mediator_ids,
        "opened_by": str(opener.id),
        "opened_by_name": opener.display_name,
        "status": "ACTIVE",
        "active": True,
        "accords": {},
        "created_at": datetime.utcnow().strftime("%d.%m.%Y %H:%M UTC"),
        "closed_at": None,
        "outcome_note": None,
    }

    category = await _get_or_create_summit_category(guild)
    text_ow, voice_ow = await _build_summit_overwrites(guild, summit_record)

    slug_part = "-".join(_slug(p["nation"]) for p in resolved_parties[:2])
    text_name = f"🕊️│zirve-{slug_part}"[:90]
    text_topic = f"{summit_id} · {title_clean[:120]} · Sebep: {reason_clean[:180]}"

    text_ch = await guild.create_text_channel(
        text_name,
        category=category,
        topic=text_topic,
        overwrites=text_ow,
        reason=f"MC Barış Zirvesi Açılışı ({summit_id})",
    )
    summit_record["text_channel_id"] = text_ch.id

    voice_ch = None
    if open_voice:
        short_pair = " ↔ ".join(p["short"] for p in resolved_parties[:3])
        voice_name = f"🕊️│Zirve · {short_pair}"[:90]
        voice_ch = await guild.create_voice_channel(
            voice_name,
            category=category,
            overwrites=voice_ow,
            reason=f"MC Barış Zirvesi Ses Salonu ({summit_id})",
        )
        summit_record["voice_channel_id"] = voice_ch.id

    ctrl_view = SummitControlView(summit_id)
    if hasattr(bot, "add_view"):
        bot.add_view(ctrl_view)

    panel_msg = await text_ch.send(embed=build_summit_header_embed(summit_record), view=ctrl_view)
    summit_record["panel_message_id"] = panel_msg.id
    summits_storage.set(summit_id, summit_record)
    await sync_guild_summit_stance_roles(guild)

    if hasattr(panel_msg, "pin"):
        try:
            await panel_msg.pin(reason="MC Zirve Kontrol Paneli")
        except Exception:
            pass

    pings = " ".join(f"<@&{p['role_id']}>" for p in resolved_parties if p.get("role_id"))
    med_ping = (" · **Arabulucu:** " + " ".join(f"<@{m}>" for m in mediator_ids)) if mediator_ids else ""
    try:
        await text_ch.send(
            f"📣 **MİLLETLER CEMİYETİ BARIŞ & KRİZ ZİRVESİ ÇAĞRISI (`{summit_id}`)**\n"
            f"{pings}{med_ping}\n"
            f"• **Sebep / Gündem:** {reason_clean}\n"
            + (f"• **Sesli Zirve Salonu:** <#{voice_ch.id}>\n" if voice_ch else "")
            + "*Lütfen delegasyonlarınız adına ateşkes ve barış koşullarını görüşün; anlaşmaya vardığınızda **`🕊️ Ateşkes / Barış Sun`** butonunu kullanın.*"
        )
    except Exception:
        pass

    party_names_str = " ↔ ".join(p["nation"] for p in resolved_parties)
    await publish_to_gazette(
        bot,
        guild.id,
        "BARIŞ ZİRVESİ AÇILDI · PEACE SUMMIT CONVENED",
        f"{summit_id} · {title_clean}",
        (
            f"**Taraflar / Parties:** `{party_names_str}`\n"
            f"**Gündem / Sebep:** {reason_clean}\n"
            f"**Toplantı Açan / Convened by:** <@{opener.id}>\n"
            f"**Konferans Odası:** <#{text_ch.id}>" + (f" · 🔊 <#{voice_ch.id}>" if voice_ch else "")
        ),
        color=SUMMIT_TYPES.get(summit_type, ("", 0x10B981))[1],
        issuer=f"{opener.display_name} · Milletler Cemiyeti Zirve Masası",
    )

    return True, summit_record, ""


async def _promote_signed_accord_to_treaty(
    guild: discord.Guild,
    bot: commands.Bot | discord.Client,
    summit: dict,
    acc: dict,
) -> str | None:
    """Create a ratified 'peace' treaty record in treaties_storage & #antlasmalar-treaties when all parties sign."""
    tid = _next_numbered_id(treaties_storage.read(), "TRT")
    party_nations = [p["nation"] for p in summit.get("parties", [])]
    party_keys = [p["key"] for p in summit.get("parties", [])]
    sponsor_nation = acc.get("proposed_by_nation") or (party_nations[0] if party_nations else "Milletler Cemiyeti")

    record = {
        "id": tid,
        "category": "peace",
        "title": f"{acc['title']} ({summit['id']})",
        "parties": " ↔ ".join(party_nations),
        "text": acc["terms"],
        "sponsor": sponsor_nation,
        "signed": list(party_keys),
        "signed_names": list(party_nations),
        "ratified": list(party_keys),
        "ratified_names": list(party_nations),
        "withdrawn": [],
        "withdrawn_names": [],
        "guild_id": guild.id,
        "channel_id": None,
        "summit_id": summit["id"],
        "created_at": datetime.utcnow().strftime("%d.%m.%Y %H:%M UTC"),
    }

    for nation in party_nations:
        crec = get_country_record(nation)
        crec["stats"]["treaties_signed"] = crec["stats"].get("treaties_signed", 0) + 1
        save_country_record(crec)

    treaties_ch = resolve_panel_channel(guild, None, "antlasmalar-treaties", None)
    jump_url = ""
    if treaties_ch and hasattr(treaties_ch, "send"):
        view = TreatyActionView(tid)
        if hasattr(bot, "add_view"):
            bot.add_view(view)
        try:
            t_msg = await treaties_ch.send(embed=treaty_embed(record), view=view)
            record["message_id"] = t_msg.id
            record["channel_id"] = treaties_ch.id
            jump_url = f"\n• **Antlaşma Kütüğü:** {t_msg.jump_url}"
        except Exception:
            pass

    treaties_storage.set(tid, record)
    await publish_to_gazette(
        bot,
        guild.id,
        "BARIŞ ANTLAŞMASI İMZALANDI · SUMMIT PEACE TREATY",
        f"{tid} · {acc['title']}",
        (
            f"**Zirve / Summit:** `{summit['id']}`\n"
            f"**İmzalayan Devletler / Signatories:** `{' ↔ '.join(party_nations)}`\n"
            f"**Özet / Terms:** {acc['terms'][:900]}{jump_url}"
        ),
        color=0x10B981,
        issuer="Milletler Cemiyeti · Barış Konferansı",
    )
    return tid


async def _finalize_summit_close(
    interaction: discord.Interaction,
    *,
    summit_id: str,
    mode: str,
    outcome_note: str | None = None,
) -> None:
    """Close/archive or delete a summit cleanly and notify Gazette (expects deferred interaction)."""
    summit = summits_storage.get(summit_id)
    if not isinstance(summit, dict) or summit.get("guild_id") != interaction.guild_id:
        return await interaction.followup.send("❌ Zirve kaydı bulunamadı.", ephemeral=True)

    if not _can_manage_existing_summit(interaction.user, summit):
        return await interaction.followup.send(
            "⛔ Zirveyi yalnızca **Genel Sekreterlik / Divan / Moderatörler**, atanan arabulucu veya zirveyi açan yetkili sonuçlandırabilir.",
            ephemeral=True,
        )

    guild = interaction.guild
    tch = guild.get_channel(int(summit["text_channel_id"])) if summit.get("text_channel_id") else None
    vch = guild.get_channel(int(summit["voice_channel_id"])) if summit.get("voice_channel_id") else None

    if mode == "peace_archive":
        new_status = "PEACE_REACHED"
        default_note = "Taraflar ateşkes / barış mutabakatına vararak zirveyi tamamladı."
        gaz_tag = "BARIŞ ZİRVESİ SONUCU · PEACE REACHED"
        gaz_col = 0x10B981
    elif mode == "failed_archive":
        new_status = "SUSPENDED"
        default_note = "Görüşmelerde uzlaşma sağlanamadığı için zirve askıya alındı."
        gaz_tag = "ZİRVE SONUÇSUZ KAPANDI · TALKS SUSPENDED"
        gaz_col = 0xF59E0B
    else:
        new_status = "CLOSED"
        default_note = "Zirve kapatıldı ve tutanak arşive kaldırıldı."
        gaz_tag = "ZİRVE KAPATILDI · SUMMIT CLOSED"
        gaz_col = 0x6B7280

    final_note = (outcome_note or "").strip() or default_note
    summit["status"] = new_status
    summit["active"] = False
    summit["closed_at"] = datetime.utcnow().strftime("%d.%m.%Y %H:%M UTC")
    summit["closed_by"] = str(interaction.user.id)
    summit["outcome_note"] = final_note
    summits_storage.set(summit_id, summit)
    await sync_guild_summit_stance_roles(guild)

    # Archive transcript if deleting channels (mirrors cogs/tickets.py)
    if mode == "delete_archive" and tch:
        lines = [
            f"=== MİLLETLER CEMİYETİ ZİRVE TUTANAĞI ({summit_id}) ===",
            f"Başlık: {summit['title']}",
            f"Taraflar: {', '.join(p['nation'] for p in summit.get('parties', []))}",
            f"Gündem: {summit['reason']}",
            f"Kapanış Notu: {final_note}",
            "=" * 55,
        ]
        if hasattr(tch, "history"):
            try:
                async for msg in tch.history(limit=200, oldest_first=True):
                    ts = msg.created_at.strftime("%d.%m.%Y %H:%M") if msg.created_at else "-"
                    body = msg.content or "[Embed / Ek]"
                    lines.append(f"[{ts}] {msg.author.display_name}: {body}")
            except Exception:
                lines.append("[Mesaj geçmişi okunamadı]")

        log_cfg = tickets_storage.get(f"log_config_{guild.id}", {})
        log_ch = guild.get_channel(log_cfg.get("channel_id")) if isinstance(log_cfg, dict) and log_cfg.get("channel_id") else None
        if not log_ch:
            log_ch = discord.utils.find(lambda c: "ticket-log" in c.name.lower(), guild.text_channels)
        if log_ch and hasattr(log_ch, "send"):
            try:
                await log_ch.send(
                    embed=discord.Embed(
                        title=f"🕊️ Zirve Arşivi · {summit_id} ({summit['title']})",
                        description=f"**Taraflar:** `{' ↔ '.join(p['nation'] for p in summit.get('parties', []))}`\n**Sonuç:** {final_note}",
                        colour=gaz_col,
                    ),
                    file=discord.File(io.BytesIO("\n".join(lines).encode("utf-8")), filename=f"{summit_id}_transcript.txt"),
                )
            except Exception:
                pass

    party_str = " ↔ ".join(p["nation"] for p in summit.get("parties", []))
    issue_no = await publish_to_gazette(
        interaction.client,
        guild.id,
        gaz_tag,
        f"{summit_id} · {summit['title']}",
        f"**Taraflar / Parties:** `{party_str}`\n**Sonuç / Outcome:** {final_note}\n**Kapatan Yetkili:** <@{interaction.user.id}>",
        color=gaz_col,
        issuer=f"{interaction.user.display_name} · Milletler Cemiyeti Zirve Masası",
    )

    if mode == "delete_archive":
        await interaction.followup.send(
            f"🗑️ **{summit_id}** zirvesi kapatıldı, tutanak arşivlendi ve odalar temizleniyor"
            + (f" (Resmî Gazete `{issue_no}`)." if issue_no else "."),
            ephemeral=True,
        )
        if vch and hasattr(vch, "delete"):
            try:
                await vch.delete(reason=f"MC Zirve Kapanışı ({summit_id})")
            except Exception:
                pass
        if tch and hasattr(tch, "delete"):
            try:
                await tch.delete(reason=f"MC Zirve Kapanışı ({summit_id})")
            except Exception:
                pass
    else:
        await archive_summit_channel(guild, summit, reason=f"MC Zirve Arşive Alındı ({summit_id})")
        if vch and hasattr(vch, "delete"):
            try:
                await vch.delete(reason=f"MC Zirve Arşivlendi — Geçici ses salonu kapatıldı ({summit_id})")
            except Exception:
                pass
        await _refresh_summit_panel_message(guild, summit)
        if tch and hasattr(tch, "send"):
            try:
                await tch.send(
                    embed=discord.Embed(
                        title=f"{SUMMIT_STATUS_LABELS.get(new_status, new_status)} · {summit_id}",
                        description=(
                            f"**Sonuç:** {final_note}\n"
                            f"**Kapatan:** <@{interaction.user.id}>\n"
                            + (f"**Resmî Gazete:** `{issue_no}`\n" if issue_no else "")
                            + "*Konferans odası salt okunur arşive alınmıştır; geçmiş mesajlar taraflarca görüntülenebilir.*"
                        ),
                        colour=gaz_col,
                    )
                )
            except Exception:
                pass
        await interaction.followup.send(
            f"✅ **{summit_id}** `{SUMMIT_STATUS_LABELS.get(new_status)}` statüsüyle arşivlendi"
            + (f" ve Resmî Gazete'de (`{issue_no}`) yayınlandı." if issue_no else "."),
            ephemeral=True,
        )


class SummitAccordModal(discord.ui.Modal, title="Ateşkes / Barış Taslağı Sun · Accord"):
    accord_title = discord.ui.TextInput(
        label="Taslak Başlığı / Accord Title",
        placeholder="Örn: Acil Ateşkes & Sınır Barış Protokolü",
        required=True,
        max_length=120,
    )
    accord_terms = discord.ui.TextInput(
        label="Ateşkes & Barış Şartları / Peace Terms",
        style=discord.TextStyle.paragraph,
        placeholder="Madde 1: Derhal ateşkes ilan edilir...\nMadde 2: Çekilen birlikler...",
        required=True,
        max_length=1800,
    )

    def __init__(self, summit_id: str = "ZRV-2026/001"):
        super().__init__()
        self.summit_id = summit_id

    async def on_submit(self, interaction: discord.Interaction):
        summit = summits_storage.get(self.summit_id)
        if not isinstance(summit, dict) or summit.get("status") != "ACTIVE":
            return await interaction.response.send_message("❌ Bu zirve artık aktif değil.", ephemeral=True)

        rec = delegates_storage.get(str(interaction.user.id))
        rec_dict = rec if isinstance(rec, dict) else None
        party = _member_matching_party(interaction.user, rec_dict, summit)
        is_allowed_diplomat = bool(party and can_member_represent_state_diplomacy(interaction.user, rec_dict))
        if not (_can_manage_existing_summit(interaction.user, summit) or is_allowed_diplomat):
            return await interaction.response.send_message(
                "⛔ Taslak sunma yetkisi yalnızca **taraf devletlerin yetkili diplomatlarına**, **Genel Sekreterliğe / Divana** veya **arabuluculara** aittir.",
                ephemeral=True,
            )

        await interaction.response.defer(ephemeral=True, thinking=True)
        accords = summit.setdefault("accords", {})
        accord_id = f"ATK-{len(accords) + 1:02d}"
        proposer_nation = party["nation"] if party else "Milletler Cemiyeti / Arabulucu"

        acc_record = {
            "accord_id": accord_id,
            "title": self.accord_title.value.strip(),
            "terms": self.accord_terms.value.strip(),
            "proposed_by_uid": str(interaction.user.id),
            "proposed_by_nation": proposer_nation,
            "accepted_keys": [],
            "accepted_labels": [],
            "rejected_labels": [],
            "status": "PROPOSED",
            "treaty_id": None,
            "message_id": None,
            "created_at": datetime.utcnow().isoformat(),
        }

        vote_view = SummitAccordVoteView(self.summit_id, accord_id)
        if hasattr(interaction.client, "add_view"):
            interaction.client.add_view(vote_view)

        tch = interaction.guild.get_channel(int(summit["text_channel_id"])) if summit.get("text_channel_id") else interaction.channel
        msg = await tch.send(embed=build_accord_embed(summit, acc_record), view=vote_view)
        acc_record["message_id"] = getattr(msg, "id", None)
        accords[accord_id] = acc_record
        summits_storage.set(self.summit_id, summit)

        await interaction.followup.send(f"✅ **{accord_id}** numaralı ateşkes/barış taslağı zirve masasına sunuldu!", ephemeral=True)


class SummitAccordVoteView(discord.ui.View):
    def __init__(self, summit_id: str, accord_id: str):
        super().__init__(timeout=None)
        self.summit_id = summit_id
        self.accord_id = accord_id

        btn_sign = discord.ui.Button(
            label="Kabul Et / İmzala · Sign",
            style=discord.ButtonStyle.success,
            emoji="✍️",
            custom_id=f"mc_sum_acc_sign:{summit_id}:{accord_id}",
        )
        btn_sign.callback = self._on_sign
        self.add_item(btn_sign)

        btn_rej = discord.ui.Button(
            label="Reddet / Revizyon · Reject",
            style=discord.ButtonStyle.danger,
            emoji="❌",
            custom_id=f"mc_sum_acc_rej:{summit_id}:{accord_id}",
        )
        btn_rej.callback = self._on_reject
        self.add_item(btn_rej)

    async def _validate_party_signer(self, interaction: discord.Interaction) -> tuple[dict, dict, dict, dict | None] | None:
        summit = summits_storage.get(self.summit_id)
        if not isinstance(summit, dict) or summit.get("status") != "ACTIVE":
            await interaction.response.send_message("❌ Bu zirve artık aktif değil.", ephemeral=True)
            return None
        acc = (summit.get("accords") or {}).get(self.accord_id)
        if not isinstance(acc, dict) or acc.get("status") != "PROPOSED":
            await interaction.response.send_message("ℹ️ Bu taslak zaten sonuçlandırılmış.", ephemeral=True)
            return None

        rec = delegates_storage.get(str(interaction.user.id))
        rec_dict = rec if isinstance(rec, dict) else None
        party = _member_matching_party(interaction.user, rec_dict, summit)
        if not party:
            await interaction.response.send_message(
                "⛔ Taslağı yalnızca **bu zirvenin taraf devletlerinden birine mensup yetkili diplomatlar** imzalayabilir veya reddedebilir.",
                ephemeral=True,
            )
            return None
        is_admin = bool(getattr(getattr(interaction.user, "guild_permissions", None), "administrator", False))
        if not (can_member_represent_state_diplomacy(interaction.user, rec_dict) or is_admin):
            await interaction.response.send_message(
                "⛔ **İmza Yetkisi Yetersiz:** Barış/ateşkes protokolünü taraf devlet adına yalnızca akredite diplomatik temsilciler (**Devlet Başkanı, Bakanlar, Temsilciler, Büyükelçi**) imzalayabilir.",
                ephemeral=True,
            )
            return None
        return summit, acc, party, rec_dict

    async def _on_sign(self, interaction: discord.Interaction):
        validated = await self._validate_party_signer(interaction)
        if not validated:
            return
        summit, acc, party, rec_dict = validated
        pkey = party["key"]
        if pkey in acc.setdefault("accepted_keys", []):
            return await interaction.response.send_message(f"ℹ️ **{party['nation']}** bu taslağı zaten imzaladı.", ephemeral=True)

        await interaction.response.defer(ephemeral=True, thinking=True)
        office_tag = (rec_dict or {}).get("official_title") or (rec_dict or {}).get("office_code") or "Yetkili Temsilci"
        acc["accepted_keys"].append(pkey)
        acc.setdefault("accepted_labels", []).append(f"**{party['nation']}** — <@{interaction.user.id}> (`{office_tag}`)")

        all_party_keys = {p["key"] for p in summit.get("parties", [])}
        all_signed = bool(all_party_keys) and all_party_keys <= set(acc["accepted_keys"])

        if all_signed:
            acc["status"] = "SIGNED"
            for item in self.children:
                item.disabled = True
            tid = await _promote_signed_accord_to_treaty(interaction.guild, interaction.client, summit, acc)
            acc["treaty_id"] = tid

        summits_storage.set(self.summit_id, summit)
        try:
            if interaction.message and hasattr(interaction.message, "edit"):
                await interaction.message.edit(embed=build_accord_embed(summit, acc), view=self)
        except Exception:
            pass

        if all_signed:
            tch = interaction.guild.get_channel(int(summit["text_channel_id"])) if summit.get("text_channel_id") else interaction.channel
            if tch and hasattr(tch, "send"):
                try:
                    await tch.send(
                        f"🕊️ **TÜM TARAFLAR İMZALADI · BARIŞ PROTOKOLÜ KABUL EDİLDİ (`{acc['accord_id']}`)**\n"
                        f"• Anlaşma **`{acc.get('treaty_id')}`** numarasıyla `#📜│antlasmalar-treaties` ve Resmî Gazete'ye işlenmiştir.\n"
                        f"*Yetkili yönetici hazır olduğunda **`🔒 Zirveyi Sonuçlandır · Close`** butonundan zirveyi Barış Sağlandı olarak kapatabilir.*"
                    )
                except Exception:
                    pass
            await interaction.followup.send(f"🕊️ **{party['nation']}** imzasıyla tüm taraflar anlaştı! Barış antlaşması (`{acc.get('treaty_id')}`) Resmî Gazete'ye işlendi.", ephemeral=True)
        else:
            await interaction.followup.send(f"✍️ **{party['nation']}** adına taslak (`{self.accord_id}`) imzalandı; diğer taraf(lar) bekleniyor.", ephemeral=True)

    async def _on_reject(self, interaction: discord.Interaction):
        validated = await self._validate_party_signer(interaction)
        if not validated:
            return
        summit, acc, party, _rec_dict = validated
        await interaction.response.defer(ephemeral=True, thinking=True)
        acc["status"] = "REJECTED"
        acc.setdefault("rejected_labels", []).append(f"**{party['nation']}** — <@{interaction.user.id}>")
        for item in self.children:
            item.disabled = True
        summits_storage.set(self.summit_id, summit)
        try:
            if interaction.message and hasattr(interaction.message, "edit"):
                await interaction.message.edit(embed=build_accord_embed(summit, acc), view=self)
        except Exception:
            pass
        await interaction.followup.send(f"❌ **{party['nation']}** adına `{self.accord_id}` taslağı reddedildi; yeni bir taslak sunabilirsiniz.", ephemeral=True)


async def set_nation_suspension_state(
    guild: discord.Guild | None,
    *,
    nation: str,
    suspended: bool,
    restore_status: str = "MEMBER",
    reason: str = "",
) -> tuple[str, str, int]:
    """Suspend a country (⛔ Askıya Alınmış) or restore its tier across JSON registries and live Discord roles."""
    crec = get_country_record(nation)
    old_status = crec.get("status", "MEMBER")
    target_status = "SUSPENDED" if suspended else (restore_status if restore_status in {"FOUNDING", "MEMBER", "OBSERVER"} else "MEMBER")
    crec["status"] = target_status
    crec["sanction_note"] = (reason or None) if suspended else None
    save_country_record(crec)

    sig_sid, sig_rec = _find_existing_charter_signature(crec["name"])
    if sig_sid and isinstance(sig_rec, dict):
        if suspended:
            sig_rec["status_note"] = (reason or "Tazminat ödenene dek Askıda")[:120]
        else:
            sig_rec["status"] = target_status
            sig_rec["tier"] = STATUS_LABELS.get(target_status, sig_rec.get("tier", ""))
            sig_rec["status_note"] = "Tazminat Ödendi · Statü İade Edildi"
        signatories_storage.set(sig_sid, sig_rec)
        if guild:
            try:
                await sync_signatories_channel(guild)
            except Exception:
                pass

    updated = await _propagate_country_status_to_members(
        guild,
        nation=crec["name"],
        country_status=target_status,
        sign_id=sig_sid,
    )
    return old_status, target_status, updated


def build_reparations_panel_text(summit: dict, rep: dict) -> str:
    """Format a plain-text (non-embed) interactive escrow confirmation status block."""
    payer_ok = bool(rep.get("payer_confirmed"))
    payee_ok = bool(rep.get("payee_confirmed"))
    completed = rep.get("status") == "COMPLETED"
    disputed = bool(rep.get("disputed"))

    payer_state = (
        f"✅ Ödedim dedi (<@{rep['payer_by']}> · `{rep.get('payer_at', '-')}`)"
        if payer_ok
        else "⏳ Henüz `Ödedim` onayını vermedi"
    )
    payee_state = (
        f"✅ Teslim Aldım / Onaylıyorum dedi (<@{rep['payee_by']}> · `{rep.get('payee_at', '-')}`)"
        if payee_ok
        else (
            f"❌ **ÖDENMEDİ İTİRAZI** (<@{rep['last_dispute_by']}> · `{rep.get('last_dispute_at', '-')}`)"
            if disputed
            else "⏳ Henüz `Teslim Aldım` onayını vermedi"
        )
    )
    state_line = (
        f"🟢 **TAMAMLANDI:** İki taraf da teyit ettiği için **`{rep['payer_nation']}`** üzerindeki askı kaldırıldı ve `{rep.get('restore_status', 'MEMBER')}` statüsü iade edildi."
        if completed
        else f"⛔ **ASKIDA (`{ROLE_SUSPENDED}`):** **`{rep['payer_nation']}`** devleti tazminat ödenip her iki taraf da alttaki butonlardan **`Ödedim` + `Teslim Aldım`** onayını verene dek askıda kalmaya devam eder."
    )

    return (
        f"**⚖️ TAZMİNAT VE BARIŞ İCRA ONAY BÖLÜMÜ (`{rep['rep_id']}` · `{summit['id']}`)**\n"
        f"• **Yükümlü Taraf (Ödeyen):** `{rep['payer_nation']}`\n"
        f"• **Hak Sahibi Taraf (Alacaklı):** `{rep['payee_nation']}`\n"
        f"• **Kesin Hüküm:** {rep['amount_label']}\n"
        f"• **Ödeyen Onayı (`💰 Ödedim`):** {payer_state}\n"
        f"• **Alacaklı Onayı (`✅ Teslim Aldım` / `❌ Ödenmedi`):** {payee_state}\n"
        f"• **Devlet Statüsü:** {state_line}"
    )[:1990]


class SummitReparationsView(discord.ui.View):
    """Persistent dual-confirmation escrow buttons keeping defendant suspended until paid and confirmed."""

    def __init__(self, summit_id: str, rep_id: str):
        super().__init__(timeout=None)
        self.summit_id = summit_id
        self.rep_id = rep_id

        self.btn_paid = discord.ui.Button(
            label="💰 Ödedim (Nova Roma)",
            style=discord.ButtonStyle.primary,
            custom_id=f"mc_rep_paid:{summit_id}:{rep_id}",
        )
        self.btn_paid.callback = self._on_paid
        self.add_item(self.btn_paid)

        self.btn_recv = discord.ui.Button(
            label="✅ Teslim Aldım / Ödendi (Azərbaycan)",
            style=discord.ButtonStyle.success,
            custom_id=f"mc_rep_recv:{summit_id}:{rep_id}",
        )
        self.btn_recv.callback = self._on_recv
        self.add_item(self.btn_recv)

        self.btn_unpaid = discord.ui.Button(
            label="❌ Ödenmedi / İtiraz Et",
            style=discord.ButtonStyle.danger,
            custom_id=f"mc_rep_unpaid:{summit_id}:{rep_id}",
        )
        self.btn_unpaid.callback = self._on_unpaid
        self.add_item(self.btn_unpaid)

    def _load_records(self) -> tuple[dict | None, dict | None]:
        summit = summits_storage.get(self.summit_id)
        if not isinstance(summit, dict):
            return None, None
        rep = (summit.get("reparations") or {}).get(self.rep_id)
        if not isinstance(rep, dict):
            return summit, None
        return summit, rep

    def _is_authorized_for_side(self, user, rep: dict, side: str) -> tuple[bool, str]:
        rec = delegates_storage.get(str(user.id))
        rec_dict = rec if isinstance(rec, dict) else None
        is_admin = bool(getattr(getattr(user, "guild_permissions", None), "administrator", False))
        on_payer = _member_belongs_to_country(user, rec_dict, rep["payer_nation"], rep["payer_key"])
        on_payee = _member_belongs_to_country(user, rec_dict, rep["payee_nation"], rep["payee_key"])

        if side == "payer":
            if on_payer and (can_member_represent_state_diplomacy(user, rec_dict) or is_admin):
                return True, ""
            if is_admin and not on_payee:
                return True, ""
            return False, f"⛔ Bu butonu yalnızca **{rep['payer_nation']}** yetkili temsilcileri veya yönetici kullanabilir."

        if on_payee and (can_member_represent_state_diplomacy(user, rec_dict) or is_admin):
            return True, ""
        if is_admin and not on_payer:
            return True, ""
        return False, f"⛔ Bu butonu yalnızca **{rep['payee_nation']}** yetkili temsilcileri veya yönetici kullanabilir."

    async def _handle_confirm(self, interaction: discord.Interaction, *, side: str):
        summit, rep = self._load_records()
        if not summit or not rep:
            return await interaction.response.send_message("❌ Tazminat kaydı bulunamadı.", ephemeral=True)
        if rep.get("status") == "COMPLETED":
            return await interaction.response.send_message("ℹ️ Bu tazminat dosyası zaten onaylanıp tamamlanmıştır.", ephemeral=True)
        ok, err = self._is_authorized_for_side(interaction.user, rep, side)
        if not ok:
            return await interaction.response.send_message(err, ephemeral=True)

        await interaction.response.defer(ephemeral=True, thinking=True)
        now_str = datetime.utcnow().strftime("%d.%m.%Y %H:%M UTC")
        rep["disputed"] = False
        if side == "payer":
            rep["payer_confirmed"] = True
            rep["payer_by"] = str(interaction.user.id)
            rep["payer_at"] = now_str
        else:
            rep["payee_confirmed"] = True
            rep["payee_by"] = str(interaction.user.id)
            rep["payee_at"] = now_str

        both_confirmed = bool(rep.get("payer_confirmed") and rep.get("payee_confirmed"))
        guild = interaction.guild
        if both_confirmed:
            rep["status"] = "COMPLETED"
            rep["completed_at"] = now_str
            for item in self.children:
                item.disabled = True
            await set_nation_suspension_state(
                guild,
                nation=rep["payer_nation"],
                suspended=False,
                restore_status=rep.get("restore_status", "MEMBER"),
                reason="",
            )

        summit.setdefault("reparations", {})[self.rep_id] = rep
        summits_storage.set(self.summit_id, summit)
        panel_txt = build_reparations_panel_text(summit, rep)
        try:
            if interaction.message and hasattr(interaction.message, "edit"):
                await interaction.message.edit(content=panel_txt, view=self)
        except Exception:
            pass

        ch = interaction.channel
        if both_confirmed:
            notice = (
                f"✅ **TAZMİNAT ÖDEMESİ TAMAMLANDI (`{self.rep_id}`)**\n"
                f"Hem **{rep['payer_nation']}** (`Ödedim`: <@{rep['payer_by']}>) hem de **{rep['payee_nation']}** (`Teslim Aldım`: <@{rep['payee_by']}>) onay verdiği için **{rep['payer_nation']}** üzerindeki `⛔ Askıda` statüsü kaldırılmış ve normal Cemiyet statüsü iade edilmiştir."
            )
            if ch and hasattr(ch, "send"):
                try:
                    await ch.send(content=notice)
                except Exception:
                    pass
            gaz_ch = resolve_panel_channel(guild, None, "resmi-gazete-official-gazette", None)
            if gaz_ch and hasattr(gaz_ch, "send"):
                try:
                    await gaz_ch.send(
                        content=(
                            f"⚖️ **RESMÎ GAZETE BİLDİRİSİ — TAZMİNAT TAMAMLANDI & ASKI KALDIRILDI (`{self.rep_id}`)**\n"
                            f"**{rep['payer_nation']}** tarafından **{rep['payee_nation']}** devletine hükmedilen tazminat ödemesi her iki tarafın karşılıklı onayıyla doğrulanmıştır. "
                            f"**{rep['payer_nation']}** devleti üzerindeki `⛔ Askıya Alınmış · Suspended` tedbiri kaldırılarak olağan üyelik statüsü geri verilmiştir."
                        )
                    )
                except Exception:
                    pass
            audit_ch = resolve_panel_channel(guild, None, "denetim-kaydi-audit-log", None)
            if audit_ch and hasattr(audit_ch, "send"):
                try:
                    await audit_ch.send(content=notice)
                except Exception:
                    pass
            await interaction.followup.send("✅ İki taraf da teyit ettiği için askı kaldırıldı ve devlet statüsü normale döndürüldü.", ephemeral=True)
        else:
            side_msg = (
                f"💰 **{rep['payer_nation']}** (`Ödedim` — <@{interaction.user.id}>) ödemeyi yaptığını bildirdi. **{rep['payee_nation']}** (`✅ Teslim Aldım`) onaylayana dek askı statüsü devam eder."
                if side == "payer"
                else f"✅ **{rep['payee_nation']}** (`Teslim Aldım` — <@{interaction.user.id}>) onay verdi. **{rep['payer_nation']}** da `💰 Ödedim` butonunu onayladığında askı kaldırılacaktır."
            )
            if ch and hasattr(ch, "send"):
                try:
                    await ch.send(content=side_msg)
                except Exception:
                    pass
            await interaction.followup.send(side_msg, ephemeral=True)

    async def _on_paid(self, interaction: discord.Interaction):
        await self._handle_confirm(interaction, side="payer")

    async def _on_recv(self, interaction: discord.Interaction):
        await self._handle_confirm(interaction, side="payee")

    async def _on_unpaid(self, interaction: discord.Interaction):
        summit, rep = self._load_records()
        if not summit or not rep:
            return await interaction.response.send_message("❌ Tazminat kaydı bulunamadı.", ephemeral=True)
        if rep.get("status") == "COMPLETED":
            return await interaction.response.send_message("ℹ️ Bu tazminat dosyası zaten tamamlanmıştır.", ephemeral=True)
        ok, err = self._is_authorized_for_side(interaction.user, rep, "payee")
        if not ok:
            return await interaction.response.send_message(err, ephemeral=True)

        await interaction.response.defer(ephemeral=True, thinking=True)
        now_str = datetime.utcnow().strftime("%d.%m.%Y %H:%M UTC")
        rep["payer_confirmed"] = False
        rep["payee_confirmed"] = False
        rep["disputed"] = True
        rep["dispute_count"] = int(rep.get("dispute_count", 0)) + 1
        rep["last_dispute_by"] = str(interaction.user.id)
        rep["last_dispute_at"] = now_str

        await set_nation_suspension_state(
            interaction.guild,
            nation=rep["payer_nation"],
            suspended=True,
            restore_status=rep.get("restore_status", "MEMBER"),
            reason=f"{rep['amount_label']} ödenmedi / teyit edilmedi ({self.rep_id})",
        )
        summit.setdefault("reparations", {})[self.rep_id] = rep
        summits_storage.set(self.summit_id, summit)
        try:
            if interaction.message and hasattr(interaction.message, "edit"):
                await interaction.message.edit(content=build_reparations_panel_text(summit, rep), view=self)
        except Exception:
            pass

        msg_txt = (
            f"❌ **ÖDENMEDİ İTİRAZI (`{self.rep_id}`)** — <@{interaction.user.id}> (**{rep['payee_nation']}**) ödemenin/teslimatın gerçekleşmediğini bildirdi.\n"
            f"**{rep['payer_nation']}**, tazminat fiilen ödenip her iki taraf da **`Ödedim` + `Teslim Aldım`** onayını verene dek **`⛔ Askıya Alınmış · Suspended`** statüsünde kalmaya devam edecektir."
        )
        if interaction.channel and hasattr(interaction.channel, "send"):
            try:
                await interaction.channel.send(content=msg_txt)
            except Exception:
                pass
        audit_ch = resolve_panel_channel(interaction.guild, None, "denetim-kaydi-audit-log", None)
        if audit_ch and hasattr(audit_ch, "send"):
            try:
                await audit_ch.send(content=msg_txt)
            except Exception:
                pass
        await interaction.followup.send(f"⛔ İtiraz işlendi; **{rep['payer_nation']}** askıda tutulmaya devam ediyor.", ephemeral=True)


async def create_reparations_escrow(
    guild: discord.Guild,
    bot: commands.Bot | discord.Client,
    *,
    summit_id: str,
    channel: discord.abc.Messageable,
    payer_nation: str,
    payee_nation: str,
    amount_label: str,
    suspend_reason: str,
) -> dict:
    """Create persistent escrow confirmation buttons and suspend payer nation until both parties confirm."""
    summit = summits_storage.get(summit_id)
    if not isinstance(summit, dict):
        raise ValueError(f"Unknown summit {summit_id}")
    all_reps = {
        rid: rrec
        for srec in summits_storage.read().values()
        if isinstance(srec, dict)
        for rid, rrec in (srec.get("reparations") or {}).items()
    }
    rep_id = _next_numbered_id(all_reps, "TAZ")
    payer_rec = get_country_record(payer_nation)
    payee_rec = get_country_record(payee_nation)
    restore_status = payer_rec.get("status", "MEMBER")
    if restore_status == "SUSPENDED":
        restore_status = "MEMBER"

    await set_nation_suspension_state(
        guild,
        nation=payer_rec["name"],
        suspended=True,
        restore_status=restore_status,
        reason=suspend_reason,
    )

    rep_rec = {
        "rep_id": rep_id,
        "summit_id": summit_id,
        "guild_id": guild.id,
        "channel_id": getattr(channel, "id", None),
        "message_id": None,
        "payer_nation": payer_rec["name"],
        "payer_key": payer_rec["key"],
        "payee_nation": payee_rec["name"],
        "payee_key": payee_rec["key"],
        "amount_label": amount_label,
        "restore_status": restore_status,
        "payer_confirmed": False,
        "payer_by": None,
        "payer_at": None,
        "payee_confirmed": False,
        "payee_by": None,
        "payee_at": None,
        "disputed": False,
        "dispute_count": 0,
        "last_dispute_by": None,
        "last_dispute_at": None,
        "status": "PENDING_PAYMENT",
        "created_at": datetime.utcnow().strftime("%d.%m.%Y %H:%M UTC"),
        "completed_at": None,
    }
    view = SummitReparationsView(summit_id, rep_id)
    if hasattr(bot, "add_view"):
        bot.add_view(view)
    msg = await channel.send(content=build_reparations_panel_text(summit, rep_rec), view=view)
    rep_rec["message_id"] = getattr(msg, "id", None)
    summit.setdefault("reparations", {})[rep_id] = rep_rec
    summits_storage.set(summit_id, summit)
    return rep_rec


class SummitAddTargetModal(discord.ui.Modal, title="Zirveye Taraf / Üye Ekle · Add Party"):
    nation_input = discord.ui.TextInput(
        label="Eklenecek Ülke Adı (Opsiyonel)",
        placeholder="Örn: İtalya / İngiltere",
        required=False,
        max_length=80,
    )
    member_input = discord.ui.TextInput(
        label="Üye ID veya @Etiket (Opsiyonel)",
        placeholder="Örn: 123456789012345678 veya <@123...>",
        required=False,
        max_length=60,
    )

    def __init__(self, summit_id: str = "ZRV-2026/001"):
        super().__init__()
        self.summit_id = summit_id

    async def on_submit(self, interaction: discord.Interaction):
        summit = summits_storage.get(self.summit_id)
        if not isinstance(summit, dict) or summit.get("status") != "ACTIVE":
            return await interaction.response.send_message("❌ Bu zirve artık aktif değil.", ephemeral=True)
        if not _can_manage_existing_summit(interaction.user, summit):
            return await interaction.response.send_message("⛔ Taraf veya üye ekleme yetkiniz yok.", ephemeral=True)

        raw_nation = (self.nation_input.value or "").strip()
        raw_member = (self.member_input.value or "").strip()
        if not raw_nation and not raw_member:
            return await interaction.response.send_message("❌ En az bir **ülke adı** veya **üye ID** girmelisiniz.", ephemeral=True)

        target_member = None
        if raw_member:
            digits = "".join(ch for ch in raw_member if ch.isdigit())
            if not digits:
                return await interaction.response.send_message("❌ Geçerli bir sayısal Discord Kullanıcı ID'si girin.", ephemeral=True)
            target_member = await resolve_member(interaction.guild, int(digits))
            if not target_member:
                return await interaction.response.send_message("❌ Bu kullanıcı sunucuda bulunamadı.", ephemeral=True)

        await interaction.response.defer(ephemeral=True, thinking=True)
        msg = await _add_target_to_summit(interaction.guild, summit, raw_nation=raw_nation or None, member=target_member, actor=interaction.user)
        await interaction.followup.send(msg, ephemeral=True)


async def _add_target_to_summit(
    guild: discord.Guild,
    summit: dict,
    *,
    raw_nation: str | None,
    member: discord.Member | None,
    actor: discord.Member,
) -> str:
    added_notes: list[str] = []
    if raw_nation:
        ok, party, err = await _resolve_country_party(guild, raw_nation)
        if not ok or not party:
            return err
        existing_keys = {p["key"] for p in summit.get("parties", [])}
        if party["key"] in existing_keys:
            added_notes.append(f"`{party['nation']}` zaten zirveye taraf.")
        else:
            summit.setdefault("parties", []).append(party)
            added_notes.append(f"🏳️ **{party['nation']}** zirveye taraf olarak eklendi")

    if member:
        uid = str(member.id)
        invited = {str(x) for x in summit.get("invited_user_ids", [])}
        if uid in invited:
            added_notes.append(f"<@{uid}> zaten davetli.")
        else:
            invited.add(uid)
            summit["invited_user_ids"] = sorted(invited)
            added_notes.append(f"🤝 <@{uid}> zirveye davet edildi")

    summits_storage.set(summit["id"], summit)
    await _apply_summit_overwrites(guild, summit)
    await _refresh_summit_panel_message(guild, summit)

    tch = guild.get_channel(int(summit["text_channel_id"])) if summit.get("text_channel_id") else None
    if tch and hasattr(tch, "send") and added_notes:
        try:
            await tch.send(f"➕ <@{actor.id}> tarafından zirve katılımı güncellendi: {' · '.join(added_notes)}")
        except Exception:
            pass
    return "✅ " + " · ".join(added_notes) if added_notes else "ℹ️ Değişiklik yapılmadı."


class SummitCloseChoiceView(discord.ui.View):
    def __init__(self, summit_id: str):
        super().__init__(timeout=120)
        self.summit_id = summit_id

    @discord.ui.button(label="Barış Sağlandı · Peace Archive", style=discord.ButtonStyle.success, emoji="🕊️")
    async def btn_peace(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        await _finalize_summit_close(interaction, summit_id=self.summit_id, mode="peace_archive")

    @discord.ui.button(label="Anlaşma Sağlanamadı · Suspend", style=discord.ButtonStyle.secondary, emoji="⏸️")
    async def btn_failed(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        await _finalize_summit_close(interaction, summit_id=self.summit_id, mode="failed_archive")

    @discord.ui.button(label="Odayı & Sesi Sil · Delete & Log", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def btn_delete(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        await _finalize_summit_close(interaction, summit_id=self.summit_id, mode="delete_archive")


class SummitControlView(discord.ui.View):
    def __init__(self, summit_id: str):
        super().__init__(timeout=None)
        self.summit_id = summit_id

        btn_accord = discord.ui.Button(
            label="Ateşkes / Barış Sun · Accord",
            style=discord.ButtonStyle.success,
            emoji="🕊️",
            custom_id=f"mc_sum_accord:{summit_id}",
        )
        btn_accord.callback = self._on_accord
        self.add_item(btn_accord)

        btn_add = discord.ui.Button(
            label="Taraf / Üye Ekle · Add Party",
            style=discord.ButtonStyle.primary,
            emoji="➕",
            custom_id=f"mc_sum_add:{summit_id}",
        )
        btn_add.callback = self._on_add
        self.add_item(btn_add)

        btn_ping = discord.ui.Button(
            label="Tarafları Çağır · Summon",
            style=discord.ButtonStyle.secondary,
            emoji="📢",
            custom_id=f"mc_sum_ping:{summit_id}",
        )
        btn_ping.callback = self._on_ping
        self.add_item(btn_ping)

        btn_close = discord.ui.Button(
            label="Zirveyi Sonuçlandır · Close",
            style=discord.ButtonStyle.danger,
            emoji="🔒",
            custom_id=f"mc_sum_close:{summit_id}",
        )
        btn_close.callback = self._on_close
        self.add_item(btn_close)

    async def _on_accord(self, interaction: discord.Interaction):
        summit = summits_storage.get(self.summit_id)
        if not isinstance(summit, dict) or summit.get("status") != "ACTIVE":
            return await interaction.response.send_message("❌ Bu zirve artık aktif değil.", ephemeral=True)
        rec = delegates_storage.get(str(interaction.user.id))
        rec_dict = rec if isinstance(rec, dict) else None
        party = _member_matching_party(interaction.user, rec_dict, summit)
        is_allowed_diplomat = bool(party and can_member_represent_state_diplomacy(interaction.user, rec_dict))
        if not (_can_manage_existing_summit(interaction.user, summit) or is_allowed_diplomat):
            return await interaction.response.send_message(
                "⛔ Taslak sunma yetkisi yalnızca **taraf devlet diplomatlarına**, **Genel Sekreterliğe / Divana** veya **arabuluculara** aittir.",
                ephemeral=True,
            )
        await interaction.response.send_modal(SummitAccordModal(self.summit_id))

    async def _on_add(self, interaction: discord.Interaction):
        summit = summits_storage.get(self.summit_id)
        if not isinstance(summit, dict) or summit.get("status") != "ACTIVE":
            return await interaction.response.send_message("❌ Bu zirve artık aktif değil.", ephemeral=True)
        if not _can_manage_existing_summit(interaction.user, summit):
            return await interaction.response.send_message(
                "⛔ Taraf veya üye ekleme yetkisi yalnızca yönetici/arabulucu veya zirve sahibine aittir.",
                ephemeral=True,
            )
        await interaction.response.send_modal(SummitAddTargetModal(self.summit_id))

    async def _on_ping(self, interaction: discord.Interaction):
        summit = summits_storage.get(self.summit_id)
        if not isinstance(summit, dict) or summit.get("status") != "ACTIVE":
            return await interaction.response.send_message("❌ Bu zirve artık aktif değil.", ephemeral=True)
        if not _can_manage_existing_summit(interaction.user, summit):
            return await interaction.response.send_message("⛔ Tarafları çağırma yetkisi yalnızca moderatör/arabulucu/yöneticilere aittir.", ephemeral=True)
        pings = " ".join(f"<@&{p['role_id']}>" for p in summit.get("parties", []) if p.get("role_id"))
        inv = " ".join(f"<@{u}>" for u in summit.get("invited_user_ids", []))
        v_note = f" · 🔊 <#{summit['voice_channel_id']}>" if summit.get("voice_channel_id") else ""
        await interaction.response.send_message(
            f"📣 **ZİRVE OTURUM ÇAĞRISI ({summit['id']}):** {pings} {inv}{v_note}\n"
            f"Barış/ateşkes müzakereleri için masaya bekleniyorsunuz *(Çağran: <@{interaction.user.id}>)*."
        )

    async def _on_close(self, interaction: discord.Interaction):
        summit = summits_storage.get(self.summit_id)
        if not isinstance(summit, dict):
            return await interaction.response.send_message("❌ Zirve kaydı bulunamadı.", ephemeral=True)
        if not _can_manage_existing_summit(interaction.user, summit):
            return await interaction.response.send_message(
                "⛔ Zirveyi yalnızca **Genel Sekreterlik / Divan / Moderatörler**, arabulucu veya zirveyi açan yetkili kapatabilir.",
                ephemeral=True,
            )
        await interaction.response.send_message(
            f"**{summit['id']} · {summit['title']}** zirvesini nasıl sonuçlandırmak istersiniz?",
            view=SummitCloseChoiceView(self.summit_id),
            ephemeral=True,
        )


class OpenSummitModal(discord.ui.Modal, title="Barış / Kriz Zirvesi Aç · Open Summit"):
    party_1 = discord.ui.TextInput(
        label="1. Taraf Ülke / 1st Party State",
        placeholder="Örn: Almanya",
        required=True,
        max_length=80,
    )
    party_2 = discord.ui.TextInput(
        label="2. Taraf Ülke / 2nd Party State",
        placeholder="Örn: Fransa",
        required=True,
        max_length=80,
    )
    extra_parties = discord.ui.TextInput(
        label="Ek Taraflar (Opsiyonel · Virgülle)",
        placeholder="Örn: İtalya, Rusya (boş bırakılabilir)",
        required=False,
        max_length=150,
    )
    reason = discord.ui.TextInput(
        label="Zirve Sebebi / Savaş & Kriz Konusu",
        style=discord.TextStyle.paragraph,
        placeholder="Örn: Sınır savaşı sebebiyle acil ateşkes ve barış görüşmeleri",
        required=True,
        max_length=400,
    )

    async def on_submit(self, interaction: discord.Interaction):
        parties_raw = [self.party_1.value, self.party_2.value]
        if self.extra_parties.value:
            parties_raw.extend(part.strip() for part in self.extra_parties.value.split(",") if part.strip())

        if not _can_open_summit_for_parties(interaction.user, parties_raw):
            return await interaction.response.send_message(
                "⛔ **Yetki Yetersiz:** Zirve açma yetkisi Genel Sekreterlik / Divan / Moderatörler veya taraf devletlerin **Devlet Başkanı / Dışişleri Bakanı**na aittir.",
                ephemeral=True,
            )

        await interaction.response.defer(ephemeral=True, thinking=True)
        ok, summit, err = await create_peace_summit(
            interaction.guild,
            interaction.client,
            opener=interaction.user,
            raw_parties=parties_raw,
            reason=self.reason.value,
            summit_type="peace",
            open_voice=True,
        )
        if not ok or not summit:
            return await interaction.followup.send(err, ephemeral=True)

        v_note = f" · 🔊 <#{summit['voice_channel_id']}>" if summit.get("voice_channel_id") else ""
        await interaction.followup.send(
            f"✅ **{summit['id']}** barış zirvesi açıldı: <#{summit['text_channel_id']}>{v_note}",
            ephemeral=True,
        )


def _can_open_summit_for_parties(member, raw_parties: list[str]) -> bool:
    """Allow Secretariat/Council/Staff anywhere, or party Head/Foreign Minister for their own conflict."""
    if is_summit_convener(member):
        return True
    rec = delegates_storage.get(str(getattr(member, "id", "")))
    rec_dict = rec if isinstance(rec, dict) else None
    if not can_member_sign_for_state(member, rec_dict):
        return False
    member_nation = (rec_dict or {}).get("nation", "")
    if not member_nation:
        member_nation = next((r.name[2:].strip() for r in getattr(member, "roles", ()) if r.name.startswith("🏳️ ")), "")
    if not member_nation:
        return False
    member_keys, member_stems = _nation_match_tokens(member_nation)
    guild = getattr(member, "guild", None)
    for raw in raw_parties:
        if not raw:
            continue
        txt = raw.strip()
        m_role = _ROLE_MENTION_RE.match(txt)
        if m_role and guild:
            r_obj = guild.get_role(int(m_role.group(1)))
            if r_obj and r_obj.name.startswith("🏳️ "):
                txt = r_obj.name[2:].strip()
        txt = txt.lstrip("@").strip()
        if txt.startswith("🏳️"):
            txt = txt[2:].strip()
        if _nation_matches_tokens(member_nation, *_nation_match_tokens(txt)):
            return True
        if guild:
            matched = _fuzzy_match_existing_nation(txt, _list_known_guild_nations(guild))
            if matched and _nation_matches_tokens(matched, member_keys, member_stems):
                return True
    return False


class SummitPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Barış Zirvesi Aç · Open Summit", style=discord.ButtonStyle.success, emoji="🕊️", custom_id="mc_sum_panel_open")
    async def btn_open(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        rec = delegates_storage.get(str(interaction.user.id))
        rec_dict = rec if isinstance(rec, dict) else None
        if not (is_summit_convener(interaction.user) or can_member_sign_for_state(interaction.user, rec_dict)):
            return await interaction.response.send_message(
                "⛔ **Yetki Yetersiz:** Barış zirvesi başlatma yetkisi **Genel Sekreterlik / Divan / Moderatörler** veya akredite **Devlet Başkanı / Dışişleri Bakanı**na aittir.",
                ephemeral=True,
            )
        await interaction.response.send_modal(OpenSummitModal())

    @discord.ui.button(label="Aktif Zirveler · Active Summits", style=discord.ButtonStyle.primary, emoji="📋", custom_id="mc_sum_panel_list")
    async def btn_list(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        await interaction.response.send_message(embed=_build_summits_list_embed(interaction.guild_id), ephemeral=True)


def _build_summits_list_embed(guild_id: int | None) -> discord.Embed:
    items = [
        rec
        for rec in summits_storage.read().values()
        if isinstance(rec, dict) and rec.get("guild_id") == guild_id
    ]
    active_lines = []
    closed_lines = []
    for s in reversed(items):
        parties = " ↔ ".join(p["nation"] for p in s.get("parties", []))
        ch_ref = f"<#{s['text_channel_id']}>" if s.get("text_channel_id") else "*Silindi*"
        v_ref = f" · 🔊 <#{s['voice_channel_id']}>" if s.get("voice_channel_id") else ""
        if s.get("status") == "ACTIVE":
            active_lines.append(f"• **`{s['id']}`** — **{parties}** · {ch_ref}{v_ref}\n  *Sebep:* {s['reason'][:90]}")
        else:
            st = SUMMIT_STATUS_LABELS.get(s.get("status", "CLOSED"), s.get("status", "CLOSED"))
            closed_lines.append(f"• **`{s['id']}`** — **{parties}** ({st})")

    embed = discord.Embed(title="🕊️ BARIŞ & KRİZ ZİRVELERİ REHBERİ · SUMMITS", colour=0x10B981)
    embed.add_field(
        name=f"🟢 Aktif Zirveler · Active ({len(active_lines)})",
        value="\n".join(active_lines[:10])[:1024] or "*Şu anda aktif barış zirvesi yok.*",
        inline=False,
    )
    embed.add_field(
        name=f"📚 Sonuçlanan Zirveler · Archive ({len(closed_lines)})",
        value="\n".join(closed_lines[:10])[:1024] or "*Henüz arşivlenmiş zirve yok.*",
        inline=False,
    )
    return embed


def build_summit_panel() -> tuple[discord.Embed, discord.ui.View]:
    embed = discord.Embed(
        title=f"{SUMMIT_PANEL_TITLES[0]} · PEACE & CRISIS SUMMIT DESK",
        description=(
            "**🇹🇷 Türkçe:**\n"
            "• Savaş veya diplomatik kriz durumlarında taraf devletleri **özel metin + ses zirve odasında** barış görüşmesine almak için kullanılır.\n"
            "• Zirve açmak için **`🕊️ Barış Zirvesi Aç`** butonunu veya `/zirve-ac` komutunu kullanabilirsiniz.\n"
            "• Konferans içinde **`🕊️ Ateşkes / Barış Sun`** ile yazılı taslak sunulur; tüm taraflar imzaladığında otomatik **Resmî Barış Antlaşması** oluşturulur.\n\n"
            "**🇬🇧 English:**\n"
            "• Convene conflicting states into a private **text + voice Peace Summit** to negotiate ceasefires and peace accords."
        ),
        colour=0x10B981,
    )
    embed.set_footer(text="Milletler Cemiyeti · Barış & Arabuluculuk Sistemi")
    return embed, SummitPanelView()


async def _ac_countries(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    q = normalize_country_key(current or "")
    nations = _list_known_guild_nations(interaction.guild)
    if q:
        nations = [n for n in nations if q in normalize_country_key(n)]
    return [app_commands.Choice(name=f"🏳️ {n}"[:100], value=n[:100]) for n in nations[:25]]


async def _ac_summit_parties(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    _sid, summit = _find_summit_by_channel(interaction.guild_id, interaction.channel_id, None)
    if summit and summit.get("parties"):
        q = normalize_country_key(current or "")
        names = [p["nation"] for p in summit["parties"]]
        if q:
            names = [n for n in names if q in normalize_country_key(n)]
        return [app_commands.Choice(name=f"🏳️ {n}"[:100], value=n[:100]) for n in names[:25]]
    return await _ac_countries(interaction, current)


class SummitsCog(commands.Cog):
    """War-to-Peace diplomatic summits with private text/voice halls, ceasefire accords, and Gazette logging."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._synced_stance_guilds: set[int] = set()

    async def cog_load(self):
        self.bot.add_view(SummitPanelView())
        for sid, rec in summits_storage.read().items():
            if not isinstance(rec, dict):
                continue
            if rec.get("status") == "ACTIVE":
                self.bot.add_view(SummitControlView(sid))
                for aid, acc in (rec.get("accords") or {}).items():
                    if isinstance(acc, dict) and acc.get("status") == "PROPOSED":
                        self.bot.add_view(SummitAccordVoteView(sid, aid))
            for rid, rep in (rec.get("reparations") or {}).items():
                if isinstance(rep, dict) and rep.get("status") == "PENDING_PAYMENT":
                    self.bot.add_view(SummitReparationsView(sid, rid))

    @commands.Cog.listener()
    async def on_ready(self):
        for g in getattr(self.bot, "guilds", ()):
            if g.id in self._synced_stance_guilds:
                continue
            self._synced_stance_guilds.add(g.id)
            try:
                await sync_guild_summit_stance_roles(g)
                for _sid, srec in summits_storage.read().items():
                    if isinstance(srec, dict) and srec.get("guild_id") == g.id and srec.get("status") == "ACTIVE":
                        await _refresh_summit_panel_message(g, srec)
            except Exception:
                pass

    @app_commands.command(
        name="zirve-ac",
        description="Savaş, barış veya yaptırım sebebiyle tarafları özel Zirve'ye (metin + ses + taraf rolü) alır",
    )
    @app_commands.describe(
        taraf1="Birinci taraf ülke adı (Yaptırım zirvesinde varsayılan Davacı)",
        taraf2="İkinci taraf ülke adı (Yaptırım zirvesinde varsayılan Davalı)",
        sebep="Zirve sebebi / gündem",
        tur="Zirve türü (Barış / Yaptırım & Tahkim / Ateşkes / Arabuluculuk)",
        taraf1_statu="Birinci ülkenin zirve rolü (otomatik tüm ülke üyelerine verilir)",
        taraf2_statu="İkinci ülkenin zirve rolü (otomatik tüm ülke üyelerine verilir)",
        ek_taraflar="İsteğe bağlı 3./4. taraf ülkeler (virgülle ayırın)",
        arabulucu="İsteğe bağlı özel arabulucu veya temsilci (@üye)",
        ses_odasi="Özel sesli zirve salonu da açılsın mı? (Varsayılan: Evet)",
        ozel_baslik="İsteğe bağlı özel zirve başlığı",
    )
    @app_commands.choices(
        tur=[
            app_commands.Choice(name="🕊️ Barış Zirvesi · Peace Summit", value="peace"),
            app_commands.Choice(name="🚨 Yaptırım & Tahkim Zirvesi (Davacı / Davalı)", value="sanction"),
            app_commands.Choice(name="🛑 Acil Ateşkes Masası · Urgent Ceasefire", value="ceasefire"),
            app_commands.Choice(name="⚖️ Cemiyet Arabuluculuğu · League Mediation", value="mediation"),
            app_commands.Choice(name="🌐 Çok Taraflı Konferans · Multilateral Crisis", value="multilateral"),
        ],
        taraf1_statu=[
            app_commands.Choice(name="⚖️ Davacı Taraf · Claimant", value="plaintiff"),
            app_commands.Choice(name="🚨 Davalı Taraf · Respondent", value="defendant"),
            app_commands.Choice(name="🕊️ Müzakere / Barış Tarafı", value="neutral"),
        ],
        taraf2_statu=[
            app_commands.Choice(name="⚖️ Davacı Taraf · Claimant", value="plaintiff"),
            app_commands.Choice(name="🚨 Davalı Taraf · Respondent", value="defendant"),
            app_commands.Choice(name="🕊️ Müzakere / Barış Tarafı", value="neutral"),
        ],
    )
    @app_commands.autocomplete(taraf1=_ac_countries, taraf2=_ac_countries)
    async def cmd_open_summit(
        self,
        interaction: discord.Interaction,
        taraf1: str,
        taraf2: str,
        sebep: str = "Savaş sebebiyle ateşkes ve barış görüşmeleri",
        tur: app_commands.Choice[str] | None = None,
        taraf1_statu: app_commands.Choice[str] | None = None,
        taraf2_statu: app_commands.Choice[str] | None = None,
        ek_taraflar: str | None = None,
        arabulucu: discord.Member | None = None,
        ses_odasi: bool = True,
        ozel_baslik: str | None = None,
    ):
        parties_raw = [taraf1, taraf2]
        if ek_taraflar:
            parties_raw.extend(part.strip() for part in ek_taraflar.split(",") if part.strip())

        if not _can_open_summit_for_parties(interaction.user, parties_raw):
            return await interaction.response.send_message(
                "⛔ **Yetki Yetersiz:** Zirve açma yetkisi **Genel Sekreterlik / Divan / Moderatörler** veya taraf devletlerin **👑 Devlet Başkanı / 🌐 Dışişleri Bakanı**na aittir.",
                ephemeral=True,
            )

        await interaction.response.defer(ephemeral=True, thinking=True)
        stances_param = None
        if taraf1_statu or taraf2_statu:
            stances_param = [
                taraf1_statu.value if taraf1_statu else "",
                taraf2_statu.value if taraf2_statu else "",
            ]
        ok, summit, err = await create_peace_summit(
            interaction.guild,
            self.bot,
            opener=interaction.user,
            raw_parties=parties_raw,
            reason=sebep,
            summit_type=tur.value if tur else "peace",
            party_stances=stances_param,
            mediator=arabulucu,
            open_voice=ses_odasi,
            custom_title=ozel_baslik,
        )
        if not ok or not summit:
            return await interaction.followup.send(err, ephemeral=True)

        v_note = f" · 🔊 <#{summit['voice_channel_id']}>" if summit.get("voice_channel_id") else ""
        party_summary = " ↔ ".join(
            f"{p['nation']} ({SUMMIT_STANCE_SPECS.get(p.get('stance', 'neutral'), SUMMIT_STANCE_SPECS['neutral'])[0]})"
            for p in summit["parties"]
        )
        await interaction.followup.send(
            f"✅ **{summit['id']}** zirvesi açıldı: <#{summit['text_channel_id']}>{v_note}\n"
            f"• **Taraflar:** `{party_summary}`\n"
            f"• **Sebep:** {summit['reason']}",
            ephemeral=True,
        )

    @app_commands.command(
        name="zirve-taraf-statu",
        description="Zirvedeki bir ülkeye Davacı/Davalı statüsü ve tüm üyelerine rolü otomatik atar",
    )
    @app_commands.describe(
        ulke="Statü verilecek taraf ülke adı (ör. Azerbaycan, Nova Roma)",
        statu="Verilecek taraf statüsü (tüm ülke üyelerine rol otomatik verilir)",
        tur="İsteğe bağlı zirve türünü güncelle (ör. Yaptırım & Tahkim Zirvesi)",
        zirve_kodu="Zirve odası dışından çağırıyorsanız ZRV kodu (ör. ZRV-2026/001)",
    )
    @app_commands.choices(
        statu=[
            app_commands.Choice(name="⚖️ Davacı Taraf · Claimant", value="plaintiff"),
            app_commands.Choice(name="🚨 Davalı Taraf · Respondent", value="defendant"),
            app_commands.Choice(name="🕊️ Müzakere / Barış Tarafı", value="neutral"),
        ],
        tur=[
            app_commands.Choice(name="🚨 Yaptırım & Tahkim Zirvesi · Sanction", value="sanction"),
            app_commands.Choice(name="🕊️ Barış Zirvesi · Peace Summit", value="peace"),
            app_commands.Choice(name="🛑 Acil Ateşkes Masası · Ceasefire", value="ceasefire"),
            app_commands.Choice(name="⚖️ Cemiyet Arabuluculuğu · Mediation", value="mediation"),
        ],
    )
    @app_commands.autocomplete(ulke=_ac_summit_parties)
    async def cmd_set_summit_party_stance(
        self,
        interaction: discord.Interaction,
        ulke: str,
        statu: app_commands.Choice[str],
        tur: app_commands.Choice[str] | None = None,
        zirve_kodu: str | None = None,
    ):
        sid, summit = _find_summit_by_channel(interaction.guild_id, interaction.channel_id, zirve_kodu)
        if not sid or not summit:
            return await interaction.response.send_message(
                "❌ Zirve bulunamadı; bu komutu zirve odası içinde kullanın veya `zirve_kodu` girin.",
                ephemeral=True,
            )
        if summit.get("status") != "ACTIVE":
            return await interaction.response.send_message("❌ Bu zirve artık aktif değil.", ephemeral=True)
        if not _can_manage_existing_summit(interaction.user, summit):
            return await interaction.response.send_message("⛔ Taraf statüsü değiştirme yetkiniz yok.", ephemeral=True)

        await interaction.response.defer(ephemeral=True, thinking=True)
        matched_party = await _find_party_in_summit(interaction.guild, summit, ulke)
        if not matched_party:
            return await interaction.followup.send(f"❌ **`{ulke.strip()}`** bu zirvede taraf olarak bulunamadı.", ephemeral=True)
        matched_party["stance"] = statu.value

        if tur is not None:
            summit["summit_type"] = tur.value
        summits_storage.set(sid, summit)

        sync_res = await sync_guild_summit_stance_roles(interaction.guild)
        await _refresh_summit_panel_message(interaction.guild, summit)

        stance_label, stance_role_name, _ = SUMMIT_STANCE_SPECS[statu.value]
        tch = interaction.guild.get_channel(int(summit["text_channel_id"])) if summit.get("text_channel_id") else None
        if tch and hasattr(tch, "send"):
            try:
                await tch.send(
                    f"🏷️ **ZİRVE TARAF STATÜSÜ GÜNCELLENDİ (`{sid}`):** "
                    f"**{matched_party['nation']}** → **{stance_label}**"
                    + (f" (`{stance_role_name}` tüm devlet üyelerine atandı)" if stance_role_name else "")
                )
            except Exception:
                pass

        await interaction.followup.send(
            f"✅ **{matched_party['nation']}** -> **{stance_label}** ayarlandı (`{sid}`).\n"
            f"• Güncel Davacılar: `{len(sync_res['claimants'])}` üye · Davalılar: `{len(sync_res['respondents'])}` üye.",
            ephemeral=True,
        )

    @app_commands.command(
        name="zirve-taraf-ekle",
        description="Aktif bir barış zirvesine yeni taraf ülke veya arabulucu/temsilci ekler",
    )
    @app_commands.describe(
        ulke="Zirveye eklenecek ülke adı (isteğe bağlı)",
        uye="Zirveye davet edilecek özel üye / temsilci (isteğe bağlı)",
        zirve_kodu="Zirve odası dışından çağırıyorsanız ZRV kodu (ör. ZRV-2026/001)",
    )
    @app_commands.autocomplete(ulke=_ac_countries)
    async def cmd_add_summit_party(
        self,
        interaction: discord.Interaction,
        ulke: str | None = None,
        uye: discord.Member | None = None,
        zirve_kodu: str | None = None,
    ):
        sid, summit = _find_summit_by_channel(interaction.guild_id, interaction.channel_id, zirve_kodu)
        if not sid or not summit:
            return await interaction.response.send_message(
                "❌ Zirve bulunamadı; bu komutu bir zirve odasının içinde kullanın veya `zirve_kodu` girin.",
                ephemeral=True,
            )
        if summit.get("status") != "ACTIVE":
            return await interaction.response.send_message("❌ Bu zirve artık aktif değil.", ephemeral=True)
        if not _can_manage_existing_summit(interaction.user, summit):
            return await interaction.response.send_message("⛔ Taraf veya üye ekleme yetkiniz yok.", ephemeral=True)
        if not (ulke and ulke.strip()) and uye is None:
            return await interaction.response.send_message("❌ En az `ulke` veya `uye` belirtmelisiniz.", ephemeral=True)

        await interaction.response.defer(ephemeral=True, thinking=True)
        msg = await _add_target_to_summit(
            interaction.guild,
            summit,
            raw_nation=(ulke.strip() if ulke else None),
            member=uye,
            actor=interaction.user,
        )
        await interaction.followup.send(msg, ephemeral=True)

    @app_commands.command(
        name="zirve-taraf-cikar",
        description="Aktif bir barış zirvesinden bir taraf ülkeyi veya üyeyi çıkarır",
    )
    @app_commands.describe(
        ulke="Çıkarılacak ülke adı (isteğe bağlı)",
        uye="Çıkarılacak özel davetli üye (isteğe bağlı)",
        zirve_kodu="Zirve odası dışından çağırıyorsanız ZRV kodu",
    )
    @app_commands.autocomplete(ulke=_ac_summit_parties)
    async def cmd_remove_summit_party(
        self,
        interaction: discord.Interaction,
        ulke: str | None = None,
        uye: discord.Member | None = None,
        zirve_kodu: str | None = None,
    ):
        sid, summit = _find_summit_by_channel(interaction.guild_id, interaction.channel_id, zirve_kodu)
        if not sid or not summit:
            return await interaction.response.send_message("❌ Zirve bulunamadı.", ephemeral=True)
        if summit.get("status") != "ACTIVE":
            return await interaction.response.send_message("❌ Bu zirve artık aktif değil.", ephemeral=True)
        if not _can_manage_existing_summit(interaction.user, summit):
            return await interaction.response.send_message("⛔ Zirveden taraf çıkarma yetkiniz yok.", ephemeral=True)
        if not (ulke and ulke.strip()) and uye is None:
            return await interaction.response.send_message("❌ Çıkarılacak `ulke` veya `uye` belirtmelisiniz.", ephemeral=True)

        await interaction.response.defer(ephemeral=True, thinking=True)
        notes: list[str] = []
        if ulke and ulke.strip():
            rem_party = await _find_party_in_summit(interaction.guild, summit, ulke)
            if rem_party:
                summit["parties"] = [p for p in summit.get("parties", []) if p["key"] != rem_party["key"]]
                notes.append(f"`{rem_party['nation']}` taraf listesinden çıkarıldı")
        if uye:
            uid = str(uye.id)
            inv = [str(x) for x in summit.get("invited_user_ids", []) if str(x) != uid]
            med = [str(x) for x in summit.get("mediator_user_ids", []) if str(x) != uid]
            if len(inv) != len(summit.get("invited_user_ids", [])) or len(med) != len(summit.get("mediator_user_ids", [])):
                summit["invited_user_ids"] = inv
                summit["mediator_user_ids"] = med
                notes.append(f"<@{uid}> özel davetten çıkarıldı")

        if not notes:
            return await interaction.followup.send("ℹ️ Belirtilen taraf/üye bu zirvede yer almıyor.", ephemeral=True)

        summits_storage.set(sid, summit)
        await sync_guild_summit_stance_roles(interaction.guild)
        await _apply_summit_overwrites(interaction.guild, summit)
        await _refresh_summit_panel_message(interaction.guild, summit)
        await interaction.followup.send("✅ " + " · ".join(notes), ephemeral=True)

    @app_commands.command(
        name="zirve-kapat",
        description="Aktif barış zirvesini sonuçlandırarak salt okunur arşivler veya siler",
    )
    @app_commands.describe(
        sonuc="Kapanış biçimi",
        sonuc_notu="Resmî Gazete ve arşive yazılacak kısa sonuç açıklaması",
        zirve_kodu="Zirve odası dışından kapatıyorsanız ZRV kodu",
    )
    @app_commands.choices(
        sonuc=[
            app_commands.Choice(name="🕊️ Barış Sağlandı (Salt Okunur Arşivle)", value="peace_archive"),
            app_commands.Choice(name="⏸️ Anlaşma Sağlanamadı (Salt Okunur Arşivle)", value="failed_archive"),
            app_commands.Choice(name="🗑️ Tutanak Alıp Odayı & Sesi Sil", value="delete_archive"),
        ]
    )
    async def cmd_close_summit(
        self,
        interaction: discord.Interaction,
        sonuc: app_commands.Choice[str],
        sonuc_notu: str | None = None,
        zirve_kodu: str | None = None,
    ):
        sid, summit = _find_summit_by_channel(interaction.guild_id, interaction.channel_id, zirve_kodu)
        if not sid or not summit:
            return await interaction.response.send_message(
                "❌ Zirve bulunamadı; bu komutu zirve odasında kullanın veya `zirve_kodu` belirtin.",
                ephemeral=True,
            )
        if not _can_manage_existing_summit(interaction.user, summit):
            return await interaction.response.send_message("⛔ Bu zirveyi sonuçlandırma yetkiniz yok.", ephemeral=True)

        await interaction.response.defer(ephemeral=True, thinking=True)
        await _finalize_summit_close(
            interaction,
            summit_id=sid,
            mode=sonuc.value,
            outcome_note=sonuc_notu,
        )

    @app_commands.command(
        name="zirveler",
        description="Sunucudaki aktif ve sonuçlanan barış/kriz zirvelerini listeler",
    )
    async def cmd_list_summits(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=_build_summits_list_embed(interaction.guild_id), ephemeral=True)

    @app_commands.command(
        name="zirve-paneli-kur",
        description="Belirtilen kanala kalıcı Barış & Kriz Zirvesi Açma Masası kurar",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(kanal="Panelin kurulacağı metin kanalı (boşsa mevcut kanal)")
    async def cmd_setup_summit_panel(self, interaction: discord.Interaction, kanal: discord.TextChannel | None = None):
        target = kanal or interaction.channel
        embed, view = build_summit_panel()
        st = await upsert_panel_message(target, SUMMIT_PANEL_TITLES, embed, view)
        await interaction.response.send_message(f"✅ Barış & Kriz Zirvesi Masası {target.mention} kanalında güncellendi (`{st}`).", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(SummitsCog(bot))
