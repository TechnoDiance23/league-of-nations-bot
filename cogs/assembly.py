import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime
from typing import Dict, Any, List

from utils.storage import assembly_storage, delegates_storage
from utils.constants import (
    ROLE_ADMIN,
    ROLE_COUNCIL_CHAIR,
    ROLE_GA_PRESIDENT,
    ROLE_SG,
    can_member_speak_in_assembly,
    resolve_panel_channel,
)
from utils.un_gazette import upsert_panel_message
from utils.embed_factory import speakers_list_embed


def _is_assembly_chair(member: discord.Member) -> bool:
    if not isinstance(member, discord.Member):
        return False
    if member.guild_permissions.administrator or member.guild_permissions.manage_guild:
        return True
    if {r.name for r in member.roles} & {ROLE_SG, ROLE_GA_PRESIDENT, ROLE_COUNCIL_CHAIR, ROLE_ADMIN}:
        return True
    rec = delegates_storage.get(str(member.id), {})
    return rec.get("office_code") in {"SG", "DSG", "GKB", "GKB-V", "ŞB"}

class PointOfOrderModal(discord.ui.Modal, title="Usul Önergesi · Point of Order"):
    motion_type = discord.ui.TextInput(
        label="Önerge Türü / Motion Type",
        placeholder="Örn / e.g.: Usul İhlali / Procedural Point, Meclis Sorusu...",
        required=True,
        max_length=100
    )
    explanation = discord.ui.TextInput(
        label="Gerekçe / Grounds & Explanation",
        style=discord.TextStyle.paragraph,
        placeholder="Talep edilen usul kararını açıklayın / Explain the procedural request...",
        required=True,
        max_length=1000
    )

    async def on_submit(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        u_data = delegates_storage.get(user_id, {})
        nation = u_data.get("nation", "Bağımsız Delege / Independent")

        embed = discord.Embed(
            title=f"⚖️ MECLİS USUL ÖNERGESİ · POINT OF ORDER",
            description=(
                f"**Önergeyi Veren / Delegate:** {interaction.user.mention} (**{nation}**)\n"
                f"**Tür / Type:** `{self.motion_type.value.strip()}`\n\n"
                f"**Açıklama / Grounds:**\n"
                f"> {self.explanation.value.strip()}\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"*Divan incelemeye aldı. / Referred to the Chair.*"
            ),
            colour=0xF59E0B,
            timestamp=datetime.utcnow()
        )
        await interaction.channel.send(embed=embed)
        await interaction.response.send_message("✅ Usul önergeniz iletildi. / Point of order submitted.", ephemeral=True)


class SpeakersListView(discord.ui.View):
    """Interactive control view for speakers' floor and queue."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Söz İste · Request Floor", emoji="✋", style=discord.ButtonStyle.primary, custom_id="spk_request_floor")
    async def btn_request_floor(self, interaction: discord.Interaction, _button: discord.ui.Button):
        user_id = str(interaction.user.id)
        queue = assembly_storage.get("speakers_queue", [])
        current = assembly_storage.get("current_speaker")

        if current and current.get("user_id") == user_id:
            return await interaction.response.send_message("🎤 Zaten kürsüdesiniz! / You already hold the floor!", ephemeral=True)

        if any(item["user_id"] == user_id for item in queue):
            return await interaction.response.send_message("⏳ Zaten sıradasınız. / You are already in the queue.", ephemeral=True)

        u_data = delegates_storage.get(user_id, {})
        if not (can_member_speak_in_assembly(interaction.user, u_data) or _is_assembly_chair(interaction.user)):
            return await interaction.response.send_message(
                "⛔ **Yetki Yetersiz · Accredited Speaker Authority Required**\n"
                "Genel Kurul kürsüsünde söz isteme yetkisi yalnızca akredite devlet/meclis yetkililerine "
                "(**Başkan, Bakanlar, Temsilciler, Büyükelçi, Milletvekili, Divan**) aittir; "
                "sade **Vatandaşlar (`Vatandaş`)** veya **Gözlemciler** söz sırası alamaz.\n"
                "*Only accredited state/parliament officials may request the floor; Citizens and Observers cannot.*",
                ephemeral=True,
            )
        queue.append({
            "user_id": user_id,
            "user_name": interaction.user.display_name,
            "nation": u_data.get("nation", "Bağımsız / Independent"),
            "office": u_data.get("office", "Delege / Delegate"),
            "joined_at": datetime.utcnow().isoformat()
        })
        assembly_storage.set("speakers_queue", queue)

        embed = speakers_list_embed(queue, current)
        try:
            await interaction.message.edit(embed=embed)
        except Exception:
            pass

        await interaction.response.send_message(f"✅ Sıraya eklendiniz / Added to queue (#{len(queue)}).", ephemeral=True)

    @discord.ui.button(label="Sıradan Çık · Leave Queue", emoji="❌", style=discord.ButtonStyle.secondary, custom_id="spk_leave_queue")
    async def btn_leave_queue(self, interaction: discord.Interaction, _button: discord.ui.Button):
        user_id = str(interaction.user.id)
        queue = assembly_storage.get("speakers_queue", [])
        filtered = [item for item in queue if item["user_id"] != user_id]

        if len(filtered) == len(queue):
            return await interaction.response.send_message("❌ Sırada değilsiniz. / You are not in the queue.", ephemeral=True)

        assembly_storage.set("speakers_queue", filtered)
        current = assembly_storage.get("current_speaker")
        embed = speakers_list_embed(filtered, current)
        try:
            await interaction.message.edit(embed=embed)
        except Exception:
            pass
        await interaction.response.send_message("✅ Sıradan ayrıldınız. / Removed from queue.", ephemeral=True)

    @discord.ui.button(label="Sıradakine Söz Ver · Call Next", emoji="🎤", style=discord.ButtonStyle.success, custom_id="spk_call_next")
    async def btn_call_next(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if not _is_assembly_chair(interaction.user):
            return await interaction.response.send_message("❌ Yalnızca Genel Kurul / Divan Başkanı çağırabilir. / Chair or Admin only.", ephemeral=True)

        queue = assembly_storage.get("speakers_queue", [])
        if not queue:
            return await interaction.response.send_message("❌ Sırada kimse yok. / Queue is empty.", ephemeral=True)

        next_speaker = queue.pop(0)
        assembly_storage.set("speakers_queue", queue)
        assembly_storage.set("current_speaker", next_speaker)

        embed = speakers_list_embed(queue, next_speaker)
        try:
            await interaction.message.edit(embed=embed)
        except Exception:
            pass

        await interaction.channel.send(
            f"📢 **KÜRSÜ VERİLDİ / FLOOR GRANTED**\n"
            f"<@{next_speaker['user_id']}> (**{next_speaker.get('nation')}** · {next_speaker.get('office')}), söz sizindir! / You now hold the floor!"
        )
        await interaction.response.send_message("✅ Sıradaki delege çağrıldı. / Next speaker called.", ephemeral=True)

    @discord.ui.button(label="Kürsüyü Boşalt · Yield Floor", emoji="⏹️", style=discord.ButtonStyle.secondary, custom_id="spk_yield_floor")
    async def btn_yield_floor(self, interaction: discord.Interaction, _button: discord.ui.Button):
        current = assembly_storage.get("current_speaker")
        if not current:
            return await interaction.response.send_message("❌ Kürsü zaten boş. / Floor is already open.", ephemeral=True)

        is_current = current.get("user_id") == str(interaction.user.id)
        is_chair = _is_assembly_chair(interaction.user)
        if not (is_current or is_chair):
            return await interaction.response.send_message("❌ Yalnızca kürsüdeki delege veya divan boşaltabilir. / Speaker or Chair only.", ephemeral=True)

        assembly_storage.set("current_speaker", None)
        queue = assembly_storage.get("speakers_queue", [])
        embed = speakers_list_embed(queue, None)
        try:
            await interaction.message.edit(embed=embed)
        except Exception:
            pass

        await interaction.channel.send(f"⏹️ <@{current['user_id']}> sözünü tamamladı / yielded the floor.")
        await interaction.response.send_message("✅ Kürsü boşaltıldı. / Floor yielded.", ephemeral=True)

    @discord.ui.button(label="Usul Önergesi · Point of Order", emoji="⚖️", style=discord.ButtonStyle.danger, custom_id="spk_point_of_order")
    async def btn_point_of_order(self, interaction: discord.Interaction, _button: discord.ui.Button):
        u_data = delegates_storage.get(str(interaction.user.id), {})
        if not (can_member_speak_in_assembly(interaction.user, u_data) or _is_assembly_chair(interaction.user)):
            return await interaction.response.send_message(
                "⛔ **Yetki Yetersiz · Parliamentary Authority Required**\n"
                "Usul önergesi sunma yetkisi yalnızca akredite devlet/meclis yetkililerine aittir; "
                "sade **Vatandaşlar (`Vatandaş`)** önerge veremez.",
                ephemeral=True,
            )
        await interaction.response.send_modal(PointOfOrderModal())


def build_speakers_panel() -> tuple[discord.Embed, discord.ui.View]:
    queue = assembly_storage.get("speakers_queue", [])
    current = assembly_storage.get("current_speaker")
    return speakers_list_embed(queue, current), SpeakersListView()


class AssemblyCog(commands.Cog):
    """Parliamentary procedure & speakers' floor management."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="konusmaci-paneli-kur", description="Meclis konuşmacı kürsüsü ve sıra panelini kurar / Setup speakers floor")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(kanal="İsteğe bağlı hedef kanal (boş bırakılırsa otomatik kürsü kanalına kurulur)")
    async def cmd_setup_speakers(self, interaction: discord.Interaction, kanal: discord.TextChannel = None):
        target = resolve_panel_channel(interaction.guild, kanal, "konusmaci-kursusu-speakers", interaction.channel)
        embed, view = build_speakers_panel()
        st = await upsert_panel_message(target, ["🎙️ Konuşmacı Kürsüsü", "🎙️ Cemiyet Konuşmacı Kürsüsü"], embed, view)
        await interaction.response.send_message(f"✅ Konuşmacı kürsüsü paneli {target.mention} kanalında güncellendi (`{st}`).", ephemeral=True)

    @app_commands.command(name="mola-onergesi", description="Mecliste lobi/mola oturumu önergesi açar / Motion for caucus")
    async def cmd_caucus(self, interaction: discord.Interaction, dakika: int, konu: str):
        u_data = delegates_storage.get(str(interaction.user.id), {})
        if not (can_member_speak_in_assembly(interaction.user, u_data) or _is_assembly_chair(interaction.user)):
            return await interaction.response.send_message(
                "⛔ **Yetki Yetersiz · Parliamentary Authority Required**\n"
                "Mola / istişare önergesi açma yetkisi yalnızca akredite devlet/meclis yetkililerine aittir; "
                "sade **Vatandaşlar (`Vatandaş`)** önerge açamaz.",
                ephemeral=True,
            )
        embed = discord.Embed(
            title="☕ MECLİS DANIŞMA & MOLA ÖNERGESİ · MOTION FOR CAUCUS",
            description=(
                f"**Teklif Eden / Delegate:** {interaction.user.mention}\n"
                f"**Talep Edilen Süre / Duration:** `{dakika} Dakika / Minutes`\n"
                f"**Görüşme Konusu / Topic:** {konu}\n\n"
                f"Meclis divanı oturuma ara verilmesi veya gayriresmi istişareye geçilmesi yönündeki önergeyi oylamaya sunabilir."
            ),
            colour=0x3B82F6,
            timestamp=datetime.utcnow()
        )
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    bot.add_view(SpeakersListView())
    cog = AssemblyCog(bot)
    await bot.add_cog(cog)
    bot.add_view(SpeakersListView())
