import os
import sys
import logging
import discord
from discord.ext import commands
from dotenv import load_dotenv

from utils.server_setup import run_auto_setup

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("InternationalDiplomaticBot")

# Load environment variables
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    logger.error("DISCORD_TOKEN not found in environment or .env file!")
    sys.exit(1)

def _build_intents() -> discord.Intents:
    import json
    import urllib.request

    res = discord.Intents.default()
    if os.getenv("ENABLE_MESSAGE_CONTENT_INTENT", "1").lower() in ("1", "true", "yes"):
        res.message_content = True
    if os.getenv("ENABLE_PRIVILEGED_INTENTS", "0").lower() in ("1", "true", "yes"):
        res.message_content = True
        token = os.getenv("DISCORD_TOKEN", "").strip()
        if token:
            try:
                req = urllib.request.Request(
                    "https://discord.com/api/v10/oauth2/applications/@me",
                    headers={
                        "Authorization": f"Bot {token}",
                        "User-Agent": "DiscordBot (https://discord.com, 1.0)",
                    },
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    app_info = json.loads(resp.read().decode("utf-8"))
                flags = int(app_info.get("flags", 0))
                if flags & ((1 << 14) | (1 << 15)):
                    res.members = True
                    logger.info("Server Members Intent doğrulanarak açıldı (app flags=%s).", flags)
                else:
                    logger.warning(
                        "ENABLE_PRIVILEGED_INTENTS=1 ancak Discord Developer Portal'da 'Server Members Intent' kapalı (flags=%s); çökmemek için members intent geçici devre dışı.",
                        flags,
                    )
            except Exception as exc:
                logger.warning("Privileged intent ön kontrolü başarısız (%s); members intent kapalı başlatılıyor.", exc)
    return res


intents = _build_intents()


class InternationalDiplomaticBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix=commands.when_mentioned_or(os.getenv("COMMAND_PREFIX", "!")),
            intents=intents,
            help_command=None,
        )
        self._guild_commands_synced: set[int] = set()
        self._global_cleared: bool = False

    async def setup_hook(self):
        initial_extensions = [
            "cogs.voting",
            "cogs.assembly",
            "cogs.delegation",
            "cogs.resolutions",
            "cogs.embassies",
            "cogs.ambassadors",
            "cogs.translation",
            "cogs.summits",
            "cogs.treaties_icj",
            "cogs.announcements",
            "cogs.tickets",
            "cogs.applications",
            "cogs.chat_guard",
            "cogs.moderation",
        ]
        for ext in initial_extensions:
            try:
                await self.load_extension(ext)
                logger.info(f"Loaded extension: {ext}")
            except Exception as e:
                logger.error(f"Failed to load extension {ext}: {e}")

        logger.info(f"Registered {len(self.tree.get_commands())} slash command templates (guild sync runs on ready).")

    async def on_ready(self):
        logger.info(f"Logged in as {self.user.name} ({self.user.id})")
        logger.info(f"Connected to {len(self.guilds)} guild(s).")
        for g in self.guilds:
            if g.id in self._guild_commands_synced:
                continue
            self._guild_commands_synced.add(g.id)
            try:
                self.tree.copy_global_to(guild=g)
                synced_g = await self.tree.sync(guild=g)
                logger.info(f"Synced {len(synced_g)} slash commands to guild {g.name} ({g.id}) immediately.")
            except Exception as e:
                logger.error(f"Failed to sync guild slash commands for {g.id}: {e}")
        if not self._global_cleared and self.application_id:
            self._global_cleared = True
            try:
                await self.http.bulk_upsert_global_commands(self.application_id, [])
                logger.info("Cleared duplicate global slash command cache.")
            except Exception as e:
                logger.warning(f"Could not clear global command cache: {e}")
        try:
            activity = discord.Activity(
                type=discord.ActivityType.watching,
                name="🏛️ League of Nations Hard-RP | /yardim",
            )
            await self.change_presence(activity=activity)
        except Exception:
            pass

    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.CommandNotFound):
            return
        logger.error(f"Command error: {error}")


bot = InternationalDiplomaticBot()


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
    logger.exception(
        "Slash command error on /%s (user=%s, guild=%s)",
        getattr(interaction.command, "name", "unknown"),
        getattr(interaction.user, "id", None),
        interaction.guild_id,
        exc_info=error,
    )
    msg = f"❌ **Komut çalıştırılırken bir hata oluştu:** `{type(error).__name__}`"
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception:
        pass


@bot.tree.command(name="yardim", description="Tüm komutları ve rehberi gösterir / Show all commands and user guide")
async def cmd_help(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🏛️ Milletler Cemiyeti · League of Nations Bot Rehberi / Guide",
        description=(
            "**parlamentum.org** & Milletler Cemiyeti / League of Nations Hard-RP diplomasi sistemi.\n"
            "*Bilingual (TR/EN) diplomatic, treaty, court, voting, and ticket platform.*\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ),
        colour=0x1E3A8A,
    )

    embed.add_field(
        name="🗳️ 1. Genel Kurul Oylamaları · General Assembly Voting",
        value=(
            "• `/oylama` — Genel Kurul (1 Ülke = 1 Eşit Oy) veya Bireysel oylama başlatır / Start GA vote.\n"
            "• `/oylama-paneli-kur` — Kalıcı oylama paneli kurar / Setup voting panel.\n"
            "• `/kararlar-listesi` & `/karar-goruntule [No]` — Kararları listeler/açar / Browse resolutions."
        ),
        inline=False,
    )

    embed.add_field(
        name="📰 2. Gazete, Duyuru, Antlaşma & Divan · Gazette, Treaties & ICJ",
        value=(
            "• `/duyuru-yap`, `/duyuru-duzenle`, `/duyuru-kanali`, `/duyuru-paneli-kur` — Duyurular / Announcements.\n"
            "• `/resmi-gazete-yayinla` & `/resmi-gazete-kanali` — Resmî Gazete / Official Gazette.\n"
            "• `/antlasma-sun` & `/antlasma-paneli-kur` — Butonlu Antlaşma Masası / Treaty Signing Desk.\n"
            "• `/dava-ac`, `/divan-paneli-kur`, `/divan-ofisi-kur` — Yargı Divanı, Savunma Çağrısı & Yargıç Ofisi.\n"
            "• `/emsal-karar`, `/emsal-listesi`, `/emsal-kaldir` — Bağlayıcı Emsal Kararlar (`EMS-YYYY/XXX`) & İçtihat Sicili.\n"
            "• `/sorusturma-ac`, `/iddianame-sun`, `/savcilik-paneli-kur` — Cemiyet Başsavcılığı & Soruşturma Bürosu."
        ),
        inline=False,
    )

    embed.add_field(
        name="🏛️ 3. Temsilcilikler, Notalar & Barış Zirveleri · Embassies & Summits",
        value=(
            "• `/temsilcilik-ac` — Daimi Temsilcilik, Büyükelçilik veya Konsolosluk açar / Open mission.\n"
            "• `/nota-gonder` — Açık veya Gizli nota yollar / Send open or encrypted note.\n"
            "• `/temsilcilikler` — Açık misyonları listeler / List active missions.\n"
            "• Özel Diplomatik Atama Odasındaki **Büyükelçi Ata** butonu aday ve hedef ülke seçimiyle atamayı tamamlar. Başkan/Bakan kendi devletindeki adayı, Genel Sekreter kayıtlı herhangi bir üyeyi seçebilir. / Use the private room’s **Appoint Ambassador** button.\n"
            "• `/elci-geri-cagir`, `/elci-istifa`, `/buyukelciler` — İlk 14 gün Bakan geri çağıramaz; Başkan veya büyükelçi görevi bitirebilir. / FM recall opens after 14 days; the President or ambassador may end it sooner.\n"
            "• `/zirve-ac`, `/zirve-taraf-statu`, `/zirve-taraf-ekle`, `/zirve-kapat`, `/zirveler` — Barış / Yaptırım Zirveleri (Davacı/Davalı rolleri, ses & ateşkes protokolü)."
        ),
        inline=False,
    )

    embed.add_field(
        name="🌐 3A. Sohbet Çevirisi · Automatic Translation",
        value=(
            "• `/ceviri-kanali-ekle`, `/ceviri-kanali-kaldir`, `/ceviri-kanallari` — Yönetici kanalın çeviri ayarlarını yönetir.\n"
            "• Sohbet kanallarında bot, Türkçe dışındaki mesajların Türkçe karşılığını otomatik gösterir.\n"
            "• Kanalın ve katılımcıların diğer dillerine yapılan çeviriler aynı yanıtın altına eklenir. Türkçe mesaj için başka bir hedef dil yoksa İngilizce kullanılır.\n"
            "• Chat translations appear in one reply below the original message."
        ),
        inline=False,
    )

    embed.add_field(
        name="📋 4. Tüzük, Akreditasyon & Yapışkan Roller · Charter, Registry & Sticky Roles",
        value=(
            "• `/tuzuk-yayinla` & `/sicil-senkronize` — Tam Tüzük & İmzacı Devletler Sicili / Charter & Signatories.\n"
            "• `/akreditasyon-masasi-kur`, `/akreditasyon-ver`, `/milliyet-sec` — İmza, görev, milliyet & oto-rol.\n"
            "• `/rol-guncelleme-talebi`, `/istifa-et`, `/yapiskan-rol-senkronize` — Rol güncelleme, istifa & yapışkan rol.\n"
            "• `/hukumet-baskani-dusur` — Genel Sekreter kararıyla gerekçeli hükûmet başkanı düşürme.\n"
            "• `/kurum-ata` & `/atama-paneli-kur` — Genel Sekreter kurumsal atama (Divan, Meclis, Sözcü, Mod) & `#kurumsal-atama-appointments`.\n"
            "• `/sicil-listesi`, `/ulke-statu`, `/ulke-karnesi`, `/delege-bilgi` — Devlet sicili & delegasyon dosyaları."
        ),
        inline=False,
    )

    embed.add_field(
        name="🎙️ 5. Kürsü, Moderasyon, Ticket & Kurulum · Floor, Mod & Setup",
        value=(
            "• `/konusmaci-paneli-kur` & `/mola-onergesi` — Konuşmacı kürsüsü / Speakers' floor & caucus.\n"
            "• `/sohbet-sifirla` & `/sohbet-kayit-kanali` — Sohbet temizleme / tam sıfırlama & `#🧹│sohbet-sifirlama-kayit` tutanak arşivi.\n"
            "• `/ceza-sicili` & `/ceza-affet` — Moderasyon ihlal sicili & ceza kaldırma.\n"
            "• `/ticket-paneli-kur`, `/ticket-sorgula`, `/ticket-devret`, `/ticket-sonuclandir`, `/yetkili-tanimla` — Özel Ticket & DM.\n"
            "• `/basvuru-alimi` & `/basvuru-paneli-kur` — Gazeteci, Moderatör & Admin alımını aç/kapat & panel kur.\n"
            "• `/otomatik-kurulum` — Tek komutta tüm TR/EN oda, ses, rol & panelleri kurar / 1-click full setup."
        ),
        inline=False,
    )

    embed.set_footer(text="League of Nations · Milletler Cemiyeti Hard-RP (TR / EN)")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="otomatik-kurulum", description="Tek komutta tüm MC-KS-001 rollerini, metin/ses kanallarını, izinleri ve panelleri kurar")
@discord.app_commands.default_permissions(administrator=True)
@discord.app_commands.describe(eski_odalar="Önceki boş/varsayılan odaların ve boş kategorilerin nasıl düzenleneceğini seçin")
@discord.app_commands.choices(
    eski_odalar=[
        discord.app_commands.Choice(name="🧹 Boş Eski Odaları Temizle & Düzenle (Önerilen)", value="clean"),
        discord.app_commands.Choice(name="📦 Tüm Eski Odalara Dokunma (Sadece Ekle/Güncelle)", value="keep"),
    ]
)
async def cmd_auto_setup(interaction: discord.Interaction, eski_odalar: discord.app_commands.Choice[str] = None):
    await interaction.response.defer(ephemeral=True)
    clean_mode = (eski_odalar.value if eski_odalar else "clean") == "clean"
    result = await run_auto_setup(interaction.guild, clean_empty_legacy=clean_mode)
    ch = result["ch_by_key"]

    embed = discord.Embed(
        title="✅ Milletler Cemiyeti Tek-Komut Kurulumu Tamamlandı!",
        description=(
            f"**Kurulan/Güncellenen Roller:** `{result['roles_count']}` adet `MC-KS-001` rolü\n"
            f"**Duyuru:** {ch['duyurular-announcements'].mention if 'duyurular-announcements' in ch else '—'} | "
            f"**Resmî Gazete:** {ch['resmi-gazete-official-gazette'].mention if 'resmi-gazete-official-gazette' in ch else '—'} | "
            f"**Denetim Kaydı:** {ch['denetim-kaydi-audit-log'].mention if 'denetim-kaydi-audit-log' in ch else '—'} | "
            f"**Ticket Log:** {ch['ticket-log-archive'].mention if 'ticket-log-archive' in ch else '—'}"
        ),
        colour=0x10B981,
    )
    embed.add_field(name="💬 Metin Kanalları", value="\n".join(result["text_summary"])[:1024] or "*Yok*", inline=True)
    embed.add_field(name="🔊 Ses Kanalları", value="\n".join(result["voice_summary"])[:1024] or "*Yok*", inline=True)
    embed.add_field(name="🎛️ Otomatik Sabitlenen Paneller", value=", ".join(result["panels_status"])[:1024] or "*Yok*", inline=False)
    if result["cleaned"]:
        embed.add_field(name="🧹 Temizlenen Boş Eski Odalar/Kategoriler", value=", ".join(result["cleaned"][:20])[:1024], inline=False)
    if result["preserved"]:
        embed.add_field(name="📦 Mesaj İçerdiği İçin Korunan Diğer Odalar", value=", ".join(result["preserved"][:20])[:1024], inline=False)
    await interaction.followup.send(embed=embed, ephemeral=True)


def main():
    bot.run(TOKEN)


if __name__ == "__main__":
    main()
