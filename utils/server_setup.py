import re
import discord

from utils.storage import embassies_storage, gazette_storage, summits_storage, tickets_storage
from utils.un_gazette import upsert_panel_message as _upsert_panel
from utils.info_channels import sync_info_and_rules_channels
from utils.constants import (
    ROLE_SPECS,
    HOISTED_ROLE_NAMES,
    ROLE_DISPLAY_ORDER,
    LEADERSHIP_ROLES,
    SPEAKER_ROLES,
    ALL_ACCREDITED_ROLES,
    ROLE_ICJ_PRESIDENT,
    ROLE_JUDGE,
    ROLE_CHIEF_PROSECUTOR,
    ROLE_PROSECUTOR,
    ROLE_MODERATOR,
    ROLE_PRESS,
    ROLE_SPOKESPERSON,
    ROLE_VERIFIED,
    CHANNEL_ALIASES,
    IDENTITY_ROLE_SPECS,
    IDENTITY_BY_CODE,
    LANG_CHANNEL_IDENTITY_CODES,
    slug_match as _slug_match,
)
from cogs.delegation import (
    ensure_role,
    build_accreditation_panel,
    build_identity_panel,
    build_sg_appointments_panel,
    sync_charter_pages,
    sync_existing_country_shorts,
    sync_signatories_channel,
)
from cogs.assembly import build_speakers_panel
from cogs.voting import build_voting_panel
from cogs.announcements import build_announcement_panel
from cogs.applications import (
    CONTROL_TITLE_PREFIX,
    PANEL_TITLE_PREFIX,
    build_application_panel,
    build_recruitment_control_panel,
    sync_moderator_role_permissions,
)
from cogs.tickets import build_ticket_panel, register_staff_member, TICKET_CATEGORY_NAME
from cogs.treaties_icj import build_treaty_panel, build_icj_panel, build_chambers_panel, build_prosecution_panel
from cogs.moderation import build_chat_reset_panel

CATEGORIES = {
    "public": "📢 CEMİYET · GENEL & OFFICIAL",
    "assembly": "🏛️ GENEL KURUL · GENERAL ASSEMBLY",
    "voice": "🎙️ DİPLOMATİK SES · DIPLOMATIC VOICE",
    "admin": "🛡️ SEKRETERLİK · STAFF & ADMIN",
}

SERVER_DESCRIPTION_TEXT = (
    "Parlamentum oyun içi KURGU/ROLEPLAY sunucusu. Gerçek kurum/devlet bağı yoktur. | Fictional Game RP Only (TR/EN/FR)"
)

CATEGORY_ALIASES = {
    "public": ["📢 CEMİYET · GENEL & OFFICIAL", "📢 MİLLETLER CEMİYETİ · GENEL & RESMÎ"],
    "assembly": ["🏛️ GENEL KURUL · GENERAL ASSEMBLY", "🏛️ GENEL KURUL & DİPLOMASİ SALONLARI"],
    "voice": ["🎙️ DİPLOMATİK SES · DIPLOMATIC VOICE", "🎙️ DİPLOMATİK SES ODALARI · VOICE"],
    "admin": ["🛡️ SEKRETERLİK · STAFF & ADMIN", "🛡️ YÖNETİM & GENEL SEKRETERLİK"],
}


def build_charter_embed() -> discord.Embed:
    embed = discord.Embed(
        title="📜 KURULUŞ ŞARTI · LEAGUE CHARTER (MC-KS-001 v2.0)",
        description=(
            "**🇹🇷 Türkçe · Temel Esaslar:**\n"
            "• **Üç Ana Organ (Md. 3):** Genel Kurul, Genel Sekreterlik ve **5 Yargıçlı Uluslararası Yargı Divanı**.\n"
            "• **Egemen Eşitlik (Md. 13 & 15):** Veto/P5 ayrıcalığı yoktur; her üye devlet **1 eşit oya** sahiptir.\n"
            "• **Temsil (Md. 9):** Her devlet **Asil** ve **Yedek Temsilci** bildirebilir *(oy önceliği Asil'dedir)*.\n"
            "• **Yeter Sayılar:** Usul kararları **1/2 Salt Çoğunluk**, önemli kararlar **2/3 Çoğunluk**.\n\n"
            "**🇬🇧 English · Core Principles:**\n"
            "• **Three Main Organs (Art. 3):** General Assembly, Secretariat, and the **5-Judge International Court (ICJ)**.\n"
            "• **Sovereign Equality (Art. 13 & 15):** No veto/P5 privilege; every Member State has **1 equal vote**.\n"
            "• **Representation (Art. 9):** Each state may appoint a **Primary** and **Alternate Rep** *(Primary holds priority)*.\n"
            "• **Thresholds:** Procedural motions require **Simple Majority (1/2)**; key decisions require **2/3 Majority**.\n\n"
            "👉 Kayıt & rol kapısı / Registration: `#🚪│kayit-masasi-register` · Şart İmza / Charter Sign: `#📜│tuzuk-charter`"
        ),
        colour=0xD97706,
    )
    embed.set_footer(text="MC-KS-001 v2.0 · Milletler Cemiyeti / League of Nations")
    return embed


def _text_ow(roles: dict, bot_member: discord.Member, default_role: discord.Role, everyone_view: bool = True, everyone_send: bool = False, writers=None, viewers=None, allow_media: bool = True):
    default_kwargs = {
        "view_channel": everyone_view,
        "send_messages": everyone_send if everyone_view else False,
    }
    if not allow_media:
        default_kwargs["attach_files"] = False
        default_kwargs["embed_links"] = False
    ow = {
        default_role: discord.PermissionOverwrite(**default_kwargs),
        bot_member: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            embed_links=True,
            attach_files=True,
            mention_everyone=True,
            read_message_history=True,
            manage_messages=True,
            manage_channels=True,
        ),
    }
    for rname in writers or []:
        if rname in roles:
            ow[roles[rname]] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                attach_files=allow_media,
                embed_links=True if allow_media else False,
            )
    for rname in viewers or []:
        if rname in roles and rname not in (writers or []):
            ow[roles[rname]] = discord.PermissionOverwrite(view_channel=True, send_messages=False)
    return ow


def _cat_ow(roles: dict, bot_member: discord.Member, default_role: discord.Role, readers) -> dict:
    ow = {
        default_role: discord.PermissionOverwrite(view_channel=False),
        bot_member: discord.PermissionOverwrite(view_channel=True, send_messages=True, connect=True, manage_channels=True, manage_messages=True),
    }
    for rname in readers or []:
        if rname in roles:
            ow[roles[rname]] = discord.PermissionOverwrite(view_channel=True)
    return ow


EXTRA_LANG_CHANNEL_SPECS = (
    ("ru", ["ru-chat", "ru-sohbet"], "💬・ru-chat", "🇷🇺 Русскоязычный чат · Rusça / Russian Chat", "🔉・RU  Chat"),
    ("de", ["de-chat", "de-sohbet"], "💬・de-chat", "🇩🇪 Deutschsprachiger Chat · Almanca / German Chat", "🔉・DE  Chat"),
)


async def _ensure_extra_lang_channels(
    guild: discord.Guild,
    roles: dict[str, discord.Role],
    bot_member: discord.Member,
    everyone: discord.Role,
    *,
    apply_overwrites: bool = False,
    text_summary: list[str] | None = None,
    voice_summary: list[str] | None = None,
) -> tuple[bool, set[int], list[str], list[str]]:
    """Create missing extra-language community text/voice channels and optionally apply overwrites."""
    created_any = False
    managed_ids: set[int] = set()
    created_text_names: list[str] = []
    created_voice_names: list[str] = []

    chat_cat = discord.utils.find(lambda c: ("sohbet" in c.name.lower() or "chat" in c.name.lower()) and "diplomatik" not in c.name.lower(), guild.categories)
    if chat_cat:
        prev_text = discord.utils.find(lambda c: _slug_match(c.name, ["es-chat"]), chat_cat.text_channels)
        for lang_code, aliases, text_name, topic, _voice_name in EXTRA_LANG_CHANNEL_SPECS:
            ch = discord.utils.find(lambda c, al=aliases: _slug_match(c.name, al), guild.text_channels)
            if not ch:
                pos = (prev_text.position + 1) if prev_text else len(chat_cat.text_channels)
                ow = discord.utils.MISSING
                if apply_overwrites:
                    lang_roles = [IDENTITY_BY_CODE[c][0] for c in LANG_CHANNEL_IDENTITY_CODES[lang_code] if c in IDENTITY_BY_CODE] + LEADERSHIP_ROLES
                    ow = _text_ow(roles, bot_member, everyone, everyone_view=False, writers=lang_roles, allow_media=False)
                ch = await guild.create_text_channel(text_name, category=chat_cat, topic=topic, position=pos, overwrites=ow)
                created_any = True
                created_text_names.append(ch.name)
                if text_summary is not None:
                    text_summary.append(f"+ #{ch.name}")
            managed_ids.add(ch.id)
            prev_text = ch

    voice_comm_cat = discord.utils.find(lambda c: "ses kanalları" in c.name.lower() or "voice channels" in c.name.lower(), guild.categories)
    if voice_comm_cat:
        prev_voice = discord.utils.find(lambda c: _slug_match(c.name, ["es-chat"]), voice_comm_cat.voice_channels)
        for lang_code, aliases, _text_name, _topic, voice_name in EXTRA_LANG_CHANNEL_SPECS:
            vc = discord.utils.find(lambda c, al=aliases: _slug_match(c.name, al), guild.voice_channels)
            if not vc:
                vpos = (prev_voice.position + 1) if prev_voice else len(voice_comm_cat.voice_channels)
                ow = discord.utils.MISSING
                if apply_overwrites:
                    lang_roles = [IDENTITY_BY_CODE[c][0] for c in LANG_CHANNEL_IDENTITY_CODES[lang_code] if c in IDENTITY_BY_CODE]
                    ow = _voice_ow(roles, bot_member, everyone, everyone_view=False, public_speak=True, viewers=lang_roles, moderators=LEADERSHIP_ROLES, allow_media=False)
                vc = await guild.create_voice_channel(voice_name, category=voice_comm_cat, position=vpos, overwrites=ow)
                created_any = True
                created_voice_names.append(vc.name)
                if voice_summary is not None:
                    voice_summary.append(f"+ 🔊 {vc.name}")
            managed_ids.add(vc.id)
            prev_voice = vc

    return created_any, managed_ids, created_text_names, created_voice_names


def _build_extra_lang_pos_payload(chat_cat: discord.CategoryChannel | None, voice_comm_cat: discord.CategoryChannel | None) -> list[dict]:
    """Build channel position updates placing extra language channels immediately after es-chat."""
    payload: list[dict] = []
    for cat, getter, anchor_alias, is_voice in (
        (chat_cat, lambda c: c.text_channels, ["es-chat"], False),
        (voice_comm_cat, lambda c: c.voice_channels, ["es-chat"], True),
    ):
        if not cat:
            continue
        channels = sorted(getter(cat), key=lambda x: (x.position, x.id))
        anchor = discord.utils.find(lambda c: _slug_match(c.name, anchor_alias), channels)
        if not anchor:
            continue
        extras = [
            c
            for _lc, aliases, *_rest in EXTRA_LANG_CHANNEL_SPECS
            if (c := discord.utils.find(lambda ch, al=aliases: _slug_match(ch.name, al), channels)) is not None
        ]
        if not extras:
            continue
        extra_ids = {c.id for c in extras}
        ordered = [c for c in channels if c.id not in extra_ids]
        anchor_idx = ordered.index(anchor)
        for offset, extra_ch in enumerate(extras, start=1):
            ordered.insert(anchor_idx + offset, extra_ch)
        for idx, ch in enumerate(ordered):
            payload.append({"id": ch.id, "position": idx, "parent_id": cat.id})
    return payload


async def ensure_community_language_channels_and_info(guild: discord.Guild) -> list[str]:
    """Ensure extra language channels (RU/DE), info/rules channels, and welcome notice include German."""
    changes: list[str] = []
    bot_member = guild.me
    if not bot_member:
        return changes
    roles = {r.name: r for r in guild.roles}
    for _code, rname, rcol, aliases, _desc in IDENTITY_ROLE_SPECS:
        if rname not in roles:
            roles[rname] = await ensure_role(guild, rname, rcol, aliases)
    for rname, rcol, aliases in ROLE_SPECS:
        if rname not in roles:
            r = discord.utils.get(guild.roles, name=rname)
            if r:
                roles[rname] = r

    created_any, _ids, txt_names, vc_names = await _ensure_extra_lang_channels(
        guild,
        roles,
        bot_member,
        guild.default_role,
        apply_overwrites=True,
    )
    if txt_names or vc_names:
        changes.extend(txt_names + vc_names)
        chat_cat = discord.utils.find(lambda c: ("sohbet" in c.name.lower() or "chat" in c.name.lower()) and "diplomatik" not in c.name.lower(), guild.categories)
        voice_comm_cat = discord.utils.find(lambda c: "ses kanalları" in c.name.lower() or "voice channels" in c.name.lower(), guild.categories)
        pos_payload = _build_extra_lang_pos_payload(chat_cat, voice_comm_cat)
        if pos_payload:
            try:
                await guild._state.http.bulk_channel_update(guild.id, pos_payload, reason="MC-KS-001 dil kanalları sıralaması")
            except Exception:
                pass

    info_res = await sync_info_and_rules_channels(guild, force_announcements=False, if_needed=True)
    changes.extend(info_res)

    ann_ch = discord.utils.find(lambda c: _slug_match(c.name, ["duyurular-announcements", "cemiyet-duyuru"]), guild.text_channels)
    if ann_ch:
        try:
            async for msg in ann_ch.history(limit=25):
                if msg.author.id == bot_member.id and "Desteklenen 5 dil (**İtalyanca, Rusça, İngilizce, İspanyolca, Türkçe**)" in (msg.content or ""):
                    updated_text = msg.content.replace(
                        "Desteklenen 5 dil (**İtalyanca, Rusça, İngilizce, İspanyolca, Türkçe**)",
                        "Desteklenen 6 dil (**İtalyanca, Rusça, İngilizce, İspanyolca, Almanca, Türkçe**)",
                    )
                    await msg.edit(content=updated_text, allowed_mentions=discord.AllowedMentions.none())
                    changes.append("Duyuru (6 dil)")
        except Exception:
            pass
    return changes


def _lang_channel_code(name: str) -> str | None:
    tokens = "".join(ch if ch.isalnum() else " " for ch in name.lower()).split()
    if len(tokens) >= 2 and tokens[0] in LANG_CHANNEL_IDENTITY_CODES and tokens[1] in ("chat", "sohbet"):
        return tokens[0]
    return None


async def _apply_community_entry_gate(
    guild: discord.Guild,
    cats: dict,
    roles: dict,
    bot_member: discord.Member,
    everyone: discord.Role,
    gate_viewers: list[str],
):
    """Hide all categories and pre-existing community channels from @everyone except the entry gate."""
    cat_ow_map = [
        (cats.get("public"), _cat_ow(roles, bot_member, everyone, gate_viewers)),
        (cats.get("assembly"), _cat_ow(roles, bot_member, everyone, gate_viewers)),
        (cats.get("voice"), _cat_ow(roles, bot_member, everyone, gate_viewers)),
        (cats.get("admin"), _cat_ow(roles, bot_member, everyone, LEADERSHIP_ROLES + [ROLE_SPOKESPERSON, ROLE_MODERATOR])),
    ]

    managed_cat_ids = {c.id for c in cats.values() if c}
    info_cat = discord.utils.find(lambda c: ("bilgilendirme" in c.name.lower() or "information" in c.name.lower()) and c.id not in managed_cat_ids, guild.categories)
    mc_world_cat = discord.utils.find(lambda c: ("milletler cemiyeti" in c.name.lower() or "league of nations" in c.name.lower()) and c.id not in managed_cat_ids, guild.categories)
    chat_cat = discord.utils.find(lambda c: ("sohbet" in c.name.lower() or "chat" in c.name.lower()) and c.id not in managed_cat_ids, guild.categories)
    voice_comm_cat = discord.utils.find(lambda c: ("ses kanalları" in c.name.lower() or "voice channels" in c.name.lower()) and c.id not in managed_cat_ids, guild.categories)

    community_cats = [c for c in (info_cat, mc_world_cat, chat_cat, voice_comm_cat) if c]
    for c in community_cats:
        cat_ow_map.append((c, _cat_ow(roles, bot_member, everyone, gate_viewers)))

    for cat, desired in cat_ow_map:
        if cat and cat.overwrites != desired:
            await cat.edit(overwrites=desired, reason="MC-KS-001 kayıt giriş kapısı kategori izinleri")

    readonly_slugs = ["kurallar-rules", "uluslar-nations", "milletler-nations", "diller-languages"]
    media_allowed_slugs = ["medya-media", "mizah-memes"]
    for cat in community_cats:
        for ch in cat.channels:
            if _slug_match(ch.name, ["topluluk-sunucu"]):
                continue
            lang_code = _lang_channel_code(ch.name)
            if isinstance(ch, (discord.TextChannel, discord.ForumChannel)):
                if lang_code:
                    desired = _text_ow(roles, bot_member, everyone, everyone_view=False, writers=gate_viewers, allow_media=False)
                elif _slug_match(ch.name, readonly_slugs):
                    desired = _text_ow(roles, bot_member, everyone, everyone_view=False, viewers=gate_viewers, writers=LEADERSHIP_ROLES)
                elif cat == info_cat:
                    desired = _text_ow(roles, bot_member, everyone, everyone_view=False, viewers=gate_viewers, writers=LEADERSHIP_ROLES)
                elif cat == chat_cat:
                    allow_media = _slug_match(ch.name, media_allowed_slugs)
                    desired = _text_ow(roles, bot_member, everyone, everyone_view=False, writers=gate_viewers, allow_media=allow_media)
                elif _slug_match(ch.name, ["world-press"]):
                    desired = _text_ow(
                        roles,
                        bot_member,
                        everyone,
                        everyone_view=False,
                        viewers=gate_viewers,
                        writers=[ROLE_PRESS, ROLE_SPOKESPERSON, ROLE_MODERATOR, *LEADERSHIP_ROLES],
                        allow_media=True,
                    )
                else:
                    desired = _text_ow(roles, bot_member, everyone, everyone_view=False, writers=gate_viewers)
            elif isinstance(ch, (discord.VoiceChannel, discord.StageChannel)):
                desired = _voice_ow(roles, bot_member, everyone, everyone_view=False, public_speak=True, viewers=gate_viewers, moderators=LEADERSHIP_ROLES, allow_media=False)
            else:
                continue

            if ch.overwrites != desired:
                await ch.edit(overwrites=desired, reason="MC-KS-001 kayıt giriş kapısı kanal izinleri")


def _voice_ow(roles: dict, bot_member: discord.Member, default_role: discord.Role, everyone_view: bool = True, public_speak: bool = False, speakers=None, moderators=None, viewers=None, allow_media: bool = True):
    default_kwargs = {
        "view_channel": everyone_view,
        "connect": everyone_view,
        "speak": public_speak if everyone_view else False,
        "stream": public_speak if everyone_view else False,
    }
    if not allow_media:
        default_kwargs["attach_files"] = False
        default_kwargs["embed_links"] = False
    ow = {
        default_role: discord.PermissionOverwrite(**default_kwargs),
        bot_member: discord.PermissionOverwrite(
            view_channel=True,
            connect=True,
            speak=True,
            send_messages=True,
            manage_messages=True,
            read_message_history=True,
            move_members=True,
            mute_members=True,
            manage_channels=True,
            attach_files=True,
            embed_links=True,
        ),
    }
    media_kwargs = {} if allow_media else {"attach_files": False, "embed_links": False}
    for rname in viewers or []:
        if rname in roles:
            ow[roles[rname]] = discord.PermissionOverwrite(view_channel=True, connect=True, speak=public_speak, stream=public_speak, **media_kwargs)
    for rname in speakers or []:
        if rname in roles:
            ow[roles[rname]] = discord.PermissionOverwrite(view_channel=True, connect=True, speak=True, stream=True, **media_kwargs)
    for rname in moderators or []:
        if rname in roles:
            ow[roles[rname]] = discord.PermissionOverwrite(view_channel=True, connect=True, speak=True, stream=True, mute_members=True, deafen_members=True, move_members=True, **media_kwargs)
    return ow


def _protected_channel_ids(guild_id: int) -> set[int]:
    ids: set[int] = set()
    for store in (embassies_storage, tickets_storage, summits_storage):
        for rec in store.read().values():
            if not isinstance(rec, dict) or rec.get("guild_id") != guild_id:
                continue
            if rec.get("active", True) or rec.get("status") in {"open", "ACTIVE", "PEACE_REACHED", "SUSPENDED", "CLOSED"}:
                for key in ("channel_id", "text_channel_id", "voice_channel_id"):
                    if rec.get(key):
                        ids.add(int(rec[key]))
    return ids


async def _ensure_or_rename_role(guild: discord.Guild, rname: str, rcol: int, aliases: list[str]) -> discord.Role | None:
    want_hoist = rname in HOISTED_ROLE_NAMES
    existing = discord.utils.get(guild.roles, name=rname)
    if existing:
        if existing.hoist != want_hoist:
            try:
                await existing.edit(hoist=want_hoist, reason="MC-KS-001 ayrı rol görünümü (hoist) senkronizasyonu")
            except Exception:
                pass
        return existing
    for alias in aliases:
        old = discord.utils.get(guild.roles, name=alias)
        if old:
            try:
                await old.edit(name=rname, colour=discord.Colour(rcol), hoist=want_hoist, reason="MC-KS-001 TR/EN bilingual role update")
                return old
            except Exception:
                return old
    created = await ensure_role(guild, rname, rcol)
    if created and created.hoist != want_hoist:
        try:
            await created.edit(hoist=want_hoist, reason="MC-KS-001 ayrı rol görünümü (hoist) senkronizasyonu")
        except Exception:
            pass
    return created


async def _organize_role_hierarchy(guild: discord.Guild, bot_member: discord.Member) -> None:
    """Order hoisted and identity/flag roles cleanly beneath the bot's top role."""
    top_pos = bot_member.top_role.position if bot_member and bot_member.top_role else 0
    if top_pos <= 2:
        return
    unmovable_above = [
        r.position
        for r in guild.roles
        if not r.is_default() and r.position > 1 and (r.managed or r.name == "yeni rol" or r >= bot_member.top_role)
    ]
    ceiling = max(1, (min(unmovable_above) if unmovable_above else top_pos) - 1)

    priority_names = list(ROLE_DISPLAY_ORDER)
    priority_index = {name: idx for idx, name in enumerate(priority_names)}

    movable = [
        r
        for r in guild.roles
        if not r.is_default()
        and not r.managed
        and r.name != "yeni rol"
        and r < bot_member.top_role
    ]
    hoisted_roles = [r for r in movable if r.hoist or r.name in priority_index]
    unhoisted_roles = [r for r in movable if r not in hoisted_roles]

    hoisted_roles.sort(key=lambda r: (priority_index.get(r.name, len(priority_names) + 1), -r.id))
    positions: dict[discord.Role, int] = {}
    count = len(hoisted_roles)
    if count == 1:
        positions[hoisted_roles[0]] = ceiling
    elif count > 1:
        span = max(1, ceiling - 2)
        for idx, role in enumerate(hoisted_roles):
            if count - 1 <= span:
                target_pos = max(2, ceiling - idx)
            else:
                target_pos = 2 + round((count - 1 - idx) * span / (count - 1))
            positions[role] = target_pos
    for role in unhoisted_roles:
        positions[role] = 1

    # Only call edit_role_positions when at least one role needs moving
    changed = {r: pos for r, pos in positions.items() if r.position != pos}
    if changed:
        try:
            await guild.edit_role_positions(positions=positions, reason="MC-KS-001 ayrı rol hiyerarşisi düzenlemesi")
        except Exception as exc:
            logger.warning("Rol sırası düzenlenemedi: %s", exc)


async def _lock_nickname_permissions(guild: discord.Guild, bot_member: discord.Member | None) -> list[str]:
    """Strip native Discord change_nickname permission from @everyone and editable roles."""
    updated: list[str] = []
    targets: list[discord.Role] = [guild.default_role]
    bot_top = bot_member.top_role if bot_member else None
    for role in guild.roles:
        if role.is_default() or (role.managed and not role.is_premium_subscriber()):
            continue
        if bot_top is not None and role >= bot_top:
            continue
        targets.append(role)

    for role in targets:
        perms = role.permissions
        if perms.change_nickname:
            new_perms = discord.Permissions(perms.value)
            new_perms.update(change_nickname=False)
            try:
                await role.edit(permissions=new_perms, reason="MC-KS-001 Takma Ad Kilidi (Bot Yönetimi)")
                updated.append(role.name)
            except Exception:
                pass
    return updated


async def run_auto_setup(guild: discord.Guild, clean_empty_legacy: bool = True) -> dict:
    # 1. Roles (preserve/rename existing legacy role names cleanly into TR/EN)
    roles: dict[str, discord.Role] = {}
    for rname, rcol, aliases in ROLE_SPECS:
        r = await _ensure_or_rename_role(guild, rname, rcol, aliases)
        if r:
            roles[rname] = r
    for _code, rname, rcol, aliases, _desc in IDENTITY_ROLE_SPECS:
        r = await _ensure_or_rename_role(guild, rname, rcol, aliases)
        if r:
            roles[rname] = r
    for sys_name in ("Admin", "Botlar"):
        sys_role = discord.utils.get(guild.roles, name=sys_name)
        if sys_role and not sys_role.hoist:
            try:
                await sys_role.edit(hoist=True, reason="MC-KS-001 ayrı rol görünümü (hoist)")
            except Exception:
                pass
    if guild.me:
        await sync_moderator_role_permissions(roles.get(ROLE_MODERATOR), guild.me)
        await _organize_role_hierarchy(guild, guild.me)
    await _lock_nickname_permissions(guild, guild.me)
    await sync_existing_country_shorts(guild)
    if "COMMUNITY" in getattr(guild, "features", []) and guild.description != SERVER_DESCRIPTION_TEXT:
        try:
            await guild.edit(
                description=SERVER_DESCRIPTION_TEXT,
                reason="MC-KS-001 Parlamentum çok dilli sunucu açıklaması",
            )
        except Exception as exc:
            logger.warning("Sunucu açıklaması güncellenemedi: %s", exc)

    # 1b. Seed staff roster for private ticket assignment (works without Server Members Intent)
    seen_uids: set[int] = set()
    if guild.owner_id:
        seen_uids.add(int(guild.owner_id))
    for m in guild.members:
        if not m.bot:
            seen_uids.add(m.id)
    try:
        async for entry in guild.audit_logs(limit=80):
            if entry.user_id:
                seen_uids.add(int(entry.user_id))
    except Exception:
        pass
    for uid in seen_uids:
        member = guild.get_member(uid)
        if not member:
            try:
                member = await guild.fetch_member(uid)
            except Exception:
                member = None
        if member and not member.bot:
            register_staff_member(member)

    everyone = guild.default_role
    bot_member = guild.me

    # 2. Categories (rename existing legacy MC categories in place if present)
    cats: dict[str, discord.CategoryChannel] = {}
    for idx, (key, cname) in enumerate(CATEGORIES.items()):
        cat = discord.utils.get(guild.categories, name=cname)
        if not cat:
            for alias in CATEGORY_ALIASES.get(key, []):
                old_cat = discord.utils.get(guild.categories, name=alias)
                if old_cat:
                    try:
                        await old_cat.edit(name=cname, reason="MC-KS-001 TR/EN kategori güncellemesi")
                    except Exception:
                        pass
                    cat = old_cat
                    break
        if not cat:
            cat = await guild.create_category(cname, position=idx)
        cats[key] = cat

    # 3. Text & Voice specifications (Full TR + EN bilingual names & topics)
    gate_viewers = list(dict.fromkeys([
        ROLE_VERIFIED,
        IDENTITY_BY_CODE["tr"][0],
        ROLE_SPOKESPERSON,
        ROLE_JUDGE,
        ROLE_CHIEF_PROSECUTOR,
        ROLE_PROSECUTOR,
        *ALL_ACCREDITED_ROLES,
        *LEADERSHIP_ROLES,
    ]))

    text_specs = [
        ("public", 0, "📢│duyurular-announcements", CHANNEL_ALIASES["duyurular-announcements"], "📢 Resmî Duyurular / Official League Announcements", _text_ow(roles, bot_member, everyone, everyone_view=False, viewers=gate_viewers, writers=LEADERSHIP_ROLES + [ROLE_SPOKESPERSON])),
        ("public", 1, "📰│resmi-gazete-official-gazette", CHANNEL_ALIASES["resmi-gazete-official-gazette"], "📰 Cemiyet Resmî Gazetesi / Official Gazette · Kararlar, Antlaşmalar, Hükümler", _text_ow(roles, bot_member, everyone, everyone_view=False, viewers=gate_viewers)),
        ("public", 2, "📜│tuzuk-charter", CHANNEL_ALIASES["tuzuk-charter"], "📜 Kuruluş Şartı / League Charter (MC-KS-001 v2.0)", _text_ow(roles, bot_member, everyone, everyone_view=False, viewers=gate_viewers, writers=LEADERSHIP_ROLES)),
        ("public", 3, "✍️│imzaci-devletler-signatories", CHANNEL_ALIASES["imzaci-devletler-signatories"], "✍️ EK II · Kuruluş Şartı İmzacı Devletler Sicili / Charter Signatories Roll (MC-KS-001)", _text_ow(roles, bot_member, everyone, everyone_view=False, viewers=gate_viewers)),
        ("public", 4, "🚪│kayit-masasi-register", CHANNEL_ALIASES["akreditasyon-accreditation"], "🚪 Giriş Kayıt & Rol Kapısı / Registration & Auto-Role Gate (MC-KS-001)", _text_ow(roles, bot_member, everyone, everyone_view=True, everyone_send=False, writers=LEADERSHIP_ROLES)),
        ("public", 5, "🎫│destek-masasi-tickets", CHANNEL_ALIASES["destek-masasi-tickets"], "🎫 Sekreterlik, Arabuluculuk, Divan & OOC Destek / Support Ticket Desk", _text_ow(roles, bot_member, everyone, everyone_view=False, viewers=gate_viewers, writers=LEADERSHIP_ROLES)),
        ("public", 6, "📝│basvuru-odasi-applications", CHANNEL_ALIASES["basvuru-odasi-applications"], "📝 Gazeteci (Basın), Moderatör & Admin Başvuru Odası / Press, Moderator & Admin Applications", _text_ow(roles, bot_member, everyone, everyone_view=False, viewers=gate_viewers, writers=LEADERSHIP_ROLES)),
        ("assembly", 0, "🏛️│genel-kurul-assembly", CHANNEL_ALIASES["genel-kurul-assembly"], "🏛️ Genel Kurul Müzakere Salonu / General Assembly Hall (Yalnızca Yetkili Delegeler · Accredited Delegates Only)", _text_ow(roles, bot_member, everyone, everyone_view=False, viewers=gate_viewers, writers=SPEAKER_ROLES)),
        ("assembly", 1, "🎙️│konusmaci-kursusu-speakers", CHANNEL_ALIASES["konusmaci-kursusu-speakers"], "🎙️ Konuşmacı Kürsüsü & Sıra / Speakers' Floor & Queue", _text_ow(roles, bot_member, everyone, everyone_view=False, viewers=gate_viewers, writers=LEADERSHIP_ROLES)),
        ("assembly", 2, "💡│onergeler-motions-votes", CHANNEL_ALIASES["onergeler-motions-votes"], "💡 Önerge & Oylama Salonu / Motions & Equal Voting Floor (1 State = 1 Vote)", _text_ow(roles, bot_member, everyone, everyone_view=False, viewers=gate_viewers, writers=LEADERSHIP_ROLES)),
        ("assembly", 3, "📜│kararlar-resolutions", CHANNEL_ALIASES["kararlar-resolutions"], "📜 Kabul Edilen Kararlar Kütüğü / Adopted Resolutions Register (MC-KARAR)", _text_ow(roles, bot_member, everyone, everyone_view=False, viewers=gate_viewers)),
        ("assembly", 4, "📜│antlasmalar-treaties", CHANNEL_ALIASES["antlasmalar-treaties"], "📜 Antlaşmalar & İmza Masası / International Treaties & Signing Desk", _text_ow(roles, bot_member, everyone, everyone_view=False, viewers=gate_viewers, writers=LEADERSHIP_ROLES)),
        ("assembly", 5, "⚖️│yargi-divani-icj-court", CHANNEL_ALIASES["yargi-divani-icj-court"], "⚖️ Uluslararası Yargı Divanı · Aktif Davalar / International Court of Justice (Art. 23-28)", _text_ow(roles, bot_member, everyone, everyone_view=False, viewers=gate_viewers, writers=LEADERSHIP_ROLES + [ROLE_ICJ_PRESIDENT, ROLE_JUDGE, ROLE_CHIEF_PROSECUTOR, ROLE_PROSECUTOR])),
        ("assembly", 6, "📚│yargi-arsivi-icj-archive", CHANNEL_ALIASES["yargi-arsivi-icj-archive"], "📚 Geçmiş Dava & Yargı Kararları Arşivi / Past ICJ & Court Verdict Archive (Art. 34)", _text_ow(roles, bot_member, everyone, everyone_view=False, viewers=gate_viewers, writers=None)),
        ("assembly", 7, "⚖️│divan-makami-chambers", CHANNEL_ALIASES["divan-makami-chambers"], "⚖️ Gizli · Divan Başkanı ve Bağımsız Yargıçlar Ofisi / ICJ Judges' Chambers", _text_ow(roles, bot_member, everyone, everyone_view=False, writers=LEADERSHIP_ROLES + [ROLE_ICJ_PRESIDENT, ROLE_JUDGE])),
        ("assembly", 8, "📜│savcilik-makami-prosecution", CHANNEL_ALIASES["savcilik-makami-prosecution"], "📜 Gizli · Cemiyet Başsavcılığı & Soruşturma Bürosu / Prosecution Chambers", _text_ow(roles, bot_member, everyone, everyone_view=False, writers=LEADERSHIP_ROLES + [ROLE_CHIEF_PROSECUTOR, ROLE_PROSECUTOR])),
        ("admin", 0, "🛡️│onay-odasi-approvals", CHANNEL_ALIASES["onay-odasi-approvals"], "🔒 Gizli · Akreditasyon & Başvuru Onay Odası / Private Secretariat & Staff Approvals", _text_ow(roles, bot_member, everyone, everyone_view=False, writers=LEADERSHIP_ROLES + [ROLE_MODERATOR])),
        ("admin", 1, "🎛️│yayin-paneli-broadcast", CHANNEL_ALIASES["yayin-paneli-broadcast"], "🔒 Gizli · Duyuru & Alım Yönetim Paneli / Private Broadcast & Recruitment Panel", _text_ow(roles, bot_member, everyone, everyone_view=False, writers=LEADERSHIP_ROLES + [ROLE_SPOKESPERSON])),
        ("admin", 2, "📋│ticket-log-archive", CHANNEL_ALIASES["ticket-log-archive"], "🔒 Gizli · Kapatılan Ticket Arşivi / Closed Ticket Transcript Archive", _text_ow(roles, bot_member, everyone, everyone_view=False, writers=LEADERSHIP_ROLES + [ROLE_MODERATOR])),
        ("admin", 3, "🛡️│denetim-kaydi-audit-log", CHANNEL_ALIASES["denetim-kaydi-audit-log"], "🔒 Gizli · Küfür Filtresi & Moderasyon Denetim Kaydı / Moderation Audit Log", _text_ow(roles, bot_member, everyone, everyone_view=False, writers=LEADERSHIP_ROLES + [ROLE_JUDGE, ROLE_CHIEF_PROSECUTOR, ROLE_PROSECUTOR, ROLE_MODERATOR])),
        ("admin", 4, "🧹│sohbet-sifirlama-kayit", CHANNEL_ALIASES["sohbet-sifirlama-kayit"], "🔒 Gizli · Sohbet Sıfırlama Kayıtları & Mesaj Arşivi / Chat Reset Transcripts", _text_ow(roles, bot_member, everyone, everyone_view=False, writers=LEADERSHIP_ROLES + [ROLE_MODERATOR])),
        ("admin", 5, "🏛️│kurumsal-atama-appointments", CHANNEL_ALIASES["kurumsal-atama-appointments"], "🔒 Gizli · Genel Sekreterlik Kurumsal Atama & Kadro Odası / SG Appointments Desk", _text_ow(roles, bot_member, everyone, everyone_view=False, writers=LEADERSHIP_ROLES)),
    ]

    voice_specs = [
        ("voice", 0, "🏛️│Genel Kurul · Assembly", ["genel kurul · assembly", "genel kurul kürsüsü", "genel-kurul-ses"], 0, _voice_ow(roles, bot_member, everyone, everyone_view=False, viewers=gate_viewers, speakers=SPEAKER_ROLES, moderators=LEADERSHIP_ROLES)),
        ("voice", 1, "⚖️│Yargı Divanı · ICJ Court", ["yargı divanı · icj court", "yargı divanı duruşma", "duruşma salonu"], 0, _voice_ow(roles, bot_member, everyone, everyone_view=False, viewers=gate_viewers, speakers=SPEAKER_ROLES + [ROLE_JUDGE, ROLE_CHIEF_PROSECUTOR, ROLE_PROSECUTOR], moderators=LEADERSHIP_ROLES + [ROLE_JUDGE])),
        ("voice", 2, "🤝│Müzakere 1 · Caucus 1", ["müzakere 1 · caucus 1", "kulis 1", "müzakere & kulis 1"], 10, _voice_ow(roles, bot_member, everyone, everyone_view=False, viewers=gate_viewers, speakers=ALL_ACCREDITED_ROLES, moderators=LEADERSHIP_ROLES)),
        ("voice", 3, "🤝│Müzakere 2 · Caucus 2", ["müzakere 2 · caucus 2", "kulis 2", "müzakere & kulis 2"], 10, _voice_ow(roles, bot_member, everyone, everyone_view=False, viewers=gate_viewers, speakers=ALL_ACCREDITED_ROLES, moderators=LEADERSHIP_ROLES)),
        ("voice", 4, "🛡️│Sekreterlik · Secretariat", ["sekreterlik · secretariat", "sekreterlik & divan", "divan odası"], 0, _voice_ow(roles, bot_member, everyone, everyone_view=False, speakers=LEADERSHIP_ROLES + [ROLE_JUDGE, ROLE_CHIEF_PROSECUTOR, ROLE_PROSECUTOR, ROLE_SPOKESPERSON], moderators=LEADERSHIP_ROLES)),
        ("voice", 5, "☕│Delege Dinlenme · Lounge", ["delege dinlenme · lounge", "delege dinlenme", "ooc"], 0, _voice_ow(roles, bot_member, everyone, everyone_view=False, public_speak=True, viewers=gate_viewers, moderators=LEADERSHIP_ROLES, allow_media=False)),
    ]

    managed_ids: set[int] = set()
    ch_by_key: dict[str, discord.TextChannel] = {}
    text_summary: list[str] = []
    voice_summary: list[str] = []

    for cat_key, pos, name, aliases, topic, ow in text_specs:
        cat = cats[cat_key]
        existing = discord.utils.find(lambda c: c.id not in managed_ids and _slug_match(c.name, aliases), guild.text_channels)
        if not existing:
            ch = await guild.create_text_channel(name, category=cat, topic=topic, overwrites=ow, position=pos)
            text_summary.append(f"+ #{ch.name}")
        else:
            ch = existing
            await ch.edit(name=name, category=cat, topic=topic, overwrites=ow, position=pos)
            text_summary.append(f"↻ #{ch.name}")
        managed_ids.add(ch.id)
        ch_by_key[aliases[0]] = ch

    for cat_key, pos, name, aliases, ulimit, ow in voice_specs:
        cat = cats[cat_key]
        existing = discord.utils.find(lambda c: c.id not in managed_ids and _slug_match(c.name, aliases), guild.voice_channels)
        if not existing:
            vc = await guild.create_voice_channel(name, category=cat, overwrites=ow, user_limit=ulimit, position=pos)
            voice_summary.append(f"+ 🔊 {vc.name}")
        else:
            vc = existing
            await vc.edit(name=name, category=cat, overwrites=ow, user_limit=ulimit, position=pos)
            voice_summary.append(f"↻ 🔊 {vc.name}")
        managed_ids.add(vc.id)

    # 3b. Ensure Russian & German community chat & voice match existing TR/EN/IT/ES community categories
    created_lang, managed_lang_ids, _lang_text_names, _lang_voice_names = await _ensure_extra_lang_channels(
        guild,
        roles,
        bot_member,
        everyone,
        text_summary=text_summary,
        voice_summary=voice_summary,
    )
    managed_ids.update(managed_lang_ids)

    # 4. Save channel configs
    if "resmi-gazete-official-gazette" in ch_by_key:
        gazette_storage.set(f"config_{guild.id}", {"channel_id": ch_by_key["resmi-gazete-official-gazette"].id})
    if "duyurular-announcements" in ch_by_key:
        gazette_storage.set(f"announce_config_{guild.id}", {"channel_id": ch_by_key["duyurular-announcements"].id})
    if "ticket-log-archive" in ch_by_key:
        tickets_storage.set(f"log_config_{guild.id}", {"channel_id": ch_by_key["ticket-log-archive"].id})

    await _apply_community_entry_gate(guild, cats, roles, bot_member, everyone, gate_viewers)

    # 5. Auto-deploy / refresh all persistent panels (matching both legacy and TR/EN titles in place)
    panels_status: list[str] = []
    if "tuzuk-charter" in ch_by_key:
        st = await sync_charter_pages(guild, ch_by_key["tuzuk-charter"])
        panels_status.append(f"Tüzük/Charter ({st})")
    if "imzaci-devletler-signatories" in ch_by_key:
        st = await sync_signatories_channel(guild)
        panels_status.append(f"İmzacı Devletler ({st})")
    if "akreditasyon-accreditation" in ch_by_key:
        e, v = build_accreditation_panel()
        st = await _upsert_panel(ch_by_key["akreditasyon-accreditation"], ["🚪 HOŞ GELDİNİZ", "🏛️ HOŞ GELDİNİZ", "🏛️ AKREDİTASYON", "🏛️ MİLLETLER CEMİYETİ · KURULUŞ ŞARTI"], e, v)
        panels_status.append(f"Akreditasyon ({st})")
    if "destek-masasi-tickets" in ch_by_key:
        e, v = build_ticket_panel()
        st = await _upsert_panel(ch_by_key["destek-masasi-tickets"], ["🎫 DESTEK", "🎫 Milletler Cemiyeti"], e, v)
        panels_status.append(f"Ticket ({st})")
    if "basvuru-odasi-applications" in ch_by_key:
        e, v = build_application_panel(guild.id)
        st = await _upsert_panel(ch_by_key["basvuru-odasi-applications"], [PANEL_TITLE_PREFIX], e, v)
        panels_status.append(f"Başvuru Odası ({st})")
    if "konusmaci-kursusu-speakers" in ch_by_key:
        e, v = build_speakers_panel()
        st = await _upsert_panel(ch_by_key["konusmaci-kursusu-speakers"], ["🎙️ Konuşmacı Kürsüsü", "🎙️ Cemiyet Konuşmacı Kürsüsü"], e, v)
        panels_status.append(f"Kürsü ({st})")
    if "onergeler-motions-votes" in ch_by_key:
        e, v = build_voting_panel()
        st = await _upsert_panel(ch_by_key["onergeler-motions-votes"], ["🏛️ GENEL KURUL OYLAMA", "🏛️ MİLLETLER CEMİYETİ GENEL KURUL ÖNERGE"], e, v)
        panels_status.append(f"Oylama ({st})")
    if "yayin-paneli-broadcast" in ch_by_key:
        e, v = build_announcement_panel()
        st = await _upsert_panel(ch_by_key["yayin-paneli-broadcast"], ["📢 DUYURU YÖNETİM PANELİ", "📢 MİLLETLER CEMİYETİ DUYURU YÖNETİM PANELİ"], e, v)
        panels_status.append(f"Duyuru Paneli ({st})")
        ce, cv = build_recruitment_control_panel(guild.id)
        cst = await _upsert_panel(ch_by_key["yayin-paneli-broadcast"], [CONTROL_TITLE_PREFIX], ce, cv)
        panels_status.append(f"Alım Kontrolü ({cst})")
    if "antlasmalar-treaties" in ch_by_key:
        e, v = build_treaty_panel()
        st = await _upsert_panel(ch_by_key["antlasmalar-treaties"], ["📜 ANTLAŞMALAR", "📜 MİLLETLER CEMİYETİ · ANTLAŞMALAR"], e, v)
        panels_status.append(f"Antlaşmalar ({st})")
    if "yargi-divani-icj-court" in ch_by_key:
        e, v = build_icj_panel(guild)
        st = await _upsert_panel(ch_by_key["yargi-divani-icj-court"], ["⚖️ ULUSLARARASI YARGI DİVANI"], e, v)
        panels_status.append(f"Yargı Divanı ({st})")
    if "divan-makami-chambers" in ch_by_key:
        e, v = build_chambers_panel(guild)
        st = await _upsert_panel(ch_by_key["divan-makami-chambers"], ["⚖️ DİVAN MAKAMI & YARGIÇLAR OFİSİ"], e, v)
        panels_status.append(f"Divan Ofisi ({st})")
    if "savcilik-makami-prosecution" in ch_by_key:
        e, v = build_prosecution_panel(guild)
        st = await _upsert_panel(ch_by_key["savcilik-makami-prosecution"], ["📜 SAVCILIK MAKAMI & SORUŞTURMA"], e, v)
        panels_status.append(f"Savcılık Ofisi ({st})")
    if "kurumsal-atama-appointments" in ch_by_key:
        e, v = build_sg_appointments_panel(guild)
        st = await _upsert_panel(ch_by_key["kurumsal-atama-appointments"], ["🏛️ GENEL SEKRETERLİK KURUMSAL ATAMA"], e, v)
        panels_status.append(f"Kurumsal Atama ({st})")
    if "sohbet-sifirlama-kayit" in ch_by_key:
        e, v = build_chat_reset_panel(guild)
        st = await _upsert_panel(ch_by_key["sohbet-sifirlama-kayit"], ["🧹 SOHBET SIFIRLAMA & ARŞİV MERKEZİ"], e, v)
        panels_status.append(f"Sohbet Arşivi ({st})")
    nations_ch = discord.utils.find(lambda c: _slug_match(c.name, CHANNEL_ALIASES["uluslar-nations"]), guild.text_channels)
    if nations_ch:
        if nations_ch.name != "🌍│uluslar-nations":
            try:
                await nations_ch.edit(name="🌍│uluslar-nations", reason="MC-KS-001 uluslar-nations kanal adı güncellemesi")
            except Exception:
                pass
        e, v = build_identity_panel()
        st = await _upsert_panel(nations_ch, ["🌍 ULUSLAR, KÖKEN & DİLLER", "🌍 MİLLETLER, KÖKEN & DİLLER"], e, v)
        panels_status.append(f"Uluslar & Dil ({st})")
    info_statuses = await sync_info_and_rules_channels(guild, force_announcements=False, if_needed=True)
    panels_status.extend(info_statuses)

    # 6. Handle removed v1 / duplicate starter rooms cleanly without losing community channels or chat history
    protected_ids = _protected_channel_ids(guild.id) | managed_ids
    cleaned: list[str] = []
    preserved: list[str] = []
    duplicate_text_aliases = [
        "duyurular-announcements",
        "votes-oylama",
        "support-destek",
        "destek-ticket",
        "ticket-support",
        "ticket-destek",
    ]
    starter_exact_names = {"general", "genel"}

    for ch in list(guild.text_channels):
        if ch.id in protected_ids:
            continue
        has_msgs = False
        try:
            async for _ in ch.history(limit=1):
                has_msgs = True
                break
        except Exception:
            has_msgs = True

        if "cemiyet-konseyi" in ch.name.lower():
            if not has_msgs:
                try:
                    await ch.delete(reason="MC-KS-001 kurulumunda kaldırılan boş P5 Konseyi kanalı temizlendi")
                    cleaned.append(f"#{ch.name} (eski P5)")
                except Exception:
                    preserved.append(f"#{ch.name}")
            else:
                try:
                    await ch.edit(
                        name="📦│eski-cemiyet-konseyi",
                        category=cats["admin"],
                        overwrites=_text_ow(roles, bot_member, everyone, everyone_view=False, viewers=LEADERSHIP_ROLES),
                    )
                    preserved.append(f"#{ch.name} -> arşivlendi")
                except Exception:
                    preserved.append(f"#{ch.name}")
            continue

        norm_name = ch.name.lstrip("🔊💬📢🗳️📌 ").strip().lower()
        is_known_duplicate = _slug_match(ch.name, duplicate_text_aliases) or norm_name in starter_exact_names
        if clean_empty_legacy and is_known_duplicate and not has_msgs:
            try:
                await ch.delete(reason="MC-KS-001 kurulumu: boş eski/tekrarlayan kanal temizlendi")
                cleaned.append(f"#{ch.name}")
            except Exception:
                preserved.append(f"#{ch.name}")
        else:
            preserved.append(f"#{ch.name}")

    for vc in list(guild.voice_channels):
        if vc.id in protected_ids:
            continue
        norm_vc = vc.name.lstrip("🔊🔉🎙️ ").strip().lower()
        is_starter_voice = norm_vc in starter_exact_names
        if clean_empty_legacy and is_starter_voice and len(vc.members) == 0:
            try:
                await vc.delete(reason="MC-KS-001 kurulumu: boş varsayılan başlangıç ses kanalı temizlendi")
                cleaned.append(f"🔊 {vc.name}")
            except Exception:
                preserved.append(f"🔊 {vc.name}")
        else:
            preserved.append(f"🔊 {vc.name}")

    # Remove empty leftover v1 / duplicate / default categories
    managed_cat_ids = {c.id for c in cats.values()}
    removable_categories = {
        "🏛️ LEAGUE OF NATIONS · MİLLETLER CEMİYETİ",
        "Milletler Cemiyeti / League of Nations",
        "🎫│MİLLETLER CEMİYETİ TİCKETLAR",
        "🎫│TİCKETLAR · SUPPORT TICKETS",
        TICKET_CATEGORY_NAME,
        "Support",
        "Destek",
        "Support / Destek",
        "Support & Tickets",
        "Metin Kanalları",
        "Ses Kanalları",
        "Text Channels",
        "Voice Channels",
    }
    for cat in list(guild.categories):
        if cat.id in managed_cat_ids or cat.channels:
            continue
        if clean_empty_legacy and cat.name in removable_categories:
            try:
                await cat.delete(reason="Boş kalan eski/tekrarlayan kategori temizlendi")
                cleaned.append(f"📁 {cat.name}")
            except Exception:
                pass

    # 7. Normalize non-overlapping category & channel positions cleanly in Discord sidebar
    try:
        info_cat = discord.utils.find(lambda c: "bilgilendirme" in c.name.lower(), guild.categories)
        mc_world_cat = discord.utils.find(lambda c: "league of nations" in c.name.lower() and c.id not in managed_cat_ids, guild.categories)
        support_cat = discord.utils.find(lambda c: "support" in c.name.lower() and c.id not in managed_cat_ids, guild.categories)

        ordered_cats: list[discord.CategoryChannel] = []
        for c in (info_cat, cats.get("public"), cats.get("assembly"), mc_world_cat, cats.get("voice"), chat_cat, voice_comm_cat):
            if c and c not in ordered_cats:
                ordered_cats.append(c)
        for c in sorted(guild.categories, key=lambda x: (x.position, x.id)):
            if c not in ordered_cats and c not in (cats.get("admin"), support_cat):
                ordered_cats.append(c)
        for c in (support_cat, cats.get("admin")):
            if c and c not in ordered_cats:
                ordered_cats.append(c)

        cat_payload = [{"id": c.id, "position": idx} for idx, c in enumerate(ordered_cats)]
        if cat_payload:
            await guild._state.http.bulk_channel_update(guild.id, cat_payload, reason="MC-KS-001 düzenli kategori sıralaması")

        pos_payload: list[dict] = []
        for cat_key, ch_list in (
            ("public", [ch_by_key[spec[3][0]] for spec in text_specs if spec[0] == "public"]),
            ("assembly", [ch_by_key[spec[3][0]] for spec in text_specs if spec[0] == "assembly"]),
            ("admin", [ch_by_key[spec[3][0]] for spec in text_specs if spec[0] == "admin"]),
        ):
            target_cat = cats.get(cat_key)
            if not target_cat:
                continue
            for idx, ch in enumerate(ch_list):
                pos_payload.append({"id": ch.id, "position": idx, "parent_id": target_cat.id})

        if cats.get("voice"):
            vcat = cats["voice"]
            vchannels = sorted(vcat.voice_channels, key=lambda x: (x.position, x.id))
            v_order = [spec[2] for spec in voice_specs]
            by_vname = {v.name: v for v in vchannels}
            ordered_vc = [by_vname[n] for n in v_order if n in by_vname]
            ordered_vc.extend(v for v in vchannels if v not in ordered_vc)
            for idx, vc in enumerate(ordered_vc):
                pos_payload.append({"id": vc.id, "position": idx, "parent_id": vcat.id})

        chat_cat = discord.utils.find(lambda c: ("sohbet" in c.name.lower() or "chat" in c.name.lower()) and "diplomatik" not in c.name.lower(), guild.categories)
        voice_comm_cat = discord.utils.find(lambda c: "ses kanalları" in c.name.lower() or "voice channels" in c.name.lower(), guild.categories)
        pos_payload.extend(_build_extra_lang_pos_payload(chat_cat, voice_comm_cat))

        if pos_payload:
            await guild._state.http.bulk_channel_update(guild.id, pos_payload, reason="MC-KS-001 düzenli kategori ve kanal sıralaması")
    except Exception:
        pass

    return {
        "roles_count": len(roles),
        "text_summary": text_summary,
        "voice_summary": voice_summary,
        "panels_status": panels_status,
        "cleaned": cleaned,
        "preserved": preserved,
        "ch_by_key": ch_by_key,
    }
