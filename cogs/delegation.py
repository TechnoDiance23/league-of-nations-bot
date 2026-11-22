import asyncio
import logging
import time
from collections import deque
from datetime import datetime, timedelta, timezone
import discord
from discord import app_commands
from discord.ext import commands, tasks

logger = logging.getLogger("InternationalDiplomaticBot.Delegation")

from utils.storage import (
    countries_storage,
    delegates_storage,
    treaties_storage,
    signatories_storage,
    sticky_roles_storage,
    summits_storage,
)
from utils.constants import (
    ROLE_SG,
    ROLE_ADMIN,
    ROLE_GA_PRESIDENT,
    ROLE_COUNCIL_CHAIR,
    ROLE_ICJ_PRESIDENT,
    ROLE_JUDGE,
    ROLE_CHIEF_PROSECUTOR,
    ROLE_PROSECUTOR,
    ROLE_CHIEF_PROSECUTOR,
    ROLE_PROSECUTOR,
    ROLE_MODERATOR,
    ROLE_SPOKESPERSON,
    ROLE_FOUNDING,
    ROLE_MEMBER,
    ROLE_PRESIDENT,
    ROLE_PRIMARY,
    ROLE_ALTERNATE,
    ROLE_HEAD_OF_DELEGATION,
    ROLE_OBSERVER,
    ROLE_CITIZEN,
    ROLE_VERIFIED,
    ROLE_SUMMIT_CLAIMANT,
    ROLE_SUMMIT_RESPONDENT,
    CHAIR_ROLE_NAMES,
    HOISTED_ROLE_NAMES,
    MANAGED_OFFICE_ROLES,
    SIGNATORY_OFFICE_CODES,
    STATUS_LABELS,
    STATUS_ROLE_NAMES,
    IDENTITY_ROLE_SPECS,
    IDENTITY_BY_CODE,
    IDENTITY_ROLE_NAMES,
    can_member_appoint_head_of_delegation,
    can_member_sign_for_state,
    format_identity_summary,
    classify_office_role,
    resolve_panel_channel,
    validate_country_name,
    validate_ingame_name,
)
from utils.charter_content import CHARTER_PAGES, build_charter_page_embeds
from utils.un_gazette import (
    ensure_country_record,
    get_country_record,
    save_country_record,
    generate_country_short,
    find_country_matches,
    sync_all_country_shorts_storage,
    normalize_country_key,
    canonical_sovereign_stem,
    publish_to_gazette,
    upsert_panel_message,
)


async def ensure_role(
    guild: discord.Guild,
    role_name: str,
    colour: int = 0x1E3A8A,
    hoist: bool | None = None,
) -> discord.Role | None:
    if not guild:
        return None
    desired_hoist = (role_name in HOISTED_ROLE_NAMES) if hoist is None else hoist
    existing = discord.utils.get(guild.roles, name=role_name)
    if existing:
        if role_name in HOISTED_ROLE_NAMES and existing.hoist != desired_hoist:
            try:
                await existing.edit(hoist=desired_hoist, reason="MC-KS-001 Ayrı Rol Görünümü / Role Hoist")
            except Exception:
                pass
        return existing
    try:
        return await guild.create_role(
            name=role_name,
            colour=discord.Colour(colour),
            hoist=desired_hoist,
            mentionable=True,
        )
    except Exception:
        return None


async def resolve_member(guild: discord.Guild, user_id: int) -> discord.Member | None:
    """Resolve a guild member via cache with REST fallback when Server Members Intent is disabled."""
    if not guild:
        return None
    member = guild.get_member(user_id)
    if member:
        return member
    try:
        return await guild.fetch_member(user_id)
    except Exception:
        return None


def is_secretariat_or_admin(member: discord.Member) -> bool:
    if not isinstance(member, discord.Member):
        return False
    if member.id == member.guild.owner_id or member.guild_permissions.administrator:
        return True
    role_names = {r.name for r in member.roles}
    if role_names & {ROLE_SG, ROLE_ADMIN, "🏛️ MC Genel Sekreteri", "🏛️ Cemiyet Yönetimi & Divan", "Admin"}:
        return True
    rec = delegates_storage.get(str(member.id), {})
    return rec.get("office_code") in {"SG", "DSG", "GKB", "GKB-V"}


def is_secretary_general_or_admin(member: discord.Member) -> bool:
    """Return True only for Guild Owner, Discord Administrator, or Secretary-General (SG)."""
    guild = getattr(member, "guild", None)
    if guild is None or not hasattr(member, "roles"):
        return False
    perms = getattr(member, "guild_permissions", None)
    if getattr(member, "id", None) == guild.owner_id or bool(getattr(perms, "administrator", False)):
        return True
    role_names = {r.name for r in member.roles}
    if role_names & {ROLE_SG, "🏛️ MC Genel Sekreteri"}:
        return True
    rec = delegates_storage.get(str(member.id), {})
    return isinstance(rec, dict) and rec.get("office_code") == "SG"


def is_head_of_state_or_government(member: discord.Member | None, rec: dict | None = None) -> bool:
    """Return True if member or active dossier represents a Head of State / Head of Government (Pres.)."""
    if member is not None and can_member_appoint_head_of_delegation(member, None):
        return True
    if not isinstance(rec, dict):
        return False
    if rec.get("status") not in {"APPROVED", "PROVISIONAL", None}:
        return False
    return rec.get("office_code") == "Pres." or rec.get("office_role_name") == ROLE_PRESIDENT


def find_approval_channel(guild: discord.Guild, fallback: discord.abc.Messageable) -> discord.abc.Messageable:
    for ch in guild.text_channels:
        low = ch.name.lower()
        if any(k in low for k in ("onay-odasi", "approvals", "divan-onay", "kayit-onay", "sekreterlik-onay")):
            return ch
    return fallback


def find_signatories_channel(guild: discord.Guild) -> discord.TextChannel | None:
    for ch in guild.text_channels:
        low = ch.name.lower()
        if any(k in low for k in ("imzaci-devletler", "signatories", "imza-sicili")):
            return ch
    return None


def _canonical_country_name_for_roles(nation: str) -> str:
    country = countries_storage.get(normalize_country_key(str(nation or "")), {})
    if isinstance(country, dict) and country.get("name"):
        return str(country["name"]).strip()
    return str(nation or "").strip()


def _country_role_name(nation_clean: str) -> str:
    canonical_nation = _canonical_country_name_for_roles(nation_clean)
    return f"🏳️ {canonical_nation}"[:95]


def _country_delegation_role_names(nation: str) -> tuple[str, str]:
    clean_nation = _canonical_country_name_for_roles(nation)
    council_prefix = "🏛️ "
    council_suffix = " Delegasyon Kurulu · Delegation Council"
    head_prefix = "🎖️ "
    head_suffix = " Delege Başkanı · Head of Delegation"
    council_name = f"{council_prefix}{clean_nation[:100 - len(council_prefix) - len(council_suffix)]}{council_suffix}"
    head_name = f"{head_prefix}{clean_nation[:100 - len(head_prefix) - len(head_suffix)]}{head_suffix}"
    return council_name, head_name


def _is_country_delegation_role_name(role_name: str) -> bool:
    if not role_name or role_name == ROLE_HEAD_OF_DELEGATION:
        return False
    return (
        (role_name.startswith("🏛️ ") and role_name.endswith(" Delegasyon Kurulu · Delegation Council"))
        or (role_name.startswith("🎖️ ") and role_name.endswith(" Delege Başkanı · Head of Delegation"))
    )


async def _ensure_country_delegation_roles(
    guild: discord.Guild | None,
    nation: str,
    country_status: str | None,
) -> tuple[discord.Role | None, discord.Role | None]:
    if not guild or country_status not in {"FOUNDING", "MEMBER"} or not nation:
        return None, None

    council_name, head_name = _country_delegation_role_names(nation)
    role_specs = ((council_name, 0x1E3A8A), (head_name, 0x0284C7))
    ensured: list[discord.Role | None] = []
    for role_name, color in role_specs:
        role = discord.utils.get(guild.roles, name=role_name)
        if role:
            if role.permissions.value or role.hoist or role.mentionable:
                try:
                    await role.edit(
                        permissions=discord.Permissions.none(),
                        hoist=False,
                        mentionable=False,
                        reason="MC-KS-001 Ülke delegasyon rolleri izin eşitlemesi",
                    )
                except Exception as exc:
                    logger.warning("Could not normalize country delegation role %s: %s", role_name, exc)
                    role = None
        else:
            try:
                role = await guild.create_role(
                    name=role_name,
                    permissions=discord.Permissions.none(),
                    colour=discord.Colour(color),
                    hoist=False,
                    mentionable=False,
                    reason="MC-KS-001 Üyelikte ülke delegasyon rollerini oluştur",
                )
            except Exception as exc:
                logger.warning("Could not create country delegation role %s: %s", role_name, exc)
                role = None
        ensured.append(role)
    return ensured[0], ensured[1]


def format_delegate_nick(country_short: str, office_code: str, rep_name: str) -> str:
    """Format canonical Discord nickname `[SHORT] Code Name` (or `[SHORT] Name` for Citizens)."""
    if office_code == "Vatandaş":
        return f"[{country_short}] {rep_name}"[:32]
    return f"[{country_short}] {office_code} {rep_name}"[:32]


def _find_existing_charter_signature(nation: str, exact_only: bool = False) -> tuple[str, dict] | tuple[None, None]:
    target_stem = canonical_sovereign_stem(nation)
    target_key = normalize_country_key(nation)
    for sid, sig in signatories_storage.read().items():
        if not isinstance(sig, dict):
            continue
        sig_stem = sig.get("stem") or canonical_sovereign_stem(sig.get("nation", ""))
        sig_key = sig.get("nation_key") or normalize_country_key(sig.get("nation", ""))
        if target_key and sig_key == target_key:
            return sid, sig
        if not exact_only and target_stem and sig_stem == target_stem:
            return sid, sig
    return None, None


def _find_pending_charter_signature(nation: str, exclude_uid: str | None = None) -> dict | None:
    target_stem = canonical_sovereign_stem(nation)
    for key, p in delegates_storage.read().items():
        if not str(key).startswith("pending_") or not isinstance(p, dict):
            continue
        if exclude_uid and str(p.get("user_id")) == str(exclude_uid):
            continue
        if p.get("request_kind", "charter_sign") != "charter_sign":
            continue
        if canonical_sovereign_stem(p.get("nation", "")) == target_stem:
            return p
    return None


_BOT_ROLE_OPS: dict[tuple[int, int], float] = {}


def mark_bot_role_op(guild_id: int | None, user_id: int | None, seconds: float = 15.0) -> None:
    """Suppress redundant audit checks immediately around intentional bot role edits."""
    if not guild_id or not user_id:
        return
    _BOT_ROLE_OPS[(int(guild_id), int(user_id))] = time.monotonic() + seconds


def is_bot_role_op_active(guild_id: int | None, user_id: int | None) -> bool:
    if not guild_id or not user_id:
        return False
    return time.monotonic() < _BOT_ROLE_OPS.get((int(guild_id), int(user_id)), 0.0)


AUTHORITY_OFFICE_ROLES = {
    *MANAGED_OFFICE_ROLES,
    *CHAIR_ROLE_NAMES,
    ROLE_SG,
    ROLE_ADMIN,
    ROLE_MODERATOR,
    ROLE_JUDGE,
    "🏛️ MC Genel Sekreteri",
    "🏛️ Cemiyet Yönetimi & Divan",
    "⚖️ Uluslararası Yargı Divanı Yargıcı",
}


def _clear_member_chair_records(uid: str) -> None:
    """Remove any institutional chair pointers belonging to uid."""
    holders = delegates_storage.get("meta_chair_holders")
    if isinstance(holders, dict):
        cleaned = {k: v for k, v in holders.items() if str(v) != str(uid)}
        if len(cleaned) != len(holders):
            delegates_storage.set("meta_chair_holders", cleaned)


async def _try_edit_nick(guild: discord.Guild, member: discord.Member, target_nick: str | None) -> str:
    """Edit member nickname and return an ephemeral warning note if Discord hierarchy/owner blocks it."""
    try:
        await member.edit(nick=(target_nick[:32] if target_nick else None))
        return ""
    except discord.Forbidden:
        desired = target_nick[:32] if target_nick else "(etiketsiz ad)"
        if member.id == guild.owner_id:
            return (
                f"\n⚠️ **Takma Ad Notu (Sunucu Sahibi):** Discord kuralı gereği bot sunucu sahibinin "
                f"takma adını otomatik değiştiremez; lütfen sunucu profilinizden manuel olarak `{desired}` yapın."
            )
        return (
            f"\n⚠️ **Takma Ad Notu:** Rol hiyerarşisi nedeniyle bot takma adınızı güncelleyemedi; "
            f"lütfen takma adınızı `{desired}` olarak güncelleyin."
        )
    except Exception:
        return ""


def save_sticky_snapshot(
    member: discord.Member,
    *,
    active: bool = True,
    resigned_role_ids: set[int] | None = None,
    locked: bool | None = None,
    update_locked: bool = False,
) -> dict:
    """Persist assignable role IDs + nickname for automatic sticky role restoration."""
    guild = member.guild
    bot_top = guild.me.top_role.position if guild.me else 0
    resigned_ids = set(resigned_role_ids or set())
    key = f"{guild.id}_{member.id}"
    prev = sticky_roles_storage.get(key, {})
    is_locked = prev.get("locked", False) if locked is None else locked

    # Protect pinned/locked sticky snapshots unless an admin explicitly re-locks or unlocks
    if isinstance(prev, dict) and prev.get("locked") and not update_locked and locked is not False:
        return prev

    prev_resigned = set(int(x) for x in prev.get("resigned_role_ids", []) if str(x).isdigit()) if isinstance(prev, dict) else set()
    all_resigned = prev_resigned | resigned_ids

    kept_roles = [
        r
        for r in member.roles
        if not r.is_default()
        and not r.managed
        and r.position < bot_top
        and r.id not in resigned_ids
    ]
    all_resigned -= {r.id for r in kept_roles}

    snap = {
        "guild_id": guild.id,
        "user_id": member.id,
        "role_ids": [r.id for r in kept_roles],
        "role_names": [r.name for r in kept_roles],
        "resigned_role_ids": sorted(all_resigned),
        "nick": member.nick,
        "active": active,
        "locked": bool(is_locked),
        "updated_at": datetime.utcnow().isoformat(),
    }
    sticky_roles_storage.set(key, snap)
    return snap


async def restore_sticky_roles_for_member(member: discord.Member) -> list[str]:
    """Restore missing sticky roles and nickname for a member if saved."""
    if not isinstance(member, discord.Member) or member.bot:
        return []
    key = f"{member.guild.id}_{member.id}"
    snap = sticky_roles_storage.get(key)
    if not isinstance(snap, dict) or not snap.get("active", True):
        return []

    bot_top = member.guild.me.top_role.position if member.guild.me else 0
    current_ids = {r.id for r in member.roles}
    resigned_ids = set(snap.get("resigned_role_ids", []))
    to_add: list[discord.Role] = []
    for rid in snap.get("role_ids", []):
        if rid in current_ids or rid in resigned_ids:
            continue
        role = member.guild.get_role(int(rid))
        if role and not role.managed and not role.is_default() and role.position < bot_top:
            to_add.append(role)

    # Fallback: resolve from saved role_names if role ID was recreated
    if not to_add and snap.get("role_names") and not current_ids & {r.id for r in member.guild.roles if r.name in snap["role_names"]}:
        for rname in snap.get("role_names", []):
            role = discord.utils.get(member.guild.roles, name=rname)
            if role and role.id not in current_ids and role.id not in resigned_ids and not role.managed and role.position < bot_top:
                to_add.append(role)

    restored_names: list[str] = []
    if to_add:
        mark_bot_role_op(member.guild.id, member.id)
        try:
            await member.add_roles(*to_add, reason="MC-KS-001 Yapışkan Rol Sistemi / Sticky Role Restore")
            restored_names = [r.name for r in to_add]
        except Exception:
            pass

    saved_nick = snap.get("nick")
    if (
        saved_nick
        and member.nick != saved_nick
        and member.id != member.guild.owner_id
        and member.top_role.position < bot_top
    ):
        try:
            await member.edit(nick=saved_nick, reason="MC-KS-001 Yapışkan İsim / Sticky Nickname Restore")
        except Exception:
            pass

    return restored_names


async def sync_existing_country_shorts(guild: discord.Guild | None) -> dict:
    """Recompute all country short codes and update delegate dossiers, sticky snapshots, and live nicks."""
    country_changes = sync_all_country_shorts_storage()
    updated_records: list[str] = []
    nick_updated: list[str] = []
    nick_skipped: list[str] = []

    all_delegates = delegates_storage.read()
    bot_top = guild.me.top_role.position if guild and guild.me else 0
    for key, rec in all_delegates.items():
        if not isinstance(rec, dict) or not rec.get("nation"):
            continue
        nation = rec["nation"]
        new_short = generate_country_short(nation)
        if not new_short:
            continue
        office_code = rec.get("office_code") or "Vatandaş"
        rep_name = rec.get("ingame_name") or rec.get("rep_name") or ""
        new_nick = format_delegate_nick(new_short, office_code, rep_name) if rep_name else rec.get("nick", "")
        changed = rec.get("country_short") != new_short or (new_nick and rec.get("nick") != new_nick)
        if changed:
            rec["country_short"] = new_short
            if new_nick:
                rec["nick"] = new_nick
            delegates_storage.set(key, rec)
            updated_records.append(str(key))

        uid_str = str(rec.get("user_id") or "")
        if not guild or not uid_str.isdigit() or str(key).startswith(("pending_", "roleupd_")):
            continue
        uid = int(uid_str)
        sticky_key = f"{guild.id}_{uid}"
        snap = sticky_roles_storage.get(sticky_key)
        if isinstance(snap, dict) and new_nick and snap.get("nick") != new_nick:
            snap["nick"] = new_nick
            sticky_roles_storage.set(sticky_key, snap)

        if not new_nick:
            continue
        member = await resolve_member(guild, uid)
        if not member:
            continue
        if member.id == guild.owner_id or member.top_role.position >= bot_top:
            if member.nick != new_nick:
                nick_skipped.append(f"{member.display_name}->{new_nick}")
            continue
        if member.nick != new_nick:
            mark_bot_role_op(guild.id, member.id, seconds=15.0)
            try:
                await member.edit(nick=new_nick, reason="MC-KS-001 Ülke Kısaltması Senkronizasyonu")
                nick_updated.append(f"{uid}:{new_nick}")
            except Exception:
                nick_skipped.append(f"{member.display_name}->{new_nick}")

    return {
        "country_changes": country_changes,
        "updated_records": updated_records,
        "nick_updated": nick_updated,
        "nick_skipped": nick_skipped,
    }


async def _apply_rp_roles(
    guild: discord.Guild,
    member: discord.Member,
    *,
    nation_clean: str,
    office_role_name: str,
    country_status: str | None,
    new_nick: str | None,
    strip_previous_offices: bool = True,
    old_nation: str | None = None,
) -> list[str]:
    removed_ids: set[int] = set()
    to_remove: list[discord.Role] = []
    desired_country_role_names: set[str] = set()
    country_delegation_roles: list[discord.Role] = []

    if country_status in {"FOUNDING", "MEMBER"}:
        council_name, head_name = _country_delegation_role_names(nation_clean)
        council_role, head_role = await _ensure_country_delegation_roles(guild, nation_clean, country_status)
        if office_role_name in {ROLE_HEAD_OF_DELEGATION, ROLE_PRIMARY, ROLE_ALTERNATE}:
            desired_country_role_names.add(council_name)
            if council_role:
                country_delegation_roles.append(council_role)
            if office_role_name == ROLE_HEAD_OF_DELEGATION:
                desired_country_role_names.add(head_name)
                if head_role:
                    country_delegation_roles.append(head_role)

    if strip_previous_offices:
        strip_names = set(MANAGED_OFFICE_ROLES)
        if not office_role_name or office_role_name in {ROLE_CITIZEN, ROLE_OBSERVER} or "Basın" in office_role_name:
            strip_names |= AUTHORITY_OFFICE_ROLES
        for r in member.roles:
            if r.name in strip_names and r.name != office_role_name:
                to_remove.append(r)
                removed_ids.add(r.id)

    for role in member.roles:
        if _is_country_delegation_role_name(role.name) and role.name not in desired_country_role_names:
            if role not in to_remove:
                to_remove.append(role)
            removed_ids.add(role.id)

    if old_nation and normalize_country_key(old_nation) != normalize_country_key(nation_clean):
        old_cr = discord.utils.get(guild.roles, name=_country_role_name(old_nation))
        if old_cr and old_cr in member.roles:
            to_remove.append(old_cr)
            removed_ids.add(old_cr.id)

    if country_status in STATUS_ROLE_NAMES:
        for st_code, st_rname in STATUS_ROLE_NAMES.items():
            if st_code != country_status:
                sr_old = discord.utils.get(guild.roles, name=st_rname)
                if sr_old and sr_old in member.roles:
                    to_remove.append(sr_old)
                    removed_ids.add(sr_old.id)

    if to_remove:
        mark_bot_role_op(guild.id, member.id)
        try:
            await member.remove_roles(*to_remove, reason="MC-KS-001 Rol Güncelleme / Role Update")
        except Exception:
            pass

    to_add: list[discord.Role] = []
    if office_role_name:
        r_office = await ensure_role(guild, office_role_name)
        if r_office and r_office not in member.roles:
            to_add.append(r_office)

    for country_role in country_delegation_roles:
        if country_role not in member.roles and country_role not in to_add:
            to_add.append(country_role)

    if country_status in STATUS_ROLE_NAMES:
        col = 0xF59E0B if country_status == "FOUNDING" else (0x10B981 if country_status == "MEMBER" else 0x6B7280)
        r_stat = await ensure_role(guild, STATUS_ROLE_NAMES[country_status], col)
        if r_stat and r_stat not in member.roles:
            to_add.append(r_stat)

    r_country = await ensure_role(guild, _country_role_name(nation_clean), 0x0EA5E9)
    if r_country and r_country not in member.roles:
        to_add.append(r_country)

    r_ver = await ensure_role(guild, ROLE_VERIFIED, 0x10B981)
    if r_ver and r_ver not in member.roles:
        to_add.append(r_ver)

    stance_map = {"plaintiff": ROLE_SUMMIT_CLAIMANT, "defendant": ROLE_SUMMIT_RESPONDENT}
    desired_stances: set[str] = set()
    n_key = normalize_country_key(nation_clean)
    n_stem = canonical_sovereign_stem(nation_clean)
    for srec in summits_storage.read().values():
        if not isinstance(srec, dict) or srec.get("guild_id") != guild.id or srec.get("status") != "ACTIVE":
            continue
        for p in srec.get("parties", []):
            p_nation = p.get("nation", "")
            p_key = p.get("key") or normalize_country_key(p_nation)
            p_stem = canonical_sovereign_stem(p_nation)
            if p_key == n_key or (n_stem and p_stem == n_stem):
                rn = stance_map.get(p.get("stance", ""))
                if rn:
                    desired_stances.add(rn)
    for st_rname in desired_stances:
        col = 0x0284C7 if st_rname == ROLE_SUMMIT_CLAIMANT else 0xDC2626
        r_st = await ensure_role(guild, st_rname, col)
        if r_st and r_st not in member.roles and r_st not in to_add:
            to_add.append(r_st)

    if to_add:
        mark_bot_role_op(guild.id, member.id)
        try:
            await member.add_roles(*to_add, reason="MC-KS-001 Otomatik Rol Atama / Auto Role Assignment")
        except Exception:
            pass

    if new_nick:
        await _try_edit_nick(guild, member, new_nick)

    # Refresh member snapshot into sticky roles after Discord applies roles
    try:
        refreshed = await guild.fetch_member(member.id)
    except Exception:
        refreshed = await resolve_member(guild, member.id) or member
    save_sticky_snapshot(refreshed, active=True, resigned_role_ids=removed_ids, update_locked=True)
    return [r.name for r in to_add]


def _resolve_authoritative_country_status(
    nation: str,
    *,
    force_distinct: bool = False,
) -> tuple[str, str | None, str | None, dict]:
    """Return (canonical_nation, sign_id, authoritative_country_status, country_record)."""
    existing_sid, existing_sig = _find_existing_charter_signature(nation, exact_only=force_distinct)
    lookup_name = existing_sig["nation"] if existing_sig else nation
    existing_crec = countries_storage.get(normalize_country_key(lookup_name))
    crec = ensure_country_record(lookup_name)
    canonical_nation = existing_sig["nation"] if existing_sig else (crec.get("name") or nation)

    if existing_sid:
        crec_status = existing_crec.get("status") if isinstance(existing_crec, dict) else None
        sig_status = existing_sig.get("status") if isinstance(existing_sig, dict) else None
        status = (
            crec_status
            if crec_status in STATUS_ROLE_NAMES
            else (sig_status if sig_status in STATUS_ROLE_NAMES else "MEMBER")
        )
        return canonical_nation, existing_sid, status, crec

    if isinstance(existing_crec, dict) and existing_crec.get("status") in {"FOUNDING", "OBSERVER", "SUSPENDED"}:
        return canonical_nation, None, existing_crec["status"], crec

    return canonical_nation, None, None, crec


def _update_sticky_status_snapshot(
    guild: discord.Guild,
    uid: int,
    *,
    add_role: discord.Role | None,
    remove_roles: list[discord.Role],
    member: discord.Member | None = None,
) -> None:
    """Keep sticky role snapshot aligned with country status changes even when pinned/locked."""
    key = f"{guild.id}_{uid}"
    snap = sticky_roles_storage.get(key)
    removed_ids = {r.id for r in remove_roles}
    removed_names = {r.name for r in remove_roles}

    if isinstance(snap, dict) and snap.get("active", True):
        if snap.get("locked") or member is None:
            role_ids = [int(rid) for rid in snap.get("role_ids", []) if int(rid) not in removed_ids]
            role_names = [rn for rn in snap.get("role_names", []) if rn not in removed_names]
            if add_role and add_role.id not in role_ids:
                role_ids.append(add_role.id)
            if add_role and add_role.name not in role_names:
                role_names.append(add_role.name)
            snap["role_ids"] = role_ids
            snap["role_names"] = role_names
            resigned = set(int(x) for x in snap.get("resigned_role_ids", []) if str(x).isdigit())
            resigned = (resigned | removed_ids) - ({add_role.id} if add_role else set())
            snap["resigned_role_ids"] = sorted(resigned)
            snap["updated_at"] = datetime.utcnow().isoformat()
            sticky_roles_storage.set(key, snap)
            return

    if member is not None:
        save_sticky_snapshot(member, active=True, resigned_role_ids=removed_ids)


async def _sync_member_country_delegation_roles(
    guild: discord.Guild | None,
    uid: str,
    record: dict,
    country_status: str | None,
) -> None:
    """Keep country Council/Head roles and sticky snapshots aligned with an approved national office."""
    if not guild or not str(uid).isdigit():
        return

    office_role_names = {
        "Del.B.": ROLE_HEAD_OF_DELEGATION,
        "Asil": ROLE_PRIMARY,
        "Yedek": ROLE_ALTERNATE,
    }
    eligible_office = (
        isinstance(record, dict)
        and record.get("status") == "APPROVED"
        and record.get("office_code") in office_role_names
        and country_status in {"FOUNDING", "MEMBER"}
        and bool(record.get("nation"))
    )

    desired_names: set[str] = set()
    desired_roles: list[discord.Role] = []
    if eligible_office:
        council_name, head_name = _country_delegation_role_names(record["nation"])
        council_role, head_role = await _ensure_country_delegation_roles(guild, record["nation"], country_status)
        desired_names.add(council_name)
        if council_role:
            desired_roles.append(council_role)
        if record.get("office_code") == "Del.B.":
            desired_names.add(head_name)
            if head_role:
                desired_roles.append(head_role)

    all_country_roles = {
        role.id: role for role in guild.roles if _is_country_delegation_role_name(role.name)
    }
    for role in desired_roles:
        all_country_roles[role.id] = role
    managed_roles = list(all_country_roles.values())
    stale_roles = [role for role in managed_roles if role.name not in desired_names]

    member = await resolve_member(guild, int(uid))
    snapshot_member = None
    if member:
        live_remove = [role for role in stale_roles if role in member.roles]
        live_add = [role for role in desired_roles if role not in member.roles]
        if live_remove:
            mark_bot_role_op(guild.id, member.id)
            try:
                await member.remove_roles(*live_remove, reason="MC-KS-001 Ülke delegasyon rolü eşitleme")
            except Exception as exc:
                logger.warning("Could not remove stale country delegation roles from %s: %s", uid, exc)
        if live_add:
            mark_bot_role_op(guild.id, member.id)
            try:
                await member.add_roles(*live_add, reason="MC-KS-001 Ülke delegasyon rolü eşitleme")
            except Exception as exc:
                logger.warning("Could not add country delegation roles to %s: %s", uid, exc)
        try:
            snapshot_member = await guild.fetch_member(member.id)
        except Exception:
            snapshot_member = await resolve_member(guild, member.id) or member

    snapshot_additions: list[discord.Role | None] = desired_roles or [None]
    for role in snapshot_additions:
        _update_sticky_status_snapshot(
            guild,
            int(uid),
            add_role=role,
            remove_roles=stale_roles,
            member=snapshot_member,
        )


async def _sync_member_country_status_role(
    guild: discord.Guild | None,
    uid: str,
    *,
    country_status: str | None,
) -> None:
    """Apply the country tier badge role (e.g., Founding State) on Discord without changing personal offices."""
    if not guild or not str(uid).isdigit():
        return
    delegate_record = delegates_storage.get(str(uid), {})
    if country_status not in STATUS_ROLE_NAMES:
        await _sync_member_country_delegation_roles(guild, str(uid), delegate_record, country_status)
        return
    status_col = 0xF59E0B if country_status == "FOUNDING" else (0x10B981 if country_status == "MEMBER" else 0x6B7280)
    target_role = await ensure_role(guild, STATUS_ROLE_NAMES[country_status], status_col)
    old_status_roles = [
        r
        for st_code, st_rname in STATUS_ROLE_NAMES.items()
        if st_code != country_status
        for r in (discord.utils.get(guild.roles, name=st_rname),)
        if r is not None
    ]
    member = await resolve_member(guild, int(uid))
    if member:
        to_remove = [r for r in old_status_roles if r in member.roles]
        if to_remove:
            mark_bot_role_op(guild.id, member.id)
            try:
                await member.remove_roles(*to_remove, reason="MC-KS-001 Ülke Statü Rolü Güncelleme")
            except Exception:
                pass
        if target_role and target_role not in member.roles:
            mark_bot_role_op(guild.id, member.id)
            try:
                await member.add_roles(target_role, reason="MC-KS-001 Ülke Statü Rolü Eşitleme")
            except Exception:
                pass
        try:
            refreshed = await guild.fetch_member(member.id)
        except Exception:
            refreshed = await resolve_member(guild, member.id) or member
        _update_sticky_status_snapshot(
            guild,
            member.id,
            add_role=target_role,
            remove_roles=old_status_roles,
            member=refreshed,
        )
    else:
        _update_sticky_status_snapshot(
            guild,
            int(uid),
            add_role=target_role,
            remove_roles=old_status_roles,
            member=None,
        )
    await _sync_member_country_delegation_roles(guild, str(uid), delegate_record, country_status)


async def _propagate_country_status_to_members(
    guild: discord.Guild | None,
    *,
    nation: str,
    country_status: str | None,
    sign_id: str | None = None,
    exclude_uid: str | None = None,
) -> int:
    """Propagate authoritative country tier status and badge role to all citizens & delegates of nation."""
    if not country_status:
        return 0
    if guild and country_status in {"FOUNDING", "MEMBER"}:
        await _ensure_country_delegation_roles(guild, nation, country_status)
    target_key = normalize_country_key(nation)
    updated_count = 0
    for uid, d in list(delegates_storage.read().items()):
        if not isinstance(d, dict) or str(uid).startswith(("pending_", "roleupd_", "meta_")):
            continue
        if exclude_uid is not None and str(uid) == str(exclude_uid):
            continue
        if normalize_country_key(d.get("nation", "")) != target_key:
            continue
        if d.get("office_code") == "Obs.":
            await _sync_member_country_delegation_roles(guild, str(uid), d, country_status)
            continue

        d["country_status"] = country_status
        if sign_id:
            d["sign_id"] = sign_id
        delegates_storage.set(str(uid), d)
        await _sync_member_country_status_role(guild, str(uid), country_status=country_status)
        updated_count += 1
    return updated_count


async def sync_all_delegates_country_statuses(guild: discord.Guild | None, *, check_live_roles: bool = False) -> list[str]:
    """Ensure every existing delegate/citizen dossier and Discord member reflects their nation's Founding/Member status."""
    synced_uids: list[str] = []
    if guild:
        for country in countries_storage.read().values():
            if not isinstance(country, dict) or not country.get("name"):
                continue
            if country.get("status") in {"FOUNDING", "MEMBER"}:
                await _ensure_country_delegation_roles(guild, country["name"], country["status"])

    for uid, rec in list(delegates_storage.read().items()):
        if not isinstance(rec, dict) or str(uid).startswith(("pending_", "roleupd_", "meta_")):
            continue
        if not rec.get("nation"):
            if guild:
                await _sync_member_country_delegation_roles(guild, str(uid), rec, None)
            continue
        canonical_nation, sig_sid, resolved_status, _crec = _resolve_authoritative_country_status(rec["nation"])
        if rec.get("office_code") == "Obs.":
            if guild:
                await _sync_member_country_delegation_roles(
                    guild,
                    str(uid),
                    rec,
                    resolved_status or rec.get("country_status"),
                )
            continue
        if not resolved_status:
            if guild:
                await _sync_member_country_delegation_roles(guild, str(uid), rec, rec.get("country_status"))
            continue
        changed = rec.get("country_status") != resolved_status or (sig_sid and rec.get("sign_id") != sig_sid)
        if changed:
            rec["country_status"] = resolved_status
            if sig_sid:
                rec["sign_id"] = sig_sid
            delegates_storage.set(str(uid), rec)
            synced_uids.append(str(uid))
        if guild and (changed or check_live_roles):
            await _sync_member_country_status_role(guild, str(uid), country_status=resolved_status)
    return synced_uids


async def assign_head_of_delegation(
    guild: discord.Guild,
    target: discord.Member,
    *,
    nation_clean: str,
    actor_id: str | int,
    reason: str = "MC-KS-001 Delege Başkanı Ataması",
) -> dict:
    """Grant Head of Delegation to target within nation_clean, demoting prior same-nation holder to Primary Rep."""
    crec = ensure_country_record(nation_clean)
    sig_sid, sig_rec = _find_existing_charter_signature(nation_clean)
    canonical_nation = sig_rec["nation"] if sig_rec else (crec.get("name") or nation_clean)
    target_key = normalize_country_key(canonical_nation)
    target_stem = canonical_sovereign_stem(canonical_nation)

    replaced_uids: list[str] = []
    for uid, d in list(delegates_storage.read().items()):
        if not isinstance(d, dict) or str(uid).startswith(("pending_", "roleupd_", "meta_")):
            continue
        if str(uid) == str(target.id) or d.get("office_code") != "Del.B.":
            continue
        d_nation = d.get("nation", "")
        if normalize_country_key(d_nation) != target_key and not (target_stem and canonical_sovereign_stem(d_nation) == target_stem):
            continue
        prev_member = await resolve_member(guild, int(uid))
        prev_short = d.get("country_short") or crec.get("short") or generate_country_short(canonical_nation)
        prev_name = d.get("rep_name") or (prev_member.display_name if prev_member else "Delegate")
        prev_nick = f"[{prev_short}] Asil {prev_name}"[:32]
        if prev_member:
            await _apply_rp_roles(
                guild,
                prev_member,
                nation_clean=canonical_nation,
                office_role_name=ROLE_PRIMARY,
                country_status=d.get("country_status"),
                new_nick=prev_nick,
                strip_previous_offices=True,
            )
        d["office_code"] = "Asil"
        d["office"] = "🏛️ Asil Temsilci · Primary Rep"
        d["office_role_name"] = ROLE_PRIMARY
        d["official_title"] = "Asil Temsilci (Önceki Delege Başkanı)"
        d["nick"] = prev_nick
        d["updated_at"] = datetime.utcnow().isoformat()
        delegates_storage.set(str(uid), d)
        replaced_uids.append(str(uid))

    t_uid = str(target.id)
    target_rec = delegates_storage.get(t_uid) or {}
    rep_name = target_rec.get("rep_name") or target.display_name
    country_short = target_rec.get("country_short") or crec.get("short") or generate_country_short(canonical_nation)
    _cn, resolved_sid, resolved_status, _crec = _resolve_authoritative_country_status(canonical_nation)
    country_status = resolved_status or target_rec.get("country_status")
    sig_sid = sig_sid or resolved_sid
    new_nick = f"[{country_short}] Del.B. {rep_name}"[:32]

    await _apply_rp_roles(
        guild,
        target,
        nation_clean=canonical_nation,
        office_role_name=ROLE_HEAD_OF_DELEGATION,
        country_status=country_status,
        new_nick=new_nick,
        strip_previous_offices=True,
    )

    updated = {
        **target_rec,
        "user_id": t_uid,
        "nation": canonical_nation,
        "country_short": country_short,
        "rep_name": rep_name,
        "ingame_name": target_rec.get("ingame_name") or rep_name,
        "official_title": "Delege Başkanı · Head of Delegation",
        "office": "🎖️ Delege Başkanı · Head of Delegation",
        "office_code": "Del.B.",
        "office_role_name": ROLE_HEAD_OF_DELEGATION,
        "country_status": country_status,
        "sign_id": sig_sid or target_rec.get("sign_id"),
        "nick": new_nick,
        "status": "APPROVED",
        "approved_by": str(actor_id),
        "updated_at": datetime.utcnow().isoformat(),
    }
    updated.pop("requested_office", None)
    updated.pop("requested_office_code", None)
    updated.pop("requested_office_role", None)
    delegates_storage.set(t_uid, updated)
    delegates_storage.delete(f"pending_office_{t_uid}")
    return {"nation": canonical_nation, "nick": new_nick, "replaced_uids": replaced_uids}


async def assign_institutional_chair(
    guild: discord.Guild,
    target: discord.Member,
    *,
    role_name: str,
    office_code: str,
    actor_id: str | int,
    single_holder: bool = True,
) -> list[str]:
    """Grant an institutional role, removing prior holders only when single_holder=True, keeping nation roles."""
    chair_role = await ensure_role(guild, role_name)
    holders = delegates_storage.get("meta_chair_holders")
    if not isinstance(holders, dict):
        holders = {}

    prior_ids: set[int] = set()
    if single_holder:
        recorded_prev = holders.get(role_name)
        if recorded_prev and str(recorded_prev) != str(target.id):
            try:
                prior_ids.add(int(recorded_prev))
            except ValueError:
                pass
        if chair_role:
            for m in getattr(chair_role, "members", ()):
                if m.id != target.id:
                    prior_ids.add(m.id)

    replaced_uids: list[str] = []
    if chair_role:
        for pid in prior_ids:
            prev_member = await resolve_member(guild, pid)
            if prev_member and chair_role in prev_member.roles:
                mark_bot_role_op(guild.id, prev_member.id)
                try:
                    await prev_member.remove_roles(chair_role, reason=f"MC-KS-001 Başkanlık Devri ({office_code})")
                except Exception:
                    pass
                refreshed_prev = await resolve_member(guild, prev_member.id) or prev_member
                save_sticky_snapshot(refreshed_prev, active=True, resigned_role_ids={chair_role.id}, update_locked=True)
                replaced_uids.append(str(pid))

        if chair_role not in target.roles:
            mark_bot_role_op(guild.id, target.id)
            try:
                await target.add_roles(chair_role, reason=f"MC-KS-001 Kurumsal Makam Ataması ({office_code})")
            except Exception:
                pass
        try:
            refreshed = await guild.fetch_member(target.id)
        except Exception:
            refreshed = await resolve_member(guild, target.id) or target
        save_sticky_snapshot(refreshed, active=True, update_locked=True)

    if single_holder:
        holders[role_name] = str(target.id)
        delegates_storage.set("meta_chair_holders", holders)
    else:
        multi = delegates_storage.get("meta_chair_holders_multi")
        if not isinstance(multi, dict):
            multi = {}
        bucket = [str(x) for x in multi.get(role_name, []) if str(x) != str(target.id)]
        bucket.append(str(target.id))
        multi[role_name] = bucket
        delegates_storage.set("meta_chair_holders_multi", multi)

    rec = delegates_storage.get(str(target.id))
    if isinstance(rec, dict):
        chairs = set(rec.get("chair_roles") or [])
        chairs.add(role_name)
        rec["chair_roles"] = sorted(chairs)
        rec["updated_at"] = datetime.utcnow().isoformat()
        delegates_storage.set(str(target.id), rec)
    return replaced_uids


async def revoke_institutional_chair(
    guild: discord.Guild,
    target: discord.Member,
    *,
    role_name: str,
    office_code: str,
) -> bool:
    """Remove an institutional seat role from `target` while keeping nationality/office roles."""
    chair_role = discord.utils.get(guild.roles, name=role_name)
    removed = False
    if chair_role and chair_role in target.roles:
        mark_bot_role_op(guild.id, target.id)
        try:
            await target.remove_roles(chair_role, reason=f"MC-KS-001 Kurumsal Görev Sonu ({office_code})")
            removed = True
        except Exception:
            pass
    try:
        refreshed = await guild.fetch_member(target.id)
    except Exception:
        refreshed = await resolve_member(guild, target.id) or target
    resigned_ids = {chair_role.id} if chair_role else set()
    save_sticky_snapshot(refreshed, active=True, resigned_role_ids=resigned_ids, update_locked=True)

    holders = delegates_storage.get("meta_chair_holders")
    if isinstance(holders, dict) and str(holders.get(role_name)) == str(target.id):
        holders.pop(role_name, None)
        delegates_storage.set("meta_chair_holders", holders)
    multi = delegates_storage.get("meta_chair_holders_multi")
    if isinstance(multi, dict) and isinstance(multi.get(role_name), list):
        multi[role_name] = [str(x) for x in multi[role_name] if str(x) != str(target.id)]
        delegates_storage.set("meta_chair_holders_multi", multi)

    rec = delegates_storage.get(str(target.id))
    if isinstance(rec, dict):
        chairs = [c for c in (rec.get("chair_roles") or []) if c != role_name]
        if chairs:
            rec["chair_roles"] = chairs
        else:
            rec.pop("chair_roles", None)
        rec["updated_at"] = datetime.utcnow().isoformat()
        delegates_storage.set(str(target.id), rec)
    return removed


INSTITUTIONAL_APPOINTMENT_SPECS: list[tuple[str, str, str, str, bool, str]] = [
    ("db", ROLE_ICJ_PRESIDENT, "DB", "⚖️ Divan Başkanı · ICJ President", True, "Yargı Divanı başı (`#⚖️│yargi-divani-icj-court` · `#⚖️│divan-makami-chambers`)"),
    ("uyd", ROLE_JUDGE, "UYD", "⚖️ Divan Yargıcı · ICJ Judge", False, "Bağımsız Divan Yargıcı (Md. 23 · dava, hüküm & emsal karar yetkisi)"),
    ("bsv", ROLE_CHIEF_PROSECUTOR, "BSV", "⚖️ Cemiyet Başsavcısı · Chief Prosecutor", True, "Savcılık Makamı başı (`#📜│savcilik-makami-prosecution` · soruşturma & iddianame)"),
    ("svc", ROLE_PROSECUTOR, "SVC", "📜 Cemiyet Savcısı · Prosecutor", False, "Soruşturma açma, iddianame sunma & Divan mütalaası yetkisi"),
    ("gkb", ROLE_GA_PRESIDENT, "GKB", "🪑 Genel Kurul Başkanı · Assembly Pres.", True, "Genel Kurul oturum & oylama başkanı (`#🏛️│genel-kurul-assembly`)"),
    ("sb", ROLE_COUNCIL_CHAIR, "ŞB", "🏛️ Şûra Başkanı · Council Chair", True, "Şûra & Barış Zirveleri yönetimi (`#🕊️ BARIŞ ZİRVELERİ`)"),
    ("spokes", ROLE_SPOKESPERSON, "Spokesperson", "📰 Cemiyet Sözcüsü · Spokesperson", False, "Resmî Gazete & duyuru yayımlama sözcüsü (`#📢│duyurular-announcements`)"),
    ("staff", ROLE_ADMIN, "Staff", "🏛️ Cemiyet Yönetimi · Staff & Council", False, "Sekreterlik yönetim kadrosu (`🛡️ SEKRETERLİK` erişimi)"),
    ("mod", ROLE_MODERATOR, "Mod", "🛡️ Moderatör · Moderator", False, "Sohbet koruması, `/sohbet-sifirla` ve ticket denetimi"),
]
INSTITUTIONAL_APPOINTMENT_BY_KEY = {spec[0]: spec for spec in INSTITUTIONAL_APPOINTMENT_SPECS}


def _collect_live_seat_holders(guild: discord.Guild | None, role_name: str, single_holder: bool) -> list[str]:
    """Return formatted <@uid> strings currently holding `role_name` via registries or guild cache."""
    uids: list[str] = []
    seen: set[str] = set()
    if single_holder:
        holders = delegates_storage.get("meta_chair_holders")
        if isinstance(holders, dict) and holders.get(role_name):
            uid = str(holders[role_name])
            if uid not in seen:
                seen.add(uid)
                uids.append(uid)
    else:
        multi = delegates_storage.get("meta_chair_holders_multi")
        if isinstance(multi, dict):
            for uid in multi.get(role_name, []) or []:
                s = str(uid)
                if s not in seen:
                    seen.add(s)
                    uids.append(s)
    for uid, d in delegates_storage.read().items():
        if isinstance(d, dict) and role_name in (d.get("chair_roles") or []):
            s = str(uid)
            if s not in seen:
                seen.add(s)
                uids.append(s)
    if guild:
        r = discord.utils.get(guild.roles, name=role_name)
        if r:
            for m in getattr(r, "members", ()):
                s = str(m.id)
                if s not in seen:
                    seen.add(s)
                    uids.append(s)
        for m in getattr(guild, "members", ()):
            if any(mr.name == role_name for mr in getattr(m, "roles", ())):
                s = str(m.id)
                if s not in seen:
                    seen.add(s)
                    uids.append(s)
    return [f"<@{u}>" for u in uids]


def build_sg_appointments_panel(guild: discord.Guild | None = None) -> tuple[discord.Embed, discord.ui.View]:
    """Build Secretary-General's institutional appointment reference & interactive dispatch desk."""
    chambers = resolve_panel_channel(guild, None, "divan-makami-chambers", None) if guild else None
    prosecution = resolve_panel_channel(guild, None, "savcilik-makami-prosecution", None) if guild else None
    court = resolve_panel_channel(guild, None, "yargi-divani-icj-court", None) if guild else None
    assembly = resolve_panel_channel(guild, None, "genel-kurul-assembly", None) if guild else None
    broadcast = resolve_panel_channel(guild, None, "yayin-paneli-broadcast", None) if guild else None

    judicial_keys = {"db", "uyd", "bsv", "svc", "mod"}
    judicial_lines: list[str] = []
    exec_lines: list[str] = []
    for key, role_name, code, title, single_holder, scope in INSTITUTIONAL_APPOINTMENT_SPECS:
        holders_fmt = ", ".join(_collect_live_seat_holders(guild, role_name, single_holder)) or "*Boş · Vacant*"
        row = f"• **{title}** (`{code}`) — {holders_fmt}\n  ↳ {scope}"
        if key in judicial_keys:
            judicial_lines.append(row)
        else:
            exec_lines.append(row)

    embed = discord.Embed(
        title="🏛️ GENEL SEKRETERLİK KURUMSAL ATAMA & KADRO ODASI",
        description=(
            "Sayın **Genel Sekreter (`🏛️ Genel Sekreter`)** ve **Yönetici**, Cemiyet organlarına kimi nereye atayacağınızı bu odadan **tek tıkla butonlarla** veya **`/kurum-ata`** komutuyla yönetebilirsiniz.\n"
            "*Tüm atamalar kalıcı yapışkan role, `#📰│resmi-gazete-official-gazette`'ye ve `#🛡️│denetim-kaydi-audit-log`'a otomatik işlenir.*"
        ),
        colour=0xD97706,
    )
    embed.add_field(
        name="⚖️ Yargı, Savcılık & Denetim Organları",
        value="\n".join(judicial_lines)[:1024],
        inline=False,
    )
    embed.add_field(
        name="🏛️ Meclis, Şûra & Sekreterlik Kadrosu",
        value="\n".join(exec_lines)[:1024],
        inline=False,
    )
    embed.add_field(
        name="🗺️ Görev Odaları & Yetki Haritası",
        value=(
            f"• **Yargı Divanı (Başkan & Yargıçlar):** {court.mention if court else '`#⚖️│yargi-divani-icj-court`'} & {chambers.mention if chambers else '`#⚖️│divan-makami-chambers`'}\n"
            f"• **Savcılık Makamı (Başsavcı & Savcılar):** {prosecution.mention if prosecution else '`#📜│savcilik-makami-prosecution`'} (`/sorusturma-ac` · `/iddianame-sun`)\n"
            f"• **Genel Kurul / Meclis Başkanları:** {assembly.mention if assembly else '`#🏛️│genel-kurul-assembly`'} & `#🎙️│konusmaci-kursusu-speakers`\n"
            f"• **Sözcü & Yayın:** {broadcast.mention if broadcast else '`#🎛️│yayin-paneli-broadcast`'} & `#📢│duyurular-announcements`\n"
            "• **Ülke Makamları:** `/akreditasyon-ver`, `/delege-baskani-ata`, `/ulke-statu`, `/hukumet-baskani-dusur`"
        )[:1024],
        inline=False,
    )
    embed.set_footer(text="MC-KS-001 Genel Sekreterlik Atama Masası · Tek tık buton veya /kurum-ata")
    return embed, SGAppointmentDeskView()


async def execute_institutional_appointment(
    guild: discord.Guild,
    bot: commands.Bot | discord.Client,
    *,
    actor: discord.Member,
    target: discord.Member,
    spec_key: str,
    revoke: bool = False,
    reason: str | None = None,
) -> dict:
    """Apply or revoke an institutional appointment cleanly with Gazette, Audit Log, DM, and panel refresh."""
    spec = INSTITUTIONAL_APPOINTMENT_BY_KEY[spec_key]
    _key, role_name, code, title, single_holder, _scope = spec
    reason_clean = (reason or "").strip() or ("Genel Sekreterlik kararıyla görev sona erdirildi." if revoke else "Genel Sekreterlik kurumsal atama kararı.")

    if revoke:
        removed = await revoke_institutional_chair(guild, target, role_name=role_name, office_code=code)
        replaced: list[str] = []
        verb = "Görevden Alındı" if removed else "Kayıt Temizlendi"
        gaz_tag = "KURUMSAL GÖREV SONU · SEAT VACATED"
        gaz_col = 0x6B7280
    else:
        replaced = await assign_institutional_chair(
            guild,
            target,
            role_name=role_name,
            office_code=code,
            actor_id=actor.id,
            single_holder=single_holder,
        )
        verb = "Atandı"
        gaz_tag = "KURUMSAL ATAMA · INSTITUTIONAL APPOINTMENT"
        gaz_col = 0xD97706

    issue_no = await publish_to_gazette(
        bot,
        guild.id,
        gaz_tag,
        f"{title} — {verb}",
        (
            f"**Makam / Office:** `{role_name}` (`{code}`)\n"
            f"**İlgili Üye / Member:** <@{target.id}>\n"
            f"**İşlem / Action:** {verb}\n"
            f"**Karar Makamı / By:** <@{actor.id}>\n"
            f"**Not / Reason:** {reason_clean}"
            + (f"\n**Önceki Görevli Devri:** {', '.join(f'<@{r}>' for r in replaced)}" if replaced else "")
        ),
        color=gaz_col,
        issuer=f"{actor.display_name} · Genel Sekreterlik",
    )

    audit_ch = resolve_panel_channel(guild, None, "denetim-kaydi-audit-log", None)
    if isinstance(audit_ch, discord.TextChannel):
        try:
            await audit_ch.send(
                f"🏛️ **KURUMSAL ATAMA ({verb.upper()}):** <@{target.id}> → **`{role_name}`** (`{code}`) | Yetkili: <@{actor.id}> | `{reason_clean}`"
            )
        except Exception:
            pass

    try:
        await target.send(
            f"🏛️ **Genel Sekreterlik Kurumsal Kararı — {title} ({verb})**\n"
            f"• **Makam:** `{role_name}` (`{code}`)\n"
            f"• **Yetkili:** <@{actor.id}>\n"
            f"• **Açıklama:** {reason_clean}"
            + (f"\n• **Resmî Gazete:** `{issue_no}`" if issue_no else "")
        )
    except Exception:
        pass

    apt_ch = resolve_panel_channel(guild, None, "kurumsal-atama-appointments", None)
    if isinstance(apt_ch, discord.TextChannel):
        try:
            emb, vw = build_sg_appointments_panel(guild)
            await upsert_panel_message(apt_ch, ["🏛️ GENEL SEKRETERLİK KURUMSAL ATAMA"], emb, vw)
        except Exception:
            pass

    if spec_key in {"db", "uyd", "bsv", "svc"}:
        try:
            from cogs.treaties_icj import build_icj_panel, build_chambers_panel, build_prosecution_panel
            court_ch = resolve_panel_channel(guild, None, "yargi-divani-icj-court", None)
            if isinstance(court_ch, discord.TextChannel):
                ce, cv = build_icj_panel(guild)
                await upsert_panel_message(court_ch, ["⚖️ ULUSLARARASI YARGI DİVANI"], ce, cv)
            chm_ch = resolve_panel_channel(guild, None, "divan-makami-chambers", None)
            if isinstance(chm_ch, discord.TextChannel):
                de, dv = build_chambers_panel(guild)
                await upsert_panel_message(chm_ch, ["⚖️ DİVAN MAKAMI & YARGIÇLAR OFİSİ"], de, dv)
            pros_ch = resolve_panel_channel(guild, None, "savcilik-makami-prosecution", None)
            if isinstance(pros_ch, discord.TextChannel):
                pe, pv = build_prosecution_panel(guild)
                await upsert_panel_message(pros_ch, ["📜 SAVCILIK MAKAMI & SORUŞTURMA"], pe, pv)
        except Exception:
            pass

    return {"role_name": role_name, "code": code, "title": title, "replaced": replaced, "issue_no": issue_no, "verb": verb}


class _SGAppointmentPickerView(discord.ui.View):
    """Ephemeral two-step user + institutional office picker for Secretary-General."""

    def __init__(self, *, revoke: bool = False):
        super().__init__(timeout=180)
        self.revoke = revoke
        self.selected_member: discord.Member | None = None
        self.selected_key: str = INSTITUTIONAL_APPOINTMENT_SPECS[0][0]

        self.user_sel = discord.ui.UserSelect(
            placeholder="1. Atanacak/Alınacak Üyeyi Seçin · Select Member",
            min_values=1,
            max_values=1,
        )
        self.user_sel.callback = self._on_user
        self.add_item(self.user_sel)

        opts = [
            discord.SelectOption(label=title[:100], value=key, description=f"{code} · {scope[:85]}")
            for key, _rname, code, title, _sh, scope in INSTITUTIONAL_APPOINTMENT_SPECS
        ]
        self.seat_sel = discord.ui.Select(
            placeholder="2. Kurumsal Makamı Seçin · Select Seat",
            min_values=1,
            max_values=1,
            options=opts,
        )
        self.seat_sel.callback = self._on_seat
        self.add_item(self.seat_sel)

        btn_go = discord.ui.Button(
            label="🗑️ Görevden Al" if revoke else "✅ Atamayı Onayla · Appoint",
            style=discord.ButtonStyle.danger if revoke else discord.ButtonStyle.success,
        )
        btn_go.callback = self._on_submit
        self.add_item(btn_go)

    async def _on_user(self, interaction: discord.Interaction):
        vals = getattr(self.user_sel, "values", []) or []
        if vals:
            self.selected_member = vals[0]
        await interaction.response.defer()

    async def _on_seat(self, interaction: discord.Interaction):
        vals = interaction.data.get("values") or []
        if vals:
            self.selected_key = vals[0]
        await interaction.response.defer()

    async def _on_submit(self, interaction: discord.Interaction):
        if not is_secretary_general_or_admin(interaction.user):
            return await interaction.response.send_message("⛔ Sadece Genel Sekreterlik veya Yöneticiler atama yapabilir.", ephemeral=True)
        if not self.selected_member or not isinstance(self.selected_member, discord.Member):
            resolved = await resolve_member(interaction.guild, int(self.selected_member.id)) if self.selected_member else None
            if not resolved:
                return await interaction.response.send_message("❌ Lütfen listeden geçerli bir sunucu üyesi seçin.", ephemeral=True)
            self.selected_member = resolved
        await interaction.response.defer(ephemeral=True, thinking=True)
        res = await execute_institutional_appointment(
            interaction.guild,
            interaction.client,
            actor=interaction.user,
            target=self.selected_member,
            spec_key=self.selected_key,
            revoke=self.revoke,
        )
        await interaction.followup.send(
            f"✅ <@{self.selected_member.id}> için **{res['title']}** işlemi tamamlandı (**{res['verb']}**)"
            + (f" · Resmî Gazete `{res['issue_no']}`." if res["issue_no"] else "."),
            ephemeral=True,
        )


class SGAppointmentDeskView(discord.ui.View):
    """Persistent Secretary-General Appointment Room control buttons."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Üye Seç & Makama Ata · Appoint", style=discord.ButtonStyle.success, emoji="🎖️", custom_id="mc_sg_appoint_open")
    async def btn_appoint(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if not is_secretary_general_or_admin(interaction.user):
            return await interaction.response.send_message("⛔ Bu masa yalnızca **Genel Sekreter** veya **Admin** içindir.", ephemeral=True)
        await interaction.response.send_message(
            "🏛️ Aşağıdan **üyeyi** ve **kurumsal makamı** seçip **✅ Atamayı Onayla** butonuna basın:",
            view=_SGAppointmentPickerView(revoke=False),
            ephemeral=True,
        )

    @discord.ui.button(label="Kurumsal Görev Al · Revoke", style=discord.ButtonStyle.danger, emoji="🗑️", custom_id="mc_sg_revoke_open")
    async def btn_revoke(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if not is_secretary_general_or_admin(interaction.user):
            return await interaction.response.send_message("⛔ Bu masa yalnızca **Genel Sekreter** veya **Admin** içindir.", ephemeral=True)
        await interaction.response.send_message(
            "🗑️ Görevi kaldırılacak **üyeyi** ve **kurumsal makamı** seçip **🗑️ Görevden Al** butonuna basın:",
            view=_SGAppointmentPickerView(revoke=True),
            ephemeral=True,
        )

    @discord.ui.button(label="Kadroyu Yenile · Refresh", style=discord.ButtonStyle.secondary, emoji="🔄", custom_id="mc_sg_refresh")
    async def btn_refresh(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if not is_secretary_general_or_admin(interaction.user):
            return await interaction.response.send_message("⛔ Sadece Genel Sekreterlik veya Yönetici.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        emb, vw = build_sg_appointments_panel(interaction.guild)
        if interaction.message and hasattr(interaction.message, "edit"):
            await interaction.message.edit(embed=emb, view=vw)
        await interaction.followup.send("✅ Güncel kurumsal görevli kadrosu yenilendi.", ephemeral=True)


def build_identity_select_options() -> list[discord.SelectOption]:
    return [
        discord.SelectOption(label=rname[:100], value=code, description=desc[:100])
        for code, rname, _col, _aliases, desc in IDENTITY_ROLE_SPECS
    ]


def make_identity_select(
    custom_id: str,
    *,
    min_values: int = 1,
    max_values: int = 2,
    required: bool | None = None,
) -> discord.ui.Select:
    kwargs = {}
    if required is not None:
        kwargs["required"] = required
    return discord.ui.Select(
        custom_id=custom_id,
        placeholder="Milliyetinizi / dilinizi seçin · Select identity...",
        min_values=min_values,
        max_values=max_values,
        options=build_identity_select_options(),
        **kwargs,
    )


REGISTRATION_OFFICE_OPTIONS: list[tuple[str, str, str]] = [
    ("Vatandaş / Görevim yok", "🪪 Vatandaş / Görevim Yok · Citizen", "Otomatik onaylanır · Ülke, dil ve Vatandaş rolü"),
    ("Devlet Başkanı", "👑 Devlet Başkanı · Head of State", "Genel Sekreterlik onayı · Başkanlık rolü"),
    ("Dışişleri Bakanı", "🌐 Dışişleri Bakanı · Foreign Minister", "Genel Sekreterlik onayı · Antlaşma/imza yetkisi"),
    ("İçişleri Bakanı", "🏛️ İçişleri Bakanı · Interior Minister", "Genel Sekreterlik onayı · İçişleri Bakanlığı"),
    ("Ekonomi Bakanı", "💰 Ekonomi Bakanı · Economy Minister", "Genel Sekreterlik onayı · Ekonomi/Ticaret"),
    ("Savunma Bakanı", "🛡️ Savunma Bakanı · Defense Minister", "Genel Sekreterlik onayı · Savunma Bakanlığı"),
    ("Bakan / Kabine", "🏛️ Bakan / Kabine · Cabinet Minister", "Genel Sekreterlik onayı · Kabine üyeliği"),
    ("Ulus Temsilcisi", "🏛️ Ulus Temsilcisi · MP / Parliament", "Genel Sekreterlik onayı · Parlamento temsilcisi"),
    ("Asil Temsilci", "🏛️ Asil Temsilci · Primary Rep", "Genel Sekreterlik onayı · Oy kullanma yetkisi"),
    ("Yedek Temsilci", "🏛️ Yedek Temsilci · Alternate Rep", "Genel Sekreterlik onayı · Yedek temsilcilik"),
    ("Büyükelçi", "🏛️ Büyükelçi · Ambassador", "Genel Sekreterlik onayı · Büyükelçilik"),
    ("Konsolosluk", "🛂 Konsolosluk · Consulate", "Genel Sekreterlik onayı · Konsolosluk"),
    ("Cemiyet Sözcüsü", "📰 Cemiyet Sözcüsü · Spokesperson", "Genel Sekreterlik onayı · Sözcülük"),
    ("Akredite Basın", "📰 Akredite Basın · Press", "Otomatik onaylanır · Basın akreditasyonu"),
    ("Gözlemci", "👁️ Gözlemci · Observer", "Otomatik onaylanır · Gözlemci statüsü"),
]
NON_PRIVILEGED_OFFICE_CODES = {"Vatandaş", "Press", "Obs."}


def build_office_select_options() -> list[discord.SelectOption]:
    return [
        discord.SelectOption(label=label[:100], value=value[:100], description=desc[:100])
        for value, label, desc in REGISTRATION_OFFICE_OPTIONS
    ]


def make_office_select(
    custom_id: str = "mc_reg_office_select",
    *,
    required: bool | None = True,
) -> discord.ui.Select:
    kwargs = {}
    if required is not None:
        kwargs["required"] = required
    return discord.ui.Select(
        custom_id=custom_id,
        placeholder="Görevinizi / rolünüzü seçin · Select your role...",
        min_values=1,
        max_values=1,
        options=build_office_select_options(),
        **kwargs,
    )


async def apply_identity_roles(
    guild: discord.Guild,
    member: discord.Member,
    codes: list[str],
    *,
    update_delegate: bool = True,
    grant_verified: bool = False,
) -> list[str]:
    """Assign native-script identity/language roles cleanly and persist them in sticky storage."""
    if not guild or not isinstance(member, discord.Member):
        return []
    wanted_codes = [c for c in dict.fromkeys(codes or []) if c in IDENTITY_BY_CODE]
    if not wanted_codes:
        return []

    wanted_names = {IDENTITY_BY_CODE[c][0] for c in wanted_codes}
    to_remove: list[discord.Role] = []
    removed_ids: set[int] = set()
    for r in member.roles:
        if r.name in IDENTITY_ROLE_NAMES and r.name not in wanted_names:
            to_remove.append(r)
            removed_ids.add(r.id)

    if to_remove:
        mark_bot_role_op(guild.id, member.id)
        try:
            await member.remove_roles(*to_remove, reason="MC-KS-001 Milliyet & Dil Güncelleme")
        except Exception:
            pass

    to_add: list[discord.Role] = []
    assigned_names: list[str] = []
    for code in wanted_codes:
        rname, col, aliases, _desc = IDENTITY_BY_CODE[code]
        role = discord.utils.get(guild.roles, name=rname)
        if not role:
            for alias in aliases:
                old = discord.utils.get(guild.roles, name=alias)
                if old:
                    role = old
                    try:
                        await old.edit(name=rname, reason="MC-KS-001 Milliyet Rolü Standardizasyonu")
                    except Exception:
                        pass
                    break
        if not role:
            role = await ensure_role(guild, rname, col)
        if role:
            assigned_names.append(role.name)
            if role not in member.roles:
                to_add.append(role)

    if grant_verified:
        r_ver = await ensure_role(guild, ROLE_VERIFIED, 0x10B981)
        if r_ver and r_ver not in member.roles:
            to_add.append(r_ver)

    if to_add:
        mark_bot_role_op(guild.id, member.id)
        try:
            await member.add_roles(*to_add, reason="MC-KS-001 Milliyet & Dil Rolü / Identity Role")
        except Exception:
            pass

    if update_delegate:
        uid = str(member.id)
        rec = delegates_storage.get(uid)
        if isinstance(rec, dict):
            rec["identity_codes"] = wanted_codes
            rec["identity_summary"] = format_identity_summary(wanted_codes)
            delegates_storage.set(uid, rec)

    try:
        refreshed = await guild.fetch_member(member.id)
    except Exception:
        refreshed = await resolve_member(guild, member.id) or member
    save_sticky_snapshot(refreshed, active=True, resigned_role_ids=removed_ids, update_locked=True)
    return assigned_names


async def _handle_identity_select_interaction(interaction: discord.Interaction):
    member = interaction.user if isinstance(interaction.user, discord.Member) else await resolve_member(interaction.guild, interaction.user.id)
    if not member:
        return await interaction.response.send_message("❌ Üye bilgisi alınamadı.", ephemeral=True)
    await interaction.response.defer(ephemeral=True, thinking=True)
    chosen = list(interaction.data.get("values", []))
    rec = delegates_storage.get(str(member.id), {})
    already_approved = (isinstance(rec, dict) and rec.get("status") == "APPROVED") or any(
        r.name == ROLE_VERIFIED for r in member.roles
    )
    assigned = await apply_identity_roles(
        interaction.guild,
        member,
        chosen,
        grant_verified=already_approved,
    )
    label_str = ", ".join(f"`{n}`" for n in assigned) if assigned else format_identity_summary(chosen)
    if already_approved:
        await interaction.followup.send(
            f"✅ **Milliyet & dil rolünüz güncellendi:** {label_str}\n"
            f"*(Tüm rolleriniz yapışkan sistemle korunmaktadır.)*",
            ephemeral=True,
        )
    else:
        await interaction.followup.send(
            f"🌐 **Milliyet & dil rolünüz atandı:** {label_str}\n"
            f"⚠️ **Sunucuyu açmak (`{ROLE_VERIFIED}`) için ülke ve tam ad zorunludur!**\n"
            f"👉 Lütfen yukarıdaki **`✍️ Şartı İmzala`** *(Başkan / Dışişleri)* veya **`👤 Bakan / Görev Kaydı`** butonuna basıp **Devlet Tam Adını (yalnızca alfabeyle)** ve **Oyun İçi Tam Adınızı** yazın.",
            ephemeral=True,
        )


async def _handle_ui_error(interaction: discord.Interaction, error: Exception, source: str) -> None:
    """Log UI/modal/view exceptions and notify the user cleanly instead of failing silently."""
    logger.exception("UI error in %s (user=%s, guild=%s)", source, getattr(interaction.user, "id", None), interaction.guild_id, exc_info=error)
    msg = (
        "❌ **İşlem sırasında beklenmeyen bir hata oluştu.**\n"
        "Lütfen tekrar deneyin; sorun sürerse Genel Sekreterliğe veya Yönetim'e bildirin.\n"
        f"*Hata kodu / Error:* `{type(error).__name__}`"
    )
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception:
        pass


class _SafeErrorView(discord.ui.View):
    """Base View ensuring unhandled exceptions are logged and reported to the user."""

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        await _handle_ui_error(interaction, error, f"{type(self).__name__}:{getattr(item, 'custom_id', item)}")


class _SafeErrorModal(discord.ui.Modal):
    """Base Modal ensuring unhandled submit exceptions are logged and reported to the user."""

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await _handle_ui_error(interaction, error, type(self).__name__)


class IdentitySelectView(_SafeErrorView):
    """Persistent native-script nationality & language selector."""

    def __init__(self):
        super().__init__(timeout=None)
        sel = make_identity_select("mc_identity_select", min_values=1, max_values=2)
        sel.callback = self._on_select
        self.add_item(sel)

    async def _on_select(self, interaction: discord.Interaction):
        await _handle_identity_select_interaction(interaction)


def build_identity_panel() -> tuple[discord.Embed, discord.ui.View]:
    embed = discord.Embed(
        title="🌍 ULUSLAR, KÖKEN & DİLLER · NATIONS & LANGUAGE ROLES",
        description=(
            "**🇹🇷 Türkçe · Kendi Alfabeniz & Dilinizle Rol Alın:**\n"
            "Aşağıdaki açılır menüden kendi ülkenizi / milliyetinizi *(en fazla 2 seçim)* işaretleyin; bot kendi alfabesindeki rolü (**`🇹🇷 Türk`**, **`🇦🇿 Azərbaycan Türkü`**, **`🇰🇿 Қазақ`**, **`🇰🇬 Кыргыз`**, **`🇺🇿 Oʻzbek`**, **`🇹🇲 Türkmen`**, **`🇷🇺 Русский`**, **`🇮🇹 Italiano`**, **`🇬🇧 British · English`**, **`🇺🇸 American`**, **`🇫🇷 Français`**, **`🇨🇳 中国人 · 中文`** vb.) anında verir.\n\n"
            "**🇬🇧 English / 🌐 Multilingual:**\n"
            "Select up to **2** native-script nationality/language roles from the dropdown below. Your selection is saved as a sticky role."
        ),
        colour=0x0EA5E9,
    )
    embed.set_footer(text="Milletler Cemiyeti · Native-Script Identity Desk")
    return embed, IdentitySelectView()


def _message_matches_payload(msg: discord.Message, emb: discord.Embed, vw: discord.ui.View | None) -> bool:
    """Return True when an existing bot message already matches the target embed and components."""
    if (getattr(msg, "content", None) or "") != "":
        return False
    embeds = getattr(msg, "embeds", None) or []
    if len(embeds) != 1:
        return False
    cur = embeds[0]
    if (cur.title or "") != (emb.title or "") or (cur.description or "") != (emb.description or ""):
        return False
    cur_footer = getattr(getattr(cur, "footer", None), "text", None) or ""
    exp_footer = getattr(getattr(emb, "footer", None), "text", None) or ""
    if cur_footer != exp_footer:
        return False
    if vw is None:
        return not bool(getattr(msg, "components", None))
    exp_ids = {getattr(item, "custom_id", None) for item in vw.children if getattr(item, "custom_id", None)}
    return _extract_message_custom_ids(msg) == exp_ids


async def _upsert_message_sequence(
    channel: discord.TextChannel,
    payloads: list[tuple[discord.Embed, discord.ui.View | None]],
    pin_first: bool = True,
) -> str:
    """Idempotently replace/update an ordered sequence of bot messages in a read-only channel."""
    existing: list[discord.Message] = []
    try:
        async for msg in channel.history(limit=50, oldest_first=True):
            if msg.author.id == channel.guild.me.id:
                existing.append(msg)
    except Exception:
        return "failed"

    if len(existing) == len(payloads):
        for msg, (emb, vw) in zip(existing, payloads):
            if _message_matches_payload(msg, emb, vw):
                continue
            try:
                await msg.edit(content=None, embed=emb, view=vw)
            except Exception:
                pass
        return "updated"

    if 0 < len(existing) < len(payloads):
        titles_match = all(
            msg.embeds and (msg.embeds[0].title or "").strip() == (emb.title or "").strip()
            for msg, (emb, _vw) in zip(existing, payloads[: len(existing)])
        )
        if titles_match:
            for emb, vw in payloads[len(existing) :]:
                await channel.send(embed=emb, view=vw)
            if pin_first and existing and not existing[0].pinned:
                try:
                    await existing[0].pin()
                except Exception:
                    pass
            return "restored"

    for msg in existing:
        try:
            await msg.delete()
        except Exception:
            pass

    created_first = None
    for idx, (emb, vw) in enumerate(payloads):
        sent = await channel.send(embed=emb, view=vw)
        if idx == 0:
            created_first = sent
    if pin_first and created_first:
        try:
            await created_first.pin()
        except Exception:
            pass
    return "created"


def build_signatories_embeds() -> list[discord.Embed]:
    """Build live EK II Charter Signatories Roll embed(s) from signatories_storage."""
    all_sigs = [v for v in signatories_storage.read().values() if isinstance(v, dict)]
    if not all_sigs:
        empty = discord.Embed(
            title="✍️ EK II — İMZACI DEVLETLER SİCİLİ · CHARTER SIGNATORIES ROLL (MC-KS-001)",
            description=(
                "**🇹🇷 Türkçe:** Milletler Cemiyeti Kuruluş Şartı'nı imzalayıp Genel Sekreterlik onayı alan egemen devletler bu sayfada **otomatik** olarak yayımlanır *(Her devlet yalnızca 1 kez imza atabilir · Md. 6, 7 & 39)*.\n"
                "**🇬🇧 English:** Sovereign states whose digital signature on the `MC-KS-001` Charter has been ratified appear here **automatically** *(One signature per state · Art. 6, 7 & 39)*.\n\n"
                "⏳ *Henüz onaylanmış imzacı devlet kaydı bulunmuyor. İmza atmak için `#📜│tuzuk-charter` altındaki **`✍️ Kuruluş Şartı'nı İmzala`** butonunu kullanın.*"
            ),
            colour=0x10B981,
        )
        empty.set_footer(text="MC-KS-001 · EK II Otomatik İmza Sicili / Live Signatory Roll")
        return [empty]

    founding_cnt = sum(1 for s in all_sigs if "FOUNDING" in str(s.get("status", "")) or "Kurucu" in str(s.get("tier", "")))
    member_cnt = len(all_sigs) - founding_cnt
    header = (
        "**🇹🇷 Türkçe:** Kuruluş Şartı'nı dijital olarak imzalayıp onaylanan devletlerin resmî sicilidir *(Her devlet 1 kez imza atar; bakan/temsilci değişiklikleri otomatik güncellenir)*.\n"
        "**🇬🇧 English:** Official Registry of Sovereign Signatories to the `MC-KS-001` League Charter *(1 signature per state)*.\n\n"
        f"📊 **Toplam İmzacı / Total Signed States:** `{len(all_sigs)}` *(🌟 Kurucu / Founding: `{founding_cnt}` · 🏛️ Üye / Member: `{member_cnt}`)*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    )

    embeds: list[discord.Embed] = []
    chunk_size = 10
    for chunk_idx in range(0, len(all_sigs), chunk_size):
        chunk = all_sigs[chunk_idx : chunk_idx + chunk_size]
        lines = [header] if chunk_idx == 0 else []
        for idx, s in enumerate(chunk, start=chunk_idx + 1):
            crec = get_country_record(s.get("nation", ""))
            short = crec.get("short", generate_country_short(s.get("nation", "")))
            duty = s.get("official_title") or s.get("office_code", "-")
            tier = s.get("tier", "Üye Devlet")
            note = f" *({s['status_note']})*" if s.get("status_note") else ""
            lines.append(
                f"**{idx}. 🏳️ {s.get('nation', '-')} [`{short}`]** — `{s.get('sign_id', '-')}`\n"
                f"   • **Temsilci / Rep:** {s.get('rep_name', '-')} (<@{s.get('user_id', '0')}>)\n"
                f"   • **Görev / Office:** `{duty}` · **Statü:** {tier}{note}\n"
                f"   • **İmza Tarihi / Signed:** `{s.get('signed_at', '-')}`"
            )
        emb = discord.Embed(
            title=(
                "✍️ EK II — İMZACI DEVLETLER SİCİLİ · CHARTER SIGNATORIES (MC-KS-001)"
                if chunk_idx == 0
                else f"✍️ EK II — İMZACI DEVLETLER SİCİLİ (Sayfa {chunk_idx // chunk_size + 1})"
            ),
            description="\n".join(lines)[:4000],
            colour=0x10B981,
        )
        emb.set_footer(text=f"MC-KS-001 Madde 34 & 39 Resmî Arşiv · Güncelleme: {datetime.utcnow().strftime('%d.%m.%Y %H:%M UTC')}")
        embeds.append(emb)
    return embeds


async def sync_signatories_channel(guild: discord.Guild) -> str:
    """Keep #✍️│imzaci-devletler-signatories automatically synchronized."""
    ch = find_signatories_channel(guild)
    if not ch:
        return "no_channel"
    payloads = [(emb, None) for emb in build_signatories_embeds()]
    return await _upsert_message_sequence(ch, payloads, pin_first=True)


def build_charter_footer_panel() -> tuple[discord.Embed, discord.ui.View]:
    embed = discord.Embed(
        title="✍️ EK II — KURULUŞ ŞARTI DİJİTAL İMZA & GÖREV MASASI · SIGNING DESK",
        description=(
            "**🇹🇷 Türkçe · Katılım, Görev & Yapışkan Rol Sistemi:**\n"
            "• **`✍️ Şartı İmzala`:** Devletin yetkili makamı (**`👑 Devlet Başkanı`** veya **`🌐 Dışişleri Bakanı`**) alfabeyle tam ülke adını ve **oyun içi tam adını** yazarak imzalar *(Her devlet 1 kez imzalar)*.\n"
            "• **`👤 Bakan / Görev Kaydı`:** Kabine üyeleri (`İçişleri / Ekonomi / Savunma Bakanı`), `Ulus Temsilcisi` veya `Görevim yok` kaydı açar *(ülke adı ve oyun içi tam ad zorunludur)*.\n"
            "• **`🔄 Rol Güncelle` / `🚪 İstifa Et`:** Tüm roller otomatik, ayrı görünümlü ve yapışkandır.\n\n"
            "**🇬🇧 English · Charter Accession, Office & Sticky Roles:**\n"
            "• Only a state's **Head of State** or **Foreign Minister** may **`Sign Charter`**; other ministers, MPs, reps & citizens use **`Register Office`** *(full alphabet country name & in-game name required)*."
        ),
        colour=0xD97706,
    )
    embed.set_footer(text="MC-KS-001 · Sürüm 2.0 · Yapışkan Rol & Yetkili İmza Aktif")
    return embed, CharterSignPanelView()


def build_accreditation_panel() -> tuple[discord.Embed, discord.ui.View]:
    embed = discord.Embed(
        title="🚪 HOŞ GELDİNİZ · GİRİŞ KAYIT KAPISI · REGISTRATION GATE (MC-KS-001)",
        description=(
            "**🇹🇷 Türkçe · Zorunlu Kayıt & Otomatik Rol Kapısı:**\n"
            "Sunucu odalarını açmak için **`🪪 Kayıt Ol & Rol Al · Register`** butonuna tıklayın:\n"
            "• **1) Kısa Form:** **Oyun İçi Tam Adınız** *(en az 3 harf)* ve **Ülke Tam Adınız** *(yalnızca harflerle)* girilir *(ülke rolünüz `🏳️ <Ülke>` otomatik oluşturulur)*.\n"
            "• **2) Özel Seçim Kartı:** Açılan menülerden **Göreviniz / Rolünüz** *(Vatandaş/Basın/Gözlemci anında açılır; Başkan/Bakan/Temsilci seçilirse giriş yine hemen açılır ve makam Sekreterlik onayına düşer)* ile **Milliyet & Dilinizi** seçip **`✅ Kaydı Tamamla`** butonuna basın.\n\n"
            "• **Kuruluş Şartı İmzalama:** Kayıt olup sunucuya girdikten sonra yalnızca **Devlet Başkanı / Dışişleri Bakanı** içerideki `#📜│tuzuk-charter` odasından **`✍️ Şartı İmzala`** işlemini yapabilir.\n"
            "• **Diğer İşlemler:** Değişiklik için **`🔄 Rol Güncelle`**, ayrılmak için **`🚪 İstifa Et`**, kayıt özetiniz için **`🪪 Sicilim`** kullanın.\n\n"
            "**🇬🇧 English · Registration & Auto-Role Gate:**\n"
            "Click **`🪪 Kayıt Ol & Rol Al · Register`** to enter your **Full In-Game Name** and **Full Country Name (letters only)**, then pick your **Role / Office** and **Nationality & Language** from the private card to unlock the server. Heads of State & Foreign Ministers sign the Charter inside `#📜│tuzuk-charter`."
        ),
        colour=0x1E3A8A,
    )
    embed.set_footer(text="Milletler Cemiyeti · Giriş Kayıt Kapısı (Şart İmza Odası Ayrıdır · MC-KS-001)")
    return embed, AccreditationGateView()


_CHARTER_SYNC_LOCKS: dict[int, asyncio.Lock] = {}
_CHARTER_SYNCING_GUILDS: set[int] = set()
_CHARTER_LAST_SYNC_TS: dict[int, float] = {}
CHARTER_FOOTER_TITLE_PREFIX = "✍️ EK II — KURULUŞ ŞARTI DİJİTAL İMZA"


async def _run_charter_sync(guild: discord.Guild, channel: discord.TextChannel) -> str:
    """Sync the full Charter sequence while guarding against delete/edit-event re-entrancy."""
    _CHARTER_SYNCING_GUILDS.add(guild.id)
    try:
        pages = build_charter_page_embeds()
        payloads: list[tuple[discord.Embed, discord.ui.View | None]] = [(p, None) for p in pages]
        payloads.append(build_charter_footer_panel())
        return await _upsert_message_sequence(channel, payloads, pin_first=True)
    finally:
        _CHARTER_LAST_SYNC_TS[guild.id] = time.monotonic()
        _CHARTER_SYNCING_GUILDS.discard(guild.id)


async def sync_charter_pages(guild: discord.Guild, channel: discord.TextChannel) -> str:
    """Post/update the full chunked MC-KS-001 Charter + bottom Signing Panel in #📜│tuzuk-charter."""
    lock = _CHARTER_SYNC_LOCKS.setdefault(guild.id, asyncio.Lock())
    async with lock:
        return await _run_charter_sync(guild, channel)


def _extract_message_custom_ids(msg: discord.Message) -> set[str]:
    """Collect component custom_ids from either live discord.Message ActionRows or stub messages."""
    ids: set[str] = set()
    for item in getattr(msg, "components", []) or []:
        children = getattr(item, "children", None)
        if children is not None:
            for child in children:
                cid = getattr(child, "custom_id", None)
                if cid:
                    ids.add(cid)
        else:
            cid = getattr(item, "custom_id", None)
            if cid:
                ids.add(cid)
    return ids


async def ensure_charter_signing_panel(
    guild: discord.Guild,
    channel: discord.TextChannel | None = None,
) -> str | None:
    """Verify #📜│tuzuk-charter has all Charter pages plus the interactive Signing Desk; restore if missing."""
    target = channel or resolve_panel_channel(guild, None, "tuzuk-charter", None)
    if target is None:
        return None
    if guild.id in _CHARTER_SYNCING_GUILDS:
        return "busy"
    lock = _CHARTER_SYNC_LOCKS.setdefault(guild.id, asyncio.Lock())
    async with lock:
        existing: list[discord.Message] = []
        try:
            async for msg in target.history(limit=50, oldest_first=True):
                if msg.author.id == guild.me.id:
                    existing.append(msg)
        except Exception:
            return "failed"

        expected_total = len(CHARTER_PAGES) + 1
        expected_footer_ids = {
            getattr(item, "custom_id", None)
            for item in CharterSignPanelView().children
            if getattr(item, "custom_id", None)
        }
        has_footer = (
            len(existing) == expected_total
            and bool(existing[-1].embeds)
            and (existing[-1].embeds[0].title or "").startswith(CHARTER_FOOTER_TITLE_PREFIX)
            and _extract_message_custom_ids(existing[-1]) == expected_footer_ids
        )
        if has_footer:
            return "ok"
        return await _run_charter_sync(guild, target)


# Backward-compatible aliases
AccreditationDeskView = None  # assigned below after class definitions


async def _send_final_ephemeral(interaction: discord.Interaction, content: str) -> None:
    """Deliver final ephemeral outcome cleanly whether after defer or edit_message."""
    try:
        await interaction.edit_original_response(content=content, embed=None, view=None)
    except Exception:
        await interaction.followup.send(content, ephemeral=True)


def _infer_member_identity_codes(member, rec: dict | None = None) -> list[str]:
    """Read identity codes from delegate record or live member identity roles."""
    if isinstance(rec, dict) and rec.get("identity_codes"):
        return [c for c in rec["identity_codes"] if c in IDENTITY_BY_CODE][:2]
    roles = getattr(member, "roles", None) or ()
    rnames = {getattr(r, "name", str(r)) for r in roles}
    inferred = [code for code, rname, *_rest in IDENTITY_ROLE_SPECS if rname in rnames]
    return inferred[:2]


class CountryConfirmationView(_SafeErrorView):
    """Interactive confirmation shown when a typed country matches or resembles an existing state."""

    def __init__(
        self,
        *,
        user_id: int,
        typed_nation: str,
        suggestions: list[dict],
        allow_create_typed: bool,
        on_confirm,
    ):
        super().__init__(timeout=180)
        self.user_id = int(user_id)
        self.typed_nation = typed_nation
        self.on_confirm = on_confirm
        self._resolved = False

        for idx, item in enumerate(suggestions[:3]):
            short = item.get("short", "")
            label_tag = f" ({short})" if short else ""
            btn = discord.ui.Button(
                label=f"Evet, {item['name']}{label_tag}"[:80],
                style=discord.ButtonStyle.success,
                emoji="✅",
                row=0 if idx < 2 else 1,
            )
            btn.callback = self._make_pick_cb(item["name"], is_new_distinct=False)
            self.add_item(btn)

        action_row = 1 if len(suggestions[:3]) <= 2 else 2
        if allow_create_typed:
            btn_new = discord.ui.Button(
                label=f'Hayır, "{typed_nation}" yeni ülke'[:80],
                style=discord.ButtonStyle.primary,
                emoji="🆕",
                row=action_row,
            )
            btn_new.callback = self._make_pick_cb(typed_nation, is_new_distinct=True)
            self.add_item(btn_new)

        btn_cancel = discord.ui.Button(
            label="İptal / Farklı Ad Yaz",
            style=discord.ButtonStyle.secondary,
            emoji="❌",
            row=action_row,
        )
        btn_cancel.callback = self._on_cancel
        self.add_item(btn_cancel)

    def _make_pick_cb(self, chosen_nation: str, *, is_new_distinct: bool):
        async def _cb(interaction: discord.Interaction):
            if interaction.user.id != self.user_id:
                return await interaction.response.send_message("❌ Bu onay penceresi size ait değil.", ephemeral=True)
            if self._resolved:
                return await interaction.response.send_message("ℹ️ Bu seçim zaten işlendi.", ephemeral=True)
            self._resolved = True
            await interaction.response.edit_message(
                content=f"⏳ **{chosen_nation}** seçildi, işlem tamamlanıyor...",
                embed=None,
                view=None,
            )
            await self.on_confirm(interaction, chosen_nation, is_new_distinct)

        return _cb

    async def _on_cancel(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("❌ Bu onay penceresi size ait değil.", ephemeral=True)
        if self._resolved:
            return await interaction.response.send_message("ℹ️ Bu seçim zaten işlendi.", ephemeral=True)
        self._resolved = True
        await interaction.response.edit_message(
            content="❌ İşlem iptal edildi. Dilediğiniz zaman butona tekrar tıklayarak farklı bir ülke adı yazabilirsiniz.",
            embed=None,
            view=None,
        )


async def _prompt_or_finalize_country(
    interaction: discord.Interaction,
    typed_nation: str,
    *,
    finalize_fn,
    skip_key: str = "",
) -> None:
    """Confirm matching/similar countries with the user before proceeding."""
    typed_key = normalize_country_key(typed_nation)
    if skip_key and typed_key == skip_key:
        await interaction.response.defer(ephemeral=True, thinking=True)
        await finalize_fn(interaction, typed_nation, force_distinct=False)
        return

    matches = find_country_matches(typed_nation, interaction.guild)
    exact = matches.get("exact")
    similar = matches.get("similar") or []
    if not exact and not similar:
        await interaction.response.defer(ephemeral=True, thinking=True)
        await finalize_fn(interaction, typed_nation, force_distinct=False)
        return

    if exact:
        suggestions = [exact]
        allow_create = False
        prompt_text = (
            f"⚠️ **Bu Ülke Zaten Kayıtlı!**\n"
            f"Yazdığınız **{typed_nation}** adı sunucuda kayıtlı **{exact['name']}** (`{exact['short']}`) devletiyle aynıdır.\n"
            f"Bu mevcut ülkeye mi katılmak istiyorsunuz, yoksa iptal edip başka bir ülke adı mı yazacaksınız?"
        )
    else:
        suggestions = similar
        allow_create = True
        bullet_lines = "\n".join(f"• **{item['name']}** (`{item['short']}`)" for item in suggestions)
        prompt_text = (
            f"🔍 **Bu Ülke mi? · Benzer Devlet Bulundu**\n"
            f"Yazdığınız **{typed_nation}** adı sunucudaki şu kayıtlı ülkelere benziyor:\n"
            f"{bullet_lines}\n\n"
            f"Mevcut ülkeden bahsediyorsanız yukarıdaki onay butonuna tıklayın; farklı bir devletse **`🆕 Hayır, \"{typed_nation}\" yeni ülke`** ile oluşturabilirsiniz."
        )

    async def _on_pick(btn_interaction: discord.Interaction, chosen_nation: str, is_new_distinct: bool):
        await finalize_fn(btn_interaction, chosen_nation, force_distinct=is_new_distinct)

    view = CountryConfirmationView(
        user_id=interaction.user.id,
        typed_nation=typed_nation,
        suggestions=suggestions,
        allow_create_typed=allow_create,
        on_confirm=_on_pick,
    )
    await interaction.response.send_message(prompt_text, view=view, ephemeral=True)


async def _finalize_gate_registration(
    final_ix: discord.Interaction,
    *,
    name_clean: str,
    chosen_nation: str,
    duty_raw: str,
    identity_codes: list[str],
    force_distinct: bool = False,
) -> None:
    """Apply registration roles/nickname and either open server immediately or queue elevated office approval."""
    user_id = str(final_ix.user.id)
    identity_summary = format_identity_summary(identity_codes)
    member = (
        final_ix.user
        if isinstance(final_ix.user, discord.Member)
        else await resolve_member(final_ix.guild, final_ix.user.id)
    )

    canonical_nation, existing_sid, resolved_country_status, crec = _resolve_authoritative_country_status(
        chosen_nation,
        force_distinct=force_distinct,
    )
    country_short = crec.get("short") or generate_country_short(canonical_nation)
    classified = classify_office_role(duty_raw)
    signed_country_status = resolved_country_status
    now_iso = datetime.utcnow().isoformat()

    if classified["code"] in NON_PRIVILEGED_OFFICE_CODES:
        granted_status = "OBSERVER" if classified["code"] == "Obs." else signed_country_status
        target_nick = format_delegate_nick(country_short, classified["code"], name_clean)
        if member:
            if identity_codes:
                await apply_identity_roles(final_ix.guild, member, identity_codes, update_delegate=False, grant_verified=False)
            await _apply_rp_roles(
                final_ix.guild,
                member,
                nation_clean=canonical_nation,
                office_role_name=classified["role_name"],
                country_status=granted_status,
                new_nick=target_nick,
                strip_previous_offices=True,
            )
        delegates_storage.set(
            user_id,
            {
                "user_id": user_id,
                "nation": canonical_nation,
                "country_short": country_short,
                "rep_name": name_clean,
                "ingame_name": name_clean,
                "official_title": classified["raw_title"],
                "office": classified["display_label"],
                "office_code": classified["code"],
                "office_role_name": classified["role_name"],
                "identity_codes": identity_codes,
                "identity_summary": identity_summary,
                "country_status": granted_status,
                "sign_id": existing_sid,
                "nick": target_nick,
                "status": "APPROVED",
                "registered_at": now_iso,
            },
        )
        delegates_storage.delete(f"pending_office_{user_id}")
        status_line = (
            f"\n• **Ülke Statü Rolü:** `{STATUS_ROLE_NAMES[granted_status]}` *(sadece rozet; ek devlet yetkisi vermez)*"
            if granted_status in STATUS_ROLE_NAMES and classified["code"] == "Vatandaş"
            else (f"\n• **Ülke Statü Rolü:** `{STATUS_ROLE_NAMES[granted_status]}`" if granted_status in STATUS_ROLE_NAMES else "")
        )
        owner_note = (
            f"\n⚠️ **Takma Ad Notu (Sunucu Sahibi):** Discord kuralı gereği bot sunucu sahibinin takma adını değiştiremez; "
            f"lütfen sunucu profilinizden manuel olarak `{target_nick}` yapın."
            if member and member.id == final_ix.guild.owner_id
            else ""
        )
        return await _send_final_ephemeral(
            final_ix,
            f"✅ **Kaydınız Tamamlandı & Sunucu Açıldı!**\n"
            f"• **Ülke Rolü:** `{_country_role_name(canonical_nation)}` (`{country_short}`){status_line}\n"
            f"• **Görev / Rol:** `{classified['role_name']}`\n"
            f"• **Milliyet & Dil:** {identity_summary}\n"
            f"• **Takma Ad:** `{target_nick}` *(yapışkan rol aktif)*{owner_note}",
        )

    interim_nick = f"[{country_short}] {name_clean}"[:32]
    approved_nick = f"[{country_short}] {classified['code']} {name_clean}"[:32]
    if member:
        if identity_codes:
            await apply_identity_roles(final_ix.guild, member, identity_codes, update_delegate=False, grant_verified=False)
        await _apply_rp_roles(
            final_ix.guild,
            member,
            nation_clean=canonical_nation,
            office_role_name=ROLE_CITIZEN,
            country_status=signed_country_status,
            new_nick=interim_nick,
            strip_previous_offices=True,
        )

    delegates_storage.set(
        user_id,
        {
            "user_id": user_id,
            "nation": canonical_nation,
            "country_short": country_short,
            "rep_name": name_clean,
            "ingame_name": name_clean,
            "official_title": f"{classified['raw_title']} (Onay Bekliyor)",
            "requested_office": classified["display_label"],
            "requested_office_code": classified["code"],
            "requested_office_role": classified["role_name"],
            "office": "🪪 Vatandaş / Onay Bekleyen Görev",
            "office_code": "Vatandaş",
            "office_role_name": ROLE_CITIZEN,
            "identity_codes": identity_codes,
            "identity_summary": identity_summary,
            "country_status": signed_country_status,
            "sign_id": existing_sid,
            "nick": interim_nick,
            "status": "PROVISIONAL",
            "registered_at": now_iso,
        },
    )
    delegates_storage.set(
        f"pending_office_{user_id}",
        {
            "request_kind": "office_only",
            "user_id": user_id,
            "nation": canonical_nation,
            "country_short": country_short,
            "rep_name": name_clean,
            "ingame_name": name_clean,
            "official_title": classified["raw_title"],
            "office": classified["display_label"],
            "office_code": classified["code"],
            "office_role_name": classified["role_name"],
            "identity_codes": identity_codes,
            "identity_summary": identity_summary,
            "nick": approved_nick,
            "status": "PENDING",
            "requested_at": now_iso,
        },
    )

    approval_ch = find_approval_channel(final_ix.guild, final_ix.channel)
    sig_note = f"✅ İmzacı Devlet (`{existing_sid}`)" if existing_sid else "⚠️ Şartı henüz imzalamamış devlet"
    embed = discord.Embed(
        title="👤 YENİ ÜYE & ÜST GÖREV ONAY TALEBİ · OFFICE APPROVAL",
        description=(
            f"• **Başvuran / Applicant:** <@{user_id}> *(Sunucu girişi & `Vatandaş` erişimi anında açıldı)*\n"
            f"• **Devlet / State:** `{canonical_nation}` (`{country_short}`) ({sig_note})\n"
            f"• **Oyun İçi Tam Ad / In-Game Name:** `{name_clean}`\n"
            f"• **Talep Edilen Görev / Requested Office:** `{classified['role_name']}` (`{classified['code']}`)\n"
            f"• **Seçilen Milliyet & Dil / Identity:** {identity_summary}\n"
            f"• **Ülke Rolü (Verildi):** `{_country_role_name(canonical_nation)}` (`{country_short}`)"
        ),
        colour=0x2563EB,
    )
    view = OfficeApprovalView(user_id)
    final_ix.client.add_view(view)
    await approval_ch.send(embed=embed, view=view)

    charter_hint = ""
    if not existing_sid and classified["code"] in SIGNATORY_OFFICE_CODES:
        charter_hint = (
            "\n• **Kuruluş Şartı İmzası:** Onay sonrası devletiniz adına imza atmak için içerideki "
            "`#📜│tuzuk-charter` odasındaki **`✍️ Şartı İmzala`** butonunu kullanın."
        )
    owner_note = (
        f"\n⚠️ **Takma Ad Notu (Sunucu Sahibi):** Discord kuralı gereği bot sunucu sahibinin takma adını değiştiremez; "
        f"lütfen sunucu profilinizden manuel olarak `{approved_nick}` *(şu an `{interim_nick}`)* yapın."
        if member and member.id == final_ix.guild.owner_id
        else ""
    )
    await _send_final_ephemeral(
        final_ix,
        f"✅ **Sunucu Erişiminiz Açıldı & Görev Başvurunuz İletildi!**\n"
        f"• **Şu An Aktif Rolleriniz:** `{ROLE_VERIFIED}`, `{_country_role_name(canonical_nation)}` (`{country_short}`), `{ROLE_CITIZEN}` ve {identity_summary}\n"
        f"• **Onay Bekleyen Makam:** `{classified['role_name']}` — Genel Sekreterlik / Yönetici onayladığında rolünüz ve isminiz (`{approved_nick}`) otomatik güncellenecektir.{charter_hint}{owner_note}",
    )


class RegistrationStep2View(_SafeErrorView):
    """Cross-platform (Desktop & Mobile safe) Step 2 ephemeral role, language, and country confirmation view."""

    def __init__(
        self,
        *,
        user_id: int,
        name_clean: str,
        typed_nation: str,
        exact_match: dict | None = None,
        similar_matches: list[dict] | None = None,
        initial_identity_codes: list[str] | None = None,
    ):
        super().__init__(timeout=300)
        self.user_id = int(user_id)
        self.name_clean = name_clean
        self.typed_nation = typed_nation
        self.exact_match = exact_match
        self.similar_matches = list(similar_matches or [])[:3]
        self.needs_country_confirm = bool(self.exact_match or self.similar_matches)
        self.confirmed_nation: str | None = None if self.needs_country_confirm else typed_nation
        self.force_distinct = False
        self.selected_office_raw: str | None = None
        self.selected_identity_codes: list[str] = [c for c in (initial_identity_codes or []) if c in IDENTITY_BY_CODE][:2]
        self._resolved = False
        self._country_buttons: list[tuple[discord.ui.Button, str, bool]] = []

        self.office_select = make_office_select("mc_reg_step2_office", required=None)
        self.office_select.row = 0
        self.office_select.callback = self._on_office_select
        self.add_item(self.office_select)

        self.identity_select = make_identity_select("mc_reg_step2_identity", min_values=1, max_values=2, required=None)
        self.identity_select.row = 1
        for opt in self.identity_select.options:
            opt.default = opt.value in self.selected_identity_codes
        self.identity_select.callback = self._on_identity_select
        self.add_item(self.identity_select)

        if self.needs_country_confirm:
            suggestions = [self.exact_match] if self.exact_match else self.similar_matches
            for item in suggestions[:3]:
                short = item.get("short", "")
                label_tag = f" ({short})" if short else ""
                btn = discord.ui.Button(
                    label=f"Evet, {item['name']}{label_tag}"[:80],
                    style=discord.ButtonStyle.primary,
                    emoji="✅",
                    row=2,
                )
                btn.callback = self._make_country_cb(item["name"], is_new_distinct=False)
                self._country_buttons.append((btn, item["name"], False))
                self.add_item(btn)

            if not self.exact_match:
                btn_new = discord.ui.Button(
                    label=f'Yeni Ülke: "{typed_nation}"'[:80],
                    style=discord.ButtonStyle.secondary,
                    emoji="🆕",
                    row=2,
                )
                btn_new.callback = self._make_country_cb(typed_nation, is_new_distinct=True)
                self._country_buttons.append((btn_new, typed_nation, True))
                self.add_item(btn_new)

        action_row = 3 if self.needs_country_confirm else 2
        self.btn_complete = discord.ui.Button(
            label="Kaydı Tamamla & Sunucuyu Aç",
            style=discord.ButtonStyle.success,
            emoji="🚀",
            row=action_row,
        )
        self.btn_complete.callback = self._on_complete
        self.add_item(self.btn_complete)

        self.btn_cancel = discord.ui.Button(
            label="İptal",
            style=discord.ButtonStyle.danger,
            emoji="❌",
            row=action_row,
        )
        self.btn_cancel.callback = self._on_cancel
        self.add_item(self.btn_cancel)

    def _sync_select_defaults(self) -> None:
        for opt in self.office_select.options:
            opt.default = opt.value == self.selected_office_raw
        for opt in self.identity_select.options:
            opt.default = opt.value in self.selected_identity_codes
        for btn, cname, is_dist in self._country_buttons:
            is_picked = self.confirmed_nation == cname and self.force_distinct == is_dist
            btn.style = discord.ButtonStyle.success if is_picked else discord.ButtonStyle.secondary

    def build_embed(self) -> discord.Embed:
        if self.confirmed_nation:
            c_short = generate_country_short(self.confirmed_nation)
            nation_status = f"✅ **{self.confirmed_nation}** (`{c_short}`)"
        elif self.exact_match:
            nation_status = f"⚠️ Zaten kayıtlı: **{self.exact_match['name']}** — aşağıdan **`✅ Evet, ...`** butonuna basın"
        else:
            sim_str = ", ".join(f"**{x['name']}**" for x in self.similar_matches)
            nation_status = f"🔍 Benzer bulundu ({sim_str}) — aşağıdan mevcut ülkeyi veya **`🆕 Yeni Ülke`**'yi seçin"

        office_status = (
            f"✅ `{classify_office_role(self.selected_office_raw)['role_name']}`"
            if self.selected_office_raw
            else "⏳ *1. menüden görevinizi seçin*"
        )
        identity_status = (
            f"✅ {format_identity_summary(self.selected_identity_codes)}"
            if self.selected_identity_codes
            else "⏳ *2. menüden milliyet & dilinizi seçin*"
        )
        return discord.Embed(
            title="🪪 KAYIT ADIM 2/2 — ROL, DİL & ÜLKE ONAYI",
            description=(
                "Aşağıdaki menülerden **Görevinizi** ve **Milliyet / Dilinizi** seçin, "
                "ardından **`🚀 Kaydı Tamamla & Sunucuyu Aç`** butonuna basın.\n\n"
                f"• **1️⃣ Oyun İçi Ad:** ✅ `{self.name_clean}`\n"
                f"• **2️⃣ Ülke:** {nation_status}\n"
                f"• **3️⃣ Görev / Rol:** {office_status}\n"
                f"• **4️⃣ Milliyet & Dil:** {identity_status}"
            ),
            colour=0x1E3A8A,
        )

    def _make_country_cb(self, chosen_nation: str, *, is_new_distinct: bool):
        async def _cb(interaction: discord.Interaction):
            if interaction.user.id != self.user_id:
                return await interaction.response.send_message("❌ Bu kayıt kartı size ait değil.", ephemeral=True)
            if self._resolved:
                return await interaction.response.send_message("ℹ️ Bu kayıt zaten tamamlandı veya iptal edildi.", ephemeral=True)
            self.confirmed_nation = chosen_nation
            self.force_distinct = is_new_distinct
            self._sync_select_defaults()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)

        return _cb

    async def _on_office_select(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("❌ Bu kayıt kartı size ait değil.", ephemeral=True)
        if self._resolved:
            return await interaction.response.send_message("ℹ️ Bu kayıt zaten tamamlandı veya iptal edildi.", ephemeral=True)
        vals = list(interaction.data.get("values", []))
        self.selected_office_raw = vals[0] if vals else None
        self._sync_select_defaults()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _on_identity_select(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("❌ Bu kayıt kartı size ait değil.", ephemeral=True)
        if self._resolved:
            return await interaction.response.send_message("ℹ️ Bu kayıt zaten tamamlandı veya iptal edildi.", ephemeral=True)
        self.selected_identity_codes = list(interaction.data.get("values", []))[:2]
        self._sync_select_defaults()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _on_complete(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("❌ Bu kayıt kartı size ait değil.", ephemeral=True)
        if self._resolved:
            return await interaction.response.send_message("ℹ️ Bu kayıt zaten işlendi.", ephemeral=True)

        missing: list[str] = []
        if not self.confirmed_nation:
            missing.append("• **Ülke Onayı** *(Yukarıdaki ✅ / 🆕 ülke butonlarından birini seçin)*")
        if not self.selected_office_raw:
            missing.append("• **Görev / Rol** *(1. açılır menüden seçin)*")
        if not self.selected_identity_codes:
            missing.append("• **Milliyet & Dil** *(2. açılır menüden en az 1 seçim yapın)*")
        if missing:
            return await interaction.response.send_message(
                "⚠️ **Kaydı tamamlamak için eksik alanları seçin:**\n" + "\n".join(missing),
                ephemeral=True,
            )

        self._resolved = True
        await interaction.response.edit_message(
            content="⏳ **Kaydınız, rolleriniz ve sunucu erişiminiz hazırlanıyor...**",
            embed=None,
            view=None,
        )
        await _finalize_gate_registration(
            interaction,
            name_clean=self.name_clean,
            chosen_nation=self.confirmed_nation,
            duty_raw=self.selected_office_raw,
            identity_codes=self.selected_identity_codes,
            force_distinct=self.force_distinct,
        )

    async def _on_cancel(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("❌ Bu kayıt kartı size ait değil.", ephemeral=True)
        if self._resolved:
            return await interaction.response.send_message("ℹ️ Bu kayıt zaten işlendi.", ephemeral=True)
        self._resolved = True
        await interaction.response.edit_message(
            content="❌ Kayıt işlemi iptal edildi. Dilediğiniz zaman **`🪪 Kayıt Ol & Rol Al · Register`** butonuna tıklayarak yeniden başlayabilirsiniz.",
            embed=None,
            view=None,
        )


class CharterAccessionModal(_SafeErrorModal, title="MC-KS-001 Kuruluş Şartı Dijital İmza"):
    country = discord.ui.TextInput(
        label="Devlet Tam Adı (Alfabe) / Full Country Name",
        placeholder="Sadece harflerle ülke adı (Örn: Türkiye Cumhuriyeti)",
        required=True,
        max_length=70,
    )
    rep_name = discord.ui.TextInput(
        label="Oyun İçi Tam Adınız / Full In-Game Name",
        placeholder="Oyun içi tam adınız (Örn: Mustafa Kemal)",
        required=True,
        max_length=32,
    )
    duty_text = discord.ui.TextInput(
        label="Yetkili Makam (Başkan / Dışişleri Bakanı)",
        placeholder="Yalnızca: Devlet Başkanı veya Dışişleri Bakanı",
        required=True,
        max_length=70,
    )
    oath = discord.ui.TextInput(
        label="Şart Beyanı / Charter Oath (EK II)",
        default="MC-KS-001 (Sürüm 2.0) Kuruluş Şartı'nı devletim adına kabul ve imzalıyorum.",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=300,
    )

    def __init__(self, office_code: str = "", office_label: str = ""):
        super().__init__()
        self.preset_code = office_code
        self.preset_label = office_label

    async def on_submit(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        ok_c, country_clean, err_c = validate_country_name(self.country.value)
        if not ok_c:
            return await interaction.response.send_message(err_c, ephemeral=True)
        ok_n, name_clean, err_n = validate_ingame_name(self.rep_name.value)
        if not ok_n:
            return await interaction.response.send_message(err_n, ephemeral=True)

        duty_raw = self.duty_text.value.strip()
        classified = classify_office_role(duty_raw)
        if classified["code"] not in SIGNATORY_OFFICE_CODES:
            return await interaction.response.send_message(
                f"⛔ **İmza Yetkisi Kısıtı:** Kuruluş Şartı'nı devlet adına yalnızca **`👑 Devlet Başkanı`** veya **`🌐 Dışişleri Bakanı`** imzalayabilir!\n"
                f"• Yazılan görev: `{duty_raw}` → `{classified['role_name']}`\n"
                f"👉 Diğer bakanlıklar, ulus temsilcisi, temsilci veya vatandaş kaydı için **`👤 Bakan / Görev Kaydı`** butonunu kullanın.",
                ephemeral=True,
            )

        existing_rec = delegates_storage.get(user_id, {})
        identity_codes = _infer_member_identity_codes(interaction.user, existing_rec)
        identity_summary = format_identity_summary(identity_codes)
        oath_clean = self.oath.value.strip()

        # Check exact duplicate nation signature immediately ("devletler sadece bir kez imzalamalı")
        existing_sid, existing_sig = _find_existing_charter_signature(country_clean, exact_only=True)
        if existing_sid:
            return await interaction.response.send_message(
                f"⚠️ **{existing_sig['nation']}** (`{existing_sid}`) daha önce **{existing_sig['signed_at']}** tarihinde "
                f"**{existing_sig['rep_name']}** tarafından Kuruluş Şartı'nı imzalamıştır!\n"
                f"📜 Kuruluş Şartı her devlet tarafından **yalnızca 1 kez** imzalanabilir.\n"
                f"👉 Bu devletin bakanı, ulus temsilcisi, temsilcisi veya vatandaşı olarak rol almak için **`👤 Bakan / Görev Kaydı`** butonunu kullanın.",
                ephemeral=True,
            )

        async def _finalize(final_ix: discord.Interaction, chosen_nation: str, *, force_distinct: bool = False):
            sig_sid, sig_rec = _find_existing_charter_signature(chosen_nation, exact_only=force_distinct)
            if sig_sid:
                return await _send_final_ephemeral(
                    final_ix,
                    f"⚠️ **{sig_rec['nation']}** (`{sig_sid}`) daha önce **{sig_rec['signed_at']}** tarihinde "
                    f"**{sig_rec['rep_name']}** tarafından Kuruluş Şartı'nı imzalamıştır!\n"
                    f"📜 Kuruluş Şartı her devlet tarafından **yalnızca 1 kez** imzalanabilir.\n"
                    f"👉 Bu devletin bakanı, ulus temsilcisi, temsilcisi veya vatandaşı olarak rol almak için **`👤 Bakan / Görev Kaydı`** butonunu kullanın.",
                )

            pending_same = _find_pending_charter_signature(chosen_nation, exclude_uid=user_id)
            if pending_same and (not force_distinct or normalize_country_key(pending_same.get("nation", "")) == normalize_country_key(chosen_nation)):
                return await _send_final_ephemeral(
                    final_ix,
                    f"⏳ **{pending_same['nation']}** için <@{pending_same['user_id']}> tarafından onay bekleyen bir Kuruluş Şartı imzası zaten mevcut.",
                )

            office_code = classified["code"]
            office_role_name = classified["role_name"]
            country_short = generate_country_short(chosen_nation)
            new_nick = f"[{country_short}] {office_code} {name_clean}"[:32]

            pending_record = {
                "request_kind": "charter_sign",
                "user_id": user_id,
                "nation": chosen_nation,
                "country_short": country_short,
                "rep_name": name_clean,
                "ingame_name": name_clean,
                "official_title": duty_raw,
                "office": classified["display_label"],
                "office_code": office_code,
                "office_role_name": office_role_name,
                "identity_codes": identity_codes,
                "identity_summary": identity_summary,
                "oath": oath_clean,
                "nick": new_nick,
                "status": "PENDING",
                "requested_at": datetime.utcnow().isoformat(),
            }
            delegates_storage.set(f"pending_{user_id}", pending_record)

            approval_ch = find_approval_channel(final_ix.guild, final_ix.channel)
            embed = discord.Embed(
                title="✍️ ŞART İMZASI & AKREDİTASYON ONAYI · CHARTER ACCESSION (MC-KS-001)",
                description=(
                    f"**Madde 6, 7 & 39 İncelemesi / Secretariat Review:**\n\n"
                    f"• **Başvuran / Applicant:** <@{user_id}> (`{final_ix.user.name}`)\n"
                    f"• **Devlet Tam Adı / State:** `{chosen_nation}` (`{country_short}`)\n"
                    f"• **Oyun İçi Tam Ad / In-Game Name:** `{name_clean}`\n"
                    f"• **Yazılan Görev / Stated Duty:** `{duty_raw}`\n"
                    f"• **Milliyet & Dil / Identity:** {identity_summary}\n"
                    f"• **Otomatik Tanınan Rol / Auto Role:** `{office_role_name}` (`{office_code}`)\n"
                    f"• **Otomatik Ülke Rolü:** `{_country_role_name(chosen_nation)}`\n"
                    f"• **Önerilen İsim / Nickname:** `{new_nick}`\n"
                    f"• **Beyan / Oath:**\n> *{oath_clean}*"
                ),
                colour=0xD97706,
            )
            embed.set_footer(text="Onaylandığında #imzaci-devletler-signatories sayfasına otomatik işlenir & yapışkan rol aktif olur.")
            view = ApprovalActionView(user_id)
            final_ix.client.add_view(view)
            await approval_ch.send(embed=embed, view=view)

            id_hint = "" if identity_codes else "\n• *(Milliyet/dil rolünüz yoksa `#🌍│uluslar-nations` kanalından veya `/milliyet-sec` komutuyla ekleyebilirsiniz.)*"
            await _send_final_ephemeral(
                final_ix,
                f"📩 **{chosen_nation}** adına dijital Şart imzanız ve **`{duty_raw}`** göreviniz Genel Sekreterlik onayına iletildi!\n"
                f"• **Oyun İçi Ad:** `{name_clean}` · **Milliyet:** {identity_summary}\n"
                f"• **Eşleşen Görev Rolü:** `{office_role_name}` *(yapışkan rol aktif)*{id_hint}",
            )

        await _prompt_or_finalize_country(
            interaction,
            country_clean,
            finalize_fn=_finalize,
            skip_key=normalize_country_key(existing_rec.get("nation", "")),
        )


class OfficeRegisterModal(_SafeErrorModal, title="Görev & Bakanlık Kaydı · Register Office"):
    country = discord.ui.TextInput(
        label="Devlet Tam Adı (Alfabe) / Full Country Name",
        placeholder="Sadece harflerle ülke adı (Örn: Türkiye Cumhuriyeti)",
        required=True,
        max_length=70,
    )
    rep_name = discord.ui.TextInput(
        label="Oyun İçi Tam Adınız / Full In-Game Name",
        placeholder="Oyun içi tam adınız (Örn: Ahmet Yılmaz)",
        required=True,
        max_length=32,
    )
    duty_text = discord.ui.TextInput(
        label="Göreviniz (yoksa: Görevim yok)",
        placeholder="Örn: İçişleri/Dışişleri/Ekonomi Bakanı, Ulus Temsilcisi, Görevim yok",
        required=True,
        max_length=70,
    )

    async def on_submit(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        ok_c, country_clean, err_c = validate_country_name(self.country.value)
        if not ok_c:
            return await interaction.response.send_message(err_c, ephemeral=True)
        ok_n, name_clean, err_n = validate_ingame_name(self.rep_name.value)
        if not ok_n:
            return await interaction.response.send_message(err_n, ephemeral=True)

        duty_raw = self.duty_text.value.strip()
        existing_rec = delegates_storage.get(user_id, {})
        identity_codes = _infer_member_identity_codes(interaction.user, existing_rec)
        identity_summary = format_identity_summary(identity_codes)

        async def _finalize(final_ix: discord.Interaction, chosen_nation: str, *, force_distinct: bool = False):
            existing_sid, existing_sig = _find_existing_charter_signature(chosen_nation, exact_only=force_distinct)
            canonical_nation = existing_sig["nation"] if existing_sig else chosen_nation
            classified = classify_office_role(duty_raw)
            country_short = generate_country_short(canonical_nation)
            new_nick = f"[{country_short}] {classified['code']} {name_clean}"[:32]

            pending_record = {
                "request_kind": "office_only",
                "user_id": user_id,
                "nation": canonical_nation,
                "country_short": country_short,
                "rep_name": name_clean,
                "ingame_name": name_clean,
                "official_title": duty_raw,
                "office": classified["display_label"],
                "office_code": classified["code"],
                "office_role_name": classified["role_name"],
                "identity_codes": identity_codes,
                "identity_summary": identity_summary,
                "nick": new_nick,
                "status": "PENDING",
                "requested_at": datetime.utcnow().isoformat(),
            }
            delegates_storage.set(f"pending_office_{user_id}", pending_record)

            approval_ch = find_approval_channel(final_ix.guild, final_ix.channel)
            sig_note = f"✅ İmzacı Devlet (`{existing_sid}`)" if existing_sid else "⚠️ Şartı henüz imzalamamış devlet *(Gözlemci / Üye adayı)*"
            embed = discord.Embed(
                title="👤 BAKANLIK & GÖREV KAYDI TALEBİ · OFFICE REGISTRATION",
                description=(
                    f"• **Başvuran / Applicant:** <@{user_id}>\n"
                    f"• **Devlet / State:** `{canonical_nation}` (`{country_short}`) ({sig_note})\n"
                    f"• **Oyun İçi Tam Ad / In-Game Name:** `{name_clean}`\n"
                    f"• **Yazılan Görev / Stated Duty:** `{duty_raw}`\n"
                    f"• **Milliyet & Dil / Identity:** {identity_summary}\n"
                    f"• **Otomatik Rol / Auto Role:** `{classified['role_name']}` (`{classified['code']}`)\n"
                    f"• **Otomatik Ülke Rolü:** `{_country_role_name(canonical_nation)}` (`{country_short}`)"
                ),
                colour=0x2563EB,
            )
            view = OfficeApprovalView(user_id)
            final_ix.client.add_view(view)
            await approval_ch.send(embed=embed, view=view)

            id_hint = "" if identity_codes else "\n• *(Milliyet/dil rolünüz yoksa `#🌍│uluslar-nations` kanalından veya `/milliyet-sec` komutuyla seçebilirsiniz.)*"
            await _send_final_ephemeral(
                final_ix,
                f"📩 **{canonical_nation}** — **`{duty_raw}`** görev kaydınız Genel Sekreterlik onayına iletildi!\n"
                f"• **Oyun İçi Ad:** `{name_clean}` · **Milliyet:** {identity_summary}\n"
                f"• *(Onaylandığında sunucu erişiminiz ve ülke/görev rolleriniz otomatik açılacaktır.)*{id_hint}",
            )

        await _prompt_or_finalize_country(
            interaction,
            country_clean,
            finalize_fn=_finalize,
            skip_key=normalize_country_key(existing_rec.get("nation", "")),
        )


class RegistrationGateModal(_SafeErrorModal, title="Kayıt & Akreditasyon · Registration"):
    rep_name = discord.ui.TextInput(
        label="Oyun İçi Tam Adınız / Full In-Game Name",
        placeholder="Örn: Ahmet Yılmaz (en az 3 harf)",
        required=True,
        max_length=32,
    )
    country = discord.ui.TextInput(
        label="Ülke Tam Adı (Alfabe) / Full Country Name",
        placeholder="Yalnızca harflerle tam ad (Örn: Türkiye Cumhuriyeti)",
        required=True,
        max_length=70,
    )

    async def on_submit(self, interaction: discord.Interaction):
        ok_n, name_clean, err_n = validate_ingame_name(self.rep_name.value)
        if not ok_n:
            return await interaction.response.send_message(err_n, ephemeral=True)
        ok_c, country_clean, err_c = validate_country_name(self.country.value)
        if not ok_c:
            return await interaction.response.send_message(err_c, ephemeral=True)

        matches = find_country_matches(country_clean, interaction.guild)
        initial_identity = _infer_member_identity_codes(interaction.user, delegates_storage.get(str(interaction.user.id)))
        view = RegistrationStep2View(
            user_id=interaction.user.id,
            name_clean=name_clean,
            typed_nation=country_clean,
            exact_match=matches.get("exact"),
            similar_matches=matches.get("similar") or [],
            initial_identity_codes=initial_identity,
        )
        await interaction.response.send_message(embed=view.build_embed(), view=view, ephemeral=True)


class RoleUpdateRequestModal(_SafeErrorModal, title="Rol Güncelleme Talebi · Role Update"):
    country = discord.ui.TextInput(
        label="Devlet Tam Adı (Alfabe) / Full Country Name",
        placeholder="Sadece harflerle ülke adı (Örn: Türkiye Cumhuriyeti)",
        required=True,
        max_length=70,
    )
    new_duty = discord.ui.TextInput(
        label="Yeni Görev veya Rol Değişikliği",
        placeholder="Örn: Ekonomi Bakanı, Dışişleri Bakanı, Başkan, Görevim yok",
        required=True,
        max_length=70,
    )
    reason = discord.ui.TextInput(
        label="Gerekçe / Ülke Rol Notu (Opsiyonel)",
        style=discord.TextStyle.paragraph,
        placeholder="Kabine değişikliği, ülke rolü güncelleme vb. (isteğe bağlı)...",
        required=False,
        max_length=300,
    )

    def __init__(self, current_nation: str = ""):
        super().__init__()
        if current_nation:
            self.country.default = current_nation[:70]

    async def on_submit(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        ok_c, country_clean, err_c = validate_country_name(self.country.value)
        if not ok_c:
            return await interaction.response.send_message(err_c, ephemeral=True)

        duty_raw = self.new_duty.value.strip()
        reason_clean = (self.reason.value or "Temsilci / ülke rol güncelleme talebi").strip()
        existing_rec = delegates_storage.get(user_id, {})
        final_identity_codes = _infer_member_identity_codes(interaction.user, existing_rec)
        identity_summary = format_identity_summary(final_identity_codes)

        async def _finalize(final_ix: discord.Interaction, chosen_nation: str, *, force_distinct: bool = False):
            _sid, existing_sig = _find_existing_charter_signature(chosen_nation, exact_only=force_distinct)
            canonical_nation = existing_sig["nation"] if existing_sig else chosen_nation

            classified = classify_office_role(duty_raw)
            rep_name = existing_rec.get("rep_name") or final_ix.user.display_name
            country_short = generate_country_short(canonical_nation)
            new_nick = format_delegate_nick(country_short, classified["code"], rep_name)
            req_id = f"RU-{user_id}-{int(datetime.utcnow().timestamp())}"

            req_data = {
                "req_id": req_id,
                "user_id": user_id,
                "old_nation": existing_rec.get("nation"),
                "old_office": existing_rec.get("official_title", "Yok"),
                "nation": canonical_nation,
                "country_short": country_short,
                "rep_name": rep_name,
                "official_title": duty_raw,
                "office": classified["display_label"],
                "office_code": classified["code"],
                "office_role_name": classified["role_name"],
                "identity_codes": final_identity_codes,
                "identity_summary": identity_summary,
                "nick": new_nick,
                "reason": reason_clean,
                "status": "PENDING",
                "requested_at": datetime.utcnow().isoformat(),
            }
            delegates_storage.set(f"roleupd_{req_id}", req_data)

            approval_ch = find_approval_channel(final_ix.guild, final_ix.channel)
            embed = discord.Embed(
                title=f"🔄 ROL GÜNCELLEME TALEBİ · ROLE UPDATE ({req_id})",
                description=(
                    f"• **Talep Eden / Requester:** <@{user_id}>\n"
                    f"• **Mevcut Kayıt:** `{existing_rec.get('nation', 'Yeni')}` · `{existing_rec.get('official_title', '-')}`\n"
                    f"• **Yeni Devlet / Target State:** `{canonical_nation}` (`{country_short}`)\n"
                    f"• **Yeni Görev / New Duty:** `{duty_raw}`\n"
                    f"• **Milliyet & Dil / Identity:** {identity_summary}\n"
                    f"• **Atanacak Rol / New Auto Role:** `{classified['role_name']}` (`{classified['code']}`)\n"
                    f"• **Hedef Takma Ad / Target Nick:** `{new_nick}`\n"
                    f"• **Gerekçe / Reason:** {reason_clean}"
                ),
                colour=0x0EA5E9,
            )
            view = RoleUpdateApprovalView(req_id)
            final_ix.client.add_view(view)
            await approval_ch.send(embed=embed, view=view)

            await _send_final_ephemeral(
                final_ix,
                f"📩 **Rol güncelleme talebiniz (`{req_id}`) Genel Sekreterliğe iletildi!** Onaylandığında rolleriniz otomatik yenilenecek ve DM ile bildirilecektir.",
            )

        await _prompt_or_finalize_country(
            interaction,
            country_clean,
            finalize_fn=_finalize,
            skip_key=normalize_country_key(existing_rec.get("nation", "")),
        )


class ApprovalActionView(_SafeErrorView):
    def __init__(self, target_user_id: str):
        super().__init__(timeout=None)
        self.target_user_id = target_user_id

        b_founding = discord.ui.Button(
            label="Kurucu · Founding (Art. 6)",
            style=discord.ButtonStyle.success,
            emoji="🌟",
            custom_id=f"mc_appr_fnd:{target_user_id}",
        )
        b_founding.callback = self.btn_approve_founding
        self.add_item(b_founding)

        b_member = discord.ui.Button(
            label="Üye · Member (Art. 7)",
            style=discord.ButtonStyle.primary,
            emoji="✅",
            custom_id=f"mc_appr_mem:{target_user_id}",
        )
        b_member.callback = self.btn_approve_member
        self.add_item(b_member)

        b_observer = discord.ui.Button(
            label="Gözlemci · Observer",
            style=discord.ButtonStyle.secondary,
            emoji="👁️",
            custom_id=f"mc_appr_obs:{target_user_id}",
        )
        b_observer.callback = self.btn_approve_observer
        self.add_item(b_observer)

        b_reject = discord.ui.Button(
            label="Reddet · Reject",
            style=discord.ButtonStyle.danger,
            emoji="❌",
            custom_id=f"mc_appr_rej:{target_user_id}",
        )
        b_reject.callback = self.btn_reject
        self.add_item(b_reject)

    async def _finalize_approval(self, interaction: discord.Interaction, country_status: str, register_in_charter: bool):
        if not is_secretariat_or_admin(interaction.user):
            return await interaction.response.send_message(
                "❌ Yalnızca Genel Sekreterlik veya Yöneticiler onay verebilir. / Secretariat or Admin only.",
                ephemeral=True,
            )

        pending = delegates_storage.get(f"pending_{self.target_user_id}")
        if not pending:
            return await interaction.response.send_message(
                "⚠️ Bu başvuru zaten işlenmiş veya bulunamadı. / Request already processed.",
                ephemeral=True,
            )

        await interaction.response.defer()
        now_str = datetime.utcnow().strftime("%d.%m.%Y %H:%M UTC")
        sign_id = None
        duplicate_note = ""

        if register_in_charter:
            existing_sid, existing_sig = _find_existing_charter_signature(pending["nation"])
            if existing_sid:
                sign_id = existing_sid
                pending["nation"] = existing_sig["nation"]
                crec = ensure_country_record(pending["nation"])
                if crec.get("status") in {"FOUNDING", "MEMBER"}:
                    country_status = crec["status"]
                duplicate_note = f"\nℹ️ *({existing_sig['nation']} Şartı daha önce imzaladığı için ek imza satırı açılmadı.)*"
            else:
                all_sigs = signatories_storage.read()
                sign_id = f"MC-KS-001/SIGN-{len(all_sigs) + 1:03d}"
                sig_record = {
                    "sign_id": sign_id,
                    "nation": pending["nation"],
                    "nation_key": normalize_country_key(pending["nation"]),
                    "stem": canonical_sovereign_stem(pending["nation"]),
                    "rep_name": pending["rep_name"],
                    "official_title": pending["official_title"],
                    "office_code": pending["office_code"],
                    "user_id": self.target_user_id,
                    "status": country_status,
                    "tier": "Kurucu Üye / Founding State (Art. 6)" if country_status == "FOUNDING" else "Üye Devlet / Member State (Art. 7)",
                    "signed_at": now_str,
                    "approved_by": str(interaction.user.id),
                }
                signatories_storage.set(sign_id, sig_record)

                crec = ensure_country_record(pending["nation"])
                crec["status"] = country_status
                save_country_record(crec)
        else:
            crec = ensure_country_record(pending["nation"])
            if country_status == "OBSERVER":
                crec["status"] = "OBSERVER"
                save_country_record(crec)

        pending["status"] = "APPROVED"
        pending["country_status"] = country_status
        pending["sign_id"] = sign_id
        pending["approved_by"] = str(interaction.user.id)
        pending["registered_at"] = datetime.utcnow().isoformat()
        delegates_storage.set(self.target_user_id, pending)
        delegates_storage.delete(f"pending_{self.target_user_id}")
        delegates_storage.delete(f"pending_office_{self.target_user_id}")

        propagated_cnt = await _propagate_country_status_to_members(
            interaction.guild,
            nation=pending["nation"],
            country_status=country_status,
            sign_id=sign_id,
            exclude_uid=self.target_user_id,
        )

        member = await resolve_member(interaction.guild, int(self.target_user_id))
        granted_roles: list[str] = []
        if member:
            office_role_name = pending.get("office_role_name") or classify_office_role(pending.get("official_title", ""))["role_name"]
            if pending.get("identity_codes"):
                await apply_identity_roles(interaction.guild, member, pending["identity_codes"], update_delegate=False)
            granted_roles = await _apply_rp_roles(
                interaction.guild,
                member,
                nation_clean=pending["nation"],
                office_role_name=office_role_name,
                country_status=country_status,
                new_nick=pending.get("nick"),
                strip_previous_offices=True,
            )
            try:
                await member.send(
                    embed=discord.Embed(
                        title="✅ Akreditasyon & Şart İmzanız Onaylandı!",
                        description=(
                            f"**{pending['nation']}** adına yaptığınız başvuru Genel Sekreterlik tarafından onaylandı.\n"
                            f"• **Görev / Office:** `{pending['official_title']}` (`{pending['office_code']}`)\n"
                            + (f"• **İmza Sicil No:** `{sign_id}`\n" if sign_id else "")
                            + f"• **Atanan Yapışkan Roller:** {', '.join(f'`{r}`' for r in granted_roles) or '`Mevcut`'}"
                        ),
                        colour=0x10B981,
                    )
                )
            except Exception:
                pass

        for item in self.children:
            item.disabled = True
        tier_label = STATUS_LABELS.get(country_status, "Observer / Staff")
        await interaction.edit_original_response(
            content=(
                f"✅ **ONAYLANDI / APPROVED** — <@{self.target_user_id}> (`{pending['nick']}`) · `{tier_label}`"
                + (f" · `{sign_id}`" if sign_id else "")
                + duplicate_note
            ),
            view=self,
        )

        # Auto-sync #imzaci-devletler-signatories live roll & Official Gazette
        if register_in_charter and sign_id:
            await sync_signatories_channel(interaction.guild)
            if not duplicate_note:
                await publish_to_gazette(
                    interaction.client,
                    interaction.guild_id,
                    "ŞART KATILIMI · CHARTER ACCESSION (MD./ART. 39)",
                    f"{pending['nation']} — Milletler Cemiyeti'ne Katıldı / Joined the League",
                    (
                        f"**{pending['nation']}**, `MC-KS-001` Kuruluş Şartı'nı dijital olarak imzalayıp onaylandı.\n"
                        f"*{pending['nation']} has digitally signed the MC-KS-001 Charter and acceded.*"
                    ),
                    fields=[
                        ("İmza ID / Sign ID", f"`{sign_id}`", True),
                        ("Temsilci / Delegate", f"{pending['rep_name']} ({pending['official_title']})", True),
                        ("Statü / Tier", "Kurucu Üye / Founding" if country_status == "FOUNDING" else "Üye Devlet / Member", True),
                    ],
                    color=0x10B981,
                )

    async def btn_approve_founding(self, interaction: discord.Interaction):
        await self._finalize_approval(interaction, "FOUNDING", True)

    async def btn_approve_member(self, interaction: discord.Interaction):
        await self._finalize_approval(interaction, "MEMBER", True)

    async def btn_approve_observer(self, interaction: discord.Interaction):
        await self._finalize_approval(interaction, "OBSERVER", False)

    async def btn_reject(self, interaction: discord.Interaction):
        if not is_secretariat_or_admin(interaction.user):
            return await interaction.response.send_message("❌ Yalnızca Genel Sekreterlik veya Yöneticiler reddedebilir.", ephemeral=True)
        delegates_storage.delete(f"pending_{self.target_user_id}")
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content=f"❌ **REDDEDİLDİ / REJECTED** — <@{self.target_user_id}> · <@{interaction.user.id}>",
            view=self,
        )


class OfficeApprovalView(_SafeErrorView):
    def __init__(self, target_user_id: str):
        super().__init__(timeout=None)
        self.target_user_id = target_user_id

        b_ok = discord.ui.Button(label="Onayla & Rol Ver · Approve", style=discord.ButtonStyle.success, emoji="✅", custom_id=f"mc_off_ok:{target_user_id}")
        b_ok.callback = self.btn_approve
        self.add_item(b_ok)

        b_no = discord.ui.Button(label="Reddet · Reject", style=discord.ButtonStyle.danger, emoji="❌", custom_id=f"mc_off_no:{target_user_id}")
        b_no.callback = self.btn_reject
        self.add_item(b_no)

    async def btn_approve(self, interaction: discord.Interaction):
        if not is_secretariat_or_admin(interaction.user):
            return await interaction.response.send_message("❌ Yalnızca Genel Sekreterlik veya Yöneticiler onay verebilir.", ephemeral=True)
        pending = delegates_storage.get(f"pending_office_{self.target_user_id}")
        if not pending:
            return await interaction.response.send_message("⚠️ Başvuru zaten işlenmiş.", ephemeral=True)

        await interaction.response.defer()
        canonical_nation, sig_sid, resolved_status, crec = _resolve_authoritative_country_status(pending["nation"])
        pending["nation"] = canonical_nation
        country_status = "OBSERVER" if pending.get("office_code") == "Obs." else resolved_status

        pending["status"] = "APPROVED"
        pending["country_status"] = country_status
        pending["sign_id"] = sig_sid
        pending["approved_by"] = str(interaction.user.id)
        pending["registered_at"] = datetime.utcnow().isoformat()
        delegates_storage.set(self.target_user_id, pending)
        delegates_storage.delete(f"pending_office_{self.target_user_id}")

        member = await resolve_member(interaction.guild, int(self.target_user_id))
        if member:
            if pending.get("identity_codes"):
                await apply_identity_roles(interaction.guild, member, pending["identity_codes"], update_delegate=False)
            await _apply_rp_roles(
                interaction.guild,
                member,
                nation_clean=pending["nation"],
                office_role_name=pending["office_role_name"],
                country_status=country_status,
                new_nick=pending.get("nick"),
                strip_previous_offices=True,
            )
            try:
                await member.send(f"✅ **{pending['nation']}** — `{pending['official_title']}` görev kaydınız onaylandı ve yapışkan rolleriniz atandı!")
            except Exception:
                pass

        for item in self.children:
            item.disabled = True
        await interaction.edit_original_response(
            content=f"✅ **GÖREV ONAYLANDI** — <@{self.target_user_id}> (`{pending['nick']}` · `{pending['office_role_name']}`)",
            view=self,
        )

    async def btn_reject(self, interaction: discord.Interaction):
        if not is_secretariat_or_admin(interaction.user):
            return await interaction.response.send_message("❌ Yalnızca Genel Sekreterlik veya Yöneticiler reddedebilir.", ephemeral=True)
        delegates_storage.delete(f"pending_office_{self.target_user_id}")
        cur = delegates_storage.get(self.target_user_id)
        if isinstance(cur, dict) and cur.get("status") == "PROVISIONAL":
            cur["status"] = "APPROVED"
            cur["official_title"] = "Vatandaş / Görevim Yok"
            cur["office"] = "🪪 Vatandaş / Görevi Yok"
            cur["office_code"] = "Vatandaş"
            cur["office_role_name"] = ROLE_CITIZEN
            cur.pop("requested_office", None)
            cur.pop("requested_office_code", None)
            cur.pop("requested_office_role", None)
            cur["updated_at"] = datetime.utcnow().isoformat()
            delegates_storage.set(self.target_user_id, cur)
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content=f"❌ **REDDEDİLDİ** — <@{self.target_user_id}>", view=self)


class RoleUpdateApprovalView(_SafeErrorView):
    def __init__(self, req_id: str):
        super().__init__(timeout=None)
        self.req_id = req_id

        b_ok = discord.ui.Button(label="Onayla & Güncelle · Apply", style=discord.ButtonStyle.success, emoji="✅", custom_id=f"mc_ru_ok:{req_id}")
        b_ok.callback = self.btn_approve
        self.add_item(b_ok)

        b_no = discord.ui.Button(label="Reddet · Reject", style=discord.ButtonStyle.danger, emoji="❌", custom_id=f"mc_ru_no:{req_id}")
        b_no.callback = self.btn_reject
        self.add_item(b_no)

    async def btn_approve(self, interaction: discord.Interaction):
        if not is_secretariat_or_admin(interaction.user):
            return await interaction.response.send_message("❌ Yalnızca Genel Sekreterlik veya Yöneticiler onay verebilir.", ephemeral=True)
        req = delegates_storage.get(f"roleupd_{self.req_id}")
        if not req:
            return await interaction.response.send_message("⚠️ Bu güncelleme talebi zaten işlenmiş.", ephemeral=True)

        await interaction.response.defer()
        uid = str(req["user_id"])
        existing_rec = delegates_storage.get(uid, {})
        old_nation = existing_rec.get("nation") or req.get("old_nation")
        canonical_nation, sig_sid, resolved_status, crec = _resolve_authoritative_country_status(req["nation"])
        req["nation"] = canonical_nation
        sig_rec = signatories_storage.get(sig_sid) if sig_sid else None
        country_status = "OBSERVER" if req.get("office_code") == "Obs." else resolved_status

        updated_rec = {
            **existing_rec,
            "user_id": uid,
            "nation": req["nation"],
            "country_short": req["country_short"],
            "rep_name": req["rep_name"],
            "official_title": req["official_title"],
            "office": req["office"],
            "office_code": req["office_code"],
            "office_role_name": req["office_role_name"],
            "nick": req["nick"],
            "identity_codes": req.get("identity_codes") or existing_rec.get("identity_codes", []),
            "identity_summary": req.get("identity_summary") or existing_rec.get("identity_summary", ""),
            "country_status": country_status,
            "sign_id": sig_sid or existing_rec.get("sign_id"),
            "status": "APPROVED",
            "approved_by": str(interaction.user.id),
            "updated_at": datetime.utcnow().isoformat(),
        }
        delegates_storage.set(uid, updated_rec)
        delegates_storage.delete(f"roleupd_{self.req_id}")

        # Keep Signatories roll entry in sync if user is the registered signatory for that nation
        if sig_sid and isinstance(sig_rec, dict) and str(sig_rec.get("user_id")) == uid:
            sig_rec["official_title"] = req["official_title"]
            sig_rec["office_code"] = req["office_code"]
            sig_rec["status_note"] = f"Güncellendi / Updated {datetime.utcnow().strftime('%d.%m.%Y')}"
            signatories_storage.set(sig_sid, sig_rec)
            await sync_signatories_channel(interaction.guild)

        member = await resolve_member(interaction.guild, int(uid))
        if member:
            if req.get("identity_codes"):
                await apply_identity_roles(interaction.guild, member, req["identity_codes"], update_delegate=False)
            await _apply_rp_roles(
                interaction.guild,
                member,
                nation_clean=req["nation"],
                office_role_name=req["office_role_name"],
                country_status=country_status,
                new_nick=req["nick"],
                strip_previous_offices=True,
                old_nation=old_nation,
            )
            try:
                await member.send(
                    f"🔄 **Rol Güncelleme Talebiniz Onaylandı (`{self.req_id}`)!**\n"
                    f"• **Devlet:** `{req['nation']}`\n"
                    f"• **Yeni Görev:** `{req['official_title']}` → `{req['office_role_name']}`"
                )
            except Exception:
                pass

        for item in self.children:
            item.disabled = True
        await interaction.edit_original_response(
            content=f"✅ **ROL GÜNCELLENDİ** — <@{uid}> → `{req['nation']}` · `{req['office_role_name']}`",
            view=self,
        )

    async def btn_reject(self, interaction: discord.Interaction):
        if not is_secretariat_or_admin(interaction.user):
            return await interaction.response.send_message("❌ Yalnızca Genel Sekreterlik veya Yöneticiler reddedebilir.", ephemeral=True)
        delegates_storage.delete(f"roleupd_{self.req_id}")
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content=f"❌ **ROL GÜNCELLEME REDDEDİLDİ** — `{self.req_id}`", view=self)


def _nation_match_tokens(nation_query: str) -> tuple[set[str], set[str]]:
    keys = {normalize_country_key(nation_query)} - {""}
    stems = {canonical_sovereign_stem(nation_query)} - {""}
    sig_sid, sig_rec = _find_existing_charter_signature(nation_query)
    if sig_sid and isinstance(sig_rec, dict) and sig_rec.get("nation"):
        keys.add(normalize_country_key(sig_rec["nation"]))
        st = canonical_sovereign_stem(sig_rec["nation"])
        if st:
            stems.add(st)
    return keys - {""}, stems - {""}


def _nation_matches_tokens(candidate_nation: str, keys: set[str], stems: set[str]) -> bool:
    if not candidate_nation:
        return False
    ck = normalize_country_key(candidate_nation)
    cs = canonical_sovereign_stem(candidate_nation)
    return (ck in keys) or bool(cs and cs in stems)


async def _find_head_of_government_candidates(
    guild: discord.Guild,
    nation_query: str,
) -> list[tuple[str, discord.Member | None, dict | None]]:
    """Return active Heads of State / Government matching nation_query."""
    keys, stems = _nation_match_tokens(nation_query)
    if not keys and not stems:
        return []

    by_uid: dict[str, tuple[discord.Member | None, dict | None]] = {}
    for uid, d in delegates_storage.read().items():
        if not isinstance(d, dict) or str(uid).startswith(("pending_", "roleupd_", "meta_")):
            continue
        if not str(uid).isdigit():
            continue
        d_nation = d.get("nation", "")
        if not _nation_matches_tokens(d_nation, keys, stems):
            continue
        member = await resolve_member(guild, int(uid))
        if is_head_of_state_or_government(member, d):
            by_uid[str(uid)] = (member, d)

    for member in getattr(guild, "members", ()):
        uid = str(member.id)
        if uid in by_uid:
            continue
        if not any(r.name == ROLE_PRESIDENT for r in getattr(member, "roles", ())):
            continue
        rec = delegates_storage.get(uid)
        rec_dict = rec if isinstance(rec, dict) else None
        member_nations = [r.name[2:].strip() for r in member.roles if r.name.startswith("🏳️ ")]
        if rec_dict and rec_dict.get("nation"):
            member_nations.append(rec_dict["nation"])
        if any(_nation_matches_tokens(mn, keys, stems) for mn in member_nations):
            by_uid[uid] = (member, rec_dict)

    return [(uid, pair[0], pair[1]) for uid, pair in by_uid.items()]


async def _demote_office_to_citizen(
    guild: discord.Guild,
    uid: str,
    *,
    member: discord.Member | None,
    rec: dict | None,
    strip_role_names: set[str],
    remove_reason: str,
    official_title: str,
    sig_official_title: str,
    sig_status_note: str,
    clear_chairs: bool = False,
    extra_rec_fields: dict | None = None,
) -> dict:
    """Strip office roles down to Citizen while keeping country/membership status & syncing sticky/signatory state."""
    rec_dict = rec if isinstance(rec, dict) else None
    if clear_chairs:
        _clear_member_chair_records(uid)

    nation = "-"
    if rec_dict and rec_dict.get("nation"):
        nation = rec_dict["nation"]
    elif member:
        nation = next((r.name[2:].strip() for r in member.roles if r.name.startswith("🏳️ ")), "-")

    short = (rec_dict.get("country_short") if rec_dict else "") or ""
    if not short and nation not in {"-", "Belirtilmedi", ""}:
        short = ensure_country_record(nation).get("short") or generate_country_short(nation)

    if rec_dict and rec_dict.get("rep_name"):
        rep_name = rec_dict["rep_name"]
    elif member:
        raw_disp = member.display_name
        if raw_disp.startswith("[") and "]" in raw_disp:
            raw_disp = raw_disp.split("]", 1)[1].strip()
        for prefix in ("Pres. ", "FM ", "IM ", "EM ", "DM ", "Del.B. "):
            if raw_disp.startswith(prefix):
                raw_disp = raw_disp[len(prefix):].strip()
                break
        rep_name = raw_disp or member.display_name
    else:
        rep_name = f"Üye {uid}"

    old_office = (rec_dict.get("official_title") or rec_dict.get("office")) if rec_dict else ROLE_PRESIDENT
    if not old_office or old_office == "-":
        old_office = ROLE_PRESIDENT

    removed_ids: set[int] = set()
    to_remove: list[discord.Role] = []
    if member:
        for r in member.roles:
            if (r.name in strip_role_names or _is_country_delegation_role_name(r.name)) and r.name != ROLE_CITIZEN:
                to_remove.append(r)
        if to_remove:
            mark_bot_role_op(guild.id, member.id)
            try:
                await member.remove_roles(*to_remove, reason=remove_reason)
                removed_ids.update(r.id for r in to_remove)
            except discord.Forbidden:
                for r in to_remove:
                    try:
                        await member.remove_roles(r, reason=remove_reason)
                        removed_ids.add(r.id)
                    except Exception:
                        logger.warning("Could not remove role %s from %s", r.name, uid)
            except Exception:
                logger.exception("Failed removing roles for %s during office demotion", uid)

    new_nick = f"[{short}] {rep_name}"[:32] if short else rep_name[:32]
    nick_note = ""
    if member:
        citizen_role = await ensure_role(guild, ROLE_CITIZEN, 0x64748B)
        if citizen_role:
            removed_ids.discard(citizen_role.id)
            if citizen_role not in member.roles:
                mark_bot_role_op(guild.id, member.id)
                try:
                    await member.add_roles(citizen_role, reason=remove_reason)
                except Exception:
                    pass
        nick_note = await _try_edit_nick(guild, member, new_nick)
        try:
            refreshed = await guild.fetch_member(member.id)
        except Exception:
            refreshed = await resolve_member(guild, member.id) or member
        save_sticky_snapshot(refreshed, active=True, resigned_role_ids=removed_ids, update_locked=True)
    else:
        citizen_role = discord.utils.get(guild.roles, name=ROLE_CITIZEN)
        offline_remove = [
            r
            for r in guild.roles
            if (r.name in strip_role_names or _is_country_delegation_role_name(r.name)) and r.name != ROLE_CITIZEN
        ]
        if str(uid).isdigit() and sticky_roles_storage.get(f"{guild.id}_{uid}"):
            _update_sticky_status_snapshot(
                guild,
                int(uid),
                add_role=citizen_role,
                remove_roles=offline_remove,
                member=None,
            )
            snap = sticky_roles_storage.get(f"{guild.id}_{uid}")
            if isinstance(snap, dict):
                snap["nick"] = new_nick
                sticky_roles_storage.set(f"{guild.id}_{uid}", snap)

    if rec_dict is not None:
        delegates_storage.delete(f"pending_office_{uid}")
        rec_dict["old_office"] = old_office
        rec_dict["status"] = "APPROVED"
        rec_dict["official_title"] = official_title
        rec_dict["office"] = "🪪 Vatandaş / Görevi Yok"
        rec_dict["office_code"] = "Vatandaş"
        rec_dict["office_role_name"] = ROLE_CITIZEN
        if clear_chairs:
            rec_dict.pop("chair_roles", None)
        rec_dict.pop("requested_office", None)
        rec_dict.pop("requested_office_code", None)
        rec_dict.pop("requested_office_role", None)
        rec_dict["nick"] = new_nick
        rec_dict["updated_at"] = datetime.utcnow().isoformat()
        if extra_rec_fields:
            rec_dict.update(extra_rec_fields)
        delegates_storage.set(uid, rec_dict)

    sig_sid, sig_rec = _find_existing_charter_signature(nation) if nation not in {"-", "Belirtilmedi", ""} else (None, None)
    if sig_sid and isinstance(sig_rec, dict) and str(sig_rec.get("user_id")) == uid:
        sig_rec["official_title"] = sig_official_title
        sig_rec["status_note"] = sig_status_note
        signatories_storage.set(sig_sid, sig_rec)
        await sync_signatories_channel(guild)

    return {
        "nation": nation,
        "short": short,
        "rep_name": rep_name,
        "old_office": old_office,
        "new_nick": new_nick,
        "nick_note": nick_note,
    }


async def _execute_government_fall_interaction(
    interaction: discord.Interaction,
    *,
    bot: commands.Bot | None,
    target_uid: str,
    reason_clean: str,
) -> None:
    """Apply Secretary-General's reasoned 'head of government fell' removal (expects deferred interaction)."""
    if not is_secretary_general_or_admin(interaction.user):
        return await interaction.followup.send(
            "⛔ **Yetki Yetersiz:** Bu işlem yalnızca **Genel Sekreter** veya **Sunucu Yöneticisi** tarafından yapılabilir.",
            ephemeral=True,
        )

    guild = interaction.guild
    member = await resolve_member(guild, int(target_uid)) if str(target_uid).isdigit() else None
    rec = delegates_storage.get(str(target_uid))
    if not is_head_of_state_or_government(member, rec if isinstance(rec, dict) else None):
        return await interaction.followup.send(
            f"❌ <@{target_uid}> şu anda aktif bir **Devlet / Hükûmet Başkanı (`{ROLE_PRESIDENT}`)** görevinde görünmüyor.",
            ephemeral=True,
        )

    now_utc = datetime.utcnow()
    demoted = await _demote_office_to_citizen(
        guild,
        str(target_uid),
        member=member,
        rec=rec if isinstance(rec, dict) else None,
        strip_role_names=MANAGED_OFFICE_ROLES - {ROLE_CITIZEN},
        remove_reason=f"MC-KS-001 Genel Sekreter Kararı · Hükûmet Başkanı Düştü: {reason_clean[:120]}",
        official_title="Hükûmet Başkanı Düştü · Vatandaş",
        sig_official_title="Hükûmet Düştü / Former Head of State",
        sig_status_note=f"Hükûmet Düştü {now_utc.strftime('%d.%m.%Y')}",
        clear_chairs=False,
        extra_rec_fields={
            "dismissed_from_office": (rec.get("official_title") or rec.get("office")) if isinstance(rec, dict) else ROLE_PRESIDENT,
            "dismissal_reason": reason_clean,
            "dismissed_by": str(interaction.user.id),
            "dismissed_at": now_utc.isoformat(),
        },
    )

    nation = demoted["nation"]
    rep_name = demoted["rep_name"]
    old_office = demoted["old_office"]
    issue_no = await publish_to_gazette(
        bot or interaction.client,
        interaction.guild_id,
        "HÜKÛMET BAŞKANI DÜŞTÜ · GOVERNMENT FELL",
        f"{nation} — Hükûmet Başkanı Görevden İndirildi",
        (
            f"**Devlet / State:** `{nation}`\n"
            f"**Görevden İndirilen / Removed:** <@{target_uid}> (`{rep_name}`)\n"
            f"**Önceki Makam / Former Office:** `{old_office}` → `🪪 Vatandaş`\n"
            f"**Gerekçe / Reason:** {reason_clean}\n"
            f"**Karar Makamı / Authority:** <@{interaction.user.id}> *(Genel Sekreterlik)*"
        ),
        fields=[
            ("Karar / Decree", "Hükûmet başkanı düştü · Removed from Office", True),
            ("Ülke & Vatandaşlık", "Korundu (Yapışkan rol & sicil güncellendi)", True),
        ],
        color=0xDC2626,
        issuer=f"{interaction.user.display_name} · Genel Sekreterlik / Secretariat",
    )

    audit_ch = resolve_panel_channel(guild, None, "denetim-kaydi-audit-log", None)
    if isinstance(audit_ch, discord.TextChannel):
        emb = discord.Embed(
            title="🏛️ HÜKÛMET BAŞKANI DÜŞTÜ · GÖREVDEN İNDİRME",
            description=(
                f"• **Devlet:** `{nation}`\n"
                f"• **Üye:** <@{target_uid}> (`{rep_name}`)\n"
                f"• **Önceki Makam:** `{old_office}` → `Vatandaş`\n"
                f"• **Genel Sekreter / Yetkili:** <@{interaction.user.id}>\n"
                f"• **Gerekçe:** {reason_clean}"
                + (f"\n• **Resmî Gazete:** `{issue_no}`" if issue_no else "")
            ),
            colour=0xDC2626,
            timestamp=datetime.now(timezone.utc),
        )
        emb.set_footer(text="MC-KS-001 Genel Sekreterlik Tasarrufu · Yapışkan Rol Güncellendi")
        try:
            await audit_ch.send(embed=emb)
        except Exception:
            pass

    if member:
        try:
            await member.send(
                f"🏛️ **Genel Sekreterlik Kararı — Hükûmet Başkanı Düştü**\n"
                f"**{nation}** adına yürüttüğünüz `{old_office}` görevi Genel Sekreterlik (<@{interaction.user.id}>) tarafından sonlandırılmıştır.\n"
                f"• **Gerekçe:** {reason_clean}\n"
                f"• **Güncel Statünüz:** `{nation}` Vatandaşı *(ülke ve vatandaşlık rolleriniz ile yapışkan rolünüz korundu)*."
                + (f"\n• **Resmî Gazete No:** `{issue_no}`" if issue_no else "")
            )
        except Exception:
            pass

    await interaction.followup.send(
        f"✅ **{nation}** hükûmet başkanı <@{target_uid}> (`{old_office}`) **\"Hükûmet başkanı düştü\"** kararıyla görevden indirildi.\n"
        f"• **Gerekçe:** {reason_clean}\n"
        f"• **Yeni Statü:** `Vatandaş` (`{demoted['new_nick']}` · yapışkan rol kilitlendi)"
        + (f"\n• **Resmî Gazete:** `{issue_no}`" if issue_no else "")
        + demoted["nick_note"],
        ephemeral=True,
    )


class GovernmentFallReasonModal(_SafeErrorModal):
    def __init__(self, bot: commands.Bot | None, target_uid: str, target_label: str):
        super().__init__(title="Gerekçeli Hükûmet Düşürme Kararı"[:45])
        self.bot = bot
        self.target_uid = str(target_uid)
        self.target_info = discord.ui.TextInput(
            label="Görevden İndirilecek Makam",
            default=target_label[:100],
            required=False,
            max_length=100,
        )
        self.reason_input = discord.ui.TextInput(
            label="Gerekçe (Hükûmet başkanı düştü)",
            style=discord.TextStyle.paragraph,
            placeholder="Örn: Mecliste güvenoyu alamadı, hükûmet başkanı düştü.",
            required=True,
            min_length=3,
            max_length=500,
        )
        self.add_item(self.target_info)
        self.add_item(self.reason_input)

    async def on_submit(self, interaction: discord.Interaction):
        if not is_secretary_general_or_admin(interaction.user):
            return await interaction.response.send_message(
                "⛔ Bu kararı yalnızca **Genel Sekreter** veya **Sunucu Yöneticisi** onaylayabilir.",
                ephemeral=True,
            )
        reason_clean = (self.reason_input.value or "").strip()
        if len(reason_clean) < 3:
            return await interaction.response.send_message(
                "❌ **Gerekçe Zorunlu:** Geçerli bir gerekçe girmelisiniz *(en az 3 karakter)*.",
                ephemeral=True,
            )
        await interaction.response.defer(ephemeral=True, thinking=True)
        await _execute_government_fall_interaction(
            interaction,
            bot=self.bot,
            target_uid=self.target_uid,
            reason_clean=reason_clean,
        )


async def _perform_resignation(interaction: discord.Interaction, full_leave: bool):
    uid = str(interaction.user.id)
    rec = delegates_storage.get(uid)
    member = interaction.user if isinstance(interaction.user, discord.Member) else await resolve_member(interaction.guild, interaction.user.id)
    role_strippable = [
        r for r in (getattr(member, "roles", None) or ())
        if r.name in (AUTHORITY_OFFICE_ROLES - {ROLE_CITIZEN}) or _is_country_delegation_role_name(r.name)
    ]
    if not rec and not role_strippable:
        return await interaction.response.send_message("ℹ️ Üzerinizde istifa edilecek aktif bir görev, basın veya temsilcilik rolü bulunmuyor.", ephemeral=True)

    await interaction.response.defer(ephemeral=True, thinking=True)
    if not rec:
        _clear_member_chair_records(uid)
        removed_ids = set()
        if member and role_strippable:
            mark_bot_role_op(interaction.guild_id, member.id)
            for r in role_strippable:
                try:
                    await member.remove_roles(r, reason="MC-KS-001 Kendi İsteğiyle İstifa / Voluntary Resignation")
                    removed_ids.add(r.id)
                except Exception:
                    pass
        if not full_leave and member:
            cit_role = await ensure_role(interaction.guild, ROLE_CITIZEN, 0x64748B)
            if cit_role and cit_role not in member.roles:
                try:
                    await member.add_roles(cit_role, reason="İstifa sonrası vatandaşlık rolü korundu")
                except Exception:
                    pass
        if member:
            save_sticky_snapshot(member, active=not full_leave, resigned_role_ids=removed_ids, update_locked=True)
        return await interaction.followup.send(
            f"✅ Üzerinizdeki tüm görev, basın ve temsilcilik rolleri kaldırıldı (`{len(removed_ids)}` rol); vatandaşlık ve ülke bilginiz korundu.",
            ephemeral=True,
        )
    old_office = rec.get("official_title") or rec.get("office", "-")
    nation = rec.get("nation", "-")
    rep_name = rec.get("rep_name") or (member.display_name if member else interaction.user.display_name)

    if full_leave:
        _clear_member_chair_records(uid)
        removed_ids: set[int] = set()
        to_remove: list[discord.Role] = []
        if member:
            for r in member.roles:
                if (
                    r.name in AUTHORITY_OFFICE_ROLES
                    or r.name in STATUS_ROLE_NAMES.values()
                    or r.name == ROLE_VERIFIED
                    or r.name == _country_role_name(nation)
                    or r.name.startswith("🏳️ ")
                    or _is_country_delegation_role_name(r.name)
                ):
                    to_remove.append(r)
            if to_remove:
                mark_bot_role_op(interaction.guild_id, member.id)
                try:
                    await member.remove_roles(*to_remove, reason="MC-KS-001 Kendi İsteğiyle İstifa / Voluntary Resignation")
                    removed_ids.update(r.id for r in to_remove)
                except discord.Forbidden:
                    for r in to_remove:
                        try:
                            await member.remove_roles(r, reason="MC-KS-001 Kendi İsteğiyle İstifa / Voluntary Resignation")
                            removed_ids.add(r.id)
                        except Exception:
                            logger.warning("Could not remove role %s from %s on resignation", r.name, uid)
                except Exception:
                    logger.exception("Failed removing roles on resignation for %s", uid)

        nick_note = ""
        if member:
            nick_note = await _try_edit_nick(interaction.guild, member, None)
            try:
                refreshed = await interaction.guild.fetch_member(member.id)
            except Exception:
                refreshed = await resolve_member(interaction.guild, member.id) or member
            save_sticky_snapshot(refreshed, active=False, resigned_role_ids=removed_ids, locked=False, update_locked=True)
        delegates_storage.delete(uid)
        delegates_storage.delete(f"pending_office_{uid}")
        summary_msg = f"🏳️ **{nation}** delegasyonundaki tüm görev ve ülke rollerinizden başarıyla istifa ettiniz.{nick_note}"

        # Mark resignation note cleanly on Signatory Roll if applicable
        sig_sid, sig_rec = _find_existing_charter_signature(nation)
        if sig_sid and isinstance(sig_rec, dict) and str(sig_rec.get("user_id")) == uid:
            sig_rec["official_title"] = "Ayrıldı / Resigned"
            sig_rec["status_note"] = f"İstifa / Resigned {datetime.utcnow().strftime('%d.%m.%Y')}"
            signatories_storage.set(sig_sid, sig_rec)
            await sync_signatories_channel(interaction.guild)
    else:
        demoted = await _demote_office_to_citizen(
            interaction.guild,
            uid,
            member=member,
            rec=rec,
            strip_role_names=AUTHORITY_OFFICE_ROLES - {ROLE_CITIZEN},
            remove_reason="MC-KS-001 Kendi İsteğiyle İstifa / Voluntary Resignation",
            official_title="Görevim Yok (İstifa Etti)",
            sig_official_title="Görevden İstifa (Vatandaş)",
            sig_status_note=f"İstifa / Resigned {datetime.utcnow().strftime('%d.%m.%Y')}",
            clear_chairs=True,
        )
        summary_msg = (
            f"🪪 **`{demoted['old_office']}`** görevinizden istifa ettiniz; **`{demoted['nation']}`** ülke kaydınız ve `Vatandaş` rolünüz korundu *(yapışkan rol güncellendi)*.{demoted['nick_note']}"
        )

    await publish_to_gazette(
        interaction.client,
        interaction.guild_id,
        "GÖREV İSTİFASI · RESIGNATION",
        f"{nation} — {rep_name} Görevinden İstifa Etti",
        f"**Temsilci:** <@{uid}> (`{rep_name}`)\n**Önceki Görev:** `{old_office}`\n**Tür:** {'Tam Delegasyon Ayrılığı' if full_leave else 'Görev İstifası (Ülke üyesi kaldı)'}",
        color=0x6B7280,
    )
    await interaction.followup.send(summary_msg, ephemeral=True)


class ResignChoiceView(_SafeErrorView):
    def __init__(self):
        super().__init__(timeout=180)

    @discord.ui.button(label="Sadece Görevimden İstifa Et · Resign Office", style=discord.ButtonStyle.primary, emoji="🪪")
    async def btn_office_only(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _perform_resignation(interaction, full_leave=False)

    @discord.ui.button(label="Tüm Ülke & Cemiyet Rollerimden Ayrıl · Leave All", style=discord.ButtonStyle.danger, emoji="🏳️")
    async def btn_full_leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _perform_resignation(interaction, full_leave=True)


class ResignationDeskView(_SafeErrorView):
    """Persistent panel allowing members to resign from all offices/press while keeping citizenship & country."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Tüm Görev, Temsilcilik & Basından İstifa Et (Vatandaş Kal)",
        style=discord.ButtonStyle.primary,
        emoji="🪪",
        custom_id="mc_resign_desk_office",
        row=0,
    )
    async def btn_office_only(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await _perform_resignation(interaction, full_leave=False)

    @discord.ui.button(
        label="Ülke & Cemiyet Dahil Tüm Rollerden Ayrıl · Leave All",
        style=discord.ButtonStyle.danger,
        emoji="🏳️",
        custom_id="mc_resign_desk_full",
        row=1,
    )
    async def btn_full_leave(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await _perform_resignation(interaction, full_leave=True)


def build_resignation_panel() -> tuple[discord.Embed, discord.ui.View]:
    embed = discord.Embed(
        title="📤 GÖREV, TEMSİLCİLİK & BASIN İSTİFA PANELİ · RESIGNATION DESK",
        description=(
            "Üzerinizdeki **tüm görev, temsilcilik, yargı/savcılık, moderasyon ve Akredite Basın** rollerinden tek tıkla istifa edebilirsiniz.\n\n"
            "• **🪪 Tüm Görev, Temsilcilik & Basından İstifa Et:** Tüm resmî görev, başkanlık, delegasyon ve basın etiketlerinizi kaldırır; "
            "**`🪪 Vatandaş`**, **kayıtlı ülkeniz (`🏳️ ...`)**, **milliyetiniz** ve **`✅ Kayıtlı Üye`** rolleriniz **korunur**.\n"
            "• **🏳️ Ülke & Cemiyet Dahil Tüm Rollerden Ayrıl:** Delegasyondan ve ülke kaydınızdan tamamen çıkış yapar."
        ),
        colour=0xEF4444,
    )
    embed.set_footer(text="MC-KS-001 Gönüllü İstifa Masası · Yapışkan rol otomatik güncellenir")
    return embed, ResignationDeskView()


async def _handle_gate_register(interaction: discord.Interaction):
    uid = str(interaction.user.id)
    existing = delegates_storage.get(uid)
    if existing and existing.get("status") in {"APPROVED", "PROVISIONAL"}:
        status_note = " *(üst görev onay bekliyor)*" if existing.get("status") == "PROVISIONAL" else ""
        return await interaction.response.send_message(
            f"ℹ️ Zaten **{existing['nation']}** adına kayıtlısınız (`{existing.get('official_title', '-')}`){status_note}.\n"
            f"Görev veya ülke değiştirmek için **`🔄 Rol Güncelle`**, ayrılmak için **`🚪 İstifa Et`** butonunu kullanın.",
            ephemeral=True,
        )
    await interaction.response.send_modal(RegistrationGateModal())


async def _handle_gate_charter_sign(interaction: discord.Interaction):
    uid = str(interaction.user.id)
    existing = delegates_storage.get(uid)
    if existing and existing.get("status") == "APPROVED":
        sig_sid = existing.get("sign_id")
        if not sig_sid and existing.get("nation"):
            sig_sid, _ = _find_existing_charter_signature(existing["nation"])
        if sig_sid or not can_member_sign_for_state(interaction.user, existing):
            return await interaction.response.send_message(
                f"ℹ️ Zaten **{existing['nation']}** adına kayıtlısınız (`{existing.get('official_title', '-')}`).\n"
                f"Görevinizi değiştirmek için **`🔄 Rol Güncelle`**, görevden ayrılmak için **`🚪 İstifa Et`** butonunu kullanın.",
                ephemeral=True,
            )
    await interaction.response.send_modal(CharterAccessionModal())


async def _handle_gate_role_update(interaction: discord.Interaction):
    existing = delegates_storage.get(str(interaction.user.id), {})
    await interaction.response.send_modal(RoleUpdateRequestModal(existing.get("nation", "")))


async def _handle_gate_resign(interaction: discord.Interaction):
    rec = delegates_storage.get(str(interaction.user.id))
    if not rec:
        return await interaction.response.send_message("ℹ️ Aktif bir göreviniz veya ülke kaydınız bulunmuyor.", ephemeral=True)
    await interaction.response.send_message(
        f"**🚪 İstifa Seçenekleri ({rec.get('nation')} · `{rec.get('official_title', '-')}`):**\n"
        f"• **Sadece Görevden İstifa:** Bakanlık/temsilci rolünüzü bırakıp vatandaş olarak ülkenizde kalırsınız.\n"
        f"• **Tamamen Ayrıl:** Ülke ve Cemiyet rollerinizin tamamı kaldırılır ve yapışkan rol kaydınız kapatılır.",
        view=ResignChoiceView(),
        ephemeral=True,
    )


async def _handle_gate_my_record(interaction: discord.Interaction):
    data = delegates_storage.get(str(interaction.user.id))
    if not data:
        return await interaction.response.send_message("ℹ️ Henüz kayıtlı bir temsilcilik/görev kaydınız yok.", ephemeral=True)
    snap = sticky_roles_storage.get(f"{interaction.guild_id}_{interaction.user.id}", {})
    pending_line = (
        f"• **Onay Bekleyen Görev:** `{data.get('requested_office_role')}` ⏳\n"
        if data.get("status") == "PROVISIONAL" and data.get("requested_office_role")
        else ""
    )
    emb = discord.Embed(
        title=f"🪪 Diplomatik Sicilim · {interaction.user.display_name}",
        description=(
            f"• **Devlet / State:** `{data.get('nation')}` (`{data.get('country_short')}`)\n"
            f"• **Yazılan Görev / Stated Duty:** `{data.get('official_title', '-')}`\n"
            f"• **Atanan Rol / Role:** `{data.get('office_role_name', data.get('office', '-'))}`\n"
            + pending_line
            + (f"• **Milliyet & Dil / Identity:** {data.get('identity_summary')}\n" if data.get("identity_summary") else "")
            + (f"• **Devlet Statüsü / Status:** `{STATUS_ROLE_NAMES.get(data.get('country_status'), data.get('country_status'))}`\n" if data.get("country_status") else "")
            + (f"• **Şart İmza No:** `{data.get('sign_id')}`\n" if data.get("sign_id") else "")
            + f"• **Yapışkan Rol Korunan Sayı:** `{len(snap.get('role_ids', []))}`"
        ),
        colour=0x1E3A8A,
    )
    await interaction.response.send_message(embed=emb, ephemeral=True)


class AccreditationGateView(_SafeErrorView):
    """Simplified 4-button entry Registration Gate on #🚪│kayit-masasi-register (Charter signing lives inside #📜│tuzuk-charter)."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Kayıt Ol & Rol Al · Register", style=discord.ButtonStyle.success, emoji="🪪", custom_id="mc_gate_register", row=0)
    async def btn_register(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _handle_gate_register(interaction)

    @discord.ui.button(label="Rol Güncelle · Role Update", style=discord.ButtonStyle.secondary, emoji="🔄", custom_id="mc_gate_role_update", row=0)
    async def btn_role_update(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _handle_gate_role_update(interaction)

    @discord.ui.button(label="Sicilim · My Record", style=discord.ButtonStyle.primary, emoji="🪪", custom_id="mc_gate_my_record", row=1)
    async def btn_my_record(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _handle_gate_my_record(interaction)

    @discord.ui.button(label="İstifa Et · Resign", style=discord.ButtonStyle.danger, emoji="🚪", custom_id="mc_gate_resign", row=1)
    async def btn_resign(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _handle_gate_resign(interaction)


class CharterSignPanelView(_SafeErrorView):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Şartı İmzala · Sign Charter", style=discord.ButtonStyle.success, emoji="✍️", custom_id="mc_charter_sign", row=0)
    async def btn_sign_charter(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _handle_gate_charter_sign(interaction)

    @discord.ui.button(label="Bakan / Görev Kaydı · Office", style=discord.ButtonStyle.primary, emoji="👤", custom_id="mc_duty_register", row=0)
    async def btn_register_office(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = str(interaction.user.id)
        existing = delegates_storage.get(uid)
        if existing and existing.get("status") in {"APPROVED", "PROVISIONAL"}:
            return await interaction.response.send_message(
                f"ℹ️ Zaten **{existing['nation']}** kaydınız var. Yeni görev için **`🔄 Rol Güncelle`** butonunu kullanın.",
                ephemeral=True,
            )
        await interaction.response.send_modal(OfficeRegisterModal())

    @discord.ui.button(label="Rol Güncelle · Role Update", style=discord.ButtonStyle.secondary, emoji="🔄", custom_id="mc_role_update", row=0)
    async def btn_role_update(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _handle_gate_role_update(interaction)

    @discord.ui.button(label="İstifa Et · Resign Office", style=discord.ButtonStyle.danger, emoji="🚪", custom_id="mc_role_resign", row=0)
    async def btn_resign(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _handle_gate_resign(interaction)

    @discord.ui.button(label="Sicilim · My Record", style=discord.ButtonStyle.secondary, emoji="🪪", custom_id="mc_my_dossier", row=1)
    async def btn_my_record(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _handle_gate_my_record(interaction)

    @discord.ui.button(label="Yapışkan Rol Eşitle · Sync", style=discord.ButtonStyle.secondary, emoji="📌", custom_id="mc_sticky_sync", row=1)
    async def btn_sticky_sync(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        member = interaction.user if isinstance(interaction.user, discord.Member) else await resolve_member(interaction.guild, interaction.user.id)
        if not member:
            return await interaction.followup.send("❌ Üye bilgisi alınamadı.", ephemeral=True)
        restored = await restore_sticky_roles_for_member(member)
        if restored:
            await interaction.followup.send(
                f"📌 **Yapışkan rolleriniz geri yüklendi:** {', '.join(f'`{r}`' for r in restored)}",
                ephemeral=True,
            )
        else:
            save_sticky_snapshot(member, active=True)
            await interaction.followup.send("✅ Tüm kayıtlı yapışkan rolleriniz zaten üzerinizde ve güncellendi.", ephemeral=True)

    @discord.ui.button(label="İmzacı Devletler · Signatories", style=discord.ButtonStyle.secondary, emoji="📜", custom_id="mc_view_signatories", row=1)
    async def btn_view_sigs(self, interaction: discord.Interaction, button: discord.ui.Button):
        sig_ch = find_signatories_channel(interaction.guild)
        all_sigs = [v for v in signatories_storage.read().values() if isinstance(v, dict)]
        ch_link = f"Tam sicil sayfası için: {sig_ch.mention}" if sig_ch else ""
        preview = "\n".join(
            f"• **{s['nation']}** [`{(get_country_record(s.get('nation', '')) or {}).get('short', generate_country_short(s.get('nation', '')))}`] (`{s['sign_id']}`) — {s['rep_name']} · `{s['official_title']}`"
            for s in all_sigs[-15:]
        ) or "*Henüz imzacı devlet yok.*"
        await interaction.response.send_message(
            embed=discord.Embed(
                title=f"✍️ İmzacı Devletler Özeti ({len(all_sigs)} Devlet)",
                description=f"{ch_link}\n\n{preview}"[:4000],
                colour=0x10B981,
            ),
            ephemeral=True,
        )


AccreditationDeskView = AccreditationGateView


_AUDIT_MISSING = object()


class DelegationCog(commands.Cog):
    """MC-KS-001 Charter Digital Signature, Signatories Roll, Auto-Roles, Resignation & Sticky Roles Cog."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._lock_restore_ts: dict[int, float] = {}
        self._handled_audit_ids: set[int] = set()
        self._handled_audit_order: deque[int] = deque(maxlen=400)
        self._audit_watermarks: dict[int, int] = {}
        self._nick_audit_watermarks: dict[int, int] = {}
        self._audit_lock = asyncio.Lock()

    async def cog_unload(self):
        if self.audit_role_watcher.is_running():
            self.audit_role_watcher.cancel()
        if self.charter_panel_watchdog.is_running():
            self.charter_panel_watchdog.cancel()

    def _mark_audit_seen(self, entry_id: int) -> bool:
        """Record an audit log entry ID; return False if already processed."""
        if entry_id in self._handled_audit_ids:
            return False
        if len(self._handled_audit_order) >= self._handled_audit_order.maxlen:
            evicted = self._handled_audit_order.popleft()
            self._handled_audit_ids.discard(evicted)
        self._handled_audit_order.append(entry_id)
        self._handled_audit_ids.add(entry_id)
        return True

    async def _send_audit_security_embed(
        self,
        guild: discord.Guild,
        *,
        title: str,
        description: str,
        member: discord.Member | None = None,
        dm_text: str | None = None,
    ) -> None:
        audit_ch = resolve_panel_channel(guild, None, "denetim-kaydi-audit-log", None)
        if isinstance(audit_ch, discord.TextChannel):
            emb = discord.Embed(
                title=title,
                description=description,
                colour=0xDC2626,
                timestamp=datetime.now(timezone.utc),
            )
            emb.set_footer(text="MC-KS-001 Manuel Değişiklik Koruma · Yalnızca Bot Butonları & Kurucu Aytmatov Muaf")
            try:
                await audit_ch.send(embed=emb)
            except Exception:
                pass
        if member and dm_text:
            try:
                await member.send(dm_text)
            except Exception:
                pass

    def _expected_nick_for_member(self, guild: discord.Guild, target_id: int, fallback_nick=None):
        rec = delegates_storage.get(str(target_id))
        if isinstance(rec, dict) and rec.get("status") in {"APPROVED", "PROVISIONAL"} and rec.get("nick"):
            return rec["nick"]
        snap = sticky_roles_storage.get(f"{guild.id}_{target_id}")
        if isinstance(snap, dict) and snap.get("active", True) and "nick" in snap:
            return snap["nick"]
        return fallback_nick

    async def _revert_self_role_entry(self, guild: discord.Guild, entry: discord.AuditLogEntry) -> bool:
        actor_id = getattr(entry.user, "id", None) if entry.user else None
        target_id = getattr(entry.target, "id", None) if entry.target else None
        if not actor_id or not target_id:
            return False
        if actor_id != target_id or target_id == guild.owner_id:
            return False
        if self.bot.user and actor_id == self.bot.user.id:
            return False
        if not self._mark_audit_seen(entry.id):
            return False

        try:
            member = await guild.fetch_member(target_id)
        except Exception:
            member = guild.get_member(target_id)
        if not member or member.bot:
            return False

        bot_top = guild.me.top_role.position if guild.me else 0
        raw_added = getattr(getattr(entry, "after", None), "roles", None) or []
        raw_removed = getattr(getattr(entry, "before", None), "roles", None) or []

        to_remove: list[discord.Role] = []
        for robj in raw_added:
            role = guild.get_role(robj.id)
            if role and not role.managed and not role.is_default() and role.position < bot_top and role in member.roles:
                to_remove.append(role)

        to_add: list[discord.Role] = []
        for robj in raw_removed:
            role = guild.get_role(robj.id)
            if role and not role.managed and not role.is_default() and role.position < bot_top and role not in member.roles:
                to_add.append(role)

        if not to_remove and not to_add:
            return False

        mark_bot_role_op(guild.id, member.id, seconds=18.0)
        reverted_removed: list[str] = []
        reverted_added: list[str] = []
        if to_remove:
            try:
                await member.remove_roles(*to_remove, reason="MC-KS-001 Yetkisiz Manuel Rol Alma Engellendi")
                reverted_removed = [r.name for r in to_remove]
            except Exception:
                pass
        if to_add:
            try:
                await member.add_roles(*to_add, reason="MC-KS-001 Yetkisiz Manuel Rol Silme İade Edildi")
                reverted_added = [r.name for r in to_add]
            except Exception:
                pass

        if not reverted_removed and not reverted_added:
            return False

        try:
            fresh = await guild.fetch_member(member.id)
            key = f"{guild.id}_{member.id}"
            snap = sticky_roles_storage.get(key)
            if isinstance(snap, dict) and snap.get("active", True):
                if snap.get("locked"):
                    await restore_sticky_roles_for_member(fresh)
                else:
                    save_sticky_snapshot(fresh, active=True)
        except Exception:
            pass

        await self._send_audit_security_embed(
            guild,
            title="⛔ YETKİSİZ MANUEL ROL DEĞİŞİKLİĞİ ENGELLENDİ",
            description=(
                f"**Üye:** <@{member.id}> (`{member}` · `{member.id}`)\n"
                f"**Tespit:** Üye Discord üzerinden kendi rolünü elle değiştirdi (`Audit ID: {entry.id}`).\n"
                f"**Geri Alınan Eklemeler:** {', '.join(f'`{n}`' for n in reverted_removed) or '*Yok*'}\n"
                f"**İade Edilen Silmeler:** {', '.join(f'`{n}`' for n in reverted_added) or '*Yok*'}"
            ),
            member=member,
            dm_text=(
                "⛔ **Yetkisiz Manuel Rol Değişikliği Engellendi**\n"
                "Discord profilinizden kendi kendinize elle rol ekleyemez veya kaldıramazsınız.\n"
                "Yaptığınız değişiklik otomatik olarak geri alındı; lütfen yalnızca bot butonlarını/panellerini kullanın."
            ),
        )
        return True

    async def _revert_manual_nick_entry(self, guild: discord.Guild, entry: discord.AuditLogEntry) -> bool:
        after_nick = getattr(getattr(entry, "after", None), "nick", _AUDIT_MISSING)
        before_nick = getattr(getattr(entry, "before", None), "nick", _AUDIT_MISSING)
        if after_nick is _AUDIT_MISSING and before_nick is _AUDIT_MISSING:
            return False

        actor_id = getattr(entry.user, "id", None) if entry.user else None
        target_id = getattr(entry.target, "id", None) if entry.target else None
        if not actor_id or not target_id or target_id == guild.owner_id:
            return False
        if self.bot.user and actor_id == self.bot.user.id:
            return False
        if actor_id == guild.owner_id and actor_id != target_id:
            return False
        if not self._mark_audit_seen(entry.id):
            return False

        try:
            member = await guild.fetch_member(target_id)
        except Exception:
            member = guild.get_member(target_id)
        if not member or member.bot:
            return False

        bot_top = guild.me.top_role.position if guild.me else 0
        if member.top_role.position >= bot_top:
            return False

        fallback_nick = None if before_nick is _AUDIT_MISSING else before_nick
        expected_nick = self._expected_nick_for_member(guild, target_id, fallback_nick=fallback_nick)
        if member.nick == expected_nick:
            return False

        attempted_nick = member.nick if after_nick is _AUDIT_MISSING else after_nick
        mark_bot_role_op(guild.id, member.id, seconds=18.0)
        try:
            await member.edit(nick=expected_nick, reason="MC-KS-001 Yetkisiz Manuel Takma Ad Değişikliği Engellendi")
        except Exception:
            return False

        await self._send_audit_security_embed(
            guild,
            title="⛔ YETKİSİZ MANUEL TAKMA AD DEĞİŞİKLİĞİ ENGELLENDİ",
            description=(
                f"**Hedef Üye:** <@{member.id}> (`{member.id}`)\n"
                f"**Değiştiren:** <@{actor_id}> (`Audit ID: {entry.id}`)\n"
                f"**Denenen Ad:** `{attempted_nick or '-'}` → **Geri Yüklenen:** `{expected_nick or '(Temizlendi)'}`"
            ),
            member=member if actor_id == target_id else None,
            dm_text=(
                "⛔ **Yetkisiz Takma Ad Değişikliği Engellendi**\n"
                "Sunucu takma adları yalnızca bot üzerinden yönetilir; yaptığınız manuel değişiklik otomatik olarak geri alındı."
            ),
        )
        return True

    async def _check_recent_audit_entries(self, guild: discord.Guild, member: discord.Member, *, action, handler) -> bool:
        if member.id == guild.owner_id or member.bot:
            return False
        if not is_bot_role_op_active(guild.id, member.id):
            await asyncio.sleep(0.7)
        now_utc = datetime.now(timezone.utc)
        reverted_any = False
        async with self._audit_lock:
            try:
                entries = [e async for e in guild.audit_logs(limit=10, action=action)]
            except Exception:
                return False
            for entry in sorted(entries, key=lambda x: x.id):
                if entry.id in self._handled_audit_ids:
                    continue
                if entry.created_at and (now_utc - entry.created_at) > timedelta(seconds=30):
                    continue
                if getattr(entry.target, "id", None) == member.id:
                    if await handler(guild, entry):
                        reverted_any = True
        return reverted_any

    async def _check_and_revert_for_member(self, member: discord.Member) -> bool:
        return await self._check_recent_audit_entries(
            member.guild,
            member,
            action=discord.AuditLogAction.member_role_update,
            handler=self._revert_self_role_entry,
        )

    async def _check_and_revert_nick_for_member(self, member: discord.Member) -> bool:
        return await self._check_recent_audit_entries(
            member.guild,
            member,
            action=discord.AuditLogAction.member_update,
            handler=self._revert_manual_nick_entry,
        )

    @tasks.loop(seconds=12)
    async def audit_role_watcher(self):
        async with self._audit_lock:
            watchers = (
                (discord.AuditLogAction.member_role_update, self._audit_watermarks, self._revert_self_role_entry),
                (discord.AuditLogAction.member_update, self._nick_audit_watermarks, self._revert_manual_nick_entry),
            )
            for guild in list(self.bot.guilds):
                for action, watermarks, handler in watchers:
                    try:
                        entries = [e async for e in guild.audit_logs(limit=25, action=action)]
                    except Exception:
                        continue
                    if guild.id not in watermarks:
                        watermarks[guild.id] = max((e.id for e in entries), default=0)
                        continue
                    watermark = watermarks[guild.id]
                    newer = sorted((e for e in entries if e.id > watermark), key=lambda x: x.id)
                    if newer:
                        watermarks[guild.id] = newer[-1].id
                        for entry in newer:
                            await handler(guild, entry)

    @tasks.loop(minutes=5)
    async def charter_panel_watchdog(self):
        for g in list(self.bot.guilds):
            try:
                st = await ensure_charter_signing_panel(g)
                if st and st not in ("ok", "busy"):
                    logger.warning("Charter watchdog restored signing panel (guild=%s, status=%s).", g.id, st)
            except Exception as exc:
                logger.warning("Charter watchdog check failed in %s: %s", g.id, exc)

    @charter_panel_watchdog.before_loop
    async def _before_charter_watchdog(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener("on_ready")
    async def on_ready_start_audit_watcher(self):
        if not self.audit_role_watcher.is_running():
            self.audit_role_watcher.start()
        if not getattr(self, "_charter_pages_verified", False):
            self._charter_pages_verified = True
            for g in self.bot.guilds:
                try:
                    st = await ensure_charter_signing_panel(g)
                    if st and st not in ("ok", "busy"):
                        logger.info("Charter signing panel verified/restored on startup (guild=%s, status=%s).", g.id, st)
                except Exception as exc:
                    logger.warning("Charter signing panel startup check failed in %s: %s", g.id, exc)
        if not self.charter_panel_watchdog.is_running():
            self.charter_panel_watchdog.start()
        if not getattr(self, "_country_statuses_synced", False):
            self._country_statuses_synced = True
            for g in self.bot.guilds:
                try:
                    await sync_all_delegates_country_statuses(g, check_live_roles=True)
                except Exception as exc:
                    print(f"[sync_all_delegates_country_statuses] error in {g.id}: {exc}")

    async def _maybe_restore_charter_on_event(self, guild_id: int | None, channel_id: int, event_kind: str) -> None:
        if not guild_id or guild_id in _CHARTER_SYNCING_GUILDS:
            return
        if event_kind == "edit" and (time.monotonic() - _CHARTER_LAST_SYNC_TS.get(guild_id, 0.0)) < 5.0:
            return
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return
        charter_ch = resolve_panel_channel(guild, None, "tuzuk-charter", None)
        if not charter_ch or channel_id != charter_ch.id:
            return
        await asyncio.sleep(1.5)
        if guild_id in _CHARTER_SYNCING_GUILDS:
            return
        try:
            st = await ensure_charter_signing_panel(guild, charter_ch)
            if st and st not in ("ok", "busy"):
                logger.warning(
                    "Charter signing panel auto-restored after %s (guild=%s, channel=%s, status=%s).",
                    event_kind,
                    guild.id,
                    charter_ch.id,
                    st,
                )
        except Exception as exc:
            logger.warning("Charter signing panel auto-restore failed in %s: %s", guild.id, exc)

    @commands.Cog.listener("on_raw_message_delete")
    async def on_raw_message_delete_restore_charter(self, payload: discord.RawMessageDeleteEvent):
        await self._maybe_restore_charter_on_event(payload.guild_id, payload.channel_id, "delete")

    @commands.Cog.listener("on_raw_bulk_message_delete")
    async def on_raw_bulk_message_delete_restore_charter(self, payload: discord.RawBulkMessageDeleteEvent):
        await self._maybe_restore_charter_on_event(payload.guild_id, payload.channel_id, "bulk_delete")

    @commands.Cog.listener("on_raw_message_edit")
    async def on_raw_message_edit_restore_charter(self, payload: discord.RawMessageUpdateEvent):
        await self._maybe_restore_charter_on_event(payload.guild_id, payload.channel_id, "edit")

    async def cog_load(self):
        self.bot.add_view(AccreditationGateView())
        self.bot.add_view(CharterSignPanelView())
        self.bot.add_view(IdentitySelectView())
        self.bot.add_view(SGAppointmentDeskView())
        self.bot.add_view(ResignationDeskView())
        for key in delegates_storage.read():
            if not isinstance(key, str):
                continue
            if key.startswith("pending_office_"):
                self.bot.add_view(OfficeApprovalView(key.removeprefix("pending_office_")))
            elif key.startswith("pending_"):
                self.bot.add_view(ApprovalActionView(key.removeprefix("pending_")))
            elif key.startswith("roleupd_"):
                self.bot.add_view(RoleUpdateApprovalView(key.removeprefix("roleupd_")))

    @commands.Cog.listener("on_member_join")
    async def on_member_join_sticky(self, member: discord.Member):
        await restore_sticky_roles_for_member(member)

    @commands.Cog.listener("on_member_update")
    async def on_member_update_sticky(self, before: discord.Member, after: discord.Member):
        if after.bot or (before.roles == after.roles and before.nick == after.nick):
            return
        if before.roles != after.roles:
            if await self._check_and_revert_for_member(after):
                return
        if before.nick != after.nick:
            if await self._check_and_revert_nick_for_member(after):
                return
        key = f"{after.guild.id}_{after.id}"
        snap = sticky_roles_storage.get(key)
        if not isinstance(snap, dict) or not snap.get("active", True):
            return
        if snap.get("locked"):
            now = time.monotonic()
            if now - self._lock_restore_ts.get(after.id, 0.0) < 5.0:
                return
            self._lock_restore_ts[after.id] = now
            await restore_sticky_roles_for_member(after)
        elif before.roles != after.roles:
            save_sticky_snapshot(after, active=True)

    @commands.Cog.listener("on_member_remove")
    async def on_member_remove_sticky(self, member: discord.Member):
        if not member.bot:
            key = f"{member.guild.id}_{member.id}"
            snap = sticky_roles_storage.get(key)
            if isinstance(snap, dict) and snap.get("active", True):
                save_sticky_snapshot(member, active=True)

    @commands.Cog.listener("on_interaction")
    async def on_interaction_sticky_restore(self, interaction: discord.Interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member) or interaction.user.bot:
            return
        await restore_sticky_roles_for_member(interaction.user)

    @app_commands.command(name="tuzuk-yayinla", description="Kuruluş Şartı'nı (MC-KS-001) parça parça okunabilir şekilde ve altındaki imza paneliyle yayınlar")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(kanal="İsteğe bağlı hedef kanal (boş bırakılırsa otomatik #tuzuk-charter kanalına kurulur)")
    async def cmd_publish_charter(self, interaction: discord.Interaction, kanal: discord.TextChannel = None):
        await interaction.response.defer(ephemeral=True)
        target = resolve_panel_channel(interaction.guild, kanal, "tuzuk-charter", interaction.channel)
        status = await sync_charter_pages(interaction.guild, target)
        await interaction.followup.send(f"✅ MC-KS-001 Kuruluş Şartı (9 sayfa + EK II imza masası) {target.mention} kanalına ayarlandı (`{status}`).", ephemeral=True)

    @app_commands.command(name="akreditasyon-masasi-kur", description="MC-KS-001 Giriş Kayıt Kapısı ve Yapışkan Rol masasını kurar (Yönetici)")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(kanal="İsteğe bağlı hedef kanal (boş bırakılırsa otomatik #kayit-masasi-register kanalına kurulur)")
    async def cmd_setup_accreditation(self, interaction: discord.Interaction, kanal: discord.TextChannel = None):
        target = resolve_panel_channel(interaction.guild, kanal, "akreditasyon-accreditation", interaction.channel)
        embed, view = build_accreditation_panel()
        st = await upsert_panel_message(target, ["🚪 HOŞ GELDİNİZ", "🏛️ HOŞ GELDİNİZ", "🏛️ AKREDİTASYON", "🏛️ MİLLETLER CEMİYETİ · KURULUŞ ŞARTI"], embed, view)
        await interaction.response.send_message(f"✅ MC-KS-001 Giriş Kayıt Kapısı {target.mention} kanalında güncellendi (`{st}`).", ephemeral=True)

    @app_commands.command(name="sicil-listesi", description="EK II — Kuruluş Şartı'nı (MC-KS-001) imzalayan Kurucu ve Üye Devletler Sicilini gösterir")
    async def cmd_signatory_registry(self, interaction: discord.Interaction):
        embeds = build_signatories_embeds()
        await interaction.response.send_message(embed=embeds[0])

    @app_commands.command(name="sicil-senkronize", description="İmzacı devletler sayfasını (EK II) veritabanına göre hemen yeniden eşitler (Yönetici)")
    @app_commands.default_permissions(administrator=True)
    async def cmd_sync_signatories(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        st = await sync_signatories_channel(interaction.guild)
        synced = await sync_all_delegates_country_statuses(interaction.guild, check_live_roles=True)
        await interaction.followup.send(
            f"✅ İmzacı devletler sayfası eşitlendi (`{st}`), üye statü rozetleri tarandı (`{synced}`).",
            ephemeral=True,
        )

    @app_commands.command(name="rol-guncelleme-talebi", description="Göreviniz, bakanlığınız veya ülke rolünüz için güncelleme talebi açar")
    async def cmd_role_update_req(self, interaction: discord.Interaction):
        existing = delegates_storage.get(str(interaction.user.id), {})
        await interaction.response.send_modal(RoleUpdateRequestModal(existing.get("nation", "")))

    @app_commands.command(name="istifa-et", description="Mevcut görevinizden veya ülke delegasyonundan istifa etmenizi sağlar")
    async def cmd_resign(self, interaction: discord.Interaction):
        rec = delegates_storage.get(str(interaction.user.id))
        if not rec:
            return await interaction.response.send_message("ℹ️ Aktif bir göreviniz bulunmuyor.", ephemeral=True)
        await interaction.response.send_message(
            f"**🚪 İstifa Seçenekleri ({rec.get('nation')} · `{rec.get('official_title', '-')}`):**",
            view=ResignChoiceView(),
            ephemeral=True,
        )

    @app_commands.command(name="yapiskan-rol-senkronize", description="Yapışkan (kalıcı) rollerinizi hemen geri yükler / günceller")
    async def cmd_sticky_sync(self, interaction: discord.Interaction, uye: discord.Member = None):
        target = uye or interaction.user
        if uye and not is_secretariat_or_admin(interaction.user):
            return await interaction.response.send_message("❌ Başka bir üyenin rolünü sadece yöneticiler eşitleyebilir.", ephemeral=True)
        restored = await restore_sticky_roles_for_member(target)
        if restored:
            await interaction.response.send_message(
                f"📌 <@{target.id}> için yapışkan roller geri yüklendi: {', '.join(f'`{r}`' for r in restored)}",
                ephemeral=True,
            )
        else:
            save_sticky_snapshot(target, active=True)
            await interaction.response.send_message(f"✅ <@{target.id}> yapışkan rol anlık görüntüsü güncellendi.", ephemeral=True)

    @app_commands.command(name="yapiskan-rol-sabitle", description="Bir üyenin mevcut tüm canlı rollerini sabit/yapışkan olarak kilitler (Yönetici)")
    @app_commands.describe(uye="Rolleri sabitlenecek üye", kilitle="True: Rolleri sabit/kalıcı kilitle, False: Kilidi aç")
    async def cmd_pin_sticky_roles(self, interaction: discord.Interaction, uye: discord.Member, kilitle: bool = True):
        if not is_secretariat_or_admin(interaction.user):
            return await interaction.response.send_message("❌ Sadece Genel Sekreterlik veya Yöneticiler rol kilitleyebilir.", ephemeral=True)
        snap = save_sticky_snapshot(uye, active=True, locked=kilitle, update_locked=True)
        names = ", ".join(f"`{r}`" for r in snap.get("role_names", [])) or "*Yok*"
        state = "🔒 Sabit / Kilitli" if kilitle else "🔓 Kilit Açıldı (Normal Yapışkan)"
        await interaction.response.send_message(
            f"**{state}** — <@{uye.id}>\n• **Korunan Roller ({len(snap.get('role_ids', []))}):** {names}",
            ephemeral=True,
        )

    @app_commands.command(name="akreditasyon-ver", description="Bir üyeye doğrudan onaylı Cemiyet görevi, devleti ve yapışkan rol atar (Yönetici)")
    @app_commands.describe(uye="Akredite edilecek üye", ulke="Devlet tam adı (alfabe)", gorev="Örn: İçişleri Bakanı, Dışişleri Bakanı, Başkan, Ekonomi Bakanı, Ulus Temsilcisi, Görevim yok", isim="Oyun içi tam ad")
    async def cmd_grant_accreditation(
        self,
        interaction: discord.Interaction,
        uye: discord.Member,
        ulke: str,
        gorev: str,
        isim: str,
    ):
        if not is_secretariat_or_admin(interaction.user):
            return await interaction.response.send_message("❌ Bu komutu sadece Genel Sekreterlik veya Yöneticiler kullanabilir.", ephemeral=True)

        ok_c, country_clean, err_c = validate_country_name(ulke)
        if not ok_c:
            return await interaction.response.send_message(err_c, ephemeral=True)
        ok_n, name_clean, err_n = validate_ingame_name(isim)
        if not ok_n:
            return await interaction.response.send_message(err_n, ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        classified = classify_office_role(gorev)
        canonical_nation, sig_sid, resolved_status, crec = _resolve_authoritative_country_status(country_clean)
        country_short = generate_country_short(canonical_nation)
        new_nick = f"[{country_short}] {classified['code']} {name_clean}"[:32]
        country_status = "OBSERVER" if classified["code"] == "Obs." else resolved_status

        record = {
            "user_id": str(uye.id),
            "nation": canonical_nation,
            "country_short": country_short,
            "rep_name": name_clean,
            "ingame_name": name_clean,
            "official_title": gorev.strip(),
            "office": classified["display_label"],
            "office_code": classified["code"],
            "office_role_name": classified["role_name"],
            "country_status": country_status,
            "sign_id": sig_sid,
            "nick": new_nick,
            "status": "APPROVED",
            "approved_by": str(interaction.user.id),
            "registered_at": datetime.utcnow().isoformat(),
        }
        delegates_storage.set(str(uye.id), record)

        granted = await _apply_rp_roles(
            interaction.guild,
            uye,
            nation_clean=canonical_nation,
            office_role_name=classified["role_name"],
            country_status=country_status,
            new_nick=new_nick,
            strip_previous_offices=True,
        )
        await interaction.followup.send(
            f"✅ <@{uye.id}> üyesine `{new_nick}` unvanı ve yapışkan roller atandı: {', '.join(f'`{g}`' for g in granted) or '`Güncel`'}.",
            ephemeral=True,
        )

    @app_commands.command(name="ulke-statu", description="Bir ülkenin Cemiyet statüsünü belirler (Kurucu, Üye, Gözlemci, Askıda)")
    @app_commands.describe(ulke="Ülke adı", statu="Cemiyet Üyelik Statüsü", gerekce="Resmî Gazete açıklaması")
    @app_commands.choices(statu=[
        app_commands.Choice(name="🌟 Kurucu Üye Devlet (Madde 6)", value="FOUNDING"),
        app_commands.Choice(name="🏛️ Üye Devlet (Madde 7)", value="MEMBER"),
        app_commands.Choice(name="👁️ Gözlemci Devlet / Kuruluş (Oy Yok)", value="OBSERVER"),
        app_commands.Choice(name="⛔ Askıya Alınmış Üye (Madde 10)", value="SUSPENDED"),
    ])
    async def cmd_country_status(
        self,
        interaction: discord.Interaction,
        ulke: str,
        statu: app_commands.Choice[str],
        gerekce: str = "Genel Kurul / Genel Sekreterlik kararıyla statü güncellendi.",
    ):
        if not is_secretariat_or_admin(interaction.user):
            return await interaction.response.send_message("❌ Sadece Genel Sekreterlik veya Yöneticiler ülke statüsü değiştirebilir.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        rec = get_country_record(ulke)
        old_status = rec.get("status", "MEMBER")
        rec["status"] = statu.value
        rec["sanction_note"] = gerekce if statu.value == "SUSPENDED" else None
        save_country_record(rec)

        sig_sid, sig_rec = _find_existing_charter_signature(rec["name"])
        if sig_sid and isinstance(sig_rec, dict) and statu.value in {"FOUNDING", "MEMBER"}:
            sig_rec["status"] = statu.value
            sig_rec["tier"] = STATUS_LABELS[statu.value]
            signatories_storage.set(sig_sid, sig_rec)

        await _propagate_country_status_to_members(
            interaction.guild,
            nation=rec["name"],
            country_status=statu.value,
            sign_id=sig_sid,
        )

        issue_no = await publish_to_gazette(
            self.bot,
            interaction.guild_id,
            "ÜLKE STATÜ DEĞİŞİKLİĞİ",
            f"{rec['name']} — {STATUS_LABELS[statu.value]}",
            f"**Eski Statü:** `{STATUS_LABELS.get(old_status, old_status)}`\n**Yeni Statü:** `{STATUS_LABELS[statu.value]}`\n**Gerekçe:** {gerekce}",
            color=0xEF4444 if statu.value == "SUSPENDED" else 0x1E3A8A,
        )
        await sync_signatories_channel(interaction.guild)
        await interaction.followup.send(
            f"✅ **{rec['name']}** statüsü `{STATUS_LABELS[statu.value]}` olarak güncellendi"
            + (f" ve Resmî Gazete'de (`{issue_no}`) yayınlandı." if issue_no else "."),
            ephemeral=True,
        )

    @app_commands.command(name="ulke-karnesi", description="Bir ülkenin Cemiyet Sicilini, imza kaydını, kadrosunu, oylarını ve antlaşmalarını gösterir")
    @app_commands.describe(ulke="İncelenecek ülke adı")
    async def cmd_country_dossier(self, interaction: discord.Interaction, ulke: str):
        rec = get_country_record(ulke)
        target_key = rec["key"]
        target_stem = canonical_sovereign_stem(ulke)
        status = rec.get("status", "MEMBER")
        stats = rec.get("stats", {})

        all_delegates = delegates_storage.read()
        staff_lines = []
        for uid, d in all_delegates.items():
            if not isinstance(d, dict) or str(uid).startswith(("pending_", "roleupd_", "meta_")):
                continue
            d_stem = canonical_sovereign_stem(d.get("nation", ""))
            if normalize_country_key(d.get("nation", "")) == target_key or (target_stem and d_stem == target_stem):
                staff_lines.append(f"• <@{uid}> — `{d.get('office_code', 'Asil')}` ({d.get('official_title', d.get('office', 'Temsilci'))})")

        _, sig_info = _find_existing_charter_signature(ulke)

        all_treaties = treaties_storage.read()
        ratified_titles = [
            f"`{tid}` {t.get('title')}"
            for tid, t in all_treaties.items()
            if target_key in t.get("ratified", [])
        ]

        color = 0xEF4444 if status == "SUSPENDED" else (0xD97706 if status == "FOUNDING" else 0x1E3A8A)
        embed = discord.Embed(
            title=f"📁 ÜLKE KARNESİ · COUNTRY DOSSIER · {rec['name']} [{rec.get('short', '')}]",
            description=(
                f"**Statü / Status:** {STATUS_LABELS.get(status, status)}\n"
                + (f"**✍️ İmza Kaydı / Sign Record:** `{sig_info['sign_id']}` *({sig_info['signed_at']})*\n" if sig_info else "**✍️ İmza Kaydı / Sign Record:** *Yok / Not signed*\n")
                + (f"**⚠️ Askıya Alma Notu / Suspension Note:** `{rec['sanction_note']}`\n" if rec.get("sanction_note") else "")
                + "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            ),
            colour=color,
        )

        embed.add_field(
            name="🏛️ Yetkili Kadro · Country Delegates",
            value="\n".join(staff_lines[:15]) if staff_lines else "*Kayıtlı temsilci yok / None*",
            inline=False,
        )
        embed.add_field(
            name="🗳️ Oylamalar · Assembly Votes",
            value=(
                f"• **Önergeler / Proposals:** `{stats.get('proposals', 0)}` *(Kabul/Passed: `{stats.get('passed_proposals', 0)}`)*\n"
                f"• **Oylar / Votes:** 🟩 `{stats.get('yes', 0)}` | 🟥 `{stats.get('no', 0)}` | ⬜ `{stats.get('abstain', 0)}`"
            ),
            inline=True,
        )
        embed.add_field(
            name="⚖️ Diplomasi · Diplomacy & ICJ",
            value=(
                f"• **Gönderilen Nota / Sent:** `{stats.get('notes_sent', 0)}`\n"
                f"• **Alınan Nota / Received:** `{stats.get('notes_received', 0)}`\n"
                f"• **Divan Davası / ICJ Cases:** `{stats.get('icj_cases', 0)}`"
            ),
            inline=True,
        )
        embed.add_field(
            name="📜 Onaylı Antlaşmalar · Ratified Treaties",
            value="\n".join(ratified_titles[:10]) if ratified_titles else "*Yok / None.*",
            inline=False,
        )
        embed.set_footer(text="Milletler Cemiyeti · League of Nations Country Dossier (MC-KS-001)")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="delege-bilgi", description="Bir temsilcinin Cemiyet diplomatik sicilini görüntüler")
    async def cmd_delegate_info(self, interaction: discord.Interaction, uye: discord.Member = None):
        target = uye or interaction.user
        data = delegates_storage.get(str(target.id))

        if not data:
            return await interaction.response.send_message(
                f"ℹ️ <@{target.id}> henüz MC-KS-001 akreditasyon kaydı tamamlamamış.",
                ephemeral=True,
            )

        country_rec = get_country_record(data.get("nation", "Bağımsız"))
        embed = discord.Embed(
            title=f"🪪 Diplomatik Sicil · {target.display_name}",
            description=(
                f"• **Devlet / Kurum:** `{data.get('nation')}` (`{data.get('country_short')}`)\n"
                f"• **Ülke Statüsü:** {STATUS_LABELS.get(country_rec.get('status', 'MEMBER'))}\n"
                f"• **Görev / Role:** `{data.get('official_title', 'Temsilci')}` (`{data.get('office_code')}`)\n"
                f"• **Atanan Discord Rolü:** `{data.get('office_role_name', data.get('office'))}`\n"
                + (f"• **Milliyet & Dil / Identity:** {data.get('identity_summary')}\n" if data.get("identity_summary") else "")
                + (f"• **MC-KS-001 İmza No:** `{data.get('sign_id')}`\n" if data.get("sign_id") else "")
                + f"• **Kayıt Tarihi:** `{data.get('registered_at', 'N/A')[:10]}`"
            ),
            colour=0x1E3A8A,
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="delege-baskani-ata", description="Ülkenize Delege Başkanı, Asil veya Yedek Temsilci atar (Devlet Başkanı / Ulus Temsilcisi)")
    @app_commands.describe(
        uye="Delege olarak atanacak üye",
        gorev="Atanacak delegasyon görevi (varsayılan: Delege Başkanı)",
        ulke="İsteğe bağlı ülke adı (Yönetici için; Başkan/Ulus Temsilcisi kendi ülkesine atar)",
    )
    @app_commands.choices(gorev=[
        app_commands.Choice(name="🎖️ Delege Başkanı · Head of Delegation", value="Del.B."),
        app_commands.Choice(name="🏛️ Asil Temsilci · Primary Rep", value="Asil"),
        app_commands.Choice(name="🏛️ Yedek Temsilci · Alternate Rep", value="Yedek"),
    ])
    async def cmd_appoint_head_of_delegation(
        self,
        interaction: discord.Interaction,
        uye: discord.Member,
        gorev: app_commands.Choice[str] | None = None,
        ulke: str | None = None,
    ):
        actor_rec = delegates_storage.get(str(interaction.user.id)) or {}
        is_admin = is_secretariat_or_admin(interaction.user)
        is_state_auth = can_member_appoint_head_of_delegation(interaction.user, actor_rec)
        if not (is_admin or is_state_auth):
            return await interaction.response.send_message(
                "⛔ **Yetki Yetersiz:** Kendi ülkesine delege atama yetkisi yalnızca **`👑 Devlet Başkanı`**, **`🏛️ Ulus Temsilcisi`**, **Genel Sekreterlik** veya **Yöneticilere** aittir.",
                ephemeral=True,
            )

        target_rec = delegates_storage.get(str(uye.id))
        if not isinstance(target_rec, dict):
            target_rec = {}

        if is_state_auth and not is_admin:
            if uye.id == interaction.user.id:
                return await interaction.response.send_message(
                    "ℹ️ Zaten yetkili ülke makamındasınız; kendinizi delege yaparak mevcut görevinizi düşürmeyin.",
                    ephemeral=True,
                )
            actor_nation = actor_rec.get("nation")
            if not actor_nation or actor_rec.get("status") not in {"APPROVED", "PROVISIONAL"}:
                return await interaction.response.send_message("❌ Önce onaylı bir Devlet Başkanı veya Ulus Temsilcisi kaydınız bulunmalıdır.", ephemeral=True)
            t_nation = target_rec.get("nation", "")
            same_state = bool(
                t_nation
                and (
                    normalize_country_key(t_nation) == normalize_country_key(actor_nation)
                    or (canonical_sovereign_stem(t_nation) and canonical_sovereign_stem(t_nation) == canonical_sovereign_stem(actor_nation))
                )
            )
            if not same_state:
                return await interaction.response.send_message(
                    f"⛔ <@{uye.id}> henüz **{actor_nation}** için kayıtlı bir üye değil.\n"
                    f"Önce `#🚪│kayit-masasi-register` odasından **{actor_nation}** adına kaydını tamamlamalıdır.",
                    ephemeral=True,
                )
            target_nation = actor_nation
        else:
            if ulke:
                ok_c, country_clean, err_c = validate_country_name(ulke)
                if not ok_c:
                    return await interaction.response.send_message(err_c, ephemeral=True)
                target_nation = country_clean
            else:
                target_nation = target_rec.get("nation", "")
                if not target_nation:
                    return await interaction.response.send_message(
                        "❌ Bu üyenin ülke kaydı henüz yok; lütfen `ulke` parametresini girin veya üye önce kayıt olsun.",
                        ephemeral=True,
                    )

        await interaction.response.defer(ephemeral=True)
        choice_code = gorev.value if gorev else "Del.B."
        if choice_code == "Del.B.":
            res = await assign_head_of_delegation(
                interaction.guild,
                uye,
                nation_clean=target_nation,
                actor_id=interaction.user.id,
            )
            role_label = ROLE_HEAD_OF_DELEGATION
            nick_out = res["nick"]
            nation_out = res["nation"]
            replaced_note = (
                f"\n• Önceki Delege Başkanı (`{', '.join(f'<@{r}>' for r in res['replaced_uids'])}`) Asil Temsilci statüsüne geçirildi."
                if res["replaced_uids"]
                else ""
            )
        else:
            role_map = {
                "Asil": (ROLE_PRIMARY, "🏛️ Asil Temsilci · Primary Rep", "Asil Temsilci"),
                "Yedek": (ROLE_ALTERNATE, "🏛️ Yedek Temsilci · Alternate Rep", "Yedek Temsilci"),
            }
            office_role_name, role_label, official_title = role_map[choice_code]
            canonical_nation, sig_sid, resolved_status, _ = _resolve_authoritative_country_status(target_nation)
            c_short = target_rec.get("country_short") or generate_country_short(canonical_nation)
            rep_name = target_rec.get("rep_name") or target_rec.get("ingame_name") or uye.display_name
            nick_out = f"[{c_short}] {choice_code} {rep_name}"[:32]
            nation_out = canonical_nation
            updated_rec = {
                **target_rec,
                "user_id": str(uye.id),
                "nation": canonical_nation,
                "country_short": c_short,
                "rep_name": rep_name,
                "ingame_name": rep_name,
                "official_title": official_title,
                "office": role_label,
                "office_code": choice_code,
                "office_role_name": office_role_name,
                "country_status": resolved_status,
                "sign_id": sig_sid,
                "nick": nick_out,
                "status": "APPROVED",
                "approved_by": str(interaction.user.id),
                "updated_at": datetime.utcnow().isoformat(),
            }
            delegates_storage.set(str(uye.id), updated_rec)
            await _apply_rp_roles(
                interaction.guild,
                uye,
                nation_clean=canonical_nation,
                office_role_name=office_role_name,
                country_status=resolved_status,
                new_nick=nick_out,
                strip_previous_offices=True,
            )
            replaced_note = ""

        await publish_to_gazette(
            self.bot,
            interaction.guild_id,
            "DELEGASYON ATAMASI · DELEGATE APPOINTMENT",
            f"{nation_out} — Yeni {role_label}",
            f"**Atanan / Appointed:** <@{uye.id}> (`{nick_out}`)\n**Makam / Office:** `{role_label}`\n**Atayan / Appointed By:** <@{interaction.user.id}>",
            color=0x0284C7,
        )
        await interaction.followup.send(
            f"✅ <@{uye.id}>, **{nation_out}** ülkesinin **`{role_label}`** olarak atandı (`{nick_out}`).{replaced_note}",
            ephemeral=True,
        )

    @app_commands.command(name="milliyet-sec", description="Kendi ülke alfabeniz ve dilinizdeki milliyet rolünüzü seçersiniz")
    async def cmd_pick_identity(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "**🌐 Kendi ülkenizin alfabesi ve dilindeki rolünüzü seçin *(en fazla 2)*:**",
            view=IdentitySelectView(),
            ephemeral=True,
        )

    @app_commands.command(name="milliyet-paneli-kur", description="Uluslar / diller kanalına kalıcı ulus & dil seçim paneli kurar (Yönetici)")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(kanal="İsteğe bağlı hedef kanal (boş bırakılırsa #uluslar-nations kanalına kurulur)")
    async def cmd_setup_identity_panel(self, interaction: discord.Interaction, kanal: discord.TextChannel = None):
        target = resolve_panel_channel(interaction.guild, kanal, "uluslar-nations", interaction.channel)
        embed, view = build_identity_panel()
        st = await upsert_panel_message(target, ["🌍 ULUSLAR, KÖKEN & DİLLER", "🌍 MİLLETLER, KÖKEN & DİLLER"], embed, view)
        await interaction.response.send_message(f"✅ Ulus & Dil seçim paneli {target.mention} kanalında güncellendi (`{st}`).", ephemeral=True)

    @app_commands.command(
        name="hukumet-baskani-dusur",
        description="Genel Sekreter gerekçeli olarak 'hükûmet başkanı düştü' kararıyla görevden indirir",
    )
    @app_commands.describe(
        uye="Görevden düşürülecek Devlet / Hükûmet Başkanı (@üye)",
        gerekce="Gerekçe (ör. Mecliste güvenoyu alamadı, hükûmet başkanı düştü)",
        ulke="Üye etiketlemeden ülke adıyla bulmak için isteğe bağlı ülke adı",
    )
    async def cmd_dismiss_head_of_government(
        self,
        interaction: discord.Interaction,
        uye: discord.Member | None = None,
        gerekce: str | None = None,
        ulke: str | None = None,
    ):
        if not is_secretary_general_or_admin(interaction.user):
            return await interaction.response.send_message(
                "⛔ **Yetki Yetersiz:** Hükûmet başkanını gerekçeli olarak görevden indirme yetkisi yalnızca **Genel Sekreter (`🏛️ Genel Sekreter · Secretary-General`)** veya **Sunucu Yöneticisine** aittir.",
                ephemeral=True,
            )

        if uye is None and not (ulke or "").strip():
            return await interaction.response.send_message(
                "❌ Lütfen görevden düşürülecek **Devlet / Hükûmet Başkanını (`uye`)** veya **ülke adını (`ulke`)** belirtin.",
                ephemeral=True,
            )

        target_uid: str
        target_member: discord.Member | None
        target_rec: dict | None

        if uye is not None:
            target_uid = str(uye.id)
            target_member = uye
            raw_rec = delegates_storage.get(target_uid)
            target_rec = raw_rec if isinstance(raw_rec, dict) else None
            if (ulke or "").strip():
                keys, stems = _nation_match_tokens(ulke.strip())
                rec_nations = [r.name[2:].strip() for r in uye.roles if r.name.startswith("🏳️ ")]
                if target_rec and target_rec.get("nation"):
                    rec_nations.append(target_rec["nation"])
                if rec_nations and not any(_nation_matches_tokens(rn, keys, stems) for rn in rec_nations):
                    return await interaction.response.send_message(
                        f"❌ <@{target_uid}> üyesinin ülke kaydı (**{rec_nations[-1]}**) girdiğiniz **`{ulke.strip()}`** ülkesiyle uyuşmuyor.",
                        ephemeral=True,
                    )
            if not is_head_of_state_or_government(target_member, target_rec):
                return await interaction.response.send_message(
                    f"❌ <@{target_uid}> şu anda aktif bir **Devlet / Hükûmet Başkanı (`{ROLE_PRESIDENT}`)** görevinde görünmüyor.",
                    ephemeral=True,
                )
        else:
            candidates = await _find_head_of_government_candidates(interaction.guild, ulke.strip())
            if not candidates:
                return await interaction.response.send_message(
                    f"❌ **{ulke.strip()}** ülkesinde görev yapan aktif bir **Devlet / Hükûmet Başkanı (`{ROLE_PRESIDENT}`)** bulunamadı.",
                    ephemeral=True,
                )
            if len(candidates) > 1:
                mentions = ", ".join(f"<@{c_uid}>" for c_uid, _m, _r in candidates)
                return await interaction.response.send_message(
                    f"⚠️ **{ulke.strip()}** için birden fazla `Devlet Başkanı` adayı bulundu ({mentions}); lütfen `uye` parametresinden doğrudan kişiyi seçin.",
                    ephemeral=True,
                )
            target_uid, target_member, target_rec = candidates[0]

        if gerekce is None:
            target_nation = (
                (target_rec.get("nation") if isinstance(target_rec, dict) and target_rec.get("nation") else None)
                or (next((r.name[2:].strip() for r in target_member.roles if r.name.startswith("🏳️ ")), None) if target_member else None)
                or (ulke.strip() if ulke else "Devlet")
            )
            label_name = target_member.display_name if target_member else f"Üye {target_uid}"
            return await interaction.response.send_modal(
                GovernmentFallReasonModal(
                    self.bot,
                    target_uid,
                    f"{target_nation} — {label_name}",
                )
            )

        reason_clean = gerekce.strip()
        if len(reason_clean) < 3:
            return await interaction.response.send_message(
                "❌ **Gerekçe Zorunlu:** Hükûmet başkanını düşürmek için geçerli bir gerekçe girmelisiniz *(en az 3 karakter)*.",
                ephemeral=True,
            )

        await interaction.response.defer(ephemeral=True, thinking=True)
        await _execute_government_fall_interaction(
            interaction,
            bot=self.bot,
            target_uid=target_uid,
            reason_clean=reason_clean,
        )

    @app_commands.command(
        name="kurum-ata",
        description="Genel Sekreter / Yönetici kurumsal makam (Divan, Meclis, Sözcü, Mod) ataması yapar",
    )
    @app_commands.describe(
        uye="Atanacak veya görevi alınacak üye (@üye)",
        gorev="Kurumsal makam / koltuk",
        islem="Ata (varsayılan) veya Görevden Al",
        gerekce="Resmî Gazete & sicil açıklaması (isteğe bağlı)",
    )
    @app_commands.choices(
        gorev=[
            app_commands.Choice(name="⚖️ Divan Başkanı · ICJ President", value="db"),
            app_commands.Choice(name="⚖️ Divan Yargıcı · ICJ Judge", value="uyd"),
            app_commands.Choice(name="⚖️ Cemiyet Başsavcısı · Chief Prosecutor", value="bsv"),
            app_commands.Choice(name="📜 Cemiyet Savcısı · Prosecutor", value="svc"),
            app_commands.Choice(name="🪑 Genel Kurul Başkanı · Assembly Pres.", value="gkb"),
            app_commands.Choice(name="🏛️ Şûra Başkanı · Council Chair", value="sb"),
            app_commands.Choice(name="📰 Cemiyet Sözcüsü · Spokesperson", value="spokes"),
            app_commands.Choice(name="🏛️ Cemiyet Yönetimi · Staff & Council", value="staff"),
            app_commands.Choice(name="🛡️ Moderatör · Moderator", value="mod"),
        ],
        islem=[
            app_commands.Choice(name="🎖️ Makama Ata / Assign", value="assign"),
            app_commands.Choice(name="🗑️ Görevi Kaldır / Revoke", value="revoke"),
        ],
    )
    async def cmd_appoint_institutional(
        self,
        interaction: discord.Interaction,
        uye: discord.Member,
        gorev: app_commands.Choice[str],
        islem: app_commands.Choice[str] | None = None,
        gerekce: str | None = None,
    ):
        if not is_secretary_general_or_admin(interaction.user):
            return await interaction.response.send_message(
                "⛔ **Yetki Yetersiz:** Kurumsal atama yetkisi yalnızca **Genel Sekreter** veya **Yönetici**lere aittir.",
                ephemeral=True,
            )
        await interaction.response.defer(ephemeral=True, thinking=True)
        revoke = bool(islem and islem.value == "revoke")
        res = await execute_institutional_appointment(
            interaction.guild,
            self.bot,
            actor=interaction.user,
            target=uye,
            spec_key=gorev.value,
            revoke=revoke,
            reason=gerekce,
        )
        await interaction.followup.send(
            f"✅ <@{uye.id}> → **{res['title']}** (**{res['verb']}**)"
            + (f" · Resmî Gazete: `{res['issue_no']}`" if res["issue_no"] else ""),
            ephemeral=True,
        )

    @app_commands.command(
        name="atama-paneli-kur",
        description="Genel Sekreterlik Kurumsal Atama & Kadro odası panelini kurar / yeniler",
    )
    @app_commands.describe(kanal="İsteğe bağlı hedef kanal (boş bırakılırsa #kurumsal-atama-appointments)")
    async def cmd_setup_sg_appointments_panel(
        self,
        interaction: discord.Interaction,
        kanal: discord.TextChannel | None = None,
    ):
        if not is_secretary_general_or_admin(interaction.user):
            return await interaction.response.send_message("⛔ Sadece Genel Sekreter veya Yönetici.", ephemeral=True)
        target = resolve_panel_channel(interaction.guild, kanal, "kurumsal-atama-appointments", interaction.channel)
        emb, vw = build_sg_appointments_panel(interaction.guild)
        st = await upsert_panel_message(target, ["🏛️ GENEL SEKRETERLİK KURUMSAL ATAMA"], emb, vw)
        await interaction.response.send_message(f"✅ Kurumsal Atama Masası {target.mention} kanalında güncellendi (`{st}`).", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(DelegationCog(bot))
