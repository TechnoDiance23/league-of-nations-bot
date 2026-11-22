import asyncio
import io
import re
from datetime import datetime
import discord
from discord import app_commands
from discord.ext import commands

from utils.storage import delegates_storage, tickets_storage
from utils.constants import ROLE_SG, ROLE_ADMIN, ROLE_JUDGE, ROLE_FOUNDING, ROLE_MODERATOR, resolve_panel_channel
from utils.un_gazette import upsert_panel_message
from cogs.delegation import resolve_member

STAFF_ROLE_NAMES = [ROLE_SG, ROLE_ADMIN, ROLE_MODERATOR, "🏛️ MC Genel Sekreteri", "🏛️ Cemiyet Yönetimi & Divan", "Admin", "Yetkili", "Staff"]
JUDGE_ROLE_NAMES = [ROLE_JUDGE, "⚖️ Uluslararası Yargı Divanı Yargıcı"]
TICKET_CATEGORY_NAME = "🎫│TİCKETLAR · PRIVATE TICKETS"

TICKET_CATEGORIES = {
    "secretariat": {
        "label": "Genel Sekreterlik · Secretariat",
        "short": "🏛️ Sekreterlik",
        "emoji": "🏛️",
        "desc": "Makam, sicil ve idari başvurular / Office, registry & administrative requests",
        "prefix": "sekreterlik",
        "style": discord.ButtonStyle.primary,
    },
    "diplomacy": {
        "label": "Kriz & Arabuluculuk · Mediation",
        "short": "🕊️ Arabuluculuk",
        "emoji": "🕊️",
        "desc": "Kriz, arabuluculuk & gizli görüşme / Crisis mediation & private talks",
        "prefix": "arabuluculuk",
        "style": discord.ButtonStyle.success,
    },
    "icj": {
        "label": "Yargı Divanı · ICJ Preliminary",
        "short": "⚖️ Yargı Divanı",
        "emoji": "⚖️",
        "desc": "Divan ön danışma & delil sunumu / Court consultation & evidence filing",
        "prefix": "divan",
        "style": discord.ButtonStyle.secondary,
    },
    "ooc": {
        "label": "Yönetim & OOC · Staff Support",
        "short": "🛡️ Yönetim & OOC",
        "emoji": "🛡️",
        "desc": "Kurallar, OOC ve teknik destek / Server rules, OOC & technical support",
        "prefix": "destek",
        "style": discord.ButtonStyle.secondary,
    },
}


def _is_ticket_staff(member: discord.Member) -> bool:
    if not isinstance(member, discord.Member):
        return False
    if member.id == member.guild.owner_id:
        return True
    if member.guild_permissions.administrator or member.guild_permissions.manage_channels:
        return True
    rec = delegates_storage.get(str(member.id), {})
    if rec.get("office_code") in {"SG", "DSG", "GKB", "GKB-V", "UYD"}:
        return True
    role_names = {r.name for r in member.roles}
    return bool(role_names & (set(STAFF_ROLE_NAMES) | set(JUDGE_ROLE_NAMES)))


def _badge_for_member(member: discord.Member) -> str:
    role_names = {r.name for r in member.roles}
    rec = delegates_storage.get(str(member.id), {})
    office_code = rec.get("office_code")
    if office_code in {"SG", "DSG"} or ROLE_SG in role_names or "🏛️ MC Genel Sekreteri" in role_names:
        return "🏛️ Genel Sekreter · SG"
    if office_code == "UYD" or ROLE_JUDGE in role_names or "⚖️ Uluslararası Yargı Divanı Yargıcı" in role_names:
        return "⚖️ Divan Yargıcı · ICJ Judge"
    if office_code in {"GKB", "GKB-V"} or ROLE_ADMIN in role_names or "🏛️ Cemiyet Yönetimi & Divan" in role_names:
        return "🛡️ Cemiyet Yönetimi · Staff"
    if member.id == member.guild.owner_id:
        return "👑 Kurucu · Guild Owner"
    if member.guild_permissions.administrator or "Admin" in role_names:
        return "🛡️ Yönetici · Admin"
    if ROLE_MODERATOR in role_names or member.guild_permissions.moderate_members:
        return "🛡️ Moderatör · Moderator"
    return "👮 Yetkili · Officer"


def register_staff_member(member: discord.Member, custom_badge: str | None = None) -> None:
    if not isinstance(member, discord.Member) or member.bot or not _is_ticket_staff(member):
        return
    key = f"staff_roster_{member.guild.id}"
    roster = tickets_storage.get(key, {})
    if not isinstance(roster, dict):
        roster = {}
    uid = str(member.id)
    badge = custom_badge or _badge_for_member(member)
    entry = {"id": member.id, "name": member.display_name[:60], "badge": badge[:80]}
    if roster.get(uid) != entry:
        roster[uid] = entry
        tickets_storage.set(key, roster)


def _normalize_tracking_id(raw: str) -> str:
    cleaned = raw.strip().upper().replace("#", "")
    if cleaned.isdigit():
        return f"MC-TCK-{int(cleaned):04d}"
    m = re.match(r"^(?:MC-TCK-)?(\d+)$", cleaned)
    if m:
        return f"MC-TCK-{int(m.group(1)):04d}"
    return cleaned


def _next_tracking_id() -> tuple[int, str]:
    all_data = tickets_storage.read()
    max_existing = int(all_data.get("ticket_seq", 0) or 0)
    for k in all_data:
        m = re.match(r"^MC-TCK-(\d+)$", str(k))
        if m:
            max_existing = max(max_existing, int(m.group(1)))
    seq = max_existing + 1
    tickets_storage.set("ticket_seq", seq)
    return seq, f"MC-TCK-{seq:04d}"


def _get_ticket_by_channel(channel_id: int):
    all_tickets = tickets_storage.read()
    for tid, tdata in all_tickets.items():
        if isinstance(tdata, dict) and tdata.get("channel_id") == channel_id:
            return tid, tdata
    return None, None


def _is_assigned_or_staff(member: discord.Member, tdata: dict) -> bool:
    if str(member.id) == str(tdata.get("assigned_to_id")):
        return True
    return _is_ticket_staff(member)


async def _resolve_available_officers(guild: discord.Guild, category_key: str = "secretariat") -> list[tuple[discord.Member, str]]:
    key = f"staff_roster_{guild.id}"
    stored = tickets_storage.get(key, {})
    candidate_ids: list[int] = []

    if guild.owner_id:
        candidate_ids.append(int(guild.owner_id))

    if isinstance(stored, dict):
        for uid_str in stored:
            if str(uid_str).isdigit() and int(uid_str) not in candidate_ids:
                candidate_ids.append(int(uid_str))

    for uid_str, rec in delegates_storage.read().items():
        if isinstance(rec, dict) and rec.get("office_code") in {"SG", "DSG", "GKB", "GKB-V", "UYD"}:
            if str(uid_str).isdigit() and int(uid_str) not in candidate_ids:
                candidate_ids.append(int(uid_str))

    # Also check already cached members in memory (works for recently active members)
    for m in guild.members:
        if not m.bot and _is_ticket_staff(m) and m.id not in candidate_ids:
            candidate_ids.append(m.id)

    resolved: list[tuple[discord.Member, str]] = []
    updated_roster: dict = dict(stored) if isinstance(stored, dict) else {}
    dirty = False

    for uid in candidate_ids:
        member = guild.get_member(uid)
        if not member:
            try:
                member = await guild.fetch_member(uid)
            except Exception:
                member = None
        if not member or member.bot or not _is_ticket_staff(member):
            if str(uid) in updated_roster:
                updated_roster.pop(str(uid), None)
                dirty = True
            continue
        custom = (updated_roster.get(str(uid)) or {}).get("badge")
        badge = custom if custom and "·" in custom else _badge_for_member(member)
        entry = {"id": member.id, "name": member.display_name[:60], "badge": badge[:80]}
        if updated_roster.get(str(uid)) != entry:
            updated_roster[str(uid)] = entry
            dirty = True
        resolved.append((member, badge))

    if dirty:
        tickets_storage.set(key, updated_roster)

    # Sort judges first for ICJ category, otherwise SG -> Admin/Owner -> Judges
    def _sort_key(item: tuple[discord.Member, str]):
        m, badge = item
        if category_key == "icj":
            prio = 0 if "Yargı" in badge or "Judge" in badge else (1 if "Sekreter" in badge else 2)
        else:
            prio = 0 if "Sekreter" in badge else (1 if "Kurucu" in badge or "Yöneti" in badge else 2)
        return (prio, m.display_name.lower())

    resolved.sort(key=_sort_key)
    return resolved[:25]


def _build_private_overwrites(
    guild: discord.Guild,
    owner: discord.Member | discord.User,
    assigned_officer: discord.Member,
    extra_members: list[discord.Member] | None = None,
) -> dict:
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            manage_channels=True,
            attach_files=True,
            embed_links=True,
            manage_messages=True,
            read_message_history=True,
        ),
        owner: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            attach_files=True,
            read_message_history=True,
        ),
        assigned_officer: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            attach_files=True,
            read_message_history=True,
            manage_messages=True,
        ),
    }
    for m in extra_members or []:
        overwrites[m] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            attach_files=True,
            read_message_history=True,
        )
    return overwrites


def _format_ticket_status_embed(tid: str, tdata: dict) -> discord.Embed:
    status = tdata.get("status", "open")
    is_closed = status in {"closed", "resolved"}
    status_badge = (
        "✅ Sonuçlandı / Resolved"
        if status == "resolved"
        else ("🔒 Kapatıldı / Closed" if is_closed else "🟢 Açık / Open")
    )
    assigned_id = tdata.get("assigned_to_id") or tdata.get("claimed_by")
    assigned_str = f"<@{assigned_id}> (`{tdata.get('assigned_to_name', 'Yetkili')}`)" if assigned_id else "`Atanmadı`"
    desc = (
        f"🔖 **Takip Numarası / Tracking ID:** `{tid}`\n"
        f"📊 **Durum / Status:** {status_badge}\n"
        f"📂 **Kategori / Category:** {tdata.get('category_label', '-')}\n"
        f"👤 **Talep Sahibi / Requester:** <@{tdata.get('owner_id')}>\n"
        f"👮 **Atanan Yetkili / Assigned Officer:** {assigned_str}\n"
        f"📝 **Konu / Subject:** {tdata.get('subject', '-')}\n"
        f"🗓️ **Açılış / Opened:** {tdata.get('created_at', '-')}\n"
    )
    if is_closed:
        desc += (
            f"\n⚖️ **Sonuç / Outcome:** **{tdata.get('outcome', 'Kapatıldı / Closed')}**\n"
            f"💬 **Sonuç Notu / Resolution Note:**\n> {tdata.get('resolution_note') or 'Not belirtilmedi / No note provided.'}\n"
            f"🗓️ **Kapanış / Closed at:** {tdata.get('closed_at', '-')}\n"
            f"📬 **Özel Mesaj (DM):** {'İletildi / Sent ✅' if tdata.get('dm_close_sent') else 'İletilemedi / DM Closed ⚠️'}"
        )
    else:
        desc += f"\n💬 **Aktif Kanal / Channel:** <#{tdata.get('channel_id')}>"

    return discord.Embed(
        title=f"🔎 Takip Kaydı · Ticket Record ({tid})",
        description=desc[:4000],
        colour=0x10B981 if status == "resolved" else (0x6B7280 if is_closed else 0x2563EB),
    )


async def _finalize_ticket_close(
    interaction: discord.Interaction,
    outcome: str = "Çözümlendi / Resolved",
    resolution_note: str = "Talebiniz yetkili tarafından sonuçlandırılmıştır. / Your ticket has been resolved by the assigned officer.",
    status_value: str = "resolved",
):
    channel = interaction.channel
    tid, tdata = _get_ticket_by_channel(channel.id)
    if not tdata:
        msg = "❌ Bu kanal aktif bir ticket değil. / This channel is not an active ticket."
        if interaction.response.is_done():
            return await interaction.followup.send(msg, ephemeral=True)
        return await interaction.response.send_message(msg, ephemeral=True)

    is_owner = str(interaction.user.id) == str(tdata.get("owner_id"))
    if not (is_owner or _is_assigned_or_staff(interaction.user, tdata)):
        msg = "❌ Yalnızca talep sahibi, atanan yetkili veya yöneticiler kapatabilir. / Only owner, assigned officer or staff can close."
        if interaction.response.is_done():
            return await interaction.followup.send(msg, ephemeral=True)
        return await interaction.response.send_message(msg, ephemeral=True)

    closing_notice = f"🔒 **{tid}** sonuçlandırılıyor, transkript kaydediliyor & kişiye özel mesaj (DM) gönderiliyor... / Resolving in 5s..."
    if interaction.response.is_done():
        await interaction.followup.send(closing_notice)
    else:
        await interaction.response.send_message(closing_notice)

    # Build UTF-8 TXT transcript
    assigned_id = tdata.get("assigned_to_id") or tdata.get("claimed_by")
    assigned_name = tdata.get("assigned_to_name") or (f"ID:{assigned_id}" if assigned_id else "Atanmadı")
    closed_at = datetime.utcnow().strftime("%d.%m.%Y %H:%M UTC")
    lines = [
        f"MİLLETLER CEMİYETİ · TİCKET TRANSKRİPTİ ({tid})",
        f"Takip No / Tracking ID: {tid}",
        f"Kategori / Category: {tdata.get('category_label', '-')}",
        f"Konu / Subject: {tdata.get('subject', '-')}",
        f"Talep Sahibi / Owner: {tdata.get('owner_name', '-')} ({tdata.get('owner_id')})",
        f"Atanan Yetkili / Officer: {assigned_name} ({assigned_id})",
        f"Sonuçlandıran / Closed by: {interaction.user.display_name} ({interaction.user.id})",
        f"Karar / Outcome: {outcome}",
        f"Sonuç Açıklaması / Note: {resolution_note}",
        f"Tarih (UTC): {closed_at}",
        "=" * 60,
        "",
    ]
    try:
        async for msg in channel.history(limit=250, oldest_first=True):
            ts = msg.created_at.strftime("%d.%m %H:%M")
            content = msg.content or ""
            if msg.attachments:
                att = ", ".join(a.url for a in msg.attachments)
                content = f"{content} [Ekler: {att}]".strip()
            lines.append(f"[{ts}] {msg.author.display_name}: {content}")
    except Exception:
        lines.append("[Mesaj geçmişi okunurken hata oluştu]")

    transcript_bytes = "\n".join(lines).encode("utf-8")

    # Save permanent resolution record BEFORE deleting channel
    tdata["status"] = status_value
    tdata["outcome"] = outcome
    tdata["resolution_note"] = resolution_note
    tdata["closed_by"] = interaction.user.id
    tdata["closed_by_name"] = interaction.user.display_name
    tdata["closed_at"] = closed_at
    tickets_storage.set(tid, tdata)

    # Send Private DM to ticket opener
    dm_sent = False
    owner_id = int(tdata["owner_id"])
    owner_user = interaction.guild.get_member(owner_id) or interaction.client.get_user(owner_id)
    if not owner_user:
        try:
            owner_user = await interaction.client.fetch_user(owner_id)
        except Exception:
            owner_user = None

    if owner_user:
        dm_embed = discord.Embed(
            title=f"📬 Talebiniz Sonuçlandı · Ticket Resolved ({tid})",
            description=(
                f"**🇹🇷 Türkçe:** **{interaction.guild.name}** sunucusundaki destek talebiniz sonuçlandırılmıştır.\n"
                f"**🇬🇧 English:** Your support ticket in **{interaction.guild.name}** has been resolved.\n\n"
                f"🔖 **Takip Numarası / Tracking ID:** `{tid}`\n"
                f"📂 **Kategori / Category:** {tdata.get('category_label', '-')}\n"
                f"📝 **Konu / Subject:** {tdata.get('subject', '-')}\n"
                f"👮 **İlgilenen Yetkili / Assigned Officer:** {assigned_name}\n"
                f"⚖️ **Karar / Outcome:** **{outcome}**\n"
                f"💬 **Sonuç Açıklaması / Resolution Note:**\n> {resolution_note}\n"
                f"🗓️ **Kapanış / Closed at:** {closed_at}"
            )[:4000],
            colour=0x10B981 if status_value == "resolved" else 0x6B7280,
        )
        dm_embed.set_footer(text=f"Milletler Cemiyeti · Takip No: {tid} (Destek masasından sorgulayabilirsiniz)")
        try:
            await owner_user.send(
                embed=dm_embed,
                file=discord.File(io.BytesIO(transcript_bytes), filename=f"{tid}_transcript.txt"),
            )
            dm_sent = True
        except Exception:
            dm_sent = False

    tdata["dm_close_sent"] = dm_sent
    tickets_storage.set(tid, tdata)

    try:
        if dm_sent:
            await channel.send(f"📬 Talep sahibine (<@{owner_id}>) sonuç özel mesaj (DM) olarak iletildi. Takip No: `{tid}`")
        else:
            await channel.send(
                f"⚠️ <@{owner_id}> kullanıcısının DM kutusu kapalı olduğu için özel mesaj iletilemedi; "
                f"sonuç `{tid}` takip numarasıyla destek masasından sorgulanabilir."
            )
    except Exception:
        pass

    # Send transcript & resolution record to archive log channel
    log_cfg = tickets_storage.get(f"log_config_{interaction.guild_id}", {})
    log_ch = interaction.guild.get_channel(log_cfg.get("channel_id")) if log_cfg else None
    if not log_ch:
        log_ch = discord.utils.find(lambda c: "ticket-log" in c.name, interaction.guild.text_channels)

    if log_ch:
        assigned_str = f"<@{assigned_id}>" if assigned_id else "`Atanmadı`"
        log_embed = discord.Embed(
            title=f"📋 Ticket Sonuçlandı · {tid}",
            description=(
                f"🔖 **Takip No / Tracking ID:** `{tid}`\n"
                f"📂 **Kategori / Category:** {tdata.get('category_label', '-')}\n"
                f"📝 **Konu / Subject:** {tdata.get('subject', '-')}\n"
                f"👤 **Talep Sahibi / Owner:** <@{owner_id}>\n"
                f"👮 **Atanan Yetkili / Officer:** {assigned_str}\n"
                f"🔒 **Kapatan / Closed by:** <@{interaction.user.id}>\n"
                f"⚖️ **Karar / Outcome:** **{outcome}**\n"
                f"💬 **Not / Note:** {resolution_note[:500]}\n"
                f"📬 **Özel Mesaj (DM):** {'İletildi ✅' if dm_sent else 'DM Kapalı ⚠️'}"
            ),
            colour=0x10B981 if status_value == "resolved" else 0x6B7280,
        )
        try:
            await log_ch.send(
                embed=log_embed,
                file=discord.File(io.BytesIO(transcript_bytes), filename=f"{tid}_transcript.txt"),
            )
        except Exception:
            pass

    parent_cat = channel.category
    await asyncio.sleep(5)
    try:
        await channel.delete(reason=f"Ticket {tid} finalized by {interaction.user}")
    except Exception:
        pass

    # Clean empty private tickets category automatically so sidebar stays tidy
    if parent_cat and parent_cat.name in {TICKET_CATEGORY_NAME, "🎫│MİLLETLER CEMİYETİ TİCKETLAR", "🎫│TİCKETLAR · SUPPORT TICKETS"}:
        try:
            refreshed = interaction.guild.get_channel(parent_cat.id)
            if refreshed and len(refreshed.channels) == 0:
                await refreshed.delete(reason="Son ticket kapatıldığı için boş ticket kategorisi kaldırıldı")
        except Exception:
            pass


class AddMemberModal(discord.ui.Modal, title="Üye Ekle · Add Member to Ticket"):
    target_input = discord.ui.TextInput(
        label="Kullanıcı ID / Discord User ID",
        placeholder="Örn / e.g.: 123456789012345678",
        required=True,
        max_length=40,
    )

    async def on_submit(self, interaction: discord.Interaction):
        raw = re.sub(r"[<@!> ]", "", self.target_input.value.strip())
        if not raw.isdigit():
            return await interaction.response.send_message("❌ Geçerli bir ID girin. / Enter a valid numeric user ID.", ephemeral=True)
        member = await resolve_member(interaction.guild, int(raw))
        if not member:
            return await interaction.response.send_message("❌ Üye bulunamadı. / Member not found in server.", ephemeral=True)
        await interaction.channel.set_permissions(
            member, view_channel=True, send_messages=True, attach_files=True, read_message_history=True
        )
        await interaction.response.send_message(f"✅ <@{member.id}> ticket kanalına eklendi / added to ticket.")


class TicketResolveModal(discord.ui.Modal, title="Ticket Sonuçlandır · Resolve Ticket"):
    outcome_input = discord.ui.TextInput(
        label="Sonuç / Karar · Decision Outcome",
        placeholder="Örn: Onaylandı, Çözümlendi, Reddedildi / Approved, Resolved",
        default="Çözümlendi / Resolved",
        required=True,
        max_length=80,
    )
    note_input = discord.ui.TextInput(
        label="Kişiye DM Sonuç Mesajı / Resolution Note",
        style=discord.TextStyle.paragraph,
        placeholder="Talep sahibine özel mesaj (DM) ile iletilecek karar ve açıklama...",
        required=True,
        max_length=1500,
    )

    def __init__(self, tid: str = "MC-TCK-0001"):
        super().__init__()
        self.tid = tid

    async def on_submit(self, interaction: discord.Interaction):
        await _finalize_ticket_close(
            interaction,
            outcome=self.outcome_input.value.strip(),
            resolution_note=self.note_input.value.strip(),
            status_value="resolved",
        )


class TicketLookupModal(discord.ui.Modal, title="Takip No Sorgula · Track Ticket"):
    tracking_input = discord.ui.TextInput(
        label="Takip Numarası / Tracking ID",
        placeholder="Örn / e.g.: MC-TCK-0002 veya / or 2",
        required=True,
        max_length=30,
    )

    async def on_submit(self, interaction: discord.Interaction):
        tid = _normalize_tracking_id(self.tracking_input.value)
        tdata = tickets_storage.get(tid)
        if not isinstance(tdata, dict) or tdata.get("guild_id") != interaction.guild_id:
            return await interaction.response.send_message(
                f"❌ `{tid}` numaralı takip kaydı bulunamadı. / Tracking ID `{tid}` not found.",
                ephemeral=True,
            )
        is_allowed = (
            str(interaction.user.id) == str(tdata.get("owner_id"))
            or _is_assigned_or_staff(interaction.user, tdata)
        )
        if not is_allowed:
            return await interaction.response.send_message(
                "🔒 Gizlilik kuralı: Yalnızca talep sahibi veya atanan yetkili görüntüleyebilir. / Privacy: Requester or assigned officer only.",
                ephemeral=True,
            )
        await interaction.response.send_message(embed=_format_ticket_status_embed(tid, tdata), ephemeral=True)


class ReassignOfficerSelect(discord.ui.Select):
    def __init__(self, tid: str, officers: list[tuple[discord.Member, str]]):
        self.tid = tid
        options = [
            discord.SelectOption(
                label=m.display_name[:100],
                value=str(m.id),
                description=f"{badge} · Devret / Reassign"[:100],
                emoji="👮",
            )
            for m, badge in officers[:25]
        ]
        super().__init__(
            placeholder="👮 Devredilecek yetkiliyi seçin / Select new officer...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        new_member = await resolve_member(interaction.guild, int(self.values[0]))
        if not new_member:
            return await interaction.response.send_message("❌ Yetkili sunucuda bulunamadı.", ephemeral=True)
        await _apply_reassign(interaction, self.tid, new_member)


class ReassignOfficerUserSearch(discord.ui.UserSelect):
    def __init__(self, tid: str):
        self.tid = tid
        super().__init__(
            placeholder="🔍 Veya yetkili ara / Or pick officer by username...",
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        picked = self.values[0]
        member = picked if isinstance(picked, discord.Member) else await resolve_member(interaction.guild, picked.id)
        if not member or member.bot or not _is_ticket_staff(member):
            return await interaction.response.send_message(
                "❌ Seçilen kişi yetkili değil (Admin / Genel Sekreter / Yargıç olmalı). / Selected user is not staff.",
                ephemeral=True,
            )
        register_staff_member(member)
        await _apply_reassign(interaction, self.tid, member)


async def _apply_reassign(interaction: discord.Interaction, tid: str, new_officer: discord.Member):
    tdata = tickets_storage.get(tid)
    if not isinstance(tdata, dict):
        return await interaction.response.send_message("❌ Ticket kaydı bulunamadı.", ephemeral=True)
    channel = interaction.guild.get_channel(int(tdata.get("channel_id", 0))) or interaction.channel
    old_id = tdata.get("assigned_to_id")
    owner_id = tdata.get("owner_id")

    if old_id and int(old_id) != int(owner_id) and int(old_id) != new_officer.id:
        old_member = await resolve_member(interaction.guild, int(old_id))
        if old_member:
            try:
                await channel.set_permissions(old_member, overwrite=None)
            except Exception:
                pass

    await channel.set_permissions(
        new_officer,
        view_channel=True,
        send_messages=True,
        attach_files=True,
        read_message_history=True,
        manage_messages=True,
    )
    register_staff_member(new_officer)
    tdata["assigned_to_id"] = new_officer.id
    tdata["assigned_to_name"] = new_officer.display_name
    tdata["claimed_by"] = new_officer.id
    tickets_storage.set(tid, tdata)

    await interaction.response.send_message(
        f"✅ Talep devredildi / Reassigned to <@{new_officer.id}>.",
        ephemeral=True,
    )
    await channel.send(
        f"🔄 **Yetkili Devri / Reassigned:** `{tid}` talebi <@{interaction.user.id}> tarafından <@{new_officer.id}> yetkilisine devredildi."
    )


class ReassignOfficerView(discord.ui.View):
    def __init__(self, tid: str, officers: list[tuple[discord.Member, str]]):
        super().__init__(timeout=300)
        if officers:
            self.add_item(ReassignOfficerSelect(tid, officers))
        self.add_item(ReassignOfficerUserSearch(tid))


class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Sonuçlandır · Resolve", style=discord.ButtonStyle.success, emoji="✅", custom_id="mc_ticket_resolve")
    async def btn_resolve(self, interaction: discord.Interaction, button: discord.ui.Button):
        tid, tdata = _get_ticket_by_channel(interaction.channel_id)
        if not tdata:
            return await interaction.response.send_message("❌ Ticket kaydı bulunamadı. / Ticket record not found.", ephemeral=True)
        if not _is_assigned_or_staff(interaction.user, tdata):
            return await interaction.response.send_message(
                "❌ Yalnızca atanan yetkili veya yöneticiler sonuçlandırabilir. / Assigned officer or staff only.",
                ephemeral=True,
            )
        register_staff_member(interaction.user)
        await interaction.response.send_modal(TicketResolveModal(tid))

    @discord.ui.button(label="Yetkili Devret · Reassign", style=discord.ButtonStyle.primary, emoji="🔄", custom_id="mc_ticket_reassign")
    async def btn_reassign(self, interaction: discord.Interaction, button: discord.ui.Button):
        tid, tdata = _get_ticket_by_channel(interaction.channel_id)
        if not tdata:
            return await interaction.response.send_message("❌ Ticket kaydı bulunamadı. / Ticket record not found.", ephemeral=True)
        if not _is_assigned_or_staff(interaction.user, tdata):
            return await interaction.response.send_message(
                "❌ Yalnızca atanan yetkili veya yöneticiler devredebilir. / Assigned officer or staff only.",
                ephemeral=True,
            )
        officers = await _resolve_available_officers(interaction.guild, tdata.get("category", "secretariat"))
        await interaction.response.send_message(
            "🔄 **Yeni Yetkili Seçin / Choose New Officer:**",
            view=ReassignOfficerView(tid, officers),
            ephemeral=True,
        )

    @discord.ui.button(label="Üye Ekle · Add Member", style=discord.ButtonStyle.secondary, emoji="👥", custom_id="mc_ticket_add_member")
    async def btn_add_member(self, interaction: discord.Interaction, button: discord.ui.Button):
        tid, tdata = _get_ticket_by_channel(interaction.channel_id)
        if not tdata or not _is_assigned_or_staff(interaction.user, tdata):
            return await interaction.response.send_message("❌ Yalnızca atanan yetkili veya yöneticiler ekleyebilir. / Officer or staff only.", ephemeral=True)
        await interaction.response.send_modal(AddMemberModal())

    @discord.ui.button(label="Hızlı Kapat · Close", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="mc_ticket_close")
    async def btn_close(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _finalize_ticket_close(
            interaction,
            outcome="Kapatıldı / Closed",
            resolution_note="Kanal buton ile kapatıldı; özet ve takip kaydı DM olarak gönderildi.",
            status_value="closed",
        )


class TicketOpenModal(discord.ui.Modal, title="Talep Oluştur · Open Private Ticket"):
    subject = discord.ui.TextInput(
        label="Konu / Subject",
        placeholder="Örn / e.g.: Arabuluculuk veya Akreditasyon Talebi",
        required=True,
        max_length=120,
    )
    details = discord.ui.TextInput(
        label="Detaylı Açıklama / Detailed Description",
        style=discord.TextStyle.paragraph,
        placeholder="Devletinizi ve talebinizi yazın / State your nation & details...",
        required=True,
        max_length=1500,
    )

    def __init__(
        self,
        category_key: str,
        assigned_officer: discord.Member | discord.User | None = None,
        assigned_name: str = "Yetkili",
    ):
        cat_info = TICKET_CATEGORIES.get(category_key, TICKET_CATEGORIES["ooc"])
        target_name = assigned_officer.display_name if assigned_officer else assigned_name
        modal_title = f"{cat_info['short']} → {target_name}"[:45]
        super().__init__(title=modal_title)
        self.category_key = category_key
        self.assigned_officer_id = assigned_officer.id if assigned_officer else None
        self.assigned_officer = assigned_officer

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        cat_info = TICKET_CATEGORIES.get(self.category_key, TICKET_CATEGORIES["ooc"])

        officer = self.assigned_officer
        if not isinstance(officer, discord.Member) and self.assigned_officer_id:
            officer = await resolve_member(guild, self.assigned_officer_id)
        if not isinstance(officer, discord.Member):
            officer = await resolve_member(guild, guild.owner_id)
        if not officer:
            return await interaction.followup.send("❌ Seçilen yetkiliye ulaşılamadı. / Assigned officer unavailable.", ephemeral=True)

        register_staff_member(officer)
        seq, tid = _next_tracking_id()

        category = discord.utils.get(guild.categories, name=TICKET_CATEGORY_NAME)
        if not category:
            for legacy_name in ("🎫│MİLLETLER CEMİYETİ TİCKETLAR", "🎫│TİCKETLAR · SUPPORT TICKETS"):
                old_cat = discord.utils.get(guild.categories, name=legacy_name)
                if old_cat:
                    try:
                        await old_cat.edit(name=TICKET_CATEGORY_NAME)
                    except Exception:
                        pass
                    category = old_cat
                    break
        if not category:
            category = await guild.create_category(TICKET_CATEGORY_NAME)

        # Strictly private channel: visible ONLY to the requester + assigned officer (+ bot)
        overwrites = _build_private_overwrites(guild, interaction.user, officer)
        ch_name = f"{cat_info['prefix']}-{seq:04d}"
        ch = await guild.create_text_channel(
            ch_name,
            category=category,
            topic=f"🔒 {tid} · {cat_info['label']} · Sahip: {interaction.user.display_name} · Yetkili: {officer.display_name}",
            overwrites=overwrites,
        )

        del_rec = delegates_storage.get(str(interaction.user.id), {})
        nation_str = f"`{del_rec['nation']}`" if del_rec.get("nation") else "`Akredite Değil / Unaccredited`"
        created_at = datetime.utcnow().strftime("%d.%m.%Y %H:%M UTC")
        subj = self.subject.value.strip()
        det = self.details.value.strip()

        # Opening DM receipt with Tracking ID
        dm_open_sent = False
        try:
            open_dm = discord.Embed(
                title=f"🎫 Talebiniz Alındı · Ticket Created ({tid})",
                description=(
                    f"🔖 **Takip Numaranız / Tracking ID:** `{tid}`\n"
                    f"📂 **Kategori / Category:** {cat_info['label']}\n"
                    f"👮 **Atanan Yetkili / Assigned Officer:** {officer.display_name}\n"
                    f"📝 **Konu / Subject:** {subj}\n"
                    f"💬 **Özel Kanal / Channel:** {ch.mention}\n\n"
                    "*Yetkili talebinizi sonuçlandırdığında sonucu ve tam dökümü buradan özel mesaj (DM) olarak alacaksınız.*"
                ),
                colour=0x1E3A8A,
            )
            await interaction.user.send(embed=open_dm)
            dm_open_sent = True
        except Exception:
            dm_open_sent = False

        tickets_storage.set(
            tid,
            {
                "id": tid,
                "tracking_id": tid,
                "channel_id": ch.id,
                "guild_id": guild.id,
                "category": self.category_key,
                "category_label": cat_info["label"],
                "owner_id": interaction.user.id,
                "owner_name": interaction.user.display_name,
                "nation": del_rec.get("nation"),
                "assigned_to_id": officer.id,
                "assigned_to_name": officer.display_name,
                "claimed_by": officer.id,
                "subject": subj,
                "details": det,
                "status": "open",
                "outcome": None,
                "resolution_note": None,
                "created_at": created_at,
                "dm_open_sent": dm_open_sent,
                "dm_close_sent": False,
            },
        )

        embed = discord.Embed(
            title=f"{cat_info['emoji']} {tid} · {cat_info['label']}",
            description=(
                f"🔖 **Takip No / Tracking ID:** `{tid}`\n"
                f"👤 **Talep Sahibi / Opened by:** <@{interaction.user.id}> ({nation_str})\n"
                f"👮 **Atanan Yetkili / Assigned Officer:** <@{officer.id}> (`{_badge_for_member(officer)}`)\n"
                f"🔒 **Gizlilik / Privacy:** Yalnızca size ve atanan yetkiliye özel kanal / Private to you & officer only\n"
                f"📝 **Konu / Subject:** **{subj}**\n\n"
                f"**Detaylı Açıklama / Details:**\n> {det}"
            )[:4000],
            colour=0x1E3A8A,
        )
        embed.set_footer(text=f"Takip No: {tid} · Genel Sekreterlik / League of Nations Support")
        ping_text = f"<@{interaction.user.id}> <@{officer.id}>" if officer.id != interaction.user.id else f"<@{interaction.user.id}>"
        await ch.send(content=ping_text, embed=embed, view=TicketControlView())
        await interaction.followup.send(
            f"✅ **Özel destek kanalınız açıldı / Private ticket created!**\n"
            f"🔖 **Takip Numarası / Tracking ID:** `{tid}`\n"
            f"👮 **Atanan Yetkili / Officer:** <@{officer.id}>\n"
            f"💬 **Kanal / Channel:** {ch.mention}",
            ephemeral=True,
        )


class OfficerSelect(discord.ui.Select):
    def __init__(self, category_key: str, officers: list[tuple[discord.Member, str]]):
        self.category_key = category_key
        options = [
            discord.SelectOption(
                label=m.display_name[:100],
                value=str(m.id),
                description=f"{badge} · Seç ve formu aç / Pick & write"[:100],
                emoji="👮",
            )
            for m, badge in officers[:25]
        ]
        super().__init__(
            placeholder="👮 Atanacak yetkiliyi seçin / Choose assigned officer...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        member = await resolve_member(interaction.guild, int(self.values[0]))
        if not member:
            return await interaction.response.send_message("❌ Seçilen yetkili bulunamadı. / Officer not found.", ephemeral=True)
        await interaction.response.send_modal(TicketOpenModal(self.category_key, member))


class OfficerUserSearch(discord.ui.UserSelect):
    def __init__(self, category_key: str):
        self.category_key = category_key
        super().__init__(
            placeholder="🔍 Listede yoksa Yetkili ara / Search officer by name...",
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        picked = self.values[0]
        member = picked if isinstance(picked, discord.Member) else await resolve_member(interaction.guild, picked.id)
        if not member or member.bot or not _is_ticket_staff(member):
            return await interaction.response.send_message(
                "❌ Seçtiğiniz kişi yetkili değil. Lütfen Yönetim / Genel Sekreterlik / Divan Yargıcı / Admin rolüne sahip bir yetkili seçin.\n"
                "*(Selected user is not an authorized Staff/Officer member.)*",
                ephemeral=True,
            )
        register_staff_member(member)
        await interaction.response.send_modal(TicketOpenModal(self.category_key, member))


class OfficerPickerView(discord.ui.View):
    def __init__(self, category_key: str, officers: list[tuple[discord.Member, str]]):
        super().__init__(timeout=300)
        if officers:
            self.add_item(OfficerSelect(category_key, officers))
        self.add_item(OfficerUserSearch(category_key))


async def _start_ticket_flow(interaction: discord.Interaction, category_key: str):
    if _is_ticket_staff(interaction.user):
        register_staff_member(interaction.user)
    cat_info = TICKET_CATEGORIES.get(category_key, TICKET_CATEGORIES["ooc"])
    officers = await _resolve_available_officers(interaction.guild, category_key)
    embed = discord.Embed(
        title=f"{cat_info['emoji']} Yetkili Seçimi · Select Officer ({cat_info['short']})",
        description=(
            "**🇹🇷 Türkçe:** Bu talep **yalnızca size ve seçeceğiniz yetkiliye özel** gizli bir oda olarak açılacaktır. "
            "Aşağıdan bir yetkili seçin; seçtiğiniz anda başvuru formu açılır.\n"
            "**🇬🇧 English:** This opens a **private channel visible ONLY to you and your chosen officer**. "
            "Pick an officer below to immediately open the request form."
        ),
        colour=0x1E3A8A,
    )
    await interaction.response.send_message(embed=embed, view=OfficerPickerView(category_key, officers), ephemeral=True)


class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Sekreterlik · Secretariat", style=discord.ButtonStyle.primary, emoji="🏛️", custom_id="mc_tck_open:secretariat", row=0)
    async def btn_sec(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _start_ticket_flow(interaction, "secretariat")

    @discord.ui.button(label="Arabuluculuk · Mediation", style=discord.ButtonStyle.success, emoji="🕊️", custom_id="mc_tck_open:diplomacy", row=0)
    async def btn_dip(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _start_ticket_flow(interaction, "diplomacy")

    @discord.ui.button(label="Yargı Divanı · ICJ Court", style=discord.ButtonStyle.secondary, emoji="⚖️", custom_id="mc_tck_open:icj", row=0)
    async def btn_icj(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _start_ticket_flow(interaction, "icj")

    @discord.ui.button(label="Destek & OOC · Staff", style=discord.ButtonStyle.secondary, emoji="🛡️", custom_id="mc_tck_open:ooc", row=0)
    async def btn_ooc(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _start_ticket_flow(interaction, "ooc")

    @discord.ui.button(label="Takip No Sorgula · Track ID", style=discord.ButtonStyle.primary, emoji="🔎", custom_id="mc_tck_lookup", row=1)
    async def btn_lookup(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TicketLookupModal())

    @discord.ui.button(label="Taleplerim · My Tickets", style=discord.ButtonStyle.secondary, emoji="📋", custom_id="mc_tck_mine", row=1)
    async def btn_mine(self, interaction: discord.Interaction, button: discord.ui.Button):
        all_data = tickets_storage.read()
        uid = str(interaction.user.id)
        gid = interaction.guild_id
        mine = []
        assigned = []
        for tid, td in all_data.items():
            if not isinstance(td, dict) or td.get("guild_id") != gid or not str(tid).startswith("MC-TCK-"):
                continue
            st_icon = "🟢" if td.get("status") == "open" else ("✅" if td.get("status") == "resolved" else "🔒")
            line = f"{st_icon} `{tid}` · **{td.get('subject', '-')[:45]}** *(Yetkili: {td.get('assigned_to_name', '-')})*"
            if str(td.get("owner_id")) == uid:
                mine.append(line)
            if str(td.get("assigned_to_id")) == uid and td.get("status") == "open":
                assigned.append(line)

        desc = ""
        if mine:
            desc += "**📩 Açtığım Talepler / My Requests:**\n" + "\n".join(mine[-10:]) + "\n\n"
        if assigned:
            desc += "**👮 Bana Atanan Açık Talepler / Assigned Open Tickets:**\n" + "\n".join(assigned[-10:])
        if not desc:
            desc = "Henüz bir destek kaydınız yok. / You have no ticket records yet."
        await interaction.response.send_message(
            embed=discord.Embed(title="📋 Destek Taleplerim · My Ticket Records", description=desc[:4000], colour=0x1E3A8A),
            ephemeral=True,
        )


def build_ticket_panel() -> tuple[discord.Embed, discord.ui.View]:
    embed = discord.Embed(
        title="🎫 DESTEK & BAŞVURU MASASI · SUPPORT & TICKET DESK",
        description=(
            "**🇹🇷 Türkçe · Özel Yetkili Atamalı Sistem:**\n"
            "• Aşağıdan **butona tıklayın**, görüşmek istediğiniz **yetkiliyi seçin** ve açılan formu doldurun.\n"
            "• Açılan kanal **yalnızca size ve seçtiğiniz yetkiliye özel** görünür.\n"
            "• Her talep benzersiz **Takip Numarası (`MC-TCK-XXXX`)** alır ve **sonuçlandığında bot size özel mesaj (DM)** gönderir.\n\n"
            "**🇬🇧 English · Private Officer-Assigned Desk:**\n"
            "• Click a **category button** below, **pick your assigned officer**, and fill out the form.\n"
            "• Your channel is strictly **private to you and your selected officer**.\n"
            "• Every request receives a **Tracking ID (`MC-TCK-XXXX`)** and the bot **DMs you** once resolved."
        ),
        colour=0x1E3A8A,
    )
    embed.set_footer(text="Milletler Cemiyeti · League of Nations Private Support System")
    return embed, TicketPanelView()


class TicketsCog(commands.Cog):
    """Milletler Cemiyeti Ozel Yetkili Atamali & Takip Numarali Ticket Sistemi."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(TicketPanelView())
        self.bot.add_view(TicketControlView())

    @commands.Cog.listener("on_interaction")
    async def on_any_interaction_capture_staff(self, interaction: discord.Interaction):
        if interaction.guild and isinstance(interaction.user, discord.Member) and not interaction.user.bot:
            if _is_ticket_staff(interaction.user):
                register_staff_member(interaction.user)

    @app_commands.command(name="ticket-paneli-kur", description="İlgili resmi kanala butonlu Milletler Cemiyeti Ticket Masası kurar")
    @app_commands.describe(kanal="İsteğe bağlı hedef kanal (boş bırakılırsa otomatik #destek-masasi-tickets kanalına kurulur)")
    async def cmd_setup_ticket_panel(self, interaction: discord.Interaction, kanal: discord.TextChannel = None):
        if not _is_ticket_staff(interaction.user):
            return await interaction.response.send_message("❌ Bu komutu yalnızca Genel Sekreterlik veya Yöneticiler kullanabilir.", ephemeral=True)

        target = resolve_panel_channel(interaction.guild, kanal, "destek-masasi-tickets", interaction.channel)
        embed, view = build_ticket_panel()
        st = await upsert_panel_message(target, ["🎫 DESTEK", "🎫 Milletler Cemiyeti"], embed, view)
        await interaction.response.send_message(f"✅ Butonlu Ticket Masası {target.mention} kanalında güncellendi (`{st}`).", ephemeral=True)

    @app_commands.command(name="yetkili-tanimla", description="Ticket seçiciye görünecek bir yetkiliyi listeye ekler veya listeden çıkarır")
    @app_commands.describe(uye="Yetkili olarak tanımlanacak üye", unvan="Görünecek kısa görev unvanı (ör. Genel Sekreter)")
    async def cmd_register_officer(self, interaction: discord.Interaction, uye: discord.Member, unvan: str = None):
        if not _is_ticket_staff(interaction.user):
            return await interaction.response.send_message("❌ Yalnızca yöneticiler yetkili listesini düzenleyebilir.", ephemeral=True)
        if not _is_ticket_staff(uye):
            return await interaction.response.send_message(
                "❌ Bu üyenin yetkili rolü yok. Önce kendisine Yönetim / Sekreterlik / Yargıç / Admin rolü verin.",
                ephemeral=True,
            )
        register_staff_member(uye, custom_badge=unvan)
        await interaction.response.send_message(
            f"✅ <@{uye.id}> ticket yetkili listesine eklendi (`{unvan or _badge_for_member(uye)}`).",
            ephemeral=True,
        )

    @app_commands.command(name="ticket-sorgula", description="Takip numarası (MC-TCK-XXXX) ile ticket durumunu ve sonucunu sorgular")
    @app_commands.describe(takip_no="Örn: MC-TCK-0001 veya 1")
    async def cmd_ticket_lookup(self, interaction: discord.Interaction, takip_no: str):
        tid = _normalize_tracking_id(takip_no)
        tdata = tickets_storage.get(tid)
        if not isinstance(tdata, dict) or tdata.get("guild_id") != interaction.guild_id:
            return await interaction.response.send_message(f"❌ `{tid}` bulunamadı. / Tracking ID not found.", ephemeral=True)
        if not (str(interaction.user.id) == str(tdata.get("owner_id")) or _is_assigned_or_staff(interaction.user, tdata)):
            return await interaction.response.send_message("🔒 Bu takip numarasını görüntüleme yetkiniz yok.", ephemeral=True)
        await interaction.response.send_message(embed=_format_ticket_status_embed(tid, tdata), ephemeral=True)

    @app_commands.command(name="ticket-listele", description="Açık veya kapalı ticketları takip numaralarıyla listeler")
    @app_commands.describe(durum="Filtre: acik / kapali / hepsi")
    async def cmd_ticket_list(self, interaction: discord.Interaction, durum: str = "acik"):
        if not _is_ticket_staff(interaction.user):
            return await interaction.response.send_message("❌ Yalnızca yetkililer tüm listeyi görebilir.", ephemeral=True)
        filt = durum.lower()
        rows = []
        for tid, td in tickets_storage.read().items():
            if not isinstance(td, dict) or td.get("guild_id") != interaction.guild_id or not str(tid).startswith("MC-TCK-"):
                continue
            is_open = td.get("status") == "open"
            if filt in {"acik", "open"} and not is_open:
                continue
            if filt in {"kapali", "closed", "resolved"} and is_open:
                continue
            icon = "🟢" if is_open else "✅"
            rows.append(f"{icon} `{tid}` · <@{td.get('owner_id')}> → <@{td.get('assigned_to_id') or td.get('claimed_by') or 0}> · **{td.get('subject', '-')[:35]}**")
        text = "\n".join(rows[-20:]) or "Kayıt bulunamadı. / No matching tickets."
        await interaction.response.send_message(
            embed=discord.Embed(title=f"📋 Ticket Listesi ({durum})", description=text[:4000], colour=0x1E3A8A),
            ephemeral=True,
        )

    @app_commands.command(name="ticket-devret", description="Aktif ticket kanalını seçilen başka bir yetkiliye atar/devreder")
    @app_commands.describe(uye="Devredilecek yeni yetkili")
    async def cmd_ticket_reassign(self, interaction: discord.Interaction, uye: discord.Member):
        tid, tdata = _get_ticket_by_channel(interaction.channel_id)
        if not tdata:
            return await interaction.response.send_message("❌ Bu komut yalnızca aktif ticket kanalında kullanılabilir.", ephemeral=True)
        if not _is_assigned_or_staff(interaction.user, tdata):
            return await interaction.response.send_message("❌ Yalnızca atanan yetkili veya yöneticiler devredebilir.", ephemeral=True)
        if uye.bot or not _is_ticket_staff(uye):
            return await interaction.response.send_message("❌ Hedef üye yetkili rolüne sahip değil.", ephemeral=True)
        await _apply_reassign(interaction, tid, uye)

    @app_commands.command(name="ticket-sonuclandir", description="Ticket'ı sonuçlandırır, kişiye DM atar, takip kaydını saklar ve kanalı kapatır")
    @app_commands.describe(sonuc="Örn: Onaylandı / Çözümlendi / Reddedildi", aciklama="Kişiye DM ile iletilecek karar açıklaması")
    async def cmd_ticket_resolve(self, interaction: discord.Interaction, sonuc: str, aciklama: str):
        await _finalize_ticket_close(interaction, outcome=sonuc, resolution_note=aciklama, status_value="resolved")

    @app_commands.command(name="ticket-log-kanali", description="Kapatılan ticket transkriptlerinin gönderileceği log kanalını ayarlar")
    @app_commands.describe(kanal="Ticket log kanalı")
    async def cmd_set_ticket_log(self, interaction: discord.Interaction, kanal: discord.TextChannel):
        if not _is_ticket_staff(interaction.user):
            return await interaction.response.send_message("❌ Bu komutu yalnızca Genel Sekreterlik veya Yöneticiler kullanabilir.", ephemeral=True)
        tickets_storage.set(f"log_config_{interaction.guild_id}", {"channel_id": kanal.id})
        await interaction.response.send_message(f"✅ Ticket transkript log kanalı {kanal.mention} olarak ayarlandı.", ephemeral=True)

    @app_commands.command(name="ticket-ekle", description="Aktif ticket kanalına bir üyeyi ekler")
    @app_commands.describe(uye="Ticket'a eklenecek üye")
    async def cmd_ticket_add(self, interaction: discord.Interaction, uye: discord.Member):
        tid, tdata = _get_ticket_by_channel(interaction.channel_id)
        if not tid:
            return await interaction.response.send_message("❌ Bu komut yalnızca aktif bir ticket kanalında kullanılabilir.", ephemeral=True)
        if not _is_assigned_or_staff(interaction.user, tdata):
            return await interaction.response.send_message("❌ Yalnızca atanan yetkili veya yöneticiler ekleyebilir.", ephemeral=True)
        await interaction.channel.set_permissions(uye, view_channel=True, send_messages=True, attach_files=True, read_message_history=True)
        await interaction.response.send_message(f"✅ {uye.mention} ticket'a eklendi.")

    @app_commands.command(name="ticket-cikar", description="Aktif ticket kanalından bir üyeyi çıkarır")
    @app_commands.describe(uye="Ticket'tan çıkarılacak üye")
    async def cmd_ticket_remove(self, interaction: discord.Interaction, uye: discord.Member):
        tid, tdata = _get_ticket_by_channel(interaction.channel_id)
        if not tid:
            return await interaction.response.send_message("❌ Bu komut yalnızca aktif bir ticket kanalında kullanılabilir.", ephemeral=True)
        if not _is_assigned_or_staff(interaction.user, tdata):
            return await interaction.response.send_message("❌ Yalnızca atanan yetkili veya yöneticiler çıkarabilir.", ephemeral=True)
        await interaction.channel.set_permissions(uye, overwrite=None)
        await interaction.response.send_message(f"➖ {uye.mention} ticket'tan çıkarıldı.")

    @app_commands.command(name="ticket-kapat", description="Bulunduğunuz ticket kanalını kapatır, kişiye DM atar ve transkriptini loglar")
    @app_commands.describe(sebep="Kapanış sebebi (opsiyonel)")
    async def cmd_ticket_close(self, interaction: discord.Interaction, sebep: str = "Tamamlandı / Completed"):
        await _finalize_ticket_close(interaction, outcome="Kapatıldı / Closed", resolution_note=sebep, status_value="closed")


async def setup(bot: commands.Bot):
    await bot.add_cog(TicketsCog(bot))
