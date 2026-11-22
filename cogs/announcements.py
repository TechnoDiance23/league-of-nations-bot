import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime

from utils.storage import gazette_storage, delegates_storage
from utils.constants import ROLE_SG, ROLE_ADMIN, ROLE_SPOKESPERSON, ROLE_MEMBER, resolve_panel_channel
from utils.un_gazette import upsert_panel_message
from utils.info_channels import sync_info_and_rules_channels


def _can_announce(member: discord.Member) -> bool:
    if member.guild_permissions.administrator or member.guild_permissions.manage_messages:
        return True
    role_names = {r.name for r in member.roles}
    if role_names & {ROLE_SG, ROLE_ADMIN, ROLE_SPOKESPERSON, "🏛️ MC Genel Sekreteri", "🏛️ Cemiyet Yönetimi & Divan", "📰 Cemiyet Sözcüsü"}:
        return True
    rec = delegates_storage.get(str(member.id), {})
    return rec.get("office_code") in {"SG", "DSG", "GKB", "GKB-V", "Spokesperson"}


def _get_announcement_channel(guild: discord.Guild) -> discord.TextChannel | None:
    cfg = gazette_storage.get(f"announce_config_{guild.id}", {})
    ch_id = cfg.get("channel_id")
    if ch_id:
        ch = guild.get_channel(int(ch_id))
        if ch:
            return ch
    for ch in guild.text_channels:
        low = ch.name.lower()
        if any(k in low for k in ("duyurular-announcements", "cemiyet-duyuru", "duyuru", "announcements")):
            return ch
    return None


TYPE_STYLES = {
    "general": ("📢 RESMÎ DUYURU · OFFICIAL NOTICE", 0x1E3A8A),
    "crisis": ("🚨 ACİL KRİZ · EMERGENCY CRISIS", 0xEF4444),
    "summit": ("🏛️ ZİRVE ÇAĞRISI · SUMMIT CALL", 0xD97706),
    "charter": ("📜 TÜZÜK GÜNCELLEMESİ · CHARTER UPDATE", 0x6D28D9),
    "press": ("🕊️ BASIN BİLDİRİSİ · PRESS RELEASE", 0x10B981),
}


class AnnouncementModal(discord.ui.Modal, title="Duyuru Yayınla · Publish Announcement"):
    a_title = discord.ui.TextInput(
        label="Başlık / Headline",
        placeholder="Örn / e.g.: Olağanüstü Oturum / Emergency Session",
        required=True,
        max_length=150,
    )
    a_body = discord.ui.TextInput(
        label="Duyuru Metni / Body (Markdown)",
        style=discord.TextStyle.paragraph,
        placeholder="Duyuru metnini yazın / Enter announcement text...",
        required=True,
        max_length=2000,
    )
    a_image = discord.ui.TextInput(
        label="Görsel URL / Image URL (Opsiyonel)",
        placeholder="https://... (boş bırakılabilir / optional)",
        required=False,
        max_length=300,
    )

    def __init__(self, ann_type: str, ping_mode: str, target_channel: discord.TextChannel | None):
        super().__init__()
        self.ann_type = ann_type
        self.ping_mode = ping_mode
        self.target_channel = target_channel

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        ch = self.target_channel or _get_announcement_channel(interaction.guild) or interaction.channel
        prefix, _color = TYPE_STYLES.get(self.ann_type, ("📢 RESMÎ DUYURU · OFFICIAL NOTICE", 0x1E3A8A))

        lines = [
            f"**{prefix} · {self.a_title.value.strip()}**",
            "━━━━━━━━━━━━━━━━━━━━",
            self.a_body.value.strip(),
            "━━━━━━━━━━━━━━━━━━━━",
            f"*Yayınlayan / Issued by: {interaction.user.display_name} · League of Nations*",
        ]
        img = self.a_image.value.strip()
        if img.startswith(("http://", "https://")):
            lines.append(img)

        mention = ""
        if self.ping_mode == "everyone":
            mention = "@everyone"
        elif self.ping_mode == "here":
            mention = "@here"
        elif self.ping_mode == "members":
            role = discord.utils.get(interaction.guild.roles, name=ROLE_MEMBER) or discord.utils.get(interaction.guild.roles, name="🏛️ Cemiyet Üye Devleti")
            if role:
                mention = role.mention

        if mention:
            lines.append(mention)

        content = "\n".join(lines)[:2000]
        msg = await ch.send(
            content=content,
            allowed_mentions=discord.AllowedMentions(everyone=True, roles=True),
        )
        await interaction.followup.send(f"✅ Duyuru düz yazı olarak yayınlandı / Published in <#{ch.id}>: {msg.jump_url}", ephemeral=True)


class EditAnnouncementModal(discord.ui.Modal, title="Duyuru Düzenle · Edit Announcement"):
    a_title = discord.ui.TextInput(label="Yeni Başlık / New Title", required=True, max_length=150)
    a_body = discord.ui.TextInput(label="Yeni Metin / New Body", style=discord.TextStyle.paragraph, required=True, max_length=2000)

    def __init__(self, msg: discord.Message):
        super().__init__()
        self.msg = msg
        if msg.embeds:
            self.a_title.default = msg.embeds[0].title or ""
            desc = msg.embeds[0].description or ""
            self.a_body.default = desc.split("\n\n━━━━")[0]
        elif msg.content:
            parts = msg.content.split("\n━━━━━━━━━━━━━━━━━━━━\n")
            if len(parts) >= 2:
                self.a_title.default = parts[0].strip("* ")[:150]
                self.a_body.default = parts[1].strip()[:2000]
            else:
                self.a_body.default = msg.content.strip()[:2000]

    async def on_submit(self, interaction: discord.Interaction):
        if self.msg.embeds:
            old_embed = self.msg.embeds[0]
            old_embed.title = self.a_title.value.strip()
            old_embed.description = f"{self.a_body.value.strip()}\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            await self.msg.edit(embed=old_embed)
        else:
            new_text = (
                f"**{self.a_title.value.strip()}**\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"{self.a_body.value.strip()}\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"*Düzenleyen / Edited by: {interaction.user.display_name} · League of Nations*"
            )[:2000]
            await self.msg.edit(content=new_text, embeds=[])
        await interaction.response.send_message(f"✅ Duyuru güncellendi / Updated: {self.msg.jump_url}", ephemeral=True)


class AnnouncementPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Duyuru Yayınla · Post Notice", style=discord.ButtonStyle.primary, emoji="📢", custom_id="ann_panel_create")
    async def btn_create(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _can_announce(interaction.user):
            return await interaction.response.send_message("❌ Yetkiniz yok. / Staff or Spokesperson only.", ephemeral=True)
        await interaction.response.send_modal(AnnouncementModal("general", "none", None))

    @discord.ui.button(label="Acil Duyuru · Urgent (@everyone)", style=discord.ButtonStyle.danger, emoji="🚨", custom_id="ann_panel_crisis")
    async def btn_crisis(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _can_announce(interaction.user):
            return await interaction.response.send_message("❌ Yetkiniz yok. / Staff or Spokesperson only.", ephemeral=True)
        await interaction.response.send_modal(AnnouncementModal("crisis", "everyone", None))


def build_announcement_panel() -> tuple[discord.Embed, discord.ui.View]:
    embed = discord.Embed(
        title="📢 DUYURU YÖNETİM PANELİ · ANNOUNCEMENT PANEL",
        description=(
            "**🇹🇷 Türkçe:** Yöneticiler ve Cemiyet Sözcüsü doğrudan `#📢│duyurular-announcements` kanalına duyuru geçebilir.\n"
            "**🇬🇧 English:** Staff and Spokespersons can post official notices directly to `#📢│duyurular-announcements` below."
        ),
        colour=0x1E3A8A,
    )
    return embed, AnnouncementPanelView()


class AnnouncementsCog(commands.Cog):
    """Configurable server announcements system for admins and UN leadership."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="duyuru-kanali", description="Sunucunun resmi duyuru kanalını belirler (Yönetici)")
    @app_commands.default_permissions(administrator=True)
    async def cmd_set_announce_channel(self, interaction: discord.Interaction, kanal: discord.TextChannel):
        gazette_storage.set(f"announce_config_{interaction.guild_id}", {"channel_id": kanal.id})
        await interaction.response.send_message(f"✅ Duyuru kanalı <#{kanal.id}> olarak ayarlandı.", ephemeral=True)

    @app_commands.command(name="duyuru-yap", description="Duyuru kanalına veya seçilen kanala şık bir duyuru yayınlar")
    @app_commands.describe(tur="Duyuru türü", etiket="Bildirim etiketi", kanal="Gönderilecek kanal (boşsa varsayılan duyuru kanalı)")
    @app_commands.choices(
        tur=[
            app_commands.Choice(name="📢 Genel Duyuru", value="general"),
            app_commands.Choice(name="🚨 Acil Kriz Duyurusu", value="crisis"),
            app_commands.Choice(name="🏛️ Oturum / Zirve Çağrısı", value="summit"),
            app_commands.Choice(name="📜 Tüzük / Kural Güncellemesi", value="charter"),
            app_commands.Choice(name="🕊️ Basın Bildirisi", value="press"),
        ],
        etiket=[
            app_commands.Choice(name="Etiketsiz", value="none"),
            app_commands.Choice(name="@everyone", value="everyone"),
            app_commands.Choice(name="@here", value="here"),
            app_commands.Choice(name="🏛️ Cemiyet Üye Devleti Rolü", value="members"),
        ],
    )
    async def cmd_announce(
        self,
        interaction: discord.Interaction,
        tur: app_commands.Choice[str],
        etiket: app_commands.Choice[str],
        kanal: discord.TextChannel = None,
    ):
        if not _can_announce(interaction.user):
            return await interaction.response.send_message("❌ Duyuru yayınlamak için Yönetici veya Cemiyet Divanı yetkisi gerekir.", ephemeral=True)
        await interaction.response.send_modal(AnnouncementModal(tur.value, etiket.value, kanal))

    @app_commands.command(name="duyuru-duzenle", description="Botun attığı bir duyuru mesajını ID ile düzenler")
    @app_commands.describe(mesaj_id="Düzenlenecek duyuru mesajının ID'si", kanal="Mesajın bulunduğu kanal (boşsa duyuru kanalı)")
    async def cmd_edit_announce(self, interaction: discord.Interaction, mesaj_id: str, kanal: discord.TextChannel = None):
        if not _can_announce(interaction.user):
            return await interaction.response.send_message("❌ Duyuru düzenleme yetkiniz yok.", ephemeral=True)

        ch = kanal or _get_announcement_channel(interaction.guild) or interaction.channel
        try:
            msg = await ch.fetch_message(int(mesaj_id.strip()))
        except Exception:
            return await interaction.response.send_message("❌ Belirtilen mesaj bu kanalda bulunamadı.", ephemeral=True)

        if msg.author.id != self.bot.user.id:
            return await interaction.response.send_message("❌ Yalnızca botun gönderdiği duyurular düzenlenebilir.", ephemeral=True)

        await interaction.response.send_modal(EditAnnouncementModal(msg))

    @app_commands.command(name="duyuru-paneli-kur", description="Yöneticiler için tek tıkla duyuru yayınlama paneli kurar")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(kanal="İsteğe bağlı hedef kanal (boş bırakılırsa otomatik yayın paneli kanalına kurulur)")
    async def cmd_announce_panel(self, interaction: discord.Interaction, kanal: discord.TextChannel = None):
        target = resolve_panel_channel(interaction.guild, kanal, "yayin-paneli-broadcast", interaction.channel)
        embed, view = build_announcement_panel()
        st = await upsert_panel_message(target, ["📢 DUYURU YÖNETİM PANELİ", "📢 MİLLETLER CEMİYETİ DUYURU YÖNETİM PANELİ"], embed, view)
        await interaction.response.send_message(f"✅ Duyuru yönetim paneli {target.mention} kanalında güncellendi (`{st}`).", ephemeral=True)

    @commands.Cog.listener("on_ready")
    async def on_ready_sync_lang_and_info(self):
        if getattr(self, "_lang_info_synced", False):
            return
        self._lang_info_synced = True
        from utils.server_setup import ensure_community_language_channels_and_info

        for g in self.bot.guilds:
            try:
                changes = await ensure_community_language_channels_and_info(g)
                if changes:
                    print(f"[lang_info_sync] guild={g.id} updated: {', '.join(changes)}")
            except Exception as exc:
                print(f"[lang_info_sync] error in {g.id}: {exc}")

    @app_commands.command(name="bilgi-kanallari-yenile", description="Diller, Kurallar ve boşsa Duyurular kanallarını botla yeniden atar (Yönetici)")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(duyuru_sifirla="Duyurular kanalını da temizleyip yeniden atsın mı (Varsayılan: Hayır)")
    async def cmd_refresh_info_channels(self, interaction: discord.Interaction, duyuru_sifirla: bool = False):
        await interaction.response.defer(ephemeral=True)
        res = await sync_info_and_rules_channels(interaction.guild, force_announcements=duyuru_sifirla)
        summary = ", ".join(res) if res else "İşlenen kanal bulunamadı."
        await interaction.followup.send(f"✅ Bilgi/kurallar kanalları yenilendi: {summary}", ephemeral=True)


async def setup(bot: commands.Bot):
    bot.add_view(AnnouncementPanelView())
    await bot.add_cog(AnnouncementsCog(bot))
