import uuid
from datetime import datetime
import discord
from discord import app_commands
from discord.ext import commands

from utils.storage import (
    ambassador_appointments_storage,
    delegates_storage,
    embassies_storage,
)
from utils.constants import can_member_represent_state_diplomacy
from utils.un_gazette import (
    get_country_record,
    save_country_record,
    normalize_country_key,
    publish_to_gazette,
)


def _slug(name: str) -> str:
    tr_map = str.maketrans("çğıöşüÇĞİÖŞÜ ", "cgiosuCGIOSU-")
    cleaned = "".join(c for c in name.strip().translate(tr_map).lower() if c.isalnum() or c == "-")
    return cleaned.strip("-")[:25] or "ulke"


def _find_country_channel(guild: discord.Guild, nation_key: str) -> discord.TextChannel | None:
    data = embassies_storage.read()
    # Prefer Permanent Mission, fallback to Consulate
    for item in data.values():
        if item.get("guild_id") == guild.id and item.get("active", True):
            if item.get("type") == "mission" and item.get("nation_key") == nation_key:
                ch = guild.get_channel(int(item["channel_id"]))
                if ch:
                    return ch
    for item in data.values():
        if item.get("guild_id") == guild.id and item.get("active", True):
            if item.get("type") == "consulate" and item.get("nation_key") == nation_key:
                ch = guild.get_channel(int(item["channel_id"]))
                if ch:
                    return ch
    return None


async def _get_or_create_category(guild: discord.Guild, name: str) -> discord.CategoryChannel:
    cat = discord.utils.get(guild.categories, name=name)
    if not cat:
        cat = await guild.create_category(name)
    return cat


class CloseEmbassyView(discord.ui.View):
    def __init__(self, record_id: str):
        super().__init__(timeout=None)
        self.record_id = record_id

        btn = discord.ui.Button(
            label="Elçiliği Kapat · Close Embassy",
            style=discord.ButtonStyle.danger,
            emoji="🚫",
            custom_id=f"mc_emb_close:{record_id}",
        )
        btn.callback = self.btn_close
        self.add_item(btn)

    async def btn_close(self, interaction: discord.Interaction):
        rec = embassies_storage.get(self.record_id)
        if not rec or not rec.get("active", True):
            return await interaction.response.send_message("⚠️ Bu temsilcilik zaten kapalı. / Already closed.", ephemeral=True)

        user_data = delegates_storage.get(str(interaction.user.id), {})
        user_key = normalize_country_key(user_data.get("nation", ""))
        allowed_keys = {rec.get("nation_key"), rec.get("target_key")}
        if not interaction.user.guild_permissions.administrator and (
            user_key not in allowed_keys or not can_member_represent_state_diplomacy(interaction.user, user_data)
        ):
            return await interaction.response.send_message(
                "❌ Elçiliği yalnızca taraf devletlerin yetkili diplomatik temsilcileri (**Başkan, Bakanlar, Temsilciler, Büyükelçi**) veya yöneticiler kapatabilir; sade **Vatandaşlar (`Vatandaş`)** kapatamaz. / Authorized state diplomats or Admin only.",
                ephemeral=True,
            )

        rec["active"] = False
        rec["closed_by"] = user_data.get("nation", interaction.user.display_name)
        rec["closed_at"] = datetime.utcnow().isoformat()
        embassies_storage.set(self.record_id, rec)

        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content=f"🚫 **İLİŞKİ KESİLDİ / EMBASSY CLOSED:** {rec['closed_by']}",
            view=self,
        )

        await publish_to_gazette(
            interaction.client,
            interaction.guild_id,
            "ELÇİLİK KAPANIŞI · EMBASSY CLOSED",
            f"{rec.get('nation')} ↔ {rec.get('target_nation', '')}",
            f"**Kapatan / Closed by:** `{rec['closed_by']}`\nİkili büyükelçilik kapatıldı. / Bilateral embassy terminated.",
            color=0xEF4444,
        )


class NoteModal(discord.ui.Modal, title="Diplomatik Nota · Diplomatic Note"):
    subject = discord.ui.TextInput(
        label="Nota Konusu / Note Subject",
        placeholder="Örn / e.g.: Sınır İhlali / Border Protest or Trade Offer",
        required=True,
        max_length=120,
    )
    body = discord.ui.TextInput(
        label="Nota Metni / Diplomatic Note Body",
        style=discord.TextStyle.paragraph,
        placeholder="Resmî notanızı yazın / Enter your formal diplomatic cable...",
        required=True,
        max_length=1800,
    )

    def __init__(self, sender_nation: str, target_nation: str, note_type: str, is_public: bool):
        super().__init__()
        self.sender_nation = sender_nation
        self.target_nation = target_nation
        self.note_type = note_type
        self.is_public = is_public

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        sender_rec = get_country_record(self.sender_nation)
        target_rec = get_country_record(self.target_nation)
        sender_rec["stats"]["notes_sent"] += 1
        target_rec["stats"]["notes_received"] += 1
        save_country_record(sender_rec)
        save_country_record(target_rec)

        note_no = f"NOTA-{datetime.utcnow().year}/{str(uuid.uuid4())[:6].upper()}"
        visibility_tag = "🌐 AÇIK NOTA (Resmi Gazete)" if self.is_public else "🔒 KRİPTOLU / GİZLİ NOTA"
        color = 0xEF4444 if "Protesto" in self.note_type or "Ültimatom" in self.note_type else 0x1E3A8A

        embed = discord.Embed(
            title=f"📨 {self.note_type.upper()} · {note_no}",
            description=(
                f"**Gönderen:** `{self.sender_nation}` (<@{interaction.user.id}>)\n"
                f"**Muhatap:** `{self.target_nation}`\n"
                f"**Gizlilik:** `{visibility_tag}`\n"
                f"**Konu:** **{self.subject.value.strip()}**\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"{self.body.value.strip()}"
            ),
            colour=color,
        )
        embed.set_footer(text=f"Diplomatic Cable · {datetime.utcnow().strftime('%d.%m.%Y %H:%M UTC')}")

        target_ch = _find_country_channel(interaction.guild, target_rec["key"])
        delivered_msg = ""
        if target_ch:
            await target_ch.send(embed=embed)
            delivered_msg = f"Hedef temsilcilik kanalı (<#{target_ch.id}>) üzerinden iletildi."
        else:
            await interaction.channel.send(embed=embed)
            delivered_msg = "Hedef ülkenin özel kanalı bulunmadığı için bu kanala iletildi."

        if self.is_public:
            await publish_to_gazette(
                interaction.client,
                interaction.guild_id,
                f"AÇIK DİPLOMATİK NOTA ({self.note_type})",
                f"{self.sender_nation} ➔ {self.target_nation}: {self.subject.value.strip()}",
                self.body.value.strip()[:1200],
                fields=[("Nota No", f"`{note_no}`", True), ("Gönderen", self.sender_nation, True), ("Muhatap", self.target_nation, True)],
                color=color,
            )

        await interaction.followup.send(f"✅ **{note_no}** gönderildi. {delivered_msg}", ephemeral=True)


class EmbassiesCog(commands.Cog):
    """UN Permanent Missions, Bilateral Embassies, Optional Consulates & Diplomatic Notes."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        for rid, rec in embassies_storage.read().items():
            if isinstance(rec, dict) and rec.get("type") == "embassy" and rec.get("active", True):
                self.bot.add_view(CloseEmbassyView(rid))

    @app_commands.command(name="temsilcilik-ac", description="Cemiyet Daimi Temsilciliği, İkili Büyükelçilik veya Konsolosluk kanalı açar")
    @app_commands.describe(
        tur="Açılacak temsilcilik türü",
        hedef_ulke="İkili Büyükelçilik ise muhatap ülke adı (diğerlerinde boş bırakabilirsiniz)",
    )
    @app_commands.choices(tur=[
        app_commands.Choice(name="🏛️ Cemiyet Daimi Temsilciliği (Ülke Karargâhı)", value="mission"),
        app_commands.Choice(name="🤝 İkili Büyükelçilik (İki Ülke Arası)", value="embassy"),
        app_commands.Choice(name="🛂 Konsolosluk (Vatandaş / Başvuru / Deport Bildirimi)", value="consulate"),
    ])
    async def cmd_open_mission(
        self,
        interaction: discord.Interaction,
        tur: app_commands.Choice[str],
        hedef_ulke: str = None,
    ):
        await interaction.response.defer(ephemeral=True)
        user_data = delegates_storage.get(str(interaction.user.id))
        if not user_data or not user_data.get("nation"):
            return await interaction.followup.send(
                "❌ Temsilcilik açabilmek için önce Cemiyet Akreditasyonundan geçmelisiniz.",
                ephemeral=True,
            )
        if not interaction.user.guild_permissions.administrator and not can_member_represent_state_diplomacy(
            interaction.user,
            user_data,
            allow_consulate=(tur.value == "consulate"),
        ):
            return await interaction.followup.send(
                "⛔ **Yetki Yetersiz · Diplomatic Authority Required**\n"
                "Resmî elçilik / daimî temsilcilik / konsolosluk açma yetkisi yalnızca yetkili devlet ve diplomasi makamlarına "
                "(**Devlet Başkanı, Bakanlar, Asil/Yedek Temsilciler, Büyükelçi**; konsolosluk için ayrıca **Başkonsolos**) aittir. "
                "Sade **Vatandaşlar (`Vatandaş`)** temsilcilik açamaz.",
                ephemeral=True,
            )

        nation = user_data["nation"]
        nation_key = normalize_country_key(nation)
        guild = interaction.guild

        if tur.value == "embassy":
            if not hedef_ulke:
                return await interaction.followup.send("❌ İkili Büyükelçilik açmak için `hedef_ulke` belirtmelisiniz.", ephemeral=True)
            target_key = normalize_country_key(hedef_ulke)
            if target_key == nation_key:
                return await interaction.followup.send("❌ Kendi ülkenizle ikili elçilik açamazsınız.", ephemeral=True)

            cat = await _get_or_create_category(guild, "🤝 İKİLİ BÜYÜKELÇİLİKLER · BILATERAL EMBASSIES")
            ch_name = f"🤝│{_slug(nation)}-{_slug(hedef_ulke)}"
            ch = await guild.create_text_channel(
                ch_name,
                category=cat,
                topic=f"{nation} ↔ {hedef_ulke.strip()} İkili Büyükelçilik ve Diplomasi Odası",
            )
            rec_id = f"EMB-{str(uuid.uuid4())[:6].upper()}"
            embassies_storage.set(rec_id, {
                "id": rec_id,
                "type": "embassy",
                "guild_id": guild.id,
                "channel_id": ch.id,
                "nation": nation,
                "nation_key": nation_key,
                "target_nation": hedef_ulke.strip(),
                "target_key": target_key,
                "active": True,
                "created_at": datetime.utcnow().isoformat(),
            })
            embed = discord.Embed(
                title=f"🤝 {nation} ↔ {hedef_ulke.strip()} BÜYÜKELÇİLİĞİ AÇILDI",
                description=(
                    f"İki ülke arasındaki resmi diplomatik ilişkiler ve ikili görüşmeler için oluşturulmuştur.\n"
                    f"**Açan Temsilci:** <@{interaction.user.id}> (`{nation}`)"
                ),
                colour=0x10B981,
            )
            view = CloseEmbassyView(rec_id)
            self.bot.add_view(view)
            await ch.send(embed=embed, view=view)
            await publish_to_gazette(
                self.bot,
                guild.id,
                "BÜYÜKELÇİLİK AÇILIŞI",
                f"{nation} ↔ {hedef_ulke.strip()} Büyükelçiliği",
                f"**{nation}** ile **{hedef_ulke.strip()}** arasında ikili büyükelçilik faaliyete geçmiştir.",
                color=0x10B981,
            )
            return await interaction.followup.send(f"✅ İkili büyükelçilik açıldı: <#{ch.id}>", ephemeral=True)

        elif tur.value == "consulate":
            cat = await _get_or_create_category(guild, "🛂 KONSOLOSLUKLAR · CONSULATES")
            ch_name = f"🛂│{_slug(nation)}-konsoloslugu"
            ch = await guild.create_text_channel(
                ch_name,
                category=cat,
                topic=f"{nation} Konsolosluğu — Vatandaşlık, Vize, Çalışma/Deport Bildirim ve Başvuru Masası",
            )
            rec_id = f"CON-{str(uuid.uuid4())[:6].upper()}"
            embassies_storage.set(rec_id, {
                "id": rec_id,
                "type": "consulate",
                "guild_id": guild.id,
                "channel_id": ch.id,
                "nation": nation,
                "nation_key": nation_key,
                "active": True,
                "created_at": datetime.utcnow().isoformat(),
            })
            embed = discord.Embed(
                title=f"🛂 {nation.upper()} KONSOLOSLUĞU",
                description=(
                    f"**{nation}** Konsolosluğu hizmete açılmıştır.\n\n"
                    f"• Oyun içi (`parlamentum.org`) vatandaşlık, vize, çalışma izni veya **deport/sınır dışı** durumlarınızı buradan doğrudan yazarak konsolosluğa bildirebilirsiniz.\n"
                    f"**Açan Yetkili:** <@{interaction.user.id}>"
                ),
                colour=0x1E3A8A,
            )
            await ch.send(embed=embed)
            return await interaction.followup.send(f"✅ Konsolosluk kanalı açıldı: <#{ch.id}>", ephemeral=True)

        else:  # mission
            cat = await _get_or_create_category(guild, "🏛️ MC DAİMİ TEMSİLCİLİKLERİ · MISSIONS")
            ch_name = f"🏛️│{_slug(nation)}-temsilciligi"
            ch = await guild.create_text_channel(
                ch_name,
                category=cat,
                topic=f"{nation} Milletler Cemiyeti Daimi Temsilciliği",
            )
            rec_id = f"MIS-{str(uuid.uuid4())[:6].upper()}"
            embassies_storage.set(rec_id, {
                "id": rec_id,
                "type": "mission",
                "guild_id": guild.id,
                "channel_id": ch.id,
                "nation": nation,
                "nation_key": nation_key,
                "active": True,
                "created_at": datetime.utcnow().isoformat(),
            })
            embed = discord.Embed(
                title=f"🏛️ {nation.upper()} MC DAİMİ TEMSİLCİLİĞİ",
                description=f"**{nation}** ülkesinin Milletler Cemiyeti nezdindeki resmi temsilciliği ve nota adresi.\n**Yetkili:** <@{interaction.user.id}>",
                colour=0xD97706,
            )
            await ch.send(embed=embed)
            return await interaction.followup.send(f"✅ Cemiyet Daimi Temsilciliği açıldı: <#{ch.id}>", ephemeral=True)

    @app_commands.command(name="nota-gonder", description="Başka bir ülkeye Açık veya Kriptolu (Gizli) resmi diplomatik nota gönderir")
    @app_commands.describe(hedef_ulke="Notanın muhatabı olan ülke", tur="Nota türü", gizlilik="Açık nota Resmi Gazete'de de yayınlanır")
    @app_commands.choices(
        tur=[
            app_commands.Choice(name="📜 Nota Verbale (Standart Resmi Nota)", value="Nota Verbale"),
            app_commands.Choice(name="⚠️ Protesto / Kınama Notası", value="Protesto Notası"),
            app_commands.Choice(name="🚨 Ültimatom / Sert Uyarı Notası", value="Ültimatom"),
            app_commands.Choice(name="🤝 İş Birliği / Antlaşma Teklifi", value="İş Birliği Teklifi"),
            app_commands.Choice(name="🕊️ Tebrik / İyi Niyet Notası", value="İyi Niyet Notası"),
        ],
        gizlilik=[
            app_commands.Choice(name="🌐 Açık (Hedef Temsilcilik + Cemiyet Resmi Gazetesi)", value="public"),
            app_commands.Choice(name="🔒 Kriptolu / Gizli (Yalnızca Hedef Temsilcilik)", value="secret"),
        ],
    )
    async def cmd_send_note(
        self,
        interaction: discord.Interaction,
        hedef_ulke: str,
        tur: app_commands.Choice[str],
        gizlilik: app_commands.Choice[str],
    ):
        user_data = delegates_storage.get(str(interaction.user.id))
        if not user_data or not user_data.get("nation"):
            return await interaction.response.send_message(
                "❌ Diplomatik nota göndermek için önce akreditasyon almalısınız.",
                ephemeral=True,
            )
        if not interaction.user.guild_permissions.administrator and not can_member_represent_state_diplomacy(
            interaction.user,
            user_data,
        ):
            return await interaction.response.send_message(
                "⛔ **Yetki Yetersiz · Diplomatic Authority Required**\n"
                "Devlet adına resmî diplomatik nota, demarş veya ültimatom gönderme yetkisi yalnızca "
                "**Devlet Başkanı, Bakanlar, Asil/Yedek Temsilciler ve Büyükelçilere** aittir; "
                "sade **Vatandaşlar (`Vatandaş`)** nota gönderemez.",
                ephemeral=True,
            )

        await interaction.response.send_modal(
            NoteModal(
                sender_nation=user_data["nation"],
                target_nation=hedef_ulke.strip(),
                note_type=tur.value,
                is_public=(gizlilik.value == "public"),
            )
        )

    @app_commands.command(name="temsilcilikler", description="Sunucudaki Cemiyet Daimi Temsilcilikleri, Büyükelçilikler ve Konsoloslukları listeler")
    @app_commands.describe(sayfa="Gösterilecek sayfa numarası / Page number")
    async def cmd_list_missions(
        self,
        interaction: discord.Interaction,
        sayfa: app_commands.Range[int, 1, 100000] = 1,
    ):
        data = embassies_storage.read()
        guild_items = [v for v in data.values() if v.get("guild_id") == interaction.guild_id and v.get("active", True)]
        active_ambassadors = [
            record
            for record in ambassador_appointments_storage.read().values()
            if isinstance(record, dict)
            and record.get("status") == "active"
            and str(record.get("guild_id")) == str(interaction.guild_id)
        ]
        if not guild_items and not active_ambassadors:
            return await interaction.response.send_message("ℹ️ Henüz açılmış bir temsilcilik, büyükelçilik veya konsolosluk yok.", ephemeral=True)

        def short_name(value: object, max_length: int = 55) -> str:
            name = str(value or "")
            return name if len(name) <= max_length else name[: max_length - 1] + "…"

        missions = [
            f"• **{short_name(i.get('nation'))}** — <#{i['channel_id']}>"
            for i in guild_items if i.get("type") == "mission"
        ]
        embassies = [
            f"• **{short_name(i.get('nation'), 35)} ↔ {short_name(i.get('target_nation'), 35)}** — <#{i['channel_id']}>"
            for i in guild_items if i.get("type") == "embassy"
        ]
        consulates = [
            f"• **{short_name(i.get('nation'))} Konsolosluğu** — <#{i['channel_id']}>"
            for i in guild_items if i.get("type") == "consulate"
        ]
        page_size = 7
        page_count = max(
            1,
            (max(len(missions), len(embassies), len(consulates)) + page_size - 1)
            // page_size,
        )
        if sayfa > page_count:
            return await interaction.response.send_message(
                f"❌ Bu rehberde yalnızca {page_count} sayfa var.", ephemeral=True
            )
        start = (sayfa - 1) * page_size

        def safe_section(items: list[str]) -> str:
            page_items = items[start : start + page_size]
            return "\n".join(page_items) if page_items else "*Yok*"

        embed = discord.Embed(
            title="🌐 MİLLETLER CEMİYETİ DİPLOMATİK TEMSİLCİLİK REHBERİ",
            colour=0x1E3A8A,
        )
        embed.add_field(name="🏛️ Cemiyet Daimi Temsilcilikleri", value=safe_section(missions), inline=False)
        embed.add_field(name="🤝 İkili Büyükelçilikler", value=safe_section(embassies), inline=False)
        embed.add_field(name="🛂 Konsolosluklar", value=safe_section(consulates), inline=False)
        if not active_ambassadors:
            embed.add_field(
                name="🏛️ Etkin Büyükelçi Atamaları",
                value="*Yok*",
                inline=False,
            )
        else:
            ambassador_lines = [
                f"• **{record.get('home_nation')} → {record.get('host_nation')}** — "
                f"<@{record.get('candidate_id')}> · izinler aktif · `{record.get('id')}`"
                for record in active_ambassadors[:4]
            ]
            ambassador_lines = [line[:200] for line in ambassador_lines]
            if len(active_ambassadors) > 4:
                ambassador_lines.append(
                    f"• +{len(active_ambassadors) - 4} atama — `/buyukelciler` komutu"
                )
            embed.add_field(
                name=f"🏛️ Etkin Büyükelçi Atamaları ({len(active_ambassadors)})",
                value="\n".join(ambassador_lines),
                inline=False,
            )
        embed.set_footer(text=f"Sayfa {sayfa}/{page_count}")
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(EmbassiesCog(bot))
