import discord
from discord import app_commands
from discord.ext import commands
from utils.storage import resolutions_storage
from utils.embed_factory import resolution_archive_embed

class ResolutionsCog(commands.Cog):
    """Archived resolutions search and directory cog."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="karar-goruntule", description="Arşivlenmiş bir resmi kararı görüntüler / View resolution by ID")
    async def cmd_view_res(self, interaction: discord.Interaction, karar_no: str):
        all_res = resolutions_storage.read()
        res_data = all_res.get(karar_no.strip().upper())

        if not res_data:
            # Try fuzzy search
            for k, v in all_res.items():
                if karar_no.lower() in k.lower() or karar_no.lower() in v.get("title", "").lower():
                    res_data = v
                    break

        if not res_data:
            return await interaction.response.send_message(
                f"❌ `{karar_no}` numaralı karar arşivde bulunamadı. / Resolution not found.",
                ephemeral=True,
            )

        embed = resolution_archive_embed(res_data)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="kararlar-listesi", description="Kabul edilen kararları listeler / List adopted resolutions")
    async def cmd_list_res(self, interaction: discord.Interaction):
        all_res = resolutions_storage.read()
        if not all_res:
            return await interaction.response.send_message(
                "📜 Henüz kabul edilmiş bir karar bulunmuyor. / No adopted resolutions recorded yet.",
                ephemeral=True,
            )

        embed = discord.Embed(
            title="📜 Kabul Edilen Kararlar · Adopted Resolutions",
            description="Genel Kurul kararlarının resmî fihristi / Official General Assembly resolution register:\n\n",
            colour=0x10B981
        )

        for res_id, r in list(all_res.items())[-10:]:
            embed.add_field(
                name=f"⚖️ {res_id} — {r.get('title')}",
                value=(
                    f"**Tarih / Date:** `{r.get('timestamp')}` | **Sunan / Sponsor:** {r.get('author')}\n"
                    f"**Sonuç / Tally:** 🟩 {r.get('yes', 0)} Kabul/Yes · 🟥 {r.get('no', 0)} Ret/No"
                ),
                inline=False
            )

        embed.set_footer(text="Kararı açmak için / To read full text: /karar-goruntule [No]")
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(ResolutionsCog(bot))
