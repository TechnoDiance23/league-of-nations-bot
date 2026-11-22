"""Profanity filter with auto-mute escalation, audit logging, and ICJ court review."""

import asyncio
from collections import Counter
from datetime import datetime, timedelta, timezone
import io
import logging
from typing import Union
import discord
from discord import app_commands
from discord.ext import commands

from utils.constants import (
    CHANNEL_ALIASES,
    LEADERSHIP_ROLES,
    ROLE_ADMIN,
    ROLE_ICJ_PRESIDENT,
    ROLE_JUDGE,
    ROLE_MODERATOR,
    ROLE_SG,
    resolve_panel_channel,
    slug_match,
)
from utils.profanity import detect_profanity, mask_profane_text
from utils.storage import delegates_storage, moderation_storage, tickets_storage
from utils.un_gazette import move_court_verdict_to_archive, upsert_panel_message

CHAT_RESET_PANEL_TITLE = "🧹 SOHBET SIFIRLAMA & ARŞİV MERKEZİ · CHAT RESET DESK"

logger = logging.getLogger("InternationalDiplomaticBot.Moderation")

AUDIT_ROLE_FALLBACKS = {"🏛️ MC Genel Sekreteri", "🏛️ Cemiyet Yönetimi & Divan", "⚖️ Uluslararası Yargı Divanı Yargıcı", ROLE_MODERATOR}


def compute_profanity_timeout_minutes(strike_count: int) -> int:
    """Return 30 min base timeout, doubling at 3 repeats (and each 3-strike tier)."""
    n = max(1, int(strike_count))
    minutes = 30 * (2 ** (n // 3))
    return min(minutes, 40320)


def _is_court_authority(member: discord.Member) -> bool:
    if not isinstance(member, discord.Member):
        return False
    if member.id == member.guild.owner_id or member.guild_permissions.administrator or member.guild_permissions.moderate_members:
        return True
    role_names = {r.name for r in member.roles}
    if role_names & ({ROLE_SG, ROLE_ADMIN, ROLE_ICJ_PRESIDENT, ROLE_JUDGE} | AUDIT_ROLE_FALLBACKS):
        return True
    rec = delegates_storage.get(str(member.id), {})
    return rec.get("office_code") in {"SG", "DSG", "GKB", "GKB-V", "DB", "UYD"}


def _next_case_id() -> str:
    data = moderation_storage.read()
    seq = int(data.get("seq", 0) or 0) + 1
    cases = data.get("cases", {})
    while f"MC-CEZA-{seq:04d}" in cases:
        seq += 1
    data["seq"] = seq
    moderation_storage.write(data)
    return f"MC-CEZA-{seq:04d}"


def _get_case(case_id: str) -> dict | None:
    cases = moderation_storage.get("cases", {})
    if isinstance(cases, dict):
        return cases.get(case_id)
    return None


def _save_case(case_id: str, case_data: dict) -> None:
    cases = moderation_storage.get("cases", {})
    if not isinstance(cases, dict):
        cases = {}
    cases[case_id] = case_data
    moderation_storage.set("cases", cases)


def _get_strikes(user_id: int | str) -> int:
    strikes = moderation_storage.get("strikes", {})
    if not isinstance(strikes, dict):
        return 0
    return int(strikes.get(str(user_id), 0) or 0)


def _set_strikes(user_id: int | str, count: int) -> int:
    strikes = moderation_storage.get("strikes", {})
    if not isinstance(strikes, dict):
        strikes = {}
    val = max(0, int(count))
    strikes[str(user_id)] = val
    moderation_storage.set("strikes", strikes)
    return val


def _find_audit_channel(guild: discord.Guild) -> discord.TextChannel | None:
    ch = resolve_panel_channel(guild, None, "denetim-kaydi-audit-log", None)
    if ch is not None:
        return ch
    cfg = tickets_storage.get(f"log_config_{guild.id}", {})
    if cfg.get("channel_id"):
        ch = guild.get_channel(int(cfg["channel_id"]))
        if isinstance(ch, discord.TextChannel):
            return ch
    return resolve_panel_channel(guild, None, "onay-odasi-approvals", None)


def _find_court_channel(guild: discord.Guild) -> discord.TextChannel | None:
    return resolve_panel_channel(guild, None, "yargi-divani-icj-court", None)


async def _resolve_guild_member(guild: discord.Guild | None, user_id: int) -> discord.Member | None:
    if not guild:
        return None
    m = guild.get_member(user_id)
    if m:
        return m
    try:
        return await guild.fetch_member(user_id)
    except Exception:
        return None


async def _apply_timeout(
    guild: discord.Guild,
    member: discord.Member | None,
    minutes: int | None,
    reason: str,
) -> tuple[bool, str]:
    """Apply or clear Discord communication timeout with admin/owner safety handling."""
    if not member:
        return False, "Üye sunucuda bulunamadı / Member not in guild"
    if member.id == guild.owner_id:
        return False, "Sunucu sahibi Discord API gereği timeout edilemez (kayda işlendi)"
    if member.guild_permissions.administrator:
        return False, "Yönetici yetkisi Discord API gereği timeout edilemez (kayda işlendi)"
    bot_top = guild.me.top_role.position if guild.me else 0
    if member.top_role.position >= bot_top:
        return False, "Üye rolü bot rolünden yüksek/eşit (kayda işlendi)"
    try:
        if minutes is None:
            await member.timeout(None, reason=reason[:512])
            return True, "Timeout kaldırıldı / Mute removed"
        await member.timeout(timedelta(minutes=minutes), reason=reason[:512])
        return True, f"{minutes} dk timeout uygulandı / muted"
    except discord.Forbidden:
        return False, "Yetki sınırı nedeniyle timeout uygulanamadı"
    except discord.HTTPException as exc:
        return False, f"Timeout API hatası: {exc}"


def build_court_case_embed(case: dict) -> discord.Embed:
    status = case.get("status", "⏳ Yargılama Bekliyor / Pending Review")
    color = 0xD97706
    if "DÜŞÜRÜLDÜ" in status or "AFFEDİLDİ" in status:
        color = 0x10B981
    elif "İNDİRİLDİ" in status:
        color = 0x0EA5E9
    elif "ONAYLANDI" in status or "ARTIRILDI" in status:
        color = 0xEF4444

    desc_lines = [
        f"**Sanık / Defendant:** <@{case['user_id']}> (`{case.get('user_tag', '-')}`)",
        f"**Devlet / Nation:** `{case.get('nation', 'Kayıtsız / Unregistered')}`",
        f"**Olay Kanalı / Channel:** <#{case['channel_id']}>",
        f"**Tespit / Matched:** `{case.get('masked_match', '***')}` *(İhlal Tekrarı / Strike: `{case.get('strikes', 1)}`)*",
        f"**Uygulanan Mute / Duration:** `{case.get('duration_min', 30)} dakika / min`",
        f"**Discord Timeout:** `{case.get('timeout_note', '-')}`",
        f"**Sansürlü Özet / Redacted:** ||{case.get('masked_excerpt', '-')[:250]}||",
        f"**Durum / Status:** **{status}**",
    ]
    if case.get("verdict_reason"):
        desc_lines.extend([
            "",
            f"**Yargı Kararı / Ruling:** <@{case.get('judge_id', '0')}> (`{case.get('judge_name', '-')}`)",
            f"> {case['verdict_reason']}",
        ])

    embed = discord.Embed(
        title=f"⚖️ DİSİPLİN & KÜFÜR YARGILAMASI · {case['id']}",
        description="\n".join(desc_lines),
        colour=color,
    )
    embed.set_footer(text=f"Küfür Filtresi · Yargı İnceleme Masası · {case.get('created_at', '-')}")
    return embed


class ModVerdictModal(discord.ui.Modal, title="Yargı Kararı · Moderation Verdict"):
    reason = discord.ui.TextInput(
        label="Karar Gerekçesi / Decision Grounds",
        style=discord.TextStyle.paragraph,
        placeholder="Ceza indirimi, kaldırma, onama veya artırım gerekçesini yazın...",
        required=True,
        max_length=500,
    )

    def __init__(self, case_id: str, action_mode: str):
        super().__init__()
        self.case_id = case_id
        self.action_mode = action_mode

    async def on_submit(self, interaction: discord.Interaction):
        if not _is_court_authority(interaction.user):
            return await interaction.response.send_message(
                "❌ Yalnızca Divan Yargıçları, Genel Sekreter veya Yöneticiler karar verebilir.",
                ephemeral=True,
            )
        case = _get_case(self.case_id)
        if not case:
            return await interaction.response.send_message("❌ Vaka kaydı bulunamadı.", ephemeral=True)
        if case.get("verdict_locked"):
            return await interaction.response.send_message("⚠️ Bu vaka için yargı kararı zaten verilmiş.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        target_uid = int(case["user_id"])
        member = await _resolve_guild_member(interaction.guild, target_uid)
        reason_text = self.reason.value.strip()
        audit_reason = f"Yargı Kararı {self.case_id} ({interaction.user.display_name}): {reason_text}"

        cur_strikes = _get_strikes(target_uid)
        if self.action_mode == "drop":
            ok, note = await _apply_timeout(interaction.guild, member, None, audit_reason)
            new_strikes = _set_strikes(target_uid, max(0, cur_strikes - 1))
            case["status"] = f"🕊️ CEZA DÜŞÜRÜLDÜ / DROPPED (Yeni Sicil: {new_strikes})"
            case["timeout_note"] = note
        elif self.action_mode == "reduce":
            new_min = max(1, int(case.get("duration_min", 30)) // 2)
            ok, note = await _apply_timeout(interaction.guild, member, new_min, audit_reason)
            case["duration_min"] = new_min
            case["status"] = f"⏳ SÜRE İNDİRİLDİ ({new_min} dk) / REDUCED"
            case["timeout_note"] = note
        elif self.action_mode == "escalate":
            new_min = min(int(case.get("duration_min", 30)) * 2, 40320)
            ok, note = await _apply_timeout(interaction.guild, member, new_min, audit_reason)
            case["duration_min"] = new_min
            case["status"] = f"🔨 CEZA 2x ARTIRILDI ({new_min} dk) / DOUBLED"
            case["timeout_note"] = note
        else:
            case["status"] = "⚖️ CEZA ONAYLANDI (SABİT) / UPHELD"

        case["verdict_locked"] = True
        case["verdict_mode"] = self.action_mode
        case["verdict_reason"] = reason_text
        case["judge_id"] = interaction.user.id
        case["judge_name"] = interaction.user.display_name
        case["decided_at"] = datetime.utcnow().strftime("%d.%m.%Y %H:%M UTC")

        arch_msg = await move_court_verdict_to_archive(
            interaction.guild,
            build_court_case_embed(case),
            case,
        )
        _save_case(self.case_id, case)

        if not arch_msg:
            # Fallback when archive channel is unavailable: disable buttons in place
            disabled_view = ModCaseActionView(self.case_id)
            for item in disabled_view.children:
                item.disabled = True
            if case.get("court_channel_id") and case.get("court_message_id"):
                cch = interaction.guild.get_channel(int(case["court_channel_id"]))
                if isinstance(cch, discord.TextChannel):
                    try:
                        cmsg = await cch.fetch_message(int(case["court_message_id"]))
                        await cmsg.edit(embed=build_court_case_embed(case), view=disabled_view)
                    except Exception:
                        pass

        # Log ruling to Audit Log channel
        audit_ch = _find_audit_channel(interaction.guild)
        if audit_ch:
            try:
                await audit_ch.send(
                    embed=discord.Embed(
                        title=f"🛡️ DENETİM KAYDI · YARGI KARARI ({self.case_id})",
                        description=(
                            f"**Sanık:** <@{target_uid}>\n"
                            f"**Karar:** **{case['status']}**\n"
                            f"**Hakim / Yetkili:** <@{interaction.user.id}>\n"
                            f"**Gerekçe:** {reason_text}"
                        ),
                        colour=0x10B981 if self.action_mode in ("drop", "reduce") else 0xEF4444,
                    )
                )
            except Exception:
                pass

        # Notify affected member via DM
        if member:
            try:
                await member.send(
                    embed=discord.Embed(
                        title=f"⚖️ Yargı Kararı Bildirimi · {self.case_id}",
                        description=(
                            f"**Sonuç / Ruling:** **{case['status']}**\n"
                            f"**Gerekçe / Grounds:** {reason_text}\n"
                            f"**Güncel İhlal Siciliniz / Strikes:** `{_get_strikes(target_uid)}`"
                        ),
                        colour=0x10B981 if self.action_mode in ("drop", "reduce") else 0xD97706,
                    )
                )
            except Exception:
                pass

        arch_note = f" → Arşiv: {arch_msg.jump_url}" if arch_msg else ""
        await interaction.followup.send(f"✅ `{self.case_id}` kararı işlendi: **{case['status']}**{arch_note}", ephemeral=True)


class ModCaseActionView(discord.ui.View):
    """Persistent court judgment buttons on profanity / moderation referrals."""

    def __init__(self, case_id: str):
        super().__init__(timeout=None)
        self.case_id = case_id

        specs = [
            ("Cezayı Düşür / Affet · Drop", discord.ButtonStyle.success, "🕊️", f"mc_mod_drop:{case_id}", self._on_drop),
            ("Süreyi Yarıya İndir · Halve", discord.ButtonStyle.primary, "⏳", f"mc_mod_half:{case_id}", self._on_half),
            ("Cezayı Onayla · Uphold", discord.ButtonStyle.secondary, "⚖️", f"mc_mod_uphold:{case_id}", self._on_uphold),
            ("2 Katına Çıkar · Double", discord.ButtonStyle.danger, "🔨", f"mc_mod_double:{case_id}", self._on_double),
        ]
        for label, style, emoji, cid, cb in specs:
            btn = discord.ui.Button(label=label, style=style, emoji=emoji, custom_id=cid)
            btn.callback = cb
            self.add_item(btn)

    async def _open_verdict(self, interaction: discord.Interaction, mode: str):
        if not _is_court_authority(interaction.user):
            return await interaction.response.send_message(
                "❌ Yalnızca Divan Yargıçları (`UYD`), Genel Sekreter veya Yöneticiler karar verebilir.",
                ephemeral=True,
            )
        case = _get_case(self.case_id)
        if not case:
            return await interaction.response.send_message("❌ Vaka bulunamadı.", ephemeral=True)
        if case.get("verdict_locked"):
            return await interaction.response.send_message("⚠️ Bu vaka zaten karara bağlanmış.", ephemeral=True)
        await interaction.response.send_modal(ModVerdictModal(self.case_id, mode))

    async def _on_drop(self, interaction: discord.Interaction):
        await self._open_verdict(interaction, "drop")

    async def _on_half(self, interaction: discord.Interaction):
        await self._open_verdict(interaction, "reduce")

    async def _on_uphold(self, interaction: discord.Interaction):
        await self._open_verdict(interaction, "uphold")

    async def _on_double(self, interaction: discord.Interaction):
        await self._open_verdict(interaction, "escalate")


PROTECTED_PURGE_CHANNEL_KEYS = (
    "tuzuk-charter",
    "imzaci-devletler-signatories",
    "sohbet-sifirlama-kayit",
    "denetim-kaydi-audit-log",
    "ticket-log-archive",
    "resmi-gazete-official-gazette",
    "kararlar-resolutions",
    "yargi-arsivi-icj-archive",
)


def _is_chat_reset_authority(member) -> bool:
    guild = getattr(member, "guild", None)
    if guild is None or not hasattr(member, "roles"):
        return False
    perms = getattr(member, "guild_permissions", None)
    if getattr(member, "id", None) == guild.owner_id or bool(
        getattr(perms, "administrator", False) or getattr(perms, "manage_messages", False)
    ):
        return True
    if {r.name for r in member.roles} & ({ROLE_SG, ROLE_ADMIN, ROLE_MODERATOR} | AUDIT_ROLE_FALLBACKS | {"Admin"}):
        return True
    rec = delegates_storage.get(str(member.id), {})
    return isinstance(rec, dict) and rec.get("office_code") in {"SG", "DSG", "GKB", "GKB-V"}


def _is_protected_purge_channel(guild: discord.Guild, channel) -> bool:
    if channel is None:
        return True
    cfg_cid = moderation_storage.get(f"chat_reset_log_{guild.id}")
    if cfg_cid and int(cfg_cid) == getattr(channel, "id", None):
        return True
    ch_name = getattr(channel, "name", "") or ""
    return any(slug_match(ch_name, CHANNEL_ALIASES.get(k, [])) for k in PROTECTED_PURGE_CHANNEL_KEYS)


def _next_chat_reset_id() -> str:
    data = moderation_storage.read()
    seq = int(data.get("chat_reset_seq", 0) or 0) + 1
    resets = data.get("chat_resets", {})
    if not isinstance(resets, dict):
        resets = {}
    while f"MC-TEMIZ-{seq:04d}" in resets:
        seq += 1
    data["chat_reset_seq"] = seq
    moderation_storage.write(data)
    return f"MC-TEMIZ-{seq:04d}"


def _resolve_existing_chat_reset_log_channel(guild: discord.Guild | None) -> discord.TextChannel | None:
    """Resolve existing chat-reset archive channel synchronously without network calls."""
    if not guild:
        return None
    cfg_cid = moderation_storage.get(f"chat_reset_log_{guild.id}")
    if cfg_cid:
        existing_cfg = guild.get_channel(int(cfg_cid))
        if existing_cfg is not None:
            return existing_cfg
    return resolve_panel_channel(guild, None, "sohbet-sifirlama-kayit", None)


async def ensure_chat_reset_log_channel(guild: discord.Guild) -> discord.TextChannel | None:
    """Resolve or auto-create the private #🧹│sohbet-sifirlama-kayit archive room under Staff & Admin."""
    existing = _resolve_existing_chat_reset_log_channel(guild)
    if existing is not None:
        moderation_storage.set(f"chat_reset_log_{guild.id}", existing.id)
        return existing

    staff_cat = discord.utils.find(
        lambda c: any(tok in c.name.lower() for tok in ("sekreterlik", "staff & admin", "yönetim")),
        getattr(guild, "categories", ()),
    )
    everyone = getattr(guild, "default_role", None) or discord.utils.get(guild.roles, name="@everyone")
    overwrites: dict = {}
    if everyone:
        overwrites[everyone] = discord.PermissionOverwrite(view_channel=False, send_messages=False)
    if getattr(guild, "me", None):
        overwrites[guild.me] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            attach_files=True,
            embed_links=True,
            manage_messages=True,
        )
    for rname in list(dict.fromkeys(list(LEADERSHIP_ROLES) + [ROLE_MODERATOR, "Admin", "🏛️ MC Genel Sekreteri", "🏛️ Cemiyet Yönetimi & Divan"])):
        role = discord.utils.get(guild.roles, name=rname)
        if role:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
            )

    try:
        created = await guild.create_text_channel(
            "🧹│sohbet-sifirlama-kayit",
            category=staff_cat,
            topic="🔒 Gizli · Sohbet Sıfırlama Kayıtları & Mesaj Arşivi / Chat Reset Transcripts",
            overwrites=overwrites,
            reason="MC Sohbet Sıfırlama Kayıt Kanalı Otomatik Kurulumu",
        )
        moderation_storage.set(f"chat_reset_log_{guild.id}", created.id)
        return created
    except Exception:
        logger.exception("Failed creating chat reset log channel in guild %s", guild.id)
        return _find_audit_channel(guild)


def _split_transcript_chunks(transcript_bytes: bytes, max_bytes: int = 7_500_000) -> list[bytes]:
    """Split large UTF-8 transcripts cleanly on line boundaries so Discord attachment limits are respected."""
    if len(transcript_bytes) <= max_bytes:
        return [transcript_bytes]
    chunks: list[bytes] = []
    cur: list[bytes] = []
    cur_size = 0
    for line in transcript_bytes.splitlines(keepends=True):
        if len(line) > max_bytes:
            # Force-split exceptionally long single line cleanly
            if cur:
                chunks.append(b"".join(cur))
                cur, cur_size = [], 0
            for pos in range(0, len(line), max_bytes):
                chunks.append(line[pos : pos + max_bytes])
            continue
        if cur_size + len(line) > max_bytes and cur:
            chunks.append(b"".join(cur))
            cur, cur_size = [], 0
        cur.append(line)
        cur_size += len(line)
    if cur:
        chunks.append(b"".join(cur))
    return chunks or [transcript_bytes]


def _build_transcript_preview(messages_chrono: list) -> str:
    """Return a compact preview showing first/last archived lines within Discord field limits."""
    if not messages_chrono:
        return "*İçerik yok.*"
    picks = messages_chrono if len(messages_chrono) <= 4 else [*messages_chrono[:2], None, *messages_chrono[-2:]]
    lines: list[str] = []
    for item in picks:
        if item is None:
            lines.append(f"⋯ *({len(messages_chrono) - 4} ara mesaj `.txt` arşivindedir)* ⋯")
            continue
        ts_obj = getattr(item, "created_at", None)
        ts = ts_obj.strftime("%d.%m %H:%M") if ts_obj else "--:--"
        dname = str(getattr(getattr(item, "author", None), "display_name", "Üye"))[:24]
        raw = " ".join((getattr(item, "content", "") or "").split())
        if not raw:
            if getattr(item, "attachments", ()):
                raw = "[Ek / Dosya]"
            elif getattr(item, "embeds", ()):
                raw = "[Embed]"
            else:
                raw = "[Sistem Mesajı]"
        if len(raw) > 110:
            raw = raw[:107] + "..."
        lines.append(f"`[{ts}]` **{dname}:** {raw}")
    text = "\n".join(lines)
    return text[:950]


def _check_bot_purge_permissions(guild: discord.Guild, target) -> str | None:
    """Return a Turkish error message if the bot lacks channel permissions required to archive and purge."""
    bot_member = getattr(guild, "me", None)
    if not bot_member or not hasattr(target, "permissions_for"):
        return None
    perms = target.permissions_for(bot_member)
    missing: list[str] = []
    if not getattr(perms, "view_channel", True):
        missing.append("Kanalı Görüntüle (`view_channel`)")
    if not getattr(perms, "read_message_history", True):
        missing.append("Mesaj Geçmişini Oku (`read_message_history`)")
    if not getattr(perms, "manage_messages", True):
        missing.append("Mesajları Yönet (`manage_messages`)")
    if not missing:
        return None
    return f"⛔ Botun {target.mention} odasında şu izinleri eksik: {', '.join(missing)}"


async def _reply_reset_outcome(interaction: discord.Interaction, message: str, *, edit_original: bool = False) -> None:
    if edit_original and hasattr(interaction, "edit_original_response"):
        try:
            await interaction.edit_original_response(content=message, view=None)
            return
        except Exception:
            pass
    try:
        if hasattr(interaction, "response") and not interaction.response.is_done():
            await interaction.response.send_message(message, ephemeral=True)
            return
    except Exception:
        pass
    await interaction.followup.send(message, ephemeral=True)


async def _delete_single_safe(msg) -> bool:
    for attempt in range(2):
        try:
            await msg.delete()
            return True
        except discord.NotFound:
            return True
        except discord.HTTPException as exc:
            if getattr(exc, "status", None) == 429 and attempt == 0:
                await asyncio.sleep(float(getattr(exc, "retry_after", 1.0) or 1.0) + 0.2)
                continue
            return False
        except Exception:
            return False
    return False


def build_chat_reset_panel(guild: discord.Guild | None = None) -> tuple[discord.Embed, discord.ui.View]:
    """Build pinned management & archive index embed for #🧹│sohbet-sifirlama-kayit."""
    resets = moderation_storage.get("chat_resets", {})
    rows: list[dict] = []
    if isinstance(resets, dict):
        gid = getattr(guild, "id", None)
        for rec in resets.values():
            if isinstance(rec, dict) and (gid is None or rec.get("guild_id") in (None, gid)):
                rows.append(rec)
    rows = list(reversed(rows[-8:]))

    hist_lines = [
        (
            f"• **`{r.get('id', '-')}`** — <#{r.get('channel_id', 0)}> · "
            f"`{r.get('deleted_count', 0)}` mesaj · <@{r.get('actor_id', 0)}> · `{r.get('created_at', '-')}`"
        )
        for r in rows
    ] or ["*Henüz arşivlenmiş sohbet sıfırlama kaydı yok · No archived resets yet.*"]

    embed = discord.Embed(
        title=CHAT_RESET_PANEL_TITLE,
        description=(
            "Bulunduğunuz **herhangi bir odada `/sohbet-sifirla`** çalıştırdığınızda, silinecek tüm sohbet geçmişi **önce bu odaya `.txt` arşivi olarak kaydedilir**, "
            "ardından ilgili oda temizlenir.\n"
            "*Sabitli mesajlar (`sabitlemeleri_koru=True`) ve korumalı arşiv/tüzük kanalları otomatik olarak korunur.*"
        ),
        colour=0xD97706,
    )
    embed.add_field(
        name="🧭 Nasıl Kullanılır?",
        value=(
            "• **Bulunduğunuz Odayı Sıfırlama:** Hedef odanın içinde `/sohbet-sifirla` yazın *(doğrudan onaysız silmek için `onay: True` seçebilirsiniz)*.\n"
            "• **Kısmi Temizlik:** `/sohbet-sifirla miktar:50` yazarak yalnızca son N mesajı arşivleyip silebilirsiniz.\n"
            "• **Bu Odadan Seçim:** Aşağıdaki menüden sıfırlanacak odayı doğrudan seçebilirsiniz."
        ),
        inline=False,
    )
    embed.add_field(
        name="📂 Son Sıfırlama & Arşiv Kayıtları",
        value="\n".join(hist_lines)[:1024],
        inline=False,
    )
    embed.set_footer(text="MC-KS-001 Sohbet Sıfırlama & Arşiv Sistemi · Arşiv Öncelikli Silme")
    return embed, ChatResetDeskView()


async def _refresh_chat_reset_panel_if_present(guild: discord.Guild | None, log_ch) -> None:
    if not guild or not log_ch or not hasattr(log_ch, "history"):
        return
    try:
        emb, view = build_chat_reset_panel(guild)
        async for m in log_ch.history(limit=25):
            if m.embeds and any((e.title or "").startswith("🧹 SOHBET SIFIRLAMA & ARŞİV MERKEZİ") for e in m.embeds):
                await m.edit(embed=emb, view=view)
                return
    except Exception:
        pass


class ChatResetDeskView(discord.ui.View):
    """Persistent management desk inside #🧹│sohbet-sifirlama-kayit."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        placeholder="🧹 Sıfırlanacak & arşivlenecek odayı seçin...",
        channel_types=[discord.ChannelType.text, discord.ChannelType.voice],
        min_values=1,
        max_values=1,
        custom_id="mc_reset_desk_select",
        row=0,
    )
    async def sel_channel(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        if not _is_chat_reset_authority(interaction.user):
            return await interaction.response.send_message(
                "⛔ **Yetki Yetersiz:** Bu panel yalnızca Genel Sekreterlik, Divan, Moderatörler ve Yöneticiler içindir.",
                ephemeral=True,
            )
        selected = select.values[0] if select.values else None
        target = interaction.guild.get_channel(selected.id) if (interaction.guild and selected) else None
        if not target or not hasattr(target, "history"):
            return await interaction.response.send_message("❌ Seçilen kanal bulunamadı veya metin geçmişi desteklemiyor.", ephemeral=True)
        if _is_protected_purge_channel(interaction.guild, target):
            return await interaction.response.send_message(
                f"🛡️ **Korumalı Kanal:** {target.mention} arşiv/tüzük/kayıt kanalı olduğu için sıfırlanamaz.",
                ephemeral=True,
            )
        perm_err = _check_bot_purge_permissions(interaction.guild, target)
        if perm_err:
            return await interaction.response.send_message(perm_err, ephemeral=True)
        await interaction.response.send_message(
            f"⚠️ **Sohbet Sıfırlama Onayı**\n"
            f"{target.mention} kanalındaki tüm mesajlar (sabitliler hariç) önce `.txt` tutanağı olarak buraya arşivlenecek, ardından sıfırlanacaktır.\n"
            f"Onaylıyor musunuz?",
            view=ChatResetConfirmView(
                actor_id=interaction.user.id,
                target=target,
                reason="Arşiv masasından sohbet sıfırlandı",
                keep_pinned=True,
                notify_channel=True,
            ),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Arşiv Panelini Yenile · Refresh",
        style=discord.ButtonStyle.secondary,
        emoji="🔄",
        custom_id="mc_reset_desk_refresh",
        row=1,
    )
    async def btn_refresh(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        if not _is_chat_reset_authority(interaction.user):
            return await interaction.response.send_message("⛔ Yetkiniz bulunmuyor.", ephemeral=True)
        emb, view = build_chat_reset_panel(interaction.guild)
        try:
            await interaction.response.edit_message(embed=emb, view=view)
        except Exception:
            await interaction.response.send_message(embed=emb, ephemeral=True)


def build_chat_reset_transcript(
    *,
    reset_id: str,
    channel,
    actor,
    reason: str,
    messages_chrono: list,
) -> tuple[bytes, str]:
    now_utc = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M:%S UTC")
    author_counts = Counter(f"{getattr(m.author, 'display_name', 'User')} ({getattr(m.author, 'id', 0)})" for m in messages_chrono)
    top_summary = ", ".join(f"{who}: {cnt}" for who, cnt in author_counts.most_common(6)) or "Yok"

    lines = [
        f"=== MİLLETLER CEMİYETİ SOHBET SIFIRLAMA KAYDI ({reset_id}) ===",
        f"Kanal: #{getattr(channel, 'name', 'kanal')} (ID: {getattr(channel, 'id', 0)})",
        f"Sıfırlayan Yetkili: {getattr(actor, 'display_name', 'Yetkili')} (ID: {getattr(actor, 'id', 0)})",
        f"Tarih: {now_utc}",
        f"Gerekçe: {reason}",
        f"Silinen Mesaj Sayısı: {len(messages_chrono)}",
        f"En Aktif Katılımcılar: {top_summary}",
        "=" * 64,
        "",
    ]
    for m in messages_chrono:
        created = getattr(m, "created_at", None)
        ts = created.strftime("%Y-%m-%d %H:%M:%S UTC") if created else "-"
        content = (getattr(m, "content", "") or "").strip()
        extras: list[str] = []
        for att in getattr(m, "attachments", ()) or ():
            url = getattr(att, "url", None) or getattr(att, "filename", "dosya")
            extras.append(f"[Ek: {url}]")
        if getattr(m, "embeds", ()):
            extras.append(f"[{len(m.embeds)} Embed]")
        joined = " ".join(part for part in (content, " ".join(extras)) if part).strip() or "[Boş / Sistem Mesajı]"
        uid = getattr(m.author, "id", 0)
        dname = getattr(m.author, "display_name", "Kullanıcı")
        lines.append(f"[{ts}] {dname} ({uid}): {joined}")

    return "\n".join(lines).encode("utf-8"), top_summary


async def _run_chat_reset_core(
    *,
    guild: discord.Guild,
    target,
    actor,
    amount: int | None,
    reason: str,
    keep_pinned: bool = True,
    notify_channel: bool = True,
) -> tuple[bool, str]:
    """Archive transcript FIRST to #🧹│sohbet-sifirlama-kayit, delete messages cleanly, and return (ok, summary)."""
    if _is_protected_purge_channel(guild, target):
        return (
            False,
            f"🛡️ **Korumalı Kanal:** {target.mention} (Tüzük, Resmî Gazete, Arşiv veya Kayıt kanalı) yanlışlıkla sıfırlanmaya karşı korumalıdır.",
        )

    perm_err = _check_bot_purge_permissions(guild, target)
    if perm_err:
        return False, perm_err

    if getattr(target, "archived", False) and hasattr(target, "edit"):
        try:
            await target.edit(archived=False, reason="MC Sohbet Sıfırlama öncesi arşiv kilidi açıldı")
        except Exception:
            return (
                False,
                f"⚠️ {target.mention} arşivlenmiş bir alt başlık (`Thread`) ve kilidi açılamadı.",
            )

    max_fetch = int(amount) if amount is not None else None
    collected: list = []
    if hasattr(target, "history"):
        async for msg in target.history(limit=max_fetch, oldest_first=False):
            if keep_pinned and getattr(msg, "pinned", False):
                continue
            collected.append(msg)

    if not collected:
        return (
            False,
            f"ℹ️ {target.mention} kanalında sıfırlanacak silinebilir mesaj bulunamadı"
            + (" *(sabitli mesajlar korundu)*." if keep_pinned else "."),
        )

    chrono = list(reversed(collected))
    reset_id = _next_chat_reset_id()
    transcript_bytes, top_summary = build_chat_reset_transcript(
        reset_id=reset_id,
        channel=target,
        actor=actor,
        reason=reason,
        messages_chrono=chrono,
    )
    preview_text = _build_transcript_preview(chrono)

    # Archive FIRST to #🧹│sohbet-sifirlama-kayit so messages are never purged without an archive record
    log_ch = await ensure_chat_reset_log_channel(guild)
    if not log_ch or not hasattr(log_ch, "send"):
        return (
            False,
            "❌ Sohbet sıfırlama arşiv kanalı (`#🧹│sohbet-sifirlama-kayit`) bulunamadığı için güvenlik gereği hiçbir mesaj silinmedi.",
        )

    mode_label = f"Son {amount} Mesaj Temizliği" if amount is not None else "Tam Sohbet Sıfırlama"
    now_str = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    chunks = _split_transcript_chunks(transcript_bytes)

    emb = discord.Embed(
        title=f"🧹 SOHBET SIFIRLAMA ARŞİVİ · {reset_id}",
        description=(
            f"• **Sıfırlanan Kanal:** {target.mention} (`#{getattr(target, 'name', 'kanal')}` · `{target.id}`)\n"
            f"• **İşlem Türü:** `{mode_label}`\n"
            f"• **Sıfırlayan Yetkili:** <@{actor.id}> (`{actor.display_name}`)\n"
            f"• **Arşivlenen Mesaj:** `{len(chrono)}` *(Dosya: `{len(chunks)}` parça)*\n"
            f"• **Sabitli Mesajlar:** `{'Korundu' if keep_pinned else 'Dahil Edildi'}`\n"
            f"• **Gerekçe:** {reason}\n"
            f"• **Aktif Yazarlar:** {top_summary[:500]}\n"
            f"• **Tarih:** `{now_str}`"
        ),
        colour=0xD97706,
        timestamp=datetime.now(timezone.utc),
    )
    emb.add_field(name="💬 Arşivlenen Sohbet Önizlemesi", value=preview_text, inline=False)
    emb.set_footer(text=f"Milletler Cemiyeti Sohbet Sıfırlama Arşivi · {reset_id}")

    log_msg = None
    try:
        first_name = f"{reset_id}_{target.id}.txt" if len(chunks) == 1 else f"{reset_id}_{target.id}_part1.txt"
        log_msg = await log_ch.send(
            embed=emb,
            file=discord.File(io.BytesIO(chunks[0]), filename=first_name),
        )
        for p_idx, part_bytes in enumerate(chunks[1:], start=2):
            await log_ch.send(
                content=f"📎 **`{reset_id}` Devam Arşivi ({p_idx}/{len(chunks)})** — {target.mention}",
                file=discord.File(io.BytesIO(part_bytes), filename=f"{reset_id}_{target.id}_part{p_idx}.txt"),
            )
    except Exception as exc:
        logger.exception("Failed sending chat reset archive for %s", reset_id)
        return (
            False,
            f"❌ Arşiv {log_ch.mention} odasına gönderilemediği için güvenlik gereği mesajlar **silinmedi**: `{exc}`",
        )

    # Delete collected messages safely (bulk chunks for <14d, single delete fallback for >=14d)
    cutoff = datetime.now(timezone.utc) - timedelta(days=13, hours=22)
    recent_msgs: list = []
    old_msgs: list = []
    for m in collected:
        cat = getattr(m, "created_at", None)
        if cat is not None:
            if cat.tzinfo is None:
                cat = cat.replace(tzinfo=timezone.utc)
            if cat < cutoff:
                old_msgs.append(m)
                continue
        recent_msgs.append(m)

    deleted_count = 0
    failed_count = 0
    for idx in range(0, len(recent_msgs), 100):
        chunk = recent_msgs[idx : idx + 100]
        if len(chunk) >= 2 and hasattr(target, "delete_messages"):
            try:
                await target.delete_messages(chunk, reason=f"MC Sohbet Sıfırlama ({reset_id}): {reason[:120]}")
                deleted_count += len(chunk)
                if idx + 100 < len(recent_msgs):
                    await asyncio.sleep(0.35)
                continue
            except discord.HTTPException as exc:
                if getattr(exc, "status", None) == 429:
                    await asyncio.sleep(float(getattr(exc, "retry_after", 1.5) or 1.5) + 0.25)
            except Exception:
                pass
        for single in chunk:
            if await _delete_single_safe(single):
                deleted_count += 1
            else:
                failed_count += 1

    for o_idx, single in enumerate(old_msgs):
        if await _delete_single_safe(single):
            deleted_count += 1
        else:
            failed_count += 1
        if o_idx > 0 and len(old_msgs) > 3:
            await asyncio.sleep(0.3)

    resets = moderation_storage.get("chat_resets", {})
    if not isinstance(resets, dict):
        resets = {}
    resets[reset_id] = {
        "id": reset_id,
        "guild_id": guild.id,
        "channel_id": target.id,
        "channel_name": getattr(target, "name", "kanal"),
        "actor_id": str(actor.id),
        "mode": mode_label,
        "archived_count": len(chrono),
        "deleted_count": deleted_count,
        "failed_count": failed_count,
        "reason": reason,
        "log_channel_id": log_ch.id,
        "log_message_id": getattr(log_msg, "id", None),
        "created_at": now_str,
    }
    moderation_storage.set("chat_resets", resets)
    await _refresh_chat_reset_panel_if_present(guild, log_ch)

    if notify_channel and hasattr(target, "send"):
        try:
            await target.send(
                embed=discord.Embed(
                    title="🧹 Sohbet Sıfırlandı · Chat Reset",
                    description=(
                        f"Bu kanalın sohbeti <@{actor.id}> tarafından sıfırlandı.\n"
                        f"• **Arşivlenen & Silinen Mesaj:** `{deleted_count}` / `{len(chrono)}`\n"
                        f"• **Gerekçe:** {reason}\n"
                        f"• **Arşiv Kayıt No:** `{reset_id}` *(Tam tutanak `{getattr(log_ch, 'name', 'sohbet-sifirlama-kayit')}` odasına işlendi)*"
                    ),
                    colour=0x64748B,
                )
            )
        except Exception:
            pass

    jump_note = f" → {log_msg.jump_url}" if getattr(log_msg, "jump_url", None) else f" → {log_ch.mention}"
    fail_note = f" ⚠️ *({failed_count} sistem/eski mesaj Discord sınırı nedeniyle atlandı)*" if failed_count else ""
    return (
        True,
        f"✅ {target.mention} odasından **`{deleted_count}`** mesaj silindi (`{reset_id}`) ve tüm geçmiş {log_ch.mention} odasına arşivlendi{jump_note}.{fail_note}",
    )


async def execute_chat_reset(
    interaction: discord.Interaction,
    *,
    target,
    amount: int | None,
    reason: str,
    keep_pinned: bool = True,
    notify_channel: bool = True,
    edit_original: bool = False,
) -> None:
    """Snapshot, archive transcript FIRST to #🧹│sohbet-sifirlama-kayit, delete messages cleanly, and report."""
    _ok, summary = await _run_chat_reset_core(
        guild=interaction.guild,
        target=target,
        actor=interaction.user,
        amount=amount,
        reason=reason,
        keep_pinned=keep_pinned,
        notify_channel=notify_channel,
    )
    await _reply_reset_outcome(interaction, summary, edit_original=edit_original)


class ChatResetConfirmView(discord.ui.View):
    def __init__(
        self,
        *,
        actor_id: int,
        target,
        reason: str,
        keep_pinned: bool,
        notify_channel: bool,
    ):
        super().__init__(timeout=300)
        self.actor_id = actor_id
        self.target = target
        self.reason = reason
        self.keep_pinned = keep_pinned
        self.notify_channel = notify_channel

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.actor_id and not _is_chat_reset_authority(interaction.user):
            await interaction.response.send_message("⛔ Bu onay yalnızca komutu başlatan yetkiliye aittir.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Evet, Sohbeti Sıfırla & Arşivle", style=discord.ButtonStyle.danger, emoji="🧹")
    async def btn_confirm(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        if not await self._guard(interaction):
            return
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="⏳ Sohbet tutanağı arşivleniyor ve oda sıfırlanıyor...", view=self)
        await execute_chat_reset(
            interaction,
            target=self.target,
            amount=None,
            reason=self.reason,
            keep_pinned=self.keep_pinned,
            notify_channel=self.notify_channel,
            edit_original=True,
        )

    @discord.ui.button(label="İptal · Cancel", style=discord.ButtonStyle.secondary, emoji="❌")
    async def btn_cancel(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        if not await self._guard(interaction):
            return
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="❌ Sohbet sıfırlama işlemi iptal edildi.", view=None)


class ModerationCog(commands.Cog):
    """Multilingual profanity filter -> auto-mute -> audit log -> ICJ court judgment flow."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._recent_handled: set[int] = set()
        self._ensured_reset_guilds: set[int] = set()

    async def cog_load(self):
        self.bot.add_view(ChatResetDeskView())
        cases = moderation_storage.get("cases", {})
        if isinstance(cases, dict):
            for cid, cdata in cases.items():
                if isinstance(cdata, dict) and not cdata.get("verdict_locked"):
                    self.bot.add_view(ModCaseActionView(cid))

    @commands.Cog.listener()
    async def on_ready(self):
        for g in getattr(self.bot, "guilds", ()):
            if g.id in self._ensured_reset_guilds:
                continue
            self._ensured_reset_guilds.add(g.id)
            try:
                log_ch = await ensure_chat_reset_log_channel(g)
                if log_ch:
                    emb, view = build_chat_reset_panel(g)
                    await upsert_panel_message(log_ch, ["🧹 SOHBET SIFIRLAMA & ARŞİV MERKEZİ"], emb, view)
            except Exception:
                pass

    async def _process_message(self, message: discord.Message):
        if not message.guild or message.author.bot or message.webhook_id:
            return
        if message.type not in (discord.MessageType.default, discord.MessageType.reply):
            return
        if message.id in self._recent_handled:
            return

        hit = detect_profanity(message.content or "")
        if not hit:
            return

        self._recent_handled.add(message.id)
        if len(self._recent_handled) > 500:
            self._recent_handled = {message.id}

        raw_content = (message.content or "").strip()
        try:
            await message.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

        uid = message.author.id
        strikes = _set_strikes(uid, _get_strikes(uid) + 1)
        duration_min = compute_profanity_timeout_minutes(strikes)
        case_id = _next_case_id()

        member = message.author if isinstance(message.author, discord.Member) else await _resolve_guild_member(message.guild, uid)
        audit_reason = f"MC Küfür Filtresi ({case_id}) | Tekrar #{strikes} | {duration_min} dk mute"
        ok_mute, mute_note = await _apply_timeout(message.guild, member, duration_min, audit_reason)

        rec = delegates_storage.get(str(uid), {})
        nation = rec.get("nation") or "Kayıtsız / Unregistered"
        masked_excerpt = mask_profane_text(raw_content)

        case_data = {
            "id": case_id,
            "guild_id": message.guild.id,
            "user_id": uid,
            "user_tag": str(message.author),
            "nation": nation,
            "channel_id": message.channel.id,
            "channel_name": getattr(message.channel, "name", "-"),
            "masked_match": hit["masked"],
            "masked_excerpt": masked_excerpt[:500],
            "strikes": strikes,
            "duration_min": duration_min,
            "mute_applied": ok_mute,
            "timeout_note": mute_note,
            "status": "⏳ Yargı İncelemesinde / Pending Court Review",
            "verdict_locked": False,
            "created_at": datetime.utcnow().strftime("%d.%m.%Y %H:%M UTC"),
        }

        # 1. Post full entry to private Audit Log channel (#🛡️│denetim-kaydi-audit-log)
        audit_ch = _find_audit_channel(message.guild)
        if audit_ch:
            audit_emb = discord.Embed(
                title=f"🛡️ DENETİM KAYDI · KÜFÜR İHLALİ ({case_id})",
                description=(
                    f"**Üye / Member:** <@{uid}> (`{message.author}` · `{uid}`)\n"
                    f"**Devlet / Nation:** `{nation}`\n"
                    f"**Kanal / Channel:** <#{message.channel.id}>\n"
                    f"**Yakalanan / Match:** `{hit['masked']}`\n"
                    f"**Tekrar Sayısı / Strike:** `{strikes}` *(3 tekrarda 2 katına çıkar)*\n"
                    f"**Mute Süresi:** `{duration_min} dakika` → `{mute_note}`\n\n"
                    f"**Silinen Mesaj İçeriği / Deleted Message:**\n||{(raw_content or '-')[:800]}||"
                ),
                colour=0xEF4444,
            )
            audit_emb.set_footer(text=f"MC Denetim Sistemi · {case_data['created_at']}")
            try:
                await audit_ch.send(embed=audit_emb)
            except Exception as exc:
                logger.warning("Failed sending audit log for %s: %s", case_id, exc)

        # 2. Forward case card with Judge decision buttons to #⚖️│yargi-divani-icj-court
        court_ch = _find_court_channel(message.guild)
        if court_ch:
            view = ModCaseActionView(case_id)
            self.bot.add_view(view)
            try:
                cmsg = await court_ch.send(embed=build_court_case_embed(case_data), view=view)
                case_data["court_channel_id"] = court_ch.id
                case_data["court_message_id"] = cmsg.id
            except Exception as exc:
                logger.warning("Failed forwarding mod case %s to court: %s", case_id, exc)

        _save_case(case_id, case_data)

        # 3. Short temporary channel warning + DM notice
        tier_note = " *(3+ tekrar nedeniyle 2x artırıldı)*" if strikes >= 3 else ""
        try:
            await message.channel.send(
                f"🚫 <@{uid}> Küfür/hakaret tespit edildi. Mesaj silindi, **`{duration_min} dk`** mute uygulandı ve dosya Yargı Divanı'na (`{case_id}`) taşındı{tier_note}.",
                delete_after=8.0,
            )
        except Exception:
            pass

        try:
            await message.author.send(
                embed=discord.Embed(
                    title=f"🚫 Otomatik Mute & Yargı Sevki · {case_id}",
                    description=(
                        f"**Kanal:** <#{message.channel.id}>\n"
                        f"**İhlal Tekrarı / Strike:** `{strikes}`\n"
                        f"**Mute Süresi / Duration:** `{duration_min} dakika` (`{mute_note}`)\n"
                        f"Dosyanız denetim kaydına işlenmiş ve `#⚖️│yargi-divani-icj-court` yargı incelemesine aktarılmıştır. Yargı kararına göre cezanız düşürülebilir veya sabit tutulabilir."
                    ),
                    colour=0xEF4444,
                )
            )
        except Exception:
            pass

    @commands.Cog.listener("on_message")
    async def on_message_profanity(self, message: discord.Message):
        await self._process_message(message)

    @commands.Cog.listener("on_message_edit")
    async def on_message_edit_profanity(self, _before: discord.Message, after: discord.Message):
        await self._process_message(after)

    @app_commands.command(name="ceza-sicili", description="Bir üyenin küfür/moderasyon ihlal sayısını ve son yargı dosyalarını gösterir")
    async def cmd_mod_history(self, interaction: discord.Interaction, uye: discord.Member = None):
        target = uye or interaction.user
        if uye and not _is_court_authority(interaction.user):
            return await interaction.response.send_message("❌ Başka bir üyenin ceza sicilini sadece Yargıçlar veya Yöneticiler görebilir.", ephemeral=True)

        strikes = _get_strikes(target.id)
        cases = moderation_storage.get("cases", {})
        user_cases = [
            c for c in (cases.values() if isinstance(cases, dict) else [])
            if isinstance(c, dict) and str(c.get("user_id")) == str(target.id)
        ][-8:]
        lines = [
            f"• **`{c['id']}`** — `{c.get('duration_min', 30)} dk` · {c.get('status', '-')}"
            for c in reversed(user_cases)
        ] or ["*Kayıtlı ihlal yok / No infractions.*"]
        emb = discord.Embed(
            title=f"⚖️ Moderasyon & Ceza Sicili · {target.display_name}",
            description=(
                f"**Aktif İhlal Tekrarı / Active Strikes:** `{strikes}`\n"
                f"**Sonraki Tekrarda Mute Süresi:** `{compute_profanity_timeout_minutes(strikes + 1)} dk`\n\n"
                + "\n".join(lines)
            ),
            colour=0x1E3A8A,
        )
        await interaction.response.send_message(embed=emb, ephemeral=True)

    @app_commands.command(name="ceza-affet", description="Bir üyenin aktif mute cezasını kaldırır ve ihlal tekrarını 1 düşürür veya sıfırlar (Yargıç/Yönetici)")
    @app_commands.describe(uye="Cezası düşürülecek üye", sicil_sifirla="İhlal sayacını tamamen sıfırla (Hayır ise 1 azaltır)", sebep="Karar gerekçesi")
    async def cmd_pardon(
        self,
        interaction: discord.Interaction,
        uye: discord.Member,
        sicil_sifirla: bool = False,
        sebep: str = "Yargı / Yönetim affı",
    ):
        if not _is_court_authority(interaction.user):
            return await interaction.response.send_message("❌ Yalnızca Yargıçlar veya Yöneticiler ceza kaldırabilir.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        _ok, note = await _apply_timeout(interaction.guild, uye, None, f"Affedildi ({interaction.user.display_name}): {sebep}")
        cur = _get_strikes(uye.id)
        new_strikes = _set_strikes(uye.id, 0 if sicil_sifirla else max(0, cur - 1))

        audit_ch = _find_audit_channel(interaction.guild)
        if audit_ch:
            try:
                await audit_ch.send(
                    embed=discord.Embed(
                        title="🕊️ DENETİM KAYDI · CEZA AFFI / PARDON",
                        description=f"**Üye:** <@{uye.id}>\n**Yetkili:** <@{interaction.user.id}>\n**Yeni Sicil:** `{new_strikes}`\n**Timeout:** `{note}`\n**Sebep:** {sebep}",
                        colour=0x10B981,
                    )
                )
            except Exception:
                pass
        await interaction.followup.send(f"✅ <@{uye.id}> için ceza güncellendi (`{note}`, yeni tekrar sayısı: `{new_strikes}`).", ephemeral=True)

    @app_commands.command(
        name="sohbet-sifirla",
        description="Bulunduğunuz odayı (veya son N mesajı) sıfırlar ve tüm geçmişini kayıt odasına arşivler",
    )
    @app_commands.describe(
        miktar="Silinecek mesaj sayısı (1–1000). Boş bırakılırsa odadaki tüm sohbet sıfırlanır",
        kanal="Sıfırlanacak kanal (boş bırakılırsa bulunduğunuz oda)",
        sebep="Arşiv tutanağına yazılacak sıfırlama gerekçesi",
        sabitlemeleri_koru="Sabitlenmiş mesajlar ve bot panelleri korunsun mu? (Varsayılan: Evet)",
        kanal_bildirimi="Sıfırlandıktan sonra kanalda kısa bilgilendirme kalsın mı?",
        onay="Evet seçilirse onay butonu sormadan odayı doğrudan sıfırlar",
    )
    async def cmd_reset_chat(
        self,
        interaction: discord.Interaction,
        miktar: app_commands.Range[int, 1, 1000] | None = None,
        kanal: Union[discord.TextChannel, discord.VoiceChannel, discord.Thread, None] = None,
        sebep: str = "Yönetim tarafından sohbet sıfırlandı",
        sabitlemeleri_koru: bool = True,
        kanal_bildirimi: bool = True,
        onay: bool = False,
    ):
        if not _is_chat_reset_authority(interaction.user):
            return await interaction.response.send_message(
                "⛔ **Yetki Yetersiz:** Sohbet sıfırlama işlemi yalnızca **Genel Sekreterlik, Divan, Moderatörler veya Yöneticiler** tarafından yapılabilir.",
                ephemeral=True,
            )

        target = kanal or interaction.channel
        if not target or not hasattr(target, "history"):
            return await interaction.response.send_message("❌ Geçerli bir metin kanalı veya başlık seçilemedi.", ephemeral=True)

        if _is_protected_purge_channel(interaction.guild, target):
            return await interaction.response.send_message(
                f"🛡️ **Korumalı Kanal:** {target.mention} (Tüzük, Resmî Gazete, Arşiv veya Kayıt kanalı) sıfırlamaya karşı korumalıdır.",
                ephemeral=True,
            )

        perm_err = _check_bot_purge_permissions(interaction.guild, target)
        if perm_err:
            return await interaction.response.send_message(perm_err, ephemeral=True)

        reason_clean = (sebep or "").strip() or "Yönetim tarafından sohbet sıfırlandı"
        if miktar is None and not onay:
            log_ch = _resolve_existing_chat_reset_log_channel(interaction.guild)
            log_ref = log_ch.mention if log_ch else "`#🧹│sohbet-sifirlama-kayit`"
            return await interaction.response.send_message(
                f"⚠️ **Tam Sohbet Sıfırlama Onayı — {target.mention}**\n"
                f"Aşağıdaki **🧹 Evet, Sohbeti Sıfırla & Arşivle** butonuna bastığınızda bu odadaki tüm mesajlar "
                f"önce `.txt` arşivi olarak {log_ref} odasına kaydedilecek, ardından oda sıfırlanacaktır.\n"
                f"• **Sabitli Mesajlar:** `{'Korunacak' if sabitlemeleri_koru else 'Silinecek'}`\n"
                f"• **Gerekçe:** {reason_clean}\n"
                f"*(İpucu: Tek adımda doğrudan çalıştırmak için komutta `onay: True` seçebilirsiniz.)*",
                view=ChatResetConfirmView(
                    actor_id=interaction.user.id,
                    target=target,
                    reason=reason_clean,
                    keep_pinned=sabitlemeleri_koru,
                    notify_channel=kanal_bildirimi,
                ),
                ephemeral=True,
            )

        await interaction.response.defer(ephemeral=True, thinking=True)
        await execute_chat_reset(
            interaction,
            target=target,
            amount=int(miktar) if miktar is not None else None,
            reason=reason_clean,
            keep_pinned=sabitlemeleri_koru,
            notify_channel=kanal_bildirimi,
        )

    @commands.command(name="sohbet-sifirla", aliases=["sifirla", "temizle"])
    async def cmd_reset_chat_prefix(self, ctx: commands.Context, miktar: int | None = None, *, sebep: str = "Yönetim tarafından sohbet sıfırlandı"):
        """Instant prefix command (!sifirla / !sohbet-sifirla) to archive and reset the current room."""
        if not ctx.guild or not _is_chat_reset_authority(ctx.author):
            return await ctx.reply(
                "⛔ **Yetki Yetersiz:** Sohbet sıfırlama işlemi yalnızca **Genel Sekreterlik, Divan, Moderatörler veya Yöneticiler** tarafından yapılabilir.",
                delete_after=8.0,
            )
        amt = max(1, min(1000, miktar)) if miktar is not None else None
        reason_clean = (sebep or "").strip() or "Yönetim tarafından sohbet sıfırlandı"
        _ok, summary = await _run_chat_reset_core(
            guild=ctx.guild,
            target=ctx.channel,
            actor=ctx.author,
            amount=amt,
            reason=reason_clean,
            keep_pinned=True,
            notify_channel=True,
        )
        if not _ok:
            await ctx.reply(summary, delete_after=10.0)

    @app_commands.command(
        name="sohbet-kayit-kanali",
        description="Sohbet sıfırlama tutanaklarının gönderileceği kayıt kanalını belirler (Yönetici)",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(kanal="Sohbet sıfırlama kayıt ve tutanaklarının yazılacağı özel kanal")
    async def cmd_set_chat_reset_log_channel(self, interaction: discord.Interaction, kanal: discord.TextChannel):
        moderation_storage.set(f"chat_reset_log_{interaction.guild_id}", kanal.id)
        await interaction.response.send_message(
            f"✅ Sohbet sıfırlama tutanakları ve kayıtları bundan sonra {kanal.mention} kanalına işlenecektir.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(ModerationCog(bot))
