"""Accredited Press (Gazeteci), Moderator, and Admin applications with live open/close toggles."""

from datetime import datetime, timezone
import discord
from discord import app_commands
from discord.ext import commands

from utils.storage import applications_storage, delegates_storage, sticky_roles_storage
from utils.constants import (
    ROLE_ADMIN,
    ROLE_MODERATOR,
    ROLE_PRESS,
    ROLE_SG,
    ROLE_VERIFIED,
    resolve_panel_channel,
)
from utils.un_gazette import upsert_panel_message
from cogs.delegation import (
    ensure_role,
    find_approval_channel,
    mark_bot_role_op,
    resolve_member,
    save_sticky_snapshot,
)
from cogs.tickets import register_staff_member

PANEL_TITLE_PREFIX = "📝 BAŞVURU ODASI"
CONTROL_TITLE_PREFIX = "🎛️ ALIM YÖNETİM PANELİ"

APP_TRACKS: dict[str, dict] = {
    "press": {
        "key": "press",
        "emoji": "📰",
        "title_tr": "Gazeteci / Akredite Basın",
        "title_en": "Journalist / Accredited Press",
        "button_label": "Gazeteci Başvurusu · Press",
        "modal_title": "📰 Gazeteci / Basın Başvurusu",
        "colour": 0x0284C7,
        "target_roles": [ROLE_PRESS],
        "extra_roles": [],
        "staff_roster": False,
        "allow_mod_review": True,
        "exp_placeholder": "Basın, haber yazarlığı veya RP gazeteciliği deneyiminiz...",
        "desc_tr": "Dünya basını (`#📰・world-press-basın`) haber yazarlığı ve basın akreditasyonu.",
        "desc_en": "Publish news reports in `#📰・world-press-basın` with press accreditation.",
    },
    "moderator": {
        "key": "moderator",
        "emoji": "🛡️",
        "title_tr": "Yetkili / Moderatör",
        "title_en": "Staff / Moderator",
        "button_label": "Moderatör Başvurusu · Moderator",
        "modal_title": "🛡️ Moderatör / Yetkili Başvurusu",
        "colour": 0x0EA5E9,
        "target_roles": [ROLE_MODERATOR],
        "extra_roles": [],
        "staff_roster": True,
        "allow_mod_review": False,
        "exp_placeholder": "Discord moderasyonu, ticket yönetimi ve RP kuralları tecrübeniz...",
        "desc_tr": "Sohbet düzeni, ticket desteği, küfür/moderasyon ve basın başvurusu inceleme.",
        "desc_en": "Server moderation, ticket support, audit logs, and press reviews.",
    },
    "admin": {
        "key": "admin",
        "emoji": "🏛️",
        "title_tr": "Admin / Cemiyet Yönetimi",
        "title_en": "Admin / League Management",
        "button_label": "Admin Başvurusu · Admin",
        "modal_title": "🏛️ Admin / Yönetim Başvurusu",
        "colour": 0xD97706,
        "target_roles": [ROLE_ADMIN, "Admin"],
        "extra_roles": [],
        "staff_roster": True,
        "allow_mod_review": False,
        "exp_placeholder": "Üst düzey sunucu yönetimi, kriz/RP organizasyonu tecrübeniz...",
        "desc_tr": "Üst düzey yönetim, personel & kayıt onayları, oturum/kriz yönetimi.",
        "desc_en": "Senior administration, approvals, and full League operations.",
    },
}

_MODERATOR_PERM_FLAGS = {
    "view_audit_log": True,
    "kick_members": True,
    "ban_members": True,
    "manage_messages": True,
    "mute_members": True,
    "deafen_members": True,
    "move_members": True,
    "moderate_members": True,
    "change_nickname": False,
}


def _settings_key(guild_id: int | None) -> str:
    return f"settings_{guild_id or 'default'}"


def get_recruitment_settings(guild_id: int | None) -> dict:
    """Return persisted open/closed status for each application track."""
    raw = applications_storage.get(_settings_key(guild_id), {})
    if not isinstance(raw, dict):
        raw = {}
    return {
        "press": bool(raw.get("press", True)),
        "moderator": bool(raw.get("moderator", True)),
        "admin": bool(raw.get("admin", True)),
        "updated_by": raw.get("updated_by"),
        "updated_at": raw.get("updated_at"),
    }


def is_track_open(guild_id: int | None, track: str) -> bool:
    return bool(get_recruitment_settings(guild_id).get(track, True))


def set_track_open(guild_id: int | None, track: str, is_open: bool, *, actor_id: int | None = None) -> dict:
    """Persist open/closed recruitment state for a track and return updated settings."""
    if track not in APP_TRACKS:
        raise ValueError(f"Unknown application track: {track}")
    key = _settings_key(guild_id)
    cur = applications_storage.get(key, {})
    if not isinstance(cur, dict):
        cur = {}
    cur.setdefault("press", True)
    cur.setdefault("moderator", True)
    cur.setdefault("admin", True)
    cur[track] = bool(is_open)
    if actor_id is not None:
        cur["updated_by"] = str(actor_id)
    cur["updated_at"] = datetime.now(timezone.utc).isoformat()
    applications_storage.set(key, cur)
    return get_recruitment_settings(guild_id)


def _next_app_id() -> str:
    data = applications_storage.read()
    seq = int(data.get("seq", 0) or 0) + 1
    while f"app_MC-BSV-{seq:04d}" in data:
        seq += 1
    data["seq"] = seq
    applications_storage.write(data)
    return f"MC-BSV-{seq:04d}"


def can_manage_recruitment(member: discord.Member) -> bool:
    """Return True if member can toggle recruitment open/closed or review staff apps."""
    if not isinstance(member, discord.Member):
        return False
    if member.id == member.guild.owner_id:
        return True
    if member.guild_permissions.administrator or member.guild_permissions.manage_guild:
        return True
    role_names = {r.name for r in member.roles}
    if role_names & {ROLE_SG, ROLE_ADMIN, "🏛️ MC Genel Sekreteri", "🏛️ Cemiyet Yönetimi & Divan", "Admin"}:
        return True
    rec = delegates_storage.get(str(member.id), {})
    return rec.get("office_code") in {"SG", "DSG", "GKB", "GKB-V"}


def can_review_application(member: discord.Member, track: str) -> bool:
    """Return True if member has authority to approve/reject the given application track."""
    if can_manage_recruitment(member):
        return True
    meta = APP_TRACKS.get(track, {})
    if meta.get("allow_mod_review") and isinstance(member, discord.Member):
        if member.guild_permissions.moderate_members or any(r.name == ROLE_MODERATOR for r in member.roles):
            return True
    return False


async def sync_moderator_role_permissions(role: discord.Role | None, bot_member: discord.Member | None) -> bool:
    """Ensure the Moderator role carries safe Discord moderation permissions under bot_top."""
    if not role or not bot_member or role >= bot_member.top_role:
        return False
    perms = role.permissions
    updates = {k: v for k, v in _MODERATOR_PERM_FLAGS.items() if getattr(perms, k, None) != v}
    if not updates:
        return False
    new_perms = discord.Permissions(perms.value)
    new_perms.update(**updates)
    try:
        await role.edit(permissions=new_perms, reason="MC-KS-001 Moderatör izin senkronizasyonu")
        return True
    except Exception:
        return False


def _status_badge(is_open: bool) -> str:
    return "🟢 **AÇIK / OPEN**" if is_open else "🔴 **KAPALI / CLOSED**"


def build_application_panel(guild_id: int | None = None) -> tuple[discord.Embed, discord.ui.View]:
    """Build the public Press/Moderator/Admin application embed and view with live track status."""
    settings = get_recruitment_settings(guild_id)
    embed = discord.Embed(
        title=f"{PANEL_TITLE_PREFIX} · GAZETECİ, MODERATÖR & ADMİN BAŞVURUSU",
        description=(
            "**🇹🇷 Türkçe:** Milletler Cemiyeti bünyesinde **Akredite Basın (Gazeteci)**, "
            "**Yetkili / Moderatör** veya **Admin** kadrosuna katılmak için aşağıdaki açık alımlardan "
            "size uygun butona tıklayıp formu doldurabilirsiniz.\n\n"
            "**🇬🇧 English:** Submit an application below for **Accredited Press (Journalist)**, "
            "**Moderator**, or **Admin**. Closed tracks are paused by leadership."
        ),
        colour=0x1E3A8A,
    )
    for key in ("press", "moderator", "admin"):
        meta = APP_TRACKS[key]
        state = _status_badge(settings[key])
        embed.add_field(
            name=f"{meta['emoji']} {meta['title_tr']} · {meta['title_en']}",
            value=(
                f"**Alım Durumu / Status:** {state}\n"
                f"• **TR:** {meta['desc_tr']}\n"
                f"• **EN:** {meta['desc_en']}"
            ),
            inline=False,
        )
    embed.set_footer(text="MC-KS-001 Başvuru & Personel Sistemi · İnceleme #🛡️│onay-odasi-approvals")
    return embed, ApplicationPanelView()


def build_recruitment_control_panel(guild_id: int | None = None) -> tuple[discord.Embed, discord.ui.View]:
    """Build the staff-only recruitment open/close control embed and view."""
    settings = get_recruitment_settings(guild_id)
    lines = []
    for key in ("press", "moderator", "admin"):
        meta = APP_TRACKS[key]
        lines.append(f"{meta['emoji']} **{meta['title_tr']}:** {_status_badge(settings[key])}")
    if settings.get("updated_by"):
        lines.append(f"\n*Son Güncelleyen / Updated by:* <@{settings['updated_by']}>")
    embed = discord.Embed(
        title=f"{CONTROL_TITLE_PREFIX} · RECRUITMENT OPEN/CLOSE",
        description=(
            "**🇹🇷 Türkçe:** Gazeteci, Moderatör ve Admin başvurularını tek tıkla açıp kapatabilirsiniz. "
            "Değişiklik anında `#📝│basvuru-odasi-applications` paneline yansır.\n\n"
            "**🇬🇧 English:** Toggle Press, Moderator, and Admin applications on/off. "
            "Changes immediately sync to the public application panel.\n\n"
            + "\n".join(lines)
        ),
        colour=0xD97706,
    )
    embed.set_footer(text="Alternatif komut / Slash: /basvuru-alimi · Yönetici & Genel Sekreterlik")
    return embed, RecruitmentControlView()


async def refresh_application_panels(guild: discord.Guild | None) -> list[str]:
    """Sync both the public application panel and admin recruitment toggle panel on Discord."""
    if not guild:
        return []
    statuses: list[str] = []
    app_ch = resolve_panel_channel(guild, None, "basvuru-odasi-applications", None)
    if isinstance(app_ch, discord.TextChannel):
        emb, view = build_application_panel(guild.id)
        st = await upsert_panel_message(app_ch, [PANEL_TITLE_PREFIX], emb, view)
        statuses.append(f"Başvuru Paneli ({st})")

    ctrl_ch = resolve_panel_channel(guild, None, "yayin-paneli-broadcast", None)
    if isinstance(ctrl_ch, discord.TextChannel):
        emb, view = build_recruitment_control_panel(guild.id)
        st = await upsert_panel_message(ctrl_ch, [CONTROL_TITLE_PREFIX], emb, view)
        statuses.append(f"Alım Kontrol Paneli ({st})")
    return statuses


def _find_pending_application(guild_id: int, user_id: int, track: str) -> dict | None:
    for key, rec in applications_storage.read().items():
        if not str(key).startswith("app_") or not isinstance(rec, dict):
            continue
        if (
            rec.get("guild_id") == guild_id
            and str(rec.get("user_id")) == str(user_id)
            and rec.get("track") == track
            and rec.get("status") == "PENDING"
        ):
            return rec
    return None


def _member_already_has_track(member: discord.Member, track: str) -> bool:
    meta = APP_TRACKS[track]
    role_names = {r.name for r in member.roles}
    if track == "admin":
        return ROLE_ADMIN in role_names and "Admin" in role_names
    return any(rname in role_names for rname in meta["target_roles"])


async def submit_application_record(
    guild_id: int,
    user_id: int,
    user_tag: str,
    track: str,
    answers: dict[str, str],
) -> tuple[bool, str, dict | None]:
    """Validate and persist a new application record without touching Discord I/O."""
    if track not in APP_TRACKS:
        return False, "❌ Geçersiz başvuru kategorisi.", None
    if not is_track_open(guild_id, track):
        meta = APP_TRACKS[track]
        return (
            False,
            f"🔴 **{meta['title_tr']}** alımı şu anda yöneticiler tarafından kapatılmıştır.\n"
            f"*{meta['title_en']} applications are currently closed.*",
            None,
        )
    pending = _find_pending_application(guild_id, user_id, track)
    if pending:
        return (
            False,
            f"⏳ Zaten incelenmekte olan bir başvurunuz var (`{pending.get('id')}`). Sonuçlanmasını bekleyin.",
            None,
        )
    app_id = _next_app_id()
    rec = {
        "id": app_id,
        "guild_id": guild_id,
        "user_id": str(user_id),
        "user_tag": user_tag,
        "track": track,
        "status": "PENDING",
        "answers": answers,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    applications_storage.set(f"app_{app_id}", rec)
    return True, "", rec


async def _grant_application_roles(guild: discord.Guild, member: discord.Member, track: str) -> list[str]:
    """Assign approved Press/Moderator/Admin roles safely without removing diplomatic/country roles."""
    meta = APP_TRACKS[track]
    bot_top = guild.me.top_role.position if guild.me and guild.me.top_role else 0
    to_add: list[discord.Role] = []

    for rname in meta["target_roles"]:
        existing = discord.utils.get(guild.roles, name=rname)
        if not existing:
            existing = await ensure_role(guild, rname, meta["colour"])
        if existing and track == "moderator":
            await sync_moderator_role_permissions(existing, guild.me)
        if existing and not existing.managed and existing.position < bot_top and existing not in member.roles:
            to_add.append(existing)

    verified_role = discord.utils.get(guild.roles, name=ROLE_VERIFIED)
    if not verified_role:
        verified_role = await ensure_role(guild, ROLE_VERIFIED, 0x10B981)
    if verified_role and verified_role.position < bot_top and verified_role not in member.roles:
        to_add.append(verified_role)

    added_names: list[str] = []
    if to_add:
        mark_bot_role_op(guild.id, member.id, seconds=18.0)
        await member.add_roles(*to_add, reason=f"MC-KS-001 Başvuru Onayı ({track})")
        added_names = [r.name for r in to_add]

    refreshed = await resolve_member(guild, member.id) or member
    prev = sticky_roles_storage.get(f"{guild.id}_{member.id}", {})
    save_sticky_snapshot(
        refreshed,
        active=True,
        locked=bool(prev.get("locked", False)) if isinstance(prev, dict) else False,
        update_locked=True,
    )
    if meta.get("staff_roster"):
        register_staff_member(refreshed)
    return added_names


def _build_review_embed(rec: dict, *, decided_by: int | None = None, decision_note: str = "") -> discord.Embed:
    meta = APP_TRACKS[rec["track"]]
    status = rec.get("status", "PENDING")
    if status == "APPROVED":
        title = f"✅ BAŞVURU ONAYLANDI · APPROVED ({rec['id']})"
        col = 0x10B981
    elif status == "REJECTED":
        title = f"❌ BAŞVURU REDDEDİLDİ · REJECTED ({rec['id']})"
        col = 0xEF4444
    else:
        title = f"{meta['emoji']} YENİ BAŞVURU · APPLICATION ({rec['id']})"
        col = meta["colour"]

    answers = rec.get("answers", {})
    embed = discord.Embed(
        title=title,
        description=(
            f"**Başvuru Türü / Track:** {meta['emoji']} {meta['title_tr']} · *{meta['title_en']}*\n"
            f"**Başvuran / Applicant:** <@{rec['user_id']}> (`{rec.get('user_tag', rec['user_id'])}`)\n"
            f"**Ad & Ülke / Identity:** `{answers.get('identity', '-')}`"
        ),
        colour=col,
    )
    embed.add_field(name="📚 Deneyim / Experience", value=(answers.get("experience") or "-")[:1000], inline=False)
    embed.add_field(name="💬 Gerekçe / Motivation", value=(answers.get("motivation") or "-")[:1000], inline=False)
    embed.add_field(name="🕒 Aktiflik & Diller", value=(answers.get("availability") or "-")[:300], inline=False)
    if decided_by:
        extra = f"<@{decided_by}>"
        if decision_note:
            extra += f"\n**Not:** {decision_note[:400]}"
        embed.add_field(name="⚖️ Karar Veren / Reviewed by", value=extra, inline=False)
    embed.set_footer(text=f"Kayıt No: {rec['id']} · League of Nations Applications")
    return embed


class StaffApplicationModal(discord.ui.Modal):
    """Modal collecting applicant identity, experience, motivation, and availability."""

    app_identity = discord.ui.TextInput(
        label="Adınız & Ülkeniz / Name & Country",
        placeholder="Örn: Ahmet / Batı Kafkasya (veya Sivil/Vatandaş)",
        required=True,
        max_length=80,
    )
    app_experience = discord.ui.TextInput(
        label="Deneyiminiz / Relevant Experience",
        style=discord.TextStyle.paragraph,
        placeholder="Önceki RP, basın veya yetkili tecrübelerinizi kısaca yazın...",
        required=True,
        max_length=600,
    )
    app_motivation = discord.ui.TextInput(
        label="Neden Başvuruyorsunuz? / Why Apply?",
        style=discord.TextStyle.paragraph,
        placeholder="Bu görevde cemiyete neler katmak istiyorsunuz?",
        required=True,
        max_length=600,
    )
    app_availability = discord.ui.TextInput(
        label="Aktiflik & Diller / Activity & Lang",
        placeholder="Örn: Günde 3-4 saat aktif · Türkçe, İngilizce",
        required=True,
        max_length=150,
    )

    def __init__(self, track: str):
        meta = APP_TRACKS[track]
        super().__init__(title=meta["modal_title"][:45])
        self.track = track
        self.app_experience.placeholder = meta["exp_placeholder"][:100]

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        answers = {
            "identity": self.app_identity.value.strip(),
            "experience": self.app_experience.value.strip(),
            "motivation": self.app_motivation.value.strip(),
            "availability": self.app_availability.value.strip(),
        }
        ok, err, rec = await submit_application_record(
            interaction.guild_id,
            interaction.user.id,
            str(interaction.user),
            self.track,
            answers,
        )
        if not ok or not rec:
            return await interaction.followup.send(err or "❌ Başvuru kaydedilemedi.", ephemeral=True)

        review_ch = find_approval_channel(interaction.guild, interaction.channel)
        review_embed = _build_review_embed(rec)
        review_view = ApplicationReviewView(rec["id"])
        sent = await review_ch.send(embed=review_embed, view=review_view)
        rec["review_channel_id"] = review_ch.id
        rec["review_message_id"] = sent.id
        applications_storage.set(f"app_{rec['id']}", rec)

        meta = APP_TRACKS[self.track]
        await interaction.followup.send(
            f"✅ **{meta['title_tr']}** başvurunuz alındı (`{rec['id']}`).\n"
            f"Yetkili incelemesi için {review_ch.mention} odasına iletildi; sonuçlandığında DM ile bilgilendirileceksiniz.",
            ephemeral=True,
        )


class ApplicationRejectModal(discord.ui.Modal, title="Başvuru Reddi · Reject Application"):
    reason = discord.ui.TextInput(
        label="Red Gerekçesi / Rejection Reason",
        style=discord.TextStyle.paragraph,
        placeholder="Kısa bir gerekçe yazın (opsiyonel / optional)...",
        required=False,
        max_length=400,
    )

    def __init__(self, app_id: str, source_message: discord.Message | None):
        super().__init__()
        self.app_id = app_id
        self.source_message = source_message

    async def on_submit(self, interaction: discord.Interaction):
        rec = applications_storage.get(f"app_{self.app_id}")
        if not isinstance(rec, dict):
            return await interaction.response.send_message("❌ Başvuru kaydı bulunamadı.", ephemeral=True)
        if rec.get("status") != "PENDING":
            return await interaction.response.send_message(f"⚠️ Bu başvuru zaten sonuçlandırıldı (`{rec.get('status')}`).", ephemeral=True)
        if not can_review_application(interaction.user, rec["track"]):
            return await interaction.response.send_message("❌ Bu başvuruyu sonuçlandırma yetkiniz yok.", ephemeral=True)

        note = (self.reason.value or "").strip()
        rec["status"] = "REJECTED"
        rec["decided_by"] = str(interaction.user.id)
        rec["decided_at"] = datetime.now(timezone.utc).isoformat()
        rec["decision_note"] = note
        applications_storage.set(f"app_{self.app_id}", rec)

        disabled_view = ApplicationReviewView(self.app_id, disabled=True)
        new_embed = _build_review_embed(rec, decided_by=interaction.user.id, decision_note=note)
        if self.source_message:
            try:
                await self.source_message.edit(embed=new_embed, view=disabled_view)
            except Exception:
                pass

        applicant = await resolve_member(interaction.guild, int(rec["user_id"]))
        if applicant:
            meta = APP_TRACKS[rec["track"]]
            msg = f"❌ **{meta['title_tr']}** başvurunuz (`{self.app_id}`) yönetim tarafından reddedildi."
            if note:
                msg += f"\n**Gerekçe:** {note}"
            try:
                await applicant.send(msg)
            except Exception:
                pass

        await interaction.response.send_message(f"❌ Başvuru `{self.app_id}` reddedildi.", ephemeral=True)


class ApplicationReviewView(discord.ui.View):
    """Persistent approval/rejection controls for a submitted press/mod/admin application."""

    def __init__(self, app_id: str, *, disabled: bool = False):
        super().__init__(timeout=None)
        self.app_id = app_id

        approve_btn = discord.ui.Button(
            label="Onayla · Approve",
            emoji="✅",
            style=discord.ButtonStyle.success,
            custom_id=f"app_rev_approve_{app_id}"[:100],
            disabled=disabled,
        )
        approve_btn.callback = self._on_approve

        reject_btn = discord.ui.Button(
            label="Reddet · Reject",
            emoji="❌",
            style=discord.ButtonStyle.danger,
            custom_id=f"app_rev_reject_{app_id}"[:100],
            disabled=disabled,
        )
        reject_btn.callback = self._on_reject

        self.add_item(approve_btn)
        self.add_item(reject_btn)

    async def _on_approve(self, interaction: discord.Interaction):
        rec = applications_storage.get(f"app_{self.app_id}")
        if not isinstance(rec, dict):
            return await interaction.response.send_message("❌ Başvuru kaydı bulunamadı.", ephemeral=True)
        if rec.get("status") != "PENDING":
            return await interaction.response.send_message(f"⚠️ Bu başvuru zaten `{rec.get('status')}` olarak sonuçlandı.", ephemeral=True)
        if not can_review_application(interaction.user, rec["track"]):
            return await interaction.response.send_message("❌ Bu kategorideki başvuruyu onaylama yetkiniz yok.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        member = await resolve_member(interaction.guild, int(rec["user_id"]))
        if not member:
            return await interaction.followup.send("❌ Başvuran üye sunucuda bulunamadı.", ephemeral=True)

        added = await _grant_application_roles(interaction.guild, member, rec["track"])
        rec["status"] = "APPROVED"
        rec["decided_by"] = str(interaction.user.id)
        rec["decided_at"] = datetime.now(timezone.utc).isoformat()
        applications_storage.set(f"app_{self.app_id}", rec)

        disabled_view = ApplicationReviewView(self.app_id, disabled=True)
        new_embed = _build_review_embed(rec, decided_by=interaction.user.id)
        try:
            await interaction.message.edit(embed=new_embed, view=disabled_view)
        except Exception:
            pass

        meta = APP_TRACKS[rec["track"]]
        dm_lines = [
            f"✅ **Tebrikler! {meta['title_tr']}** başvurunuz (`{self.app_id}`) onaylandı!",
            f"*Congratulations! Your {meta['title_en']} application has been approved.*",
        ]
        if rec["track"] == "press":
            press_ch = discord.utils.find(lambda c: "world-press" in c.name.lower(), interaction.guild.text_channels)
            if press_ch:
                dm_lines.append(f"📰 Artık {press_ch.mention} kanalından haber yayınlayabilirsiniz.")
        try:
            await member.send("\n".join(dm_lines))
        except Exception:
            pass

        role_info = ", ".join(f"`{r}`" for r in added) if added else "*Mevcut roller korundu*"
        await interaction.followup.send(
            f"✅ {member.mention} başvurusu (`{self.app_id}`) onaylandı. Verilen roller: {role_info}",
            ephemeral=True,
        )

    async def _on_reject(self, interaction: discord.Interaction):
        rec = applications_storage.get(f"app_{self.app_id}")
        if not isinstance(rec, dict):
            return await interaction.response.send_message("❌ Başvuru kaydı bulunamadı.", ephemeral=True)
        if rec.get("status") != "PENDING":
            return await interaction.response.send_message(f"⚠️ Bu başvuru zaten `{rec.get('status')}` olarak sonuçlandı.", ephemeral=True)
        if not can_review_application(interaction.user, rec["track"]):
            return await interaction.response.send_message("❌ Bu kategorideki başvuruyu reddetme yetkiniz yok.", ephemeral=True)
        await interaction.response.send_modal(ApplicationRejectModal(self.app_id, interaction.message))


class ApplicationPanelView(discord.ui.View):
    """Persistent public application buttons displayed in #📝│basvuru-odasi-applications."""

    def __init__(self):
        super().__init__(timeout=None)
        for key, style in (
            ("press", discord.ButtonStyle.primary),
            ("moderator", discord.ButtonStyle.success),
            ("admin", discord.ButtonStyle.danger),
        ):
            meta = APP_TRACKS[key]
            btn = discord.ui.Button(
                label=meta["button_label"],
                emoji=meta["emoji"],
                style=style,
                custom_id=f"app_apply:{key}",
            )
            btn.callback = self._make_apply_callback(key)
            self.add_item(btn)

    def _make_apply_callback(self, track: str):
        async def _callback(interaction: discord.Interaction):
            if not interaction.guild or not isinstance(interaction.user, discord.Member):
                return await interaction.response.send_message("❌ Bu panel yalnızca sunucu içinde kullanılabilir.", ephemeral=True)
            meta = APP_TRACKS[track]
            if not is_track_open(interaction.guild_id, track):
                return await interaction.response.send_message(
                    f"🔴 **{meta['title_tr']}** alımı şu anda yöneticiler tarafından kapatılmıştır.\n"
                    f"*{meta['title_en']} applications are currently closed.*",
                    ephemeral=True,
                )
            if _member_already_has_track(interaction.user, track):
                return await interaction.response.send_message(
                    f"✅ Zaten **{meta['title_tr']}** rolüne sahipsiniz / You already hold this role.",
                    ephemeral=True,
                )
            pending = _find_pending_application(interaction.guild_id, interaction.user.id, track)
            if pending:
                return await interaction.response.send_message(
                    f"⏳ Bu kadro için incelenmekte olan başvurunuz zaten mevcut (`{pending.get('id')}`).",
                    ephemeral=True,
                )
            await interaction.response.send_modal(StaffApplicationModal(track))

        return _callback


class RecruitmentControlView(discord.ui.View):
    """Persistent admin toggle buttons to open/close Press, Moderator, and Admin applications."""

    def __init__(self):
        super().__init__(timeout=None)

    async def _toggle(self, interaction: discord.Interaction, track: str):
        if not can_manage_recruitment(interaction.user):
            return await interaction.response.send_message(
                "❌ Alımları açıp kapatmak için **Admin / Cemiyet Yönetimi / Genel Sekreter** yetkisi gerekir.",
                ephemeral=True,
            )
        await interaction.response.defer(ephemeral=True)
        new_state = not is_track_open(interaction.guild_id, track)
        set_track_open(interaction.guild_id, track, new_state, actor_id=interaction.user.id)
        await refresh_application_panels(interaction.guild)
        meta = APP_TRACKS[track]
        badge = "🟢 AÇILDI / OPENED" if new_state else "🔴 KAPATILDI / CLOSED"
        await interaction.followup.send(
            f"{meta['emoji']} **{meta['title_tr']}** başvuruları {badge} ve `#📝│basvuru-odasi-applications` güncellendi.",
            ephemeral=True,
        )

    @discord.ui.button(label="Gazeteci Alımı: Aç/Kapat", emoji="📰", style=discord.ButtonStyle.primary, custom_id="app_toggle:press")
    async def btn_toggle_press(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._toggle(interaction, "press")

    @discord.ui.button(label="Moderatör Alımı: Aç/Kapat", emoji="🛡️", style=discord.ButtonStyle.secondary, custom_id="app_toggle:moderator")
    async def btn_toggle_mod(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._toggle(interaction, "moderator")

    @discord.ui.button(label="Admin Alımı: Aç/Kapat", emoji="🏛️", style=discord.ButtonStyle.secondary, custom_id="app_toggle:admin")
    async def btn_toggle_admin(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._toggle(interaction, "admin")

    @discord.ui.button(label="Paneli Yenile · Refresh", emoji="🔄", style=discord.ButtonStyle.success, custom_id="app_toggle:refresh")
    async def btn_refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not can_manage_recruitment(interaction.user):
            return await interaction.response.send_message("❌ Yetkiniz yok.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        res = await refresh_application_panels(interaction.guild)
        await interaction.followup.send(f"🔄 Güncellendi: {', '.join(res) or 'kanal bulunamadı'}", ephemeral=True)


class ApplicationsCog(commands.Cog):
    """Cog for Press, Moderator, and Admin applications and recruitment open/close controls."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(ApplicationPanelView())
        self.bot.add_view(RecruitmentControlView())
        for key, rec in applications_storage.read().items():
            if isinstance(key, str) and key.startswith("app_") and isinstance(rec, dict) and rec.get("status") == "PENDING":
                self.bot.add_view(ApplicationReviewView(rec.get("id") or key.removeprefix("app_")))

    @app_commands.command(name="basvuru-alimi", description="Gazeteci, Moderatör ve Admin başvurularını açar veya kapatır (Yönetici)")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(kategori="Açılıp kapatılacak başvuru türü", durum="Yeni alım durumu")
    @app_commands.choices(
        kategori=[
            app_commands.Choice(name="📰 Gazeteci / Akredite Basın", value="press"),
            app_commands.Choice(name="🛡️ Yetkili / Moderatör", value="moderator"),
            app_commands.Choice(name="🏛️ Admin / Cemiyet Yönetimi", value="admin"),
            app_commands.Choice(name="📦 Tümü (Hepsi)", value="all"),
        ],
        durum=[
            app_commands.Choice(name="🟢 Aç / Open", value="open"),
            app_commands.Choice(name="🔴 Kapat / Close", value="close"),
        ],
    )
    async def cmd_toggle_recruitment(
        self,
        interaction: discord.Interaction,
        kategori: app_commands.Choice[str],
        durum: app_commands.Choice[str],
    ):
        if not can_manage_recruitment(interaction.user):
            return await interaction.response.send_message("❌ Bu işlem için Yönetim/Genel Sekreter yetkisi gerekir.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        target_open = durum.value == "open"
        tracks = ["press", "moderator", "admin"] if kategori.value == "all" else [kategori.value]
        for t in tracks:
            set_track_open(interaction.guild_id, t, target_open, actor_id=interaction.user.id)
        synced = await refresh_application_panels(interaction.guild)
        badge = "🟢 AÇILDI / OPENED" if target_open else "🔴 KAPATILDI / CLOSED"
        names = ", ".join(APP_TRACKS[t]["title_tr"] for t in tracks)
        await interaction.followup.send(
            f"✅ **{names}** başvurusu {badge}.\nSenkronize edilen paneller: {', '.join(synced) or '—'}",
            ephemeral=True,
        )

    @app_commands.command(name="basvuru-paneli-kur", description="Başvuru Odası ve Alım Yönetim panellerini kurar/yeniler (Yönetici)")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(kanal="İsteğe bağlı başvuru odası kanalı")
    async def cmd_setup_app_panels(self, interaction: discord.Interaction, kanal: discord.TextChannel = None):
        await interaction.response.defer(ephemeral=True)
        target_app = resolve_panel_channel(interaction.guild, kanal, "basvuru-odasi-applications", interaction.channel)
        emb, view = build_application_panel(interaction.guild_id)
        st_app = await upsert_panel_message(target_app, [PANEL_TITLE_PREFIX], emb, view)
        ctrl_ch = resolve_panel_channel(interaction.guild, None, "yayin-paneli-broadcast", None)
        st_ctrl = "—"
        if isinstance(ctrl_ch, discord.TextChannel):
            c_emb, c_view = build_recruitment_control_panel(interaction.guild_id)
            st_ctrl = await upsert_panel_message(ctrl_ch, [CONTROL_TITLE_PREFIX], c_emb, c_view)
        await interaction.followup.send(
            f"✅ Başvuru paneli {target_app.mention} (`{st_app}`) ve alım kontrol paneli ({st_ctrl}) güncellendi.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(ApplicationsCog(bot))
