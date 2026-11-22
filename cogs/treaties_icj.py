from datetime import datetime
import re
import discord
from discord import app_commands
from discord.ext import commands

from utils.storage import delegates_storage, treaties_storage, icj_storage, precedents_storage, prosecution_storage, gazette_storage
from utils.constants import (
    ROLE_ADMIN,
    ROLE_CHIEF_PROSECUTOR,
    ROLE_ICJ_PRESIDENT,
    ROLE_JUDGE,
    ROLE_PROSECUTOR,
    ROLE_SG,
    ROLE_SPOKESPERSON,
    can_member_represent_state_diplomacy,
    can_member_sign_for_state,
    resolve_panel_channel,
)
from utils.un_gazette import (
    get_country_record,
    save_country_record,
    normalize_country_key,
    publish_to_gazette,
    upsert_panel_message,
    move_court_verdict_to_archive,
)

TREATY_TYPES = {
    "trade": ("📦 Serbest Ticaret · Free Trade Pact", 0xD97706),
    "alliance": ("🛡️ İttifak & Savunma · Defense Alliance", 0x1E3A8A),
    "nonaggression": ("🕊️ Saldırmazlık · Non-Aggression Pact", 0x10B981),
    "peace": ("⚖️ Barış & Sınır · Peace & Border Treaty", 0x6D28D9),
    "multilateral": ("📜 Çok Taraflı · Multilateral Convention", 0x0EA5E9),
}

PRECEDENT_CATEGORIES = {
    "treaty": ("📜 Antlaşma & Yazılı Şekil · Treaty Law", 0x1E3A8A),
    "evidence": ("📎 Delil & İspat Hukuku · Evidence Standard", 0x0284C7),
    "reparations": ("💳 Tazminat & Yaptırım · Reparations & Remedies", 0xD97706),
    "sovereignty": ("🛡️ Egemenlik & Sınır · Sovereignty & Borders", 0x10B981),
    "procedure": ("⚖️ Yargılama Usulü & İçtihat · Procedural Rule", 0x6D28D9),
}

PRECEDENT_ID_RE = re.compile(r"\b(EMS-\d{4}/\d{3})\b", re.IGNORECASE)


def _extract_precedent_refs(text: str | None) -> list[str]:
    """Extract unique uppercase EMS-YYYY/XXX references cited in free text."""
    seen: list[str] = []
    for match in PRECEDENT_ID_RE.findall(text or ""):
        code = match.upper()
        if code not in seen:
            seen.append(code)
    return seen


def _increment_precedent_citations(refs: list[str]) -> list[str]:
    """Increment citation counter for existing active precedents referenced in a filing or verdict."""
    linked: list[str] = []
    for code in refs:
        rec = precedents_storage.get(code)
        if isinstance(rec, dict) and rec.get("status", "ACTIVE") == "ACTIVE":
            rec["citations"] = int(rec.get("citations", 0) or 0) + 1
            precedents_storage.set(code, rec)
            linked.append(code)
    return linked


def _list_precedents(*, include_repealed: bool = False, category: str | None = None, query: str | None = None) -> list[dict]:
    items = [p for p in precedents_storage.read().values() if isinstance(p, dict) and p.get("id")]
    q = (query or "").strip().lower()
    out: list[dict] = []
    for p in items:
        if not include_repealed and p.get("status", "ACTIVE") != "ACTIVE":
            continue
        if category and p.get("category") != category:
            continue
        if q:
            hay = f"{p.get('id', '')} {p.get('title', '')} {p.get('principle', '')} {p.get('source_ref', '')}".lower()
            if q not in hay:
                continue
        out.append(p)
    out.sort(key=lambda r: r.get("id", ""), reverse=True)
    return out


def build_precedent_embed(prec: dict) -> discord.Embed:
    """Build formal Court Precedent (Emsal Karar) archive embed."""
    cat_label, cat_col = PRECEDENT_CATEGORIES.get(prec.get("category", "procedure"), ("⚖️ Emsal İçtihat", 0x6D28D9))
    repealed = prec.get("status", "ACTIVE") != "ACTIVE"
    col = 0x6B7280 if repealed else cat_col
    badge = "~~MÜLGA / REPEALED~~" if repealed else "✅ YÜRÜRLÜKTE · BINDING PRECEDENT"
    embed = discord.Embed(
        title=f"📕 DİVAN EMSAL KARARI · COURT PRECEDENT ({prec['id']})",
        description=f"**Başlık / Title:** **{prec['title']}**\n**Durum / Status:** {badge}\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        colour=col,
    )
    embed.add_field(name="🏷️ Hukuk Dalı · Field", value=cat_label, inline=True)
    embed.add_field(name="📎 Kaynak Dosya · Ref", value=f"`{prec.get('source_ref') or 'Doğrudan İçtihat'}`", inline=True)
    embed.add_field(name="👨‍⚖️ Karar Yargıcı · Judge", value=f"<@{prec.get('judge_id', 0)}>", inline=True)
    embed.add_field(name="⚖️ Bağlayıcı Emsal İlke · Binding Principle", value=f"> {prec['principle'][:980]}", inline=False)
    if prec.get("rationale"):
        embed.add_field(name="🧾 Gerekçe & Uygulama Kapsamı · Rationale", value=f"{prec['rationale'][:980]}", inline=False)
    if repealed and prec.get("repeal_reason"):
        embed.add_field(
            name="🚫 İlga / Kaldırma Gerekçesi · Repealed",
            value=f"{prec['repeal_reason'][:900]} *({prec.get('repealed_by', '-')} · {prec.get('repealed_at', '-')})*",
            inline=False,
        )
    cites = int(prec.get("citations", 0) or 0)
    embed.set_footer(text=f"MC-KS-001 Yargı İçtihadı · Atıf: {cites} | {prec.get('created_at', '-')}")
    return embed


def build_precedents_list_embed(
    *,
    category: str | None = None,
    query: str | None = None,
    include_repealed: bool = False,
) -> discord.Embed:
    """Build categorized precedent roll embed for ICJ Court, Chambers, and /emsal-listesi."""
    rows = _list_precedents(include_repealed=include_repealed, category=category, query=query)
    title_cat = PRECEDENT_CATEGORIES[category][0] if category in PRECEDENT_CATEGORIES else "Tüm Dallar · All Fields"
    lines: list[str] = []
    for p in rows[:12]:
        cat_label, _ = PRECEDENT_CATEGORIES.get(p.get("category", "procedure"), ("⚖️ İçtihat", 0x6D28D9))
        st_tag = "~~[MÜLGA]~~ " if p.get("status", "ACTIVE") != "ACTIVE" else ""
        ref_txt = f" · Kaynak: `{p['source_ref']}`" if p.get("source_ref") else ""
        cites = int(p.get("citations", 0) or 0)
        rule_short = (p.get("principle") or "").replace("\n", " ").strip()
        if len(rule_short) > 130:
            rule_short = rule_short[:127] + "..."
        lines.append(
            f"• {st_tag}**`{p['id']}`** — **{p['title']}** *({cat_label.split('·')[0].strip()}{ref_txt} · Atıf: `{cites}`)*\n"
            f"  ↳ {rule_short}"
        )
    desc = "\n".join(lines) if lines else "*Henüz kayıtlı emsal karar bulunmuyor · No recorded Court precedents yet.*"
    embed = discord.Embed(
        title=f"📕 DİVAN EMSAL KARARLAR SİCİLİ · PRECEDENTS ({title_cat})",
        description=desc[:4000],
        colour=0x7C3AED,
    )
    embed.set_footer(text=f"Toplam: {len(rows)} emsal karar · Yeni davalarda EMS-YYYY/XXX koduyla atıf yapılabilir.")
    return embed


async def _refresh_icj_and_chambers_panels(guild: discord.Guild | None) -> None:
    if not guild:
        return
    try:
        court_ch = resolve_panel_channel(guild, None, "yargi-divani-icj-court", None)
        if court_ch:
            e, v = build_icj_panel(guild)
            await upsert_panel_message(court_ch, ["⚖️ ULUSLARARASI YARGI DİVANI"], e, v)
    except Exception:
        pass
    try:
        chm_ch = resolve_panel_channel(guild, None, "divan-makami-chambers", None)
        if chm_ch:
            e, v = build_chambers_panel(guild)
            await upsert_panel_message(chm_ch, ["⚖️ DİVAN MAKAMI & YARGIÇLAR OFİSİ"], e, v)
    except Exception:
        pass


async def register_precedent(
    *,
    guild: discord.Guild | None,
    bot: discord.Client,
    actor: discord.Member,
    title: str,
    category: str,
    principle: str,
    rationale: str = "",
    source_ref: str = "",
    attach_case_id: str | None = None,
    publish_gazette: bool = True,
) -> tuple[dict, discord.Message | None, str | None]:
    """Persist a binding ICJ Precedent (EMS-YYYY/XXX), post to Archive & Gazette, and refresh Court panels."""
    all_p = precedents_storage.read()
    year = datetime.utcnow().year
    seq = len([k for k in all_p if str(k).startswith(f"EMS-{year}/")]) + 1
    pid = f"EMS-{year}/{seq:03d}"
    while pid in all_p:
        seq += 1
        pid = f"EMS-{year}/{seq:03d}"

    cat_key = category if category in PRECEDENT_CATEGORIES else "procedure"
    cat_label, cat_col = PRECEDENT_CATEGORIES[cat_key]
    ref_clean = (source_ref or attach_case_id or "").strip()

    prec = {
        "id": pid,
        "guild_id": getattr(guild, "id", None),
        "title": title.strip(),
        "category": cat_key,
        "principle": principle.strip(),
        "rationale": rationale.strip(),
        "source_ref": ref_clean,
        "status": "ACTIVE",
        "judge_id": str(actor.id),
        "judge_name": actor.display_name,
        "created_at": datetime.utcnow().strftime("%d.%m.%Y %H:%M UTC"),
        "citations": 0,
    }

    arch_msg = None
    if guild:
        arch_ch = resolve_panel_channel(guild, None, "yargi-arsivi-icj-archive", None)
        if arch_ch and hasattr(arch_ch, "send"):
            try:
                arch_msg = await arch_ch.send(embed=build_precedent_embed(prec))
                prec["archive_channel_id"] = arch_ch.id
                prec["archive_message_id"] = arch_msg.id
            except Exception:
                pass

    issue_no = None
    if guild and publish_gazette:
        issue_no = await publish_to_gazette(
            bot,
            guild.id,
            "EMSAL KARAR · COURT PRECEDENT",
            f"{pid}: {prec['title']}",
            (
                f"**Hukuk Dalı / Field:** {cat_label}\n"
                f"**Karar Yargıcı / Judge:** <@{actor.id}>\n"
                + (f"**Kaynak Dosya / Source:** `{ref_clean}`\n" if ref_clean else "")
                + f"**Bağlayıcı İlke:** {prec['principle']}\n"
                + (f"**Gerekçe:** {prec['rationale']}" if prec["rationale"] else "")
            ),
            color=cat_col,
            issuer=f"{actor.display_name} · Uluslararası Yargı Divanı",
        )
        if issue_no:
            prec["gazette_issue"] = issue_no

    precedents_storage.set(pid, prec)

    if attach_case_id:
        case = icj_storage.get(attach_case_id)
        if isinstance(case, dict):
            case["precedent_id"] = pid
            case["precedent_title"] = prec["title"]
            icj_storage.set(attach_case_id, case)

    await _refresh_icj_and_chambers_panels(guild)
    return prec, arch_msg, issue_no


def _is_icj_or_admin(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    if {r.name for r in member.roles} & {ROLE_SG, ROLE_ADMIN, ROLE_ICJ_PRESIDENT, ROLE_JUDGE, "⚖️ Uluslararası Yargı Divanı Yargıcı"}:
        return True
    rec = delegates_storage.get(str(member.id), {})
    return rec.get("office_code") in {"UYD", "DB", "SG"}


def _is_prosecutor_or_admin(member: discord.Member) -> bool:
    """Return True if member holds Chief Prosecutor (BSV), Prosecutor (SVC), SG, or Admin authority."""
    if member.guild_permissions.administrator:
        return True
    if {r.name for r in member.roles} & {ROLE_SG, ROLE_ADMIN, ROLE_CHIEF_PROSECUTOR, ROLE_PROSECUTOR}:
        return True
    rec = delegates_storage.get(str(member.id), {})
    return rec.get("office_code") in {"BSV", "SVC", "SG"}


def _is_secretariat_or_spokesperson(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    if {r.name for r in member.roles} & {ROLE_SG, ROLE_ADMIN, ROLE_SPOKESPERSON, "📰 Cemiyet Sözcüsü"}:
        return True
    rec = delegates_storage.get(str(member.id), {})
    return rec.get("office_code") in {"SG", "DSG", "GKB", "GKB-V", "Spokesperson"}


def treaty_embed(t: dict) -> discord.Embed:
    signed_names = ", ".join(t.get("signed_names", [])) or "*Yok / None*"
    ratified_names = ", ".join(t.get("ratified_names", [])) or "*Henüz yok / None yet*"
    withdrawn_names = ", ".join(t.get("withdrawn_names", []))
    cat_label, cat_color = TREATY_TYPES.get(t.get("category", "multilateral"), ("📜 Uluslararası Antlaşma · Treaty", 0x10B981))
    parties_line = f"**Taraflar / Target Parties:** `{t['parties']}`\n" if t.get("parties") else ""

    embed = discord.Embed(
        title=f"📜 ANTLAŞMA · INTERNATIONAL TREATY · {t['id']}",
        description=(
            f"**Tür / Type:** {cat_label}\n"
            f"**Başlık / Title:** **{t['title']}**\n"
            f"**Sunan / Sponsor:** `{t['sponsor']}`\n"
            f"{parties_line}"
            f"**Tarih / Date:** `{t['created_at']}`\n\n"
            f"**Maddeler / Treaty Articles:**\n```text\n{t['text']}\n```\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ),
        colour=cat_color,
    )
    embed.add_field(
        name=f"✍️ İmzacılar · Signatories ({len(t.get('signed_names', []))})",
        value=signed_names[:1024],
        inline=False,
    )
    embed.add_field(
        name=f"📜 Onaylayanlar · Ratified States ({len(t.get('ratified_names', []))})",
        value=ratified_names[:1024],
        inline=False,
    )
    if withdrawn_names:
        embed.add_field(
            name=f"🚪 Çekilenler · Withdrawn ({len(t.get('withdrawn_names', []))})",
            value=withdrawn_names[:1024],
            inline=False,
        )
    embed.set_footer(text="Milletler Cemiyeti Antlaşmalar Kütüğü · League Treaty Desk (Art. 29-31)")
    return embed


class TreatyActionView(discord.ui.View):
    """Persistent Sign / Ratify / Withdraw buttons attached to each treaty card."""

    def __init__(self, treaty_id: str):
        super().__init__(timeout=None)
        self.treaty_id = treaty_id

        btn_sign = discord.ui.Button(
            label="İmzala · Sign",
            style=discord.ButtonStyle.primary,
            emoji="✍️",
            custom_id=f"mc_trt_sign:{treaty_id}",
        )
        btn_sign.callback = self._on_sign
        self.add_item(btn_sign)

        btn_ratify = discord.ui.Button(
            label="Onayla · Ratify",
            style=discord.ButtonStyle.success,
            emoji="📜",
            custom_id=f"mc_trt_ratify:{treaty_id}",
        )
        btn_ratify.callback = self._on_ratify
        self.add_item(btn_ratify)

        btn_withdraw = discord.ui.Button(
            label="Çekil · Withdraw",
            style=discord.ButtonStyle.danger,
            emoji="🚪",
            custom_id=f"mc_trt_withdraw:{treaty_id}",
        )
        btn_withdraw.callback = self._on_withdraw
        self.add_item(btn_withdraw)

    async def _get_nation(self, interaction: discord.Interaction) -> tuple[str, str] | None:
        user_data = delegates_storage.get(str(interaction.user.id))
        if not user_data or not user_data.get("nation"):
            await interaction.response.send_message(
                "❌ Antlaşma imzalamak için önce `#🚪│kayit-masasi-register` odasından kaydolup `#📜│tuzuk-charter` kanalında Şartı imzalamalısınız.\n"
                "*Please complete registration and sign the Charter in `#📜│tuzuk-charter` first to sign or ratify treaties.*",
                ephemeral=True,
            )
            return None
        nation = user_data["nation"]
        return nation, normalize_country_key(nation)

    async def _require_signatory(self, interaction: discord.Interaction) -> tuple[str, str] | None:
        info = await self._get_nation(interaction)
        if not info:
            return None
        user_data = delegates_storage.get(str(interaction.user.id))
        if not can_member_sign_for_state(interaction.user, user_data):
            await interaction.response.send_message(
                "⛔ **İmza Yetkisi Yetersiz · Signing Authority Required**\n"
                "Uluslararası antlaşmaları yalnızca ilgili devletin **👑 Devlet Başkanı** veya **🌐 Dışişleri Bakanı** imzalayabilir, onaylayabilir veya feshedebilir.\n"
                "*Only a State's **Head of State** or **Foreign Minister** may sign, ratify, or withdraw from treaties.*",
                ephemeral=True,
            )
            return None
        return info

    async def _on_sign(self, interaction: discord.Interaction):
        info = await self._require_signatory(interaction)
        if not info:
            return
        nation, nkey = info
        t = treaties_storage.get(self.treaty_id)
        if not t:
            return await interaction.response.send_message("❌ Antlaşma bulunamadı. / Treaty not found.", ephemeral=True)

        if nkey in t.setdefault("signed", []):
            return await interaction.response.send_message("ℹ️ Ülkeniz zaten imzalamış. / Already signed by your nation.", ephemeral=True)

        t["signed"].append(nkey)
        t.setdefault("signed_names", []).append(nation)
        if nkey in t.get("withdrawn", []):
            t["withdrawn"].remove(nkey)
            if nation in t.get("withdrawn_names", []):
                t["withdrawn_names"].remove(nation)

        crec = get_country_record(nation)
        crec["stats"]["treaties_signed"] = crec["stats"].get("treaties_signed", 0) + 1
        save_country_record(crec)

        treaties_storage.set(self.treaty_id, t)
        await interaction.response.edit_message(embed=treaty_embed(t), view=self)
        await interaction.followup.send(f"✍️ **{nation}** adına `{self.treaty_id}` imzalandı / signed!", ephemeral=True)

        await publish_to_gazette(
            interaction.client,
            interaction.guild_id,
            "ANTLAŞMA İMZASI · TREATY SIGNATURE",
            f"{nation} ✍️ {t['id']} ({t['title']})",
            f"**{nation}** `{t['id']}` numaralı antlaşmayı imzaladı. / **{nation}** has signed `{t['id']}`.",
            color=0x3B82F6,
        )

    async def _on_ratify(self, interaction: discord.Interaction):
        info = await self._require_signatory(interaction)
        if not info:
            return
        nation, nkey = info
        t = treaties_storage.get(self.treaty_id)
        if not t:
            return await interaction.response.send_message("❌ Antlaşma bulunamadı. / Treaty not found.", ephemeral=True)

        if nkey in t.setdefault("ratified", []):
            return await interaction.response.send_message("ℹ️ Ülkeniz zaten onaylamış. / Already ratified by your nation.", ephemeral=True)

        first_sign = nkey not in t.setdefault("signed", [])
        if first_sign:
            t["signed"].append(nkey)
            t.setdefault("signed_names", []).append(nation)
        t["ratified"].append(nkey)
        t.setdefault("ratified_names", []).append(nation)
        if nkey in t.get("withdrawn", []):
            t["withdrawn"].remove(nkey)
            if nation in t.get("withdrawn_names", []):
                t["withdrawn_names"].remove(nation)

        if first_sign:
            crec = get_country_record(nation)
            crec["stats"]["treaties_signed"] = crec["stats"].get("treaties_signed", 0) + 1
            save_country_record(crec)

        treaties_storage.set(self.treaty_id, t)
        await interaction.response.edit_message(embed=treaty_embed(t), view=self)
        await interaction.followup.send(f"📜 **{nation}** adına `{self.treaty_id}` onaylandı / ratified!", ephemeral=True)

        await publish_to_gazette(
            interaction.client,
            interaction.guild_id,
            "ANTLAŞMA ONAYI · TREATY RATIFICATION",
            f"{nation} 📜 {t['id']} ({t['title']})",
            f"**{nation}** `{t['id']}` antlaşmasını onaylayarak taraf oldu. / **{nation}** formally ratified `{t['id']}`.",
            color=0x10B981,
        )

    async def _on_withdraw(self, interaction: discord.Interaction):
        info = await self._require_signatory(interaction)
        if not info:
            return
        nation, nkey = info
        t = treaties_storage.get(self.treaty_id)
        if not t:
            return await interaction.response.send_message("❌ Antlaşma bulunamadı. / Treaty not found.", ephemeral=True)

        changed = False
        for lst_key, names_key in [("signed", "signed_names"), ("ratified", "ratified_names")]:
            if nkey in t.get(lst_key, []):
                t[lst_key].remove(nkey)
                changed = True
            if nation in t.get(names_key, []):
                t[names_key].remove(nation)
                changed = True

        if not changed:
            return await interaction.response.send_message("ℹ️ Ülkeniz zaten bu antlaşmaya taraf değil. / Not a party to this treaty.", ephemeral=True)

        if nkey not in t.setdefault("withdrawn", []):
            t["withdrawn"].append(nkey)
            t.setdefault("withdrawn_names", []).append(nation)

        treaties_storage.set(self.treaty_id, t)
        await interaction.response.edit_message(embed=treaty_embed(t), view=self)
        await interaction.followup.send(f"🚪 **{nation}** `{self.treaty_id}` antlaşmasından çekildi / withdrew.", ephemeral=True)

        await publish_to_gazette(
            interaction.client,
            interaction.guild_id,
            "ANTLAŞMADAN ÇEKİLME · TREATY WITHDRAWAL",
            f"{nation} 🚪 {t['id']} ({t['title']})",
            f"**{nation}** `{t['id']}` antlaşmasından çekildiğini bildirdi. / **{nation}** withdrew from `{t['id']}`.",
            color=0xEF4444,
        )


class TreatyModal(discord.ui.Modal, title="Antlaşma Sun · Propose Treaty"):
    t_title = discord.ui.TextInput(label="Antlaşma Başlığı / Treaty Title", placeholder="Örn / e.g.: Ticaret & Seyrüsefer Paktı / Trade Pact", required=True, max_length=150)
    t_parties = discord.ui.TextInput(label="Taraflar / Target Parties (Opsiyonel)", placeholder="Örn / e.g.: Tüm Üyeler / Türkiye ↔ İtalya", required=False, max_length=150)
    t_text = discord.ui.TextInput(label="Antlaşma Maddeleri / Treaty Articles", style=discord.TextStyle.paragraph, placeholder="Madde/Art. 1...\nMadde/Art. 2...", required=True, max_length=2000)

    def __init__(self, sponsor: str, category: str = "multilateral", target_channel_id: int | None = None):
        super().__init__()
        self.sponsor = sponsor
        self.category = category
        self.target_channel_id = target_channel_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        all_t = treaties_storage.read()
        tid = f"TRT-{datetime.utcnow().year}/{len(all_t) + 1:03d}"
        skey = normalize_country_key(self.sponsor)

        record = {
            "id": tid,
            "category": self.category,
            "title": self.t_title.value.strip(),
            "parties": self.t_parties.value.strip(),
            "text": self.t_text.value.strip(),
            "sponsor": self.sponsor,
            "signed": [skey],
            "signed_names": [self.sponsor],
            "ratified": [skey],
            "ratified_names": [self.sponsor],
            "withdrawn": [],
            "withdrawn_names": [],
            "guild_id": interaction.guild_id,
            "channel_id": self.target_channel_id or interaction.channel_id,
            "created_at": datetime.utcnow().strftime("%d.%m.%Y %H:%M UTC"),
        }

        crec = get_country_record(self.sponsor)
        crec["stats"]["treaties_signed"] = crec["stats"].get("treaties_signed", 0) + 1
        save_country_record(crec)

        dest = interaction.guild.get_channel(self.target_channel_id) if self.target_channel_id else None
        if not dest:
            dest = discord.utils.find(lambda c: "antlasma" in c.name.lower() or "treaties" in c.name.lower(), interaction.guild.text_channels) or interaction.channel

        view = TreatyActionView(tid)
        interaction.client.add_view(view)
        msg = await dest.send(embed=treaty_embed(record), view=view)
        record["message_id"] = msg.id
        record["channel_id"] = dest.id
        treaties_storage.set(tid, record)

        await publish_to_gazette(
            interaction.client,
            interaction.guild_id,
            "YENİ ANTLAŞMA · NEW TREATY OPEN FOR SIGNING",
            f"{tid} · {record['title']}",
            f"**Sunan / Sponsor:** `{self.sponsor}`\n{msg.jump_url}",
            color=0x1E3A8A,
        )
        await interaction.followup.send(f"✅ Antlaşma imzaya açıldı / Treaty opened (`{tid}`): {msg.jump_url}", ephemeral=True)


class TreatyTypeSelectView(discord.ui.View):
    def __init__(self, sponsor: str, target_channel_id: int | None):
        super().__init__(timeout=120)
        self.sponsor = sponsor
        self.target_channel_id = target_channel_id

        select = discord.ui.Select(
            placeholder="📜 Antlaşma türünü seçin / Select treaty type...",
            options=[
                discord.SelectOption(label="Serbest Ticaret · Free Trade Pact", value="trade", emoji="📦"),
                discord.SelectOption(label="İttifak & Savunma · Defense Alliance", value="alliance", emoji="🛡️"),
                discord.SelectOption(label="Saldırmazlık · Non-Aggression Pact", value="nonaggression", emoji="🕊️"),
                discord.SelectOption(label="Barış & Sınır · Peace & Borders", value="peace", emoji="⚖️"),
                discord.SelectOption(label="Çok Taraflı · Multilateral Convention", value="multilateral", emoji="📜"),
            ],
        )
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        await interaction.response.send_modal(TreatyModal(self.sponsor, interaction.data["values"][0], self.target_channel_id))


class TreatyPanelView(discord.ui.View):
    """Permanent Bretton-Woods style Treaty Desk Panel."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Yeni Antlaşma · Propose Treaty", style=discord.ButtonStyle.success, emoji="📜", custom_id="mc_treaty_create")
    async def btn_create_treaty(self, interaction: discord.Interaction, _button: discord.ui.Button):
        user_data = delegates_storage.get(str(interaction.user.id))
        if not user_data or not user_data.get("nation"):
            return await interaction.response.send_message(
                "❌ Antlaşma sunmak için önce `#🚪│kayit-masasi-register` odasından kaydolup `#📜│tuzuk-charter` kanalında Şartı imzalamalısınız.\n"
                "*Please complete registration and sign the Charter in `#📜│tuzuk-charter` first before proposing a treaty.*",
                ephemeral=True,
            )
        if not can_member_sign_for_state(interaction.user, user_data):
            return await interaction.response.send_message(
                "⛔ **İmza Yetkisi Yetersiz · Signing Authority Required**\n"
                "Devlet adına uluslararası antlaşma taslağı sunma yetkisi yalnızca **👑 Devlet Başkanı** veya **🌐 Dışişleri Bakanı**na aittir.\n"
                "*Only a State's **Head of State** or **Foreign Minister** may propose a treaty.*",
                ephemeral=True,
            )
        await interaction.response.send_message(
            f"**{user_data['nation']}** adına antlaşma türünü seçin / Select treaty type:",
            view=TreatyTypeSelectView(user_data["nation"], interaction.channel_id),
            ephemeral=True,
        )

    @discord.ui.button(label="Aktif Antlaşmalar · Treaties List", style=discord.ButtonStyle.primary, emoji="✍️", custom_id="mc_treaty_list")
    async def btn_list_treaties(self, interaction: discord.Interaction, _button: discord.ui.Button):
        all_t = [t for t in treaties_storage.read().values() if isinstance(t, dict)]
        if not all_t:
            return await interaction.response.send_message("ℹ️ Henüz açılmış bir antlaşma yok. / No treaties registered yet.", ephemeral=True)
        lines = []
        for t in list(reversed(all_t))[:15]:
            jmp = f"https://discord.com/channels/{t['guild_id']}/{t['channel_id']}/{t['message_id']}" if t.get("guild_id") and t.get("channel_id") and t.get("message_id") else ""
            link = f" · [Metin/Open]({jmp})" if jmp else ""
            lines.append(f"• **`{t['id']}`** — **{t['title']}** *(İmza/Sign: {len(t.get('signed_names', []))}, Onay/Ratify: {len(t.get('ratified_names', []))})*{link}")
        embed = discord.Embed(title="📚 ANTLAŞMALAR KÜTÜĞÜ · TREATIES REGISTRY", description="\n".join(lines), colour=0x1E3A8A)
        await interaction.response.send_message(embed=embed, ephemeral=True)


def build_treaty_panel() -> tuple[discord.Embed, discord.ui.View]:
    embed = discord.Embed(
        title="📜 ANTLAŞMALAR & İMZA MASASI · TREATIES & SIGNING DESK",
        description=(
            "**🇹🇷 Türkçe (MC-KS-001 Madde 29–31):**\n"
            "• **`📜 Yeni Antlaşma · Propose Treaty`** butonuna basarak Ticaret, İttifak, Saldırmazlık, Barış veya Çok Taraflı Konvansiyon taslağı açabilirsiniz.\n"
            "• **İmza Yetkisi:** Antlaşma sunma, **`✍️ İmzala`**, **`📜 Onayla`** ve **`🚪 Çekil`** işlemleri yalnızca **👑 Devlet Başkanı** veya **🌐 Dışişleri Bakanı** tarafından yapılabilir.\n"
            "• İmzalar otomatik olarak **Resmî Gazete** ve **Ülke Karnesi**'ne işlenir.\n\n"
            "**🇬🇧 English (MC-KS-001 Articles 29–31):**\n"
            "• Click **`📜 Yeni Antlaşma · Propose Treaty`** below to draft a Trade, Alliance, Non-Aggression, Peace, or Multilateral Convention.\n"
            "• **Signing Authority:** Proposing, **`✍️ Sign`**, **`📜 Ratify`**, and **`🚪 Withdraw`** are restricted to **👑 Head of State** or **🌐 Foreign Minister**."
        ),
        colour=0x10B981,
    )
    embed.set_footer(text="Milletler Cemiyeti · League of Nations Treaty Desk")
    return embed, TreatyPanelView()


class ICJBriefRequestModal(discord.ui.Modal, title="Savunma & Delil İste · Court Summons"):
    requested_party = discord.ui.TextInput(
        label="Savunma İstenecek Taraf / Target Party",
        placeholder="Örn: Davalı Nova Roma / Her iki taraf",
        required=True,
        max_length=100,
    )
    deadline_note = discord.ui.TextInput(
        label="İstenen Belgeler & Süre / Brief & Deadline",
        style=discord.TextStyle.paragraph,
        placeholder="Hangi net ekran görüntüsü, yazılı senet veya savunma bekleniyor? Süre belirtin...",
        required=True,
        max_length=1200,
    )

    def __init__(self, case_id: str, default_target: str = ""):
        super().__init__()
        self.case_id = case_id
        if default_target:
            self.requested_party.default = default_target

    async def on_submit(self, interaction: discord.Interaction):
        case = icj_storage.get(self.case_id)
        if not case:
            return await interaction.response.send_message("❌ Dava kaydı bulunamadı.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        case["status"] = "🔍 Savunma & Delil Bekleniyor · Brief Requested"
        case["summons_target"] = self.requested_party.value.strip()
        case["summons_note"] = self.deadline_note.value.strip()
        case["summons_by"] = interaction.user.display_name
        case["summons_at"] = datetime.utcnow().strftime("%d.%m.%Y %H:%M UTC")
        icj_storage.set(self.case_id, case)

        msg_ref = getattr(interaction, "message", None)
        if msg_ref and hasattr(msg_ref, "edit"):
            try:
                await msg_ref.edit(embed=build_icj_case_embed(case), view=ICJActionView(self.case_id))
            except Exception:
                pass
        dest = interaction.channel or resolve_panel_channel(interaction.guild, None, "yargi-divani-icj-court", None)
        if dest and hasattr(dest, "send"):
            try:
                await dest.send(
                    f"📩 **DİVAN ARA KARARI · DELİL & SAVUNMA TALEBİ (`{self.case_id}`)**\n"
                    f"• **Yargıç / Presiding Judge:** <@{interaction.user.id}>\n"
                    f"• **Muhatap / Party:** **{case['summons_target']}**\n"
                    f"• **İstenen Savunma & Somut Belgeler:** {case['summons_note']}\n"
                    "*Not (`MC-KS-001 Md. 27/2`): Yalnızca net, kesintisiz ekran görüntüleri ve yetkili resmî senetler delil sayılır.*"
                )
            except Exception:
                pass
        await interaction.followup.send(f"✅ `{self.case_id}` dosyası için savunma/delil çağrısı yayımlandı.", ephemeral=True)


def _collect_seat_group(
    guild: discord.Guild | None,
    *,
    single_role: str,
    single_code: str,
    multi_role: str,
    multi_code: str,
) -> tuple[list[str], list[str]]:
    """Return deduplicated mention lists (lead, members) from role cache and delegate records."""
    lead_ids: list[str] = []
    member_ids: list[str] = []

    def _add(bucket: list[str], uid: int | str) -> None:
        s = str(uid)
        if s.isdigit() and s not in bucket:
            bucket.append(s)

    holders = delegates_storage.get("meta_chair_holders")
    if isinstance(holders, dict) and holders.get(single_role):
        _add(lead_ids, holders[single_role])
    multi = delegates_storage.get("meta_chair_holders_multi")
    if isinstance(multi, dict):
        for uid in multi.get(multi_role, []) or []:
            _add(member_ids, uid)
    for uid, d in delegates_storage.read().items():
        if not isinstance(d, dict):
            continue
        chairs = set(d.get("chair_roles") or [])
        if single_role in chairs or d.get("office_code") == single_code:
            _add(lead_ids, uid)
        if multi_role in chairs or d.get("office_code") == multi_code:
            _add(member_ids, uid)
    if guild:
        r_lead = discord.utils.get(guild.roles, name=single_role)
        r_mem = discord.utils.get(guild.roles, name=multi_role)
        for m in getattr(r_lead, "members", ()):
            _add(lead_ids, m.id)
        for m in getattr(r_mem, "members", ()):
            _add(member_ids, m.id)
        for m in getattr(guild, "members", ()):
            rnames = {r.name for r in getattr(m, "roles", ())}
            if single_role in rnames:
                _add(lead_ids, m.id)
            if multi_role in rnames:
                _add(member_ids, m.id)

    return [f"<@{u}>" for u in lead_ids], [f"<@{u}>" for u in member_ids]


def _collect_court_bench(guild: discord.Guild | None) -> tuple[list[str], list[str]]:
    return _collect_seat_group(
        guild,
        single_role=ROLE_ICJ_PRESIDENT,
        single_code="DB",
        multi_role=ROLE_JUDGE,
        multi_code="UYD",
    )


def _collect_prosecutors(guild: discord.Guild | None) -> tuple[list[str], list[str]]:
    return _collect_seat_group(
        guild,
        single_role=ROLE_CHIEF_PROSECUTOR,
        single_code="BSV",
        multi_role=ROLE_PROSECUTOR,
        multi_code="SVC",
    )


def build_icj_case_embed(case: dict, *, plaintiff_uid: int | None = None) -> discord.Embed:
    """Build structured, cohesive Court dossier embed for active or updated ICJ cases."""
    p_uid = plaintiff_uid or case.get("plaintiff_uid")
    p_suffix = f" (<@{p_uid}>)" if p_uid else ""
    status = case.get("status", "⏳ Beklemede / Pending Judgment")
    col = 0x991B1B if case.get("is_prosecution") else (0x0284C7 if "Savunma" in status else 0xD97706)
    prefix = "📜 İDDİANAME · PROSECUTOR CASE" if case.get("is_prosecution") else "⚖️ YARGI DİVANI DOSYASI · ICJ CASE"
    embed = discord.Embed(
        title=f"{prefix} ({case['id']})",
        description=f"**Konu / Subject:** **{case['subject']}**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        colour=col,
    )
    embed.add_field(name="⚖️ Davacı · Plaintiff", value=f"`{case['plaintiff']}`{p_suffix}", inline=True)
    embed.add_field(name="🛡️ Davalı · Defendant", value=f"`{case['defendant']}`", inline=True)
    embed.add_field(name="📌 Aşama · Stage", value=f"`{status}`", inline=True)
    embed.add_field(name="📎 İddia, Dayanak & Talep · Claims & Evidence", value=f"> {case['details'][:980]}", inline=False)
    if case.get("cited_precedents"):
        embed.add_field(
            name="📌 Atıf Yapılan Emsal Kararlar · Cited Precedents",
            value=", ".join(f"`{p}`" for p in case["cited_precedents"][:10]),
            inline=False,
        )
    if case.get("prosecutor_brief"):
        demand_part = f"\n**⚖️ Talep Edilen Yaptırım:** {case['prosecutor_demand']}" if case.get("prosecutor_demand") else ""
        embed.add_field(
            name="📜 Savcılık Mütalaası & İddia · Prosecutor Brief",
            value=f"{case['prosecutor_brief'][:750]}{demand_part[:220]}\n*Savcı: {case.get('prosecutor_by', '-')} · {case.get('prosecutor_at', '')}*"[:1024],
            inline=False,
        )
    if case.get("summons_note"):
        embed.add_field(
            name=f"📩 Divan Ara Kararı · Summons ({case.get('summons_target', '-')})",
            value=f"{case['summons_note'][:900]}\n*Talep Eden: {case.get('summons_by', '-')} · {case.get('summons_at', '')}*",
            inline=False,
        )
    src_inq = f" · Kaynak Soruşturma: {case['prosecution_ref']}" if case.get("prosecution_ref") else ""
    embed.set_footer(text=f"MC-KS-001 Madde 23–28{src_inq} · Açılış: {case.get('created_at', '-')}")
    return embed


class ICJProsecutorBriefModal(discord.ui.Modal, title="Savcılık Mütalaası · Prosecutor Brief"):
    brief_input = discord.ui.TextInput(
        label="Savcılık Mütalaası / Legal Opinion",
        style=discord.TextStyle.paragraph,
        placeholder="Kamu hukuku değerlendirmesi, Şart ihlali ve varsa EMS-... emsal atıfları...",
        required=True,
        max_length=1000,
    )
    demand_input = discord.ui.TextInput(
        label="Talep Edilen Yaptırım / Penalty Demand",
        placeholder="Örn / e.g.: Tazminat, askıya alma veya kınama / Reparations or suspension",
        required=False,
        max_length=250,
    )

    def __init__(self, case_id: str):
        super().__init__()
        self.case_id = case_id

    async def on_submit(self, interaction: discord.Interaction):
        if not _is_prosecutor_or_admin(interaction.user):
            return await interaction.response.send_message(
                "⛔ Yalnızca **Cemiyet Başsavcısı (`BSV`)**, **Cemiyet Savcıları (`SVC`)** veya Yöneticiler mütalaa sunabilir.",
                ephemeral=True,
            )
        case = icj_storage.get(self.case_id)
        if not case:
            return await interaction.response.send_message("❌ Dava kaydı bulunamadı.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        brief_txt = self.brief_input.value.strip()
        demand_txt = (self.demand_input.value or "").strip()
        cited = _increment_precedent_citations(_extract_precedent_refs(f"{brief_txt}\n{demand_txt}"))
        merged_cited = list(dict.fromkeys((case.get("cited_precedents") or []) + cited))
        if merged_cited:
            case["cited_precedents"] = merged_cited
        case["prosecutor_brief"] = brief_txt
        case["prosecutor_demand"] = demand_txt
        case["prosecutor_by"] = interaction.user.display_name
        case["prosecutor_at"] = datetime.utcnow().strftime("%d.%m.%Y %H:%M UTC")
        icj_storage.set(self.case_id, case)

        msg_ref = getattr(interaction, "message", None)
        if msg_ref and hasattr(msg_ref, "edit"):
            try:
                await msg_ref.edit(embed=build_icj_case_embed(case), view=ICJActionView(self.case_id))
            except Exception:
                pass
        court_ch = resolve_panel_channel(interaction.guild, None, "yargi-divani-icj-court", None)
        if court_ch and hasattr(court_ch, "send"):
            try:
                await court_ch.send(
                    f"📜 **SAVCILIK MÜTALAASI SUNULDU (`{self.case_id}`)** — <@{interaction.user.id}>\n"
                    f"• **Değerlendirme:** {brief_txt[:450]}"
                    + (f"\n• **Talep Edilen Yaptırım:** **{demand_txt}**" if demand_txt else "")
                )
            except Exception:
                pass
        await interaction.followup.send(f"✅ `{self.case_id}` dosyasına Savcılık Mütalaası işlendi.", ephemeral=True)


class ICJPrecedentModal(discord.ui.Modal, title="Emsal Karar İlanı · Court Precedent"):
    title_input = discord.ui.TextInput(
        label="Emsal Başlığı / Precedent Title",
        placeholder="Örn / e.g.: Yazılı & Resmî Organ Antlaşma Şartı",
        required=True,
        max_length=120,
    )
    source_input = discord.ui.TextInput(
        label="Kaynak Dava veya Zirve No (Opsiyonel)",
        placeholder="Örn / e.g.: ICJ-2026/002 veya ZRV-2026/001",
        required=False,
        max_length=60,
    )
    principle_input = discord.ui.TextInput(
        label="Bağlayıcı Emsal İlke / Binding Rule",
        style=discord.TextStyle.paragraph,
        placeholder="Tüm üye devletleri ve sonraki davaları bağlayan temel hukuk kuralını yazın...",
        required=True,
        max_length=700,
    )
    rationale_input = discord.ui.TextInput(
        label="Divan Gerekçesi & Kapsamı / Rationale",
        style=discord.TextStyle.paragraph,
        placeholder="Kararın Şart maddeleri (MC-KS-001) ve uygulama şartları...",
        required=False,
        max_length=1000,
    )

    def __init__(self, category_key: str = "procedure", default_ref: str = "", attach_case_id: str | None = None):
        super().__init__()
        self.category_key = category_key if category_key in PRECEDENT_CATEGORIES else "procedure"
        self.attach_case_id = attach_case_id
        if default_ref:
            self.source_input.default = default_ref[:60]

    async def on_submit(self, interaction: discord.Interaction):
        if not _is_icj_or_admin(interaction.user):
            return await interaction.response.send_message(
                "⛔ Yalnızca **Divan Başkanı (`DB`)**, **Divan Yargıçları (`UYD`)** veya Yöneticiler emsal karar ilân edebilir.",
                ephemeral=True,
            )
        await interaction.response.defer(ephemeral=True)
        prec, arch_msg, issue_no = await register_precedent(
            guild=interaction.guild,
            bot=interaction.client,
            actor=interaction.user,
            title=self.title_input.value.strip(),
            category=self.category_key,
            principle=self.principle_input.value.strip(),
            rationale=(self.rationale_input.value or "").strip(),
            source_ref=(self.source_input.value or "").strip(),
            attach_case_id=self.attach_case_id,
        )
        msg_ref = getattr(interaction, "message", None)
        if self.attach_case_id and msg_ref and hasattr(msg_ref, "edit"):
            try:
                await msg_ref.edit(view=ICJArchivedVerdictView(self.attach_case_id, precedent_id=prec["id"]))
            except Exception:
                pass
        notes = [f"✅ Emsal karar ilân edildi: **`{prec['id']}`** — **{prec['title']}**"]
        if arch_msg:
            notes.append(f"• Arşiv: {arch_msg.jump_url}")
        if issue_no:
            notes.append(f"• Resmî Gazete: `{issue_no}`")
        await interaction.followup.send("\n".join(notes), ephemeral=True)


class ICJPrecedentCategoryPickerView(discord.ui.View):
    """Category selector shown when clicking Issue Precedent in Judge Chambers."""

    def __init__(self, attach_case_id: str | None = None):
        super().__init__(timeout=180)
        self.attach_case_id = attach_case_id
        sel = discord.ui.Select(
            placeholder="🏷️ Emsal kararın hukuk dalını seçin...",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=label.split("·")[0].strip()[:100],
                    value=key,
                    description=label[:100],
                )
                for key, (label, _col) in PRECEDENT_CATEGORIES.items()
            ],
        )
        sel.callback = self._on_pick
        self.add_item(sel)

    async def _on_pick(self, interaction: discord.Interaction):
        if not _is_icj_or_admin(interaction.user):
            return await interaction.response.send_message("⛔ Yalnızca Divan Heyeti işlem yapabilir.", ephemeral=True)
        cat_key = interaction.data["values"][0]
        await interaction.response.send_modal(
            ICJPrecedentModal(cat_key, default_ref=self.attach_case_id or "", attach_case_id=self.attach_case_id)
        )


class ICJArchivedVerdictView(discord.ui.View):
    """Persistent button on archived Court verdicts allowing Judges to elevate a verdict to Precedent."""

    def __init__(self, case_id: str, precedent_id: str | None = None):
        super().__init__(timeout=None)
        self.case_id = case_id
        if precedent_id:
            btn = discord.ui.Button(
                label=f"Emsal Karar İşlendi · {precedent_id}"[:80],
                style=discord.ButtonStyle.success,
                emoji="📌",
                custom_id=f"mc_icj_mark_prec:{case_id}",
                disabled=True,
            )
        else:
            btn = discord.ui.Button(
                label="Emsal Karar İşle · Mark Precedent",
                style=discord.ButtonStyle.primary,
                emoji="📕",
                custom_id=f"mc_icj_mark_prec:{case_id}",
            )
            btn.callback = self._on_mark
        self.add_item(btn)

    async def _on_mark(self, interaction: discord.Interaction):
        if not _is_icj_or_admin(interaction.user):
            return await interaction.response.send_message(
                "❌ Yalnızca **Divan Başkanı (`DB`)**, **Divan Yargıçları (`UYD`)** veya Yöneticiler bu hükmü emsal ilân edebilir.",
                ephemeral=True,
            )
        case = icj_storage.get(self.case_id) or {}
        if case.get("precedent_id"):
            return await interaction.response.send_message(
                f"ℹ️ `{self.case_id}` dosyası zaten **`{case['precedent_id']}`** emsal kararı olarak kayıtlıdır.",
                ephemeral=True,
            )
        await interaction.response.send_message(
            f"📕 **`{self.case_id}`** kararını **Emsal Karar (`EMS-YYYY/XXX`)** olarak arşivlemek için hukuk dalını seçin:",
            view=ICJPrecedentCategoryPickerView(attach_case_id=self.case_id),
            ephemeral=True,
        )


class ICJVerdictModal(discord.ui.Modal, title="Divan Kararı · ICJ Court Verdict"):
    verdict = discord.ui.TextInput(
        label="Hüküm & Gerekçe / Court Verdict",
        style=discord.TextStyle.paragraph,
        placeholder="Karar ve gerekçesini yazın / State the ruling and remedies...",
        required=True,
        max_length=1500,
    )
    emsal_ilkesi = discord.ui.TextInput(
        label="Emsal Karar İlkesi (Boşsa Normal Hüküm)",
        style=discord.TextStyle.paragraph,
        placeholder="Emsal olacaksa bağlayıcı kuralı özetleyin / Optional precedent rule...",
        required=False,
        max_length=400,
    )

    def __init__(self, case_id: str, outcome_label: str):
        super().__init__()
        self.case_id = case_id
        self.outcome_label = outcome_label

    async def on_submit(self, interaction: discord.Interaction):
        case = icj_storage.get(self.case_id)
        if not case:
            return await interaction.response.send_message("❌ Dava kaydı bulunamadı. / Case not found.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        case["status"] = self.outcome_label
        case["verdict"] = self.verdict.value.strip()
        case["judge"] = interaction.user.display_name
        case["judge_id"] = str(interaction.user.id)
        case["decided_at"] = datetime.utcnow().strftime("%d.%m.%Y %H:%M UTC")
        emsal_rule = (self.emsal_ilkesi.value or "").strip()

        is_upheld = "Haklı" in self.outcome_label or "Tazminat" in self.outcome_label
        col = 0x10B981 if "Haklı" in self.outcome_label and "Kısmi" not in self.outcome_label else (0xF59E0B if is_upheld else 0xEF4444)
        embed = discord.Embed(
            title=f"⚖️ YARGI DİVANI KARARI · ICJ VERDICT ({self.case_id})",
            description=f"**Konu / Subject:** **{case['subject']}**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            colour=col,
        )
        embed.add_field(name="⚖️ Davacı · Plaintiff", value=f"`{case['plaintiff']}`", inline=True)
        embed.add_field(name="🛡️ Davalı · Defendant", value=f"`{case['defendant']}`", inline=True)
        embed.add_field(name="👨‍⚖️ Yargıç · Presiding", value=f"<@{interaction.user.id}>", inline=True)
        embed.add_field(name="🏛️ Karar · Ruling", value=f"**{self.outcome_label}**", inline=False)
        embed.add_field(name="🧾 Gerekçeli Hüküm · Verdict & Remedies", value=f"> {case['verdict'][:1000]}", inline=False)
        if emsal_rule:
            embed.add_field(name="📕 Emsal Karar İlkesi · Binding Precedent", value=f"📌 **{emsal_rule[:950]}**", inline=False)
        embed.set_footer(text=f"Karar & Kalıcı Arşiv · {case['decided_at']}")

        prec = None
        if emsal_rule:
            prec, _prec_arch, _prec_gaz = await register_precedent(
                guild=interaction.guild,
                bot=interaction.client,
                actor=interaction.user,
                title=f"{case['plaintiff']} vs {case['defendant']} ({self.case_id})",
                category="procedure",
                principle=emsal_rule,
                rationale=case["verdict"],
                source_ref=self.case_id,
                attach_case_id=self.case_id,
                publish_gazette=False,
            )
            case["precedent_id"] = prec["id"]
            case["precedent_title"] = prec["title"]

        arch_view = ICJArchivedVerdictView(self.case_id, precedent_id=prec["id"] if prec else None)
        if hasattr(interaction.client, "add_view"):
            interaction.client.add_view(arch_view)

        arch_msg = await move_court_verdict_to_archive(
            interaction.guild,
            embed,
            case,
            source_message=getattr(interaction, "message", None),
            view=arch_view,
        )
        icj_storage.set(self.case_id, case)

        prec_note = f"\n📕 Emsal Karar işlendi: **`{prec['id']}`**" if prec else ""
        if arch_msg:
            await interaction.followup.send(
                f"✅ `{self.case_id}` kararı verildi ve geçmiş dava dosyası arşive taşındı: {arch_msg.jump_url}{prec_note}",
                ephemeral=True,
            )
        else:
            msg_ref = getattr(interaction, "message", None)
            if msg_ref and hasattr(msg_ref, "edit"):
                try:
                    await msg_ref.edit(embed=embed, view=None)
                except Exception:
                    pass
            await interaction.followup.send(f"✅ `{self.case_id}` kararı işlendi.{prec_note}", ephemeral=True)

        await publish_to_gazette(
            interaction.client,
            interaction.guild_id,
            "YARGI DİVANI KARARI · ICJ RULING",
            f"{self.case_id}: {case['plaintiff']} vs {case['defendant']}",
            (
                f"**Karar:** {self.outcome_label}\n"
                f"**Yargıç:** <@{interaction.user.id}>\n"
                f"**Hüküm:** {case['verdict']}"
                + (f"\n**📕 Emsal Karar (`{prec['id']}`):** {emsal_rule}" if prec else "")
            ),
            color=col,
            issuer=f"{interaction.user.display_name} · Uluslararası Yargı Divanı",
        )


class ICJActionView(discord.ui.View):
    def __init__(self, case_id: str):
        super().__init__(timeout=None)
        self.case_id = case_id

        btn_brief = discord.ui.Button(
            label="Savunma / Delil İste · Summons",
            style=discord.ButtonStyle.primary,
            emoji="📩",
            custom_id=f"mc_icj_brief:{case_id}",
        )
        btn_brief.callback = self._on_brief
        self.add_item(btn_brief)

        btn_guilty = discord.ui.Button(
            label="Davacı Haklı · Uphold Claim",
            style=discord.ButtonStyle.danger,
            emoji="⚖️",
            custom_id=f"mc_icj_guilty:{case_id}",
        )
        btn_guilty.callback = self._on_guilty
        self.add_item(btn_guilty)

        btn_partial = discord.ui.Button(
            label="Tazminat / Ara Hüküm · Remedy",
            style=discord.ButtonStyle.success,
            emoji="🤝",
            custom_id=f"mc_icj_partial:{case_id}",
        )
        btn_partial.callback = self._on_partial
        self.add_item(btn_partial)

        btn_dismiss = discord.ui.Button(
            label="Davayı Reddet · Dismiss",
            style=discord.ButtonStyle.secondary,
            emoji="🏛️",
            custom_id=f"mc_icj_dismiss:{case_id}",
            row=0,
        )
        btn_dismiss.callback = self._on_dismiss
        self.add_item(btn_dismiss)

        btn_prosecutor = discord.ui.Button(
            label="Savcılık Mütalaası · Prosecutor Brief",
            style=discord.ButtonStyle.secondary,
            emoji="📜",
            custom_id=f"mc_icj_prosecutor:{case_id}",
            row=1,
        )
        btn_prosecutor.callback = self._on_prosecutor
        self.add_item(btn_prosecutor)

    async def _require_judge(self, interaction: discord.Interaction) -> bool:
        if _is_icj_or_admin(interaction.user):
            return True
        await interaction.response.send_message(
            "❌ Yalnızca **Divan Başkanı (`DB`)**, **Divan Yargıçları (`UYD`)** veya Yöneticiler hüküm verebilir. *(Savcılık sadece mütalaa sunabilir.)*",
            ephemeral=True,
        )
        return False

    async def _on_prosecutor(self, interaction: discord.Interaction):
        if not _is_prosecutor_or_admin(interaction.user):
            return await interaction.response.send_message(
                "❌ Yalnızca **Cemiyet Başsavcısı (`BSV`)**, **Savcılar (`SVC`)** veya Yöneticiler Savcılık Mütalaası sunabilir.",
                ephemeral=True,
            )
        await interaction.response.send_modal(ICJProsecutorBriefModal(self.case_id))

    async def _on_brief(self, interaction: discord.Interaction):
        if not await self._require_judge(interaction):
            return
        case = icj_storage.get(self.case_id) or {}
        await interaction.response.send_modal(ICJBriefRequestModal(self.case_id, f"Davalı {case.get('defendant', '')}".strip()))

    async def _on_guilty(self, interaction: discord.Interaction):
        if not await self._require_judge(interaction):
            return
        await interaction.response.send_modal(ICJVerdictModal(self.case_id, "Davacı Haklı · Claim Upheld"))

    async def _on_partial(self, interaction: discord.Interaction):
        if not await self._require_judge(interaction):
            return
        await interaction.response.send_modal(ICJVerdictModal(self.case_id, "Kısmi Haklı / Tazminat Hükmü · Reparations Ordered"))

    async def _on_dismiss(self, interaction: discord.Interaction):
        if not await self._require_judge(interaction):
            return
        await interaction.response.send_modal(ICJVerdictModal(self.case_id, "Dava Reddedildi · Case Dismissed"))


class ICJModal(discord.ui.Modal, title="Dava Aç · File ICJ Case"):
    defendant_input = discord.ui.TextInput(label="Davalı Devlet / Defendant State", placeholder="Örn / e.g.: İtalya / Italy", required=True, max_length=100)
    subject = discord.ui.TextInput(label="Dava Konusu / Claim Subject", placeholder="Örn / e.g.: Antlaşma İhlali / Treaty Breach", required=True, max_length=150)
    details = discord.ui.TextInput(label="Deliller & Talep / Evidence & Claims", style=discord.TextStyle.paragraph, placeholder="İhlal edilen Şart/antlaşma maddesi & talep / Breached article & relief sought...", required=True, max_length=1800)

    def __init__(self, plaintiff: str, default_defendant: str = ""):
        super().__init__()
        self.plaintiff = plaintiff
        if default_defendant:
            self.defendant_input.default = default_defendant

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        all_c = icj_storage.read()
        cid = f"ICJ-{datetime.utcnow().year}/{len(all_c) + 1:03d}"
        defendant = self.defendant_input.value.strip()

        prec = get_country_record(self.plaintiff)
        drec = get_country_record(defendant)
        prec["stats"]["icj_cases"] += 1
        drec["stats"]["icj_cases"] += 1
        save_country_record(prec)
        save_country_record(drec)

        cited_refs = _increment_precedent_citations(
            _extract_precedent_refs(f"{self.subject.value}\n{self.details.value}")
        )
        case = {
            "id": cid,
            "plaintiff": self.plaintiff,
            "plaintiff_uid": str(interaction.user.id),
            "defendant": defendant,
            "subject": self.subject.value.strip(),
            "details": self.details.value.strip(),
            "cited_precedents": cited_refs,
            "status": "⏳ Ön İnceleme · Pending Judgment",
            "created_at": datetime.utcnow().strftime("%d.%m.%Y %H:%M UTC"),
        }
        icj_storage.set(cid, case)

        embed = build_icj_case_embed(case, plaintiff_uid=interaction.user.id)
        view = ICJActionView(cid)
        if hasattr(interaction.client, "add_view"):
            interaction.client.add_view(view)
        dest = resolve_panel_channel(interaction.guild, None, "yargi-divani-icj-court", interaction.channel)
        r_pres = discord.utils.get(interaction.guild.roles, name=ROLE_ICJ_PRESIDENT)
        r_judge = discord.utils.get(interaction.guild.roles, name=ROLE_JUDGE)
        bench_ping = " ".join(getattr(r, "mention", f"<@&{r.id}>") for r in (r_pres, r_judge) if r)
        msg = await dest.send(content=f"📣 **Yeni Divan Başvurusu:** {bench_ping}" if bench_ping else None, embed=embed, view=view)
        case["court_channel_id"] = dest.id
        case["court_message_id"] = msg.id
        icj_storage.set(cid, case)
        await interaction.followup.send(f"✅ Divan davası açıldı / ICJ case filed (`{cid}`): {msg.jump_url}", ephemeral=True)


def _format_icj_docket_lines(archived: bool = False) -> list[str]:
    cases = [c for c in icj_storage.read().values() if isinstance(c, dict)]
    filtered = [c for c in cases if bool(c.get("archived") or c.get("verdict")) == archived]
    return [
        f"• **`{c['id']}`** — `{c['plaintiff']}` vs `{c['defendant']}` · **{c.get('status', '-')}**"
        for c in list(reversed(filtered))[:15]
    ]


class ICJPanelView(discord.ui.View):
    """Permanent ICJ Court Filing Panel."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Yeni Dava Aç · File ICJ Case", style=discord.ButtonStyle.danger, emoji="⚖️", custom_id="mc_icj_create")
    async def btn_open_case(self, interaction: discord.Interaction, _button: discord.ui.Button):
        user_data = delegates_storage.get(str(interaction.user.id))
        if not user_data or not user_data.get("nation"):
            return await interaction.response.send_message(
                "❌ Dava açmak için önce `#🚪│kayit-masasi-register` odasından kaydolup sunucu akreditasyonunuzu tamamlamalısınız.\n"
                "*Please complete registration in `#🚪│kayit-masasi-register` first to file an ICJ case.*",
                ephemeral=True,
            )
        if not (can_member_represent_state_diplomacy(interaction.user, user_data, allow_mp_icj=True) or _is_icj_or_admin(interaction.user)):
            return await interaction.response.send_message(
                "⛔ **Yetki Yetersiz · Official State Authority Required**\n"
                "Uluslararası Yargı Divanı'nda devletlerarası dava açma yetkisi yalnızca **Devlet Başkanı, Kabine Bakanları, "
                "Asil/Yedek Temsilciler, Büyükelçiler ve Parlamento Üyelerine** aittir; sade **Vatandaşlar (`Vatandaş`)** devlet davası açamaz.\n"
                "Bireysel şikâyet veya talepler için `#🎫│destek-masasi-tickets` kanalını kullanın.\n"
                "*Only authorized state officials may file an inter-state ICJ case; Citizens cannot.*",
                ephemeral=True,
            )
        await interaction.response.send_modal(ICJModal(user_data["nation"]))

    @discord.ui.button(label="Dava Sicili · Active Docket", style=discord.ButtonStyle.secondary, emoji="📋", custom_id="mc_icj_list")
    async def btn_list_cases(self, interaction: discord.Interaction, _button: discord.ui.Button):
        lines = _format_icj_docket_lines(archived=False)
        if not lines:
            return await interaction.response.send_message(
                "ℹ️ Şu anda yargılaması süren aktif Divan davası yok. Geçmiş dosyalar için **📚 Geçmiş Dava Arşivi** butonuna veya `#📚│yargi-arsivi-icj-archive` kanalına bakın.\n"
                "*No active pending ICJ cases right now.*",
                ephemeral=True,
            )
        embed = discord.Embed(title="⚖️ AKTİF DİVAN DAVALARI · ACTIVE DOCKET", description="\n".join(lines), colour=0xD97706)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Geçmiş Dava Arşivi · Past Archive", style=discord.ButtonStyle.primary, emoji="📚", custom_id="mc_icj_archive")
    async def btn_archive_cases(self, interaction: discord.Interaction, _button: discord.ui.Button):
        lines = _format_icj_docket_lines(archived=True)
        arch_ch = resolve_panel_channel(interaction.guild, None, "yargi-arsivi-icj-archive", None)
        ch_line = f"**Arşiv Kanalı / Channel:** {arch_ch.mention}\n\n" if arch_ch else ""
        if not lines:
            return await interaction.response.send_message(
                f"{ch_line}📚 Henüz karara bağlanıp arşive kaldırılmış geçmiş Divan davası bulunmuyor.\n"
                "*No archived past ICJ verdicts yet.*",
                ephemeral=True,
            )
        embed = discord.Embed(title="📚 GEÇMİŞ DAVA ARŞİVİ · PAST ICJ ARCHIVE", description=ch_line + "\n".join(lines), colour=0x6D28D9)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Yargılama Usulü & Heyet · Court Guide", style=discord.ButtonStyle.success, emoji="📜", custom_id="mc_icj_guide")
    async def btn_court_guide(self, interaction: discord.Interaction, _button: discord.ui.Button):
        pres, judges = _collect_court_bench(interaction.guild)
        emb = discord.Embed(
            title="📜 DİVAN USULÜ, DELİL ŞARTI & YARGI HEYETİ (MC-KS-001 Md. 23–28)",
            description=(
                "**👨‍⚖️ Görevli Yargı Heyeti:**\n"
                f"• **Divan Başkanı:** {', '.join(pres) or '*Genel Sekreterlik / Seçimle Atanır*'}\n"
                f"• **Divan Yargıçları (Md. 23 · Azami 5):** {', '.join(judges) or '*Henüz atanmadı*'}\n\n"
                "**⚖️ Yargılama & Delil Standartları (Md. 26–27):**\n"
                "1. **Yazılı Delil Esası:** Devletler arası sözlü sözler veya kesik/eksik DM iddiaları geçerli antlaşma sayılmaz. İddialar net, eksiksiz ekran görüntüleri (`SS`), savaş logları veya Resmî Gazete/senetlerle belgelenmelidir.\n"
                "2. **Savunma Çağrısı:** Yargıçlar **`📩 Savunma / Delil İste`** butonuyla taraflardan süreli yazılı savunma isteyebilir.\n"
                "3. **Emsal Kararlar & İçtihat:** Yargıçlar tarafından ilân edilen **`EMS-YYYY/XXX`** emsal kararları sonraki davalarda bağlayıcı hukuk ilkesi oluşturur.\n"
                "4. **Hüküm & İcra (`Md. 28`):** **`⚖️ Davacı Haklı`**, **`🤝 Tazminat / Ara Hüküm`** veya **`🏛️ Davayı Reddet`** kararları doğrudan Resmî Gazete'ye ve `#📚│yargi-arsivi-icj-archive` arşivine işlenir."
            ),
            colour=0x6D28D9,
        )
        await interaction.response.send_message(embed=emb, ephemeral=True)

    @discord.ui.button(label="Emsal Kararlar · Precedents", style=discord.ButtonStyle.secondary, emoji="📕", custom_id="mc_icj_precedents")
    async def btn_precedents(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.send_message(embed=build_precedents_list_embed(), ephemeral=True)


def build_icj_panel(guild: discord.Guild | None = None) -> tuple[discord.Embed, discord.ui.View]:
    pres, judges = _collect_court_bench(guild)
    chiefs, prosecutors = _collect_prosecutors(guild)
    arch_ch = resolve_panel_channel(guild, None, "yargi-arsivi-icj-archive", None) if guild else None
    chambers = resolve_panel_channel(guild, None, "divan-makami-chambers", None) if guild else None
    prosecution_ch = resolve_panel_channel(guild, None, "savcilik-makami-prosecution", None) if guild else None
    active_precs = _list_precedents()
    latest_prec_note = f" *(Son: `{active_precs[0]['id']}`)*" if active_precs else ""
    embed = discord.Embed(
        title="⚖️ ULUSLARARASI YARGI DİVANI · INTERNATIONAL COURT (MC-KS-001)",
        description=(
            "**🇹🇷 Türkçe · Bağımsız Yargı & Savcılık Sistemi (Madde 23–28):**\n"
            "• **1) Dava & İddianame:** Devlet yetkilileri **`⚖️ Yeni Dava Aç`** ile başvurur; **Cemiyet Savcılığı** da kamu adına iddianame sunup mütalaa verebilir.\n"
            "• **2) İnceleme & Savunma:** Divan Başkanı ve Yargıçlar **`📩 Savunma / Delil İste`** ile yazılı cevap talep eder; ardından **`Davacı Haklı`**, **`Tazminat / Ara Hüküm`** veya **`Reddet`** kararı verir *(kuvvetler ayrılığı gereği savcılar hüküm veremez)*.\n"
            "• **3) Emsal İçtihat & Arşiv:** Yargıçların ilân ettiği **`📕 Emsal Kararlar`** ile tüm hükümler Resmî Gazete'ye ve "
            f"{arch_ch.mention if arch_ch else '`#📚│yargi-arsivi-icj-archive`'} arşivine işlenir.\n\n"
            "**🇬🇧 English:** Accredited states file claims via **`⚖️ Yeni Dava Aç`**; Prosecution files indictments & briefs; Judges rule independently."
        ),
        colour=0x6D28D9,
    )
    embed.add_field(
        name="👨‍⚖️ Divan Heyeti & Savcılık Makamı · Bench & Prosecution",
        value=(
            f"• **⚖️ Divan Başkanı (`DB`):** {', '.join(pres) or '*Atama Bekleniyor*'}\n"
            f"• **⚖️ Divan Yargıçları (`UYD`):** {', '.join(judges) or '*Atama Bekleniyor*'}\n"
            f"• **📜 Başsavcı (`BSV`) & Savcılar (`SVC`):** {', '.join(chiefs + prosecutors) or '*Atama Bekleniyor*'}\n"
            f"• **📕 Emsal Kararlar:** `{len(active_precs)}`{latest_prec_note} · **Ofisler:** {chambers.mention if chambers else '`#divan-makami`'} | {prosecution_ch.mention if prosecution_ch else '`#savcilik-makami`'}"
        ),
        inline=False,
    )
    embed.set_footer(text="Milletler Cemiyeti · Permanent Court of International Justice (Art. 23–28)")
    return embed, ICJPanelView()


class ICJChambersPanelView(discord.ui.View):
    """Persistent Judicial Chambers panel for ICJ President and Judges inside #divan-makami-chambers."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Bekleyen Davalar · Pending Docket", style=discord.ButtonStyle.primary, emoji="📋", custom_id="mc_icj_chambers_docket", row=0)
    async def btn_docket(self, interaction: discord.Interaction, _button: discord.ui.Button):
        lines = _format_icj_docket_lines(archived=False)
        if not lines:
            return await interaction.response.send_message("ℹ️ Şu an bekleyen açık Divan davası yok.", ephemeral=True)
        await interaction.response.send_message(
            embed=discord.Embed(title="⚖️ BEKLEYEN DAVA DOSYALARI · PENDING DOCKET", description="\n".join(lines), colour=0xD97706),
            ephemeral=True,
        )

    @discord.ui.button(label="Arşivlenen Hükümler · Verdicts", style=discord.ButtonStyle.secondary, emoji="📚", custom_id="mc_icj_chambers_archive", row=0)
    async def btn_archive(self, interaction: discord.Interaction, _button: discord.ui.Button):
        lines = _format_icj_docket_lines(archived=True)
        await interaction.response.send_message(
            embed=discord.Embed(
                title="📚 KARARA BAĞLANAN DİVAN DOSYALARI",
                description="\n".join(lines) if lines else "*Henüz hüküm yok.*",
                colour=0x6D28D9,
            ),
            ephemeral=True,
        )

    @discord.ui.button(label="Makam Künyesini Yenile · Refresh", style=discord.ButtonStyle.success, emoji="🔄", custom_id="mc_icj_chambers_refresh", row=0)
    async def btn_refresh(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if not _is_icj_or_admin(interaction.user):
            return await interaction.response.send_message("⛔ Yalnızca Divan Heyeti veya Yönetici.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        emb, vw = build_chambers_panel(interaction.guild)
        if interaction.message and hasattr(interaction.message, "edit"):
            await interaction.message.edit(embed=emb, view=vw)
        await interaction.followup.send("✅ Divan Makamı paneli güncellendi.", ephemeral=True)

    @discord.ui.button(label="Emsal Karar İlan Et · Issue Precedent", style=discord.ButtonStyle.danger, emoji="📕", custom_id="mc_icj_chambers_new_precedent", row=1)
    async def btn_new_precedent(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if not _is_icj_or_admin(interaction.user):
            return await interaction.response.send_message(
                "❌ Yalnızca **Divan Başkanı (`DB`)**, **Divan Yargıçları (`UYD`)** veya Yöneticiler emsal karar ilân edebilir.",
                ephemeral=True,
            )
        await interaction.response.send_message(
            "📕 Yeni **Emsal Karar (`EMS-YYYY/XXX`)** ilân etmek için ilgili hukuk dalını seçin:",
            view=ICJPrecedentCategoryPickerView(),
            ephemeral=True,
        )

    @discord.ui.button(label="Emsal İçtihat Sicili · Precedent Roll", style=discord.ButtonStyle.primary, emoji="🏛️", custom_id="mc_icj_chambers_precedents", row=1)
    async def btn_precedents(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.send_message(embed=build_precedents_list_embed(include_repealed=True), ephemeral=True)


def build_chambers_panel(guild: discord.Guild | None = None) -> tuple[discord.Embed, discord.ui.View]:
    pres, judges = _collect_court_bench(guild)
    pending_count = len(_format_icj_docket_lines(archived=False))
    closed_count = len(_format_icj_docket_lines(archived=True))
    prec_count = len(_list_precedents())
    court_ch = resolve_panel_channel(guild, None, "yargi-divani-icj-court", None) if guild else None
    arch_ch = resolve_panel_channel(guild, None, "yargi-arsivi-icj-archive", None) if guild else None

    embed = discord.Embed(
        title="⚖️ DİVAN MAKAMI & YARGIÇLAR OFİSİ · ICJ CHAMBERS",
        description=(
            "Uluslararası Yargı Divanı Başkanı ve Bağımsız Yargıçlar için özel müzakere, delil inceleme, emsal içtihat ve karar koordinasyon ofisidir.\n\n"
            f"• **Duruşma & Dava Salonu:** {court_ch.mention if court_ch else '`#⚖️│yargi-divani-icj-court`'}\n"
            f"• **Geçmiş Hüküm & Emsal Arşivi:** {arch_ch.mention if arch_ch else '`#📚│yargi-arsivi-icj-archive`'}\n"
            "• **Yetkili İşlemler:** Davalarda `📩 Savunma / Delil İste`, `⚖️ Davacı Haklı`, `🤝 Tazminat / Ara Hüküm`, `🏛️ Davayı Reddet` ile **📕 Emsal Karar İlan Et** / **`/emsal-karar`** yetkilerini kullanabilirsiniz."
        ),
        colour=0x7C3AED,
    )
    embed.add_field(name="⚖️ Divan Başkanı (`DB`)", value=", ".join(pres) or "*Henüz atanmadı*", inline=True)
    embed.add_field(name="👨‍⚖️ Divan Yargıçları (`UYD`)", value=", ".join(judges) or "*Henüz atanmadı*", inline=True)
    embed.add_field(
        name="📊 Dosya & Emsal Durumu",
        value=f"Aktif Dava: `{pending_count}` · Arşiv Hüküm: `{closed_count}` · Yürürlükteki Emsal: `{prec_count}`",
        inline=False,
    )
    embed.set_footer(text="MC-KS-001 Altıncı Bölüm · Bağımsız Yargı Ofisi & Emsal İçtihat (Madde 23–28)")
    return embed, ICJChambersPanelView()


def _next_inquiry_id() -> str:
    all_inq = prosecution_storage.read()
    year = datetime.utcnow().year
    seq = len([k for k in all_inq if str(k).startswith(f"SAV-{year}/")]) + 1
    sid = f"SAV-{year}/{seq:03d}"
    while sid in all_inq:
        seq += 1
        sid = f"SAV-{year}/{seq:03d}"
    return sid


def build_inquiry_embed(inq: dict) -> discord.Embed:
    """Build structured Prosecution Inquiry dossier embed for #📜│savcilik-makami-prosecution."""
    status = inq.get("status", "🔍 Soruşturma Açık · Active Inquiry")
    col = 0x10B981 if "Sevk" in status else (0x6B7280 if "Takipsizlik" in status or "Kapat" in status else 0x991B1B)
    embed = discord.Embed(
        title=f"📜 SAVCILIK SORUŞTURMA DOSYASI · INQUIRY ({inq['id']})",
        description=f"**Konu / Subject:** **{inq['subject']}**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        colour=col,
    )
    embed.add_field(name="🎯 Şüpheli / Muhatap", value=f"`{inq['suspect']}`", inline=True)
    embed.add_field(name="👨‍💼 Soruşturma Savcısı", value=f"<@{inq.get('prosecutor_uid', 0)}>", inline=True)
    embed.add_field(name="📌 Aşama", value=f"`{status}`", inline=True)
    embed.add_field(name="📎 Bulgular & Dayanak · Evidence", value=f"> {inq['evidence'][:980]}", inline=False)
    if inq.get("demand"):
        embed.add_field(name="⚖️ Önerilen Yaptırım / İstem", value=inq["demand"][:500], inline=False)
    if inq.get("cited_precedents"):
        embed.add_field(name="📌 Atıf Yapılan Emsaller", value=", ".join(f"`{p}`" for p in inq["cited_precedents"][:10]), inline=False)
    if inq.get("summon_notes"):
        embed.add_field(name="📩 Savcılık İfade / Delil Talepleri", value="\n".join(inq["summon_notes"][-4:])[:950], inline=False)
    if inq.get("indicted_case_id"):
        embed.add_field(name="🏛️ Divan İddianame No", value=f"**`{inq['indicted_case_id']}`**", inline=True)
    if inq.get("close_reason"):
        embed.add_field(name="📁 Kapatma / Takipsizlik Gerekçesi", value=inq["close_reason"][:800], inline=False)
    embed.set_footer(text=f"Cemiyet Başsavcılığı · Açılış: {inq.get('created_at', '-')}")
    return embed


async def _file_indictment_to_court(
    *,
    guild: discord.Guild | None,
    bot: discord.Client,
    actor: discord.Member,
    defendant: str,
    subject: str,
    evidence: str,
    demand: str = "",
    prosecution_ref: str = "",
    fallback_channel=None,
) -> tuple[dict, discord.Message | None, str | None]:
    """Create a formal Prosecutorial Indictment ICJ case, dispatch to ICJ Court & Gazette, and return (case, msg, issue_no)."""
    all_c = icj_storage.read()
    cid = f"ICJ-{datetime.utcnow().year}/{len(all_c) + 1:03d}"
    while cid in all_c:
        n = len(all_c) + 1
        cid = f"ICJ-{datetime.utcnow().year}/{n:03d}"
        if cid in all_c:
            cid = f"ICJ-{datetime.utcnow().year}/{int(datetime.utcnow().timestamp()) % 10000}"

    drec = get_country_record(defendant)
    drec["stats"]["icj_cases"] += 1
    save_country_record(drec)

    cited_refs = _increment_precedent_citations(_extract_precedent_refs(f"{subject}\n{evidence}\n{demand}"))
    now_str = datetime.utcnow().strftime("%d.%m.%Y %H:%M UTC")
    case = {
        "id": cid,
        "plaintiff": "⚖️ Cemiyet Başsavcılığı · Public Prosecution",
        "plaintiff_uid": str(actor.id),
        "defendant": defendant.strip(),
        "subject": f"[İDDİANAME] {subject.strip()}",
        "details": evidence.strip(),
        "cited_precedents": cited_refs,
        "is_prosecution": True,
        "prosecution_ref": prosecution_ref,
        "prosecutor_brief": evidence.strip()[:800],
        "prosecutor_demand": demand.strip(),
        "prosecutor_by": actor.display_name,
        "prosecutor_at": now_str,
        "status": "📜 Savcılık İddianamesi · Indicted",
        "created_at": now_str,
    }
    icj_storage.set(cid, case)

    embed = build_icj_case_embed(case, plaintiff_uid=actor.id)
    view = ICJActionView(cid)
    if hasattr(bot, "add_view"):
        bot.add_view(view)

    msg = None
    if guild:
        dest = resolve_panel_channel(guild, None, "yargi-divani-icj-court", fallback_channel)
        if dest and hasattr(dest, "send"):
            r_pres = discord.utils.get(guild.roles, name=ROLE_ICJ_PRESIDENT)
            r_judge = discord.utils.get(guild.roles, name=ROLE_JUDGE)
            bench_ping = " ".join(getattr(r, "mention", f"<@&{r.id}>") for r in (r_pres, r_judge) if r)
            msg = await dest.send(
                content=f"📜 **YENİ SAVCILIK İDDİANAMESİ (`{cid}`):** {bench_ping}" if bench_ping else f"📜 **YENİ SAVCILIK İDDİANAMESİ (`{cid}`)**",
                embed=embed,
                view=view,
            )
            case["court_channel_id"] = dest.id
            case["court_message_id"] = msg.id
            icj_storage.set(cid, case)

    issue_no = None
    if guild:
        issue_no = await publish_to_gazette(
            bot,
            guild.id,
            "SAVCILIK İDDİANAMESİ · PROSECUTION INDICTMENT",
            f"{cid} — Sanık / Accused: {case['defendant']}",
            (
                f"**İddia Makamı:** <@{actor.id}> (`Cemiyet Başsavcılığı`)\n"
                f"**Konu:** {subject.strip()}\n"
                + (f"**Talep Edilen Yaptırım:** {demand.strip()}\n" if demand.strip() else "")
                + f"**İddia Özeti:** {evidence.strip()[:500]}"
            ),
            color=0x991B1B,
            issuer=f"{actor.display_name} · Cemiyet Başsavcılığı",
        )
    return case, msg, issue_no


async def _refresh_prosecution_panel_if_present(guild: discord.Guild | None) -> None:
    if not guild:
        return
    try:
        ch = resolve_panel_channel(guild, None, "savcilik-makami-prosecution", None)
        if ch:
            e, v = build_prosecution_panel(guild)
            await upsert_panel_message(ch, ["📜 SAVCILIK MAKAMI & SORUŞTURMA"], e, v)
    except Exception:
        pass


class InvestigationSummonModal(discord.ui.Modal, title="Savcılık İfade Çağrısı · Summons"):
    party_input = discord.ui.TextInput(
        label="İfadeye Çağrılan / Summoned Party",
        placeholder="Örn / e.g.: Nova Roma veya @Üye",
        required=True,
        max_length=100,
    )
    note_input = discord.ui.TextInput(
        label="İstenen Savunma & Belgeler / Demand",
        style=discord.TextStyle.paragraph,
        placeholder="Eksiksiz ekran görüntüsü, antlaşma senedi veya yazılı ifade talebi...",
        required=True,
        max_length=800,
    )

    def __init__(self, inq_id: str, default_party: str = ""):
        super().__init__()
        self.inq_id = inq_id
        if default_party:
            self.party_input.default = default_party[:100]

    async def on_submit(self, interaction: discord.Interaction):
        if not _is_prosecutor_or_admin(interaction.user):
            return await interaction.response.send_message("⛔ Yalnızca Savcılık Makamı veya Yöneticiler işlem yapabilir.", ephemeral=True)
        inq = prosecution_storage.get(self.inq_id)
        if not inq:
            return await interaction.response.send_message("❌ Soruşturma dosyası bulunamadı.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        entry = (
            f"• **{self.party_input.value.strip()}** *(Savcı: <@{interaction.user.id}>)*: "
            f"{self.note_input.value.strip()[:300]}"
        )
        inq.setdefault("summon_notes", []).append(entry)
        inq["status"] = "📩 İfade & Delil Bekleniyor · Summoned"
        prosecution_storage.set(self.inq_id, inq)

        msg_ref = getattr(interaction, "message", None)
        if msg_ref and hasattr(msg_ref, "edit"):
            try:
                await msg_ref.edit(embed=build_inquiry_embed(inq), view=InvestigationActionView(self.inq_id))
            except Exception:
                pass
        court_ch = resolve_panel_channel(interaction.guild, None, "yargi-divani-icj-court", None)
        if court_ch and hasattr(court_ch, "send"):
            try:
                await court_ch.send(
                    f"📩 **CEMİYET BAŞSAVCILIĞI İFADE & DELİL ÇAĞRISI (`{self.inq_id}`)**\n"
                    f"• **Soruşturma Savcısı:** <@{interaction.user.id}>\n"
                    f"• **Muhatap:** **{self.party_input.value.strip()}**\n"
                    f"• **Talep:** {self.note_input.value.strip()}"
                )
            except Exception:
                pass
        await interaction.followup.send(f"✅ `{self.inq_id}` için ifade/delil çağrısı yayımlandı.", ephemeral=True)


class InquiryCloseModal(discord.ui.Modal, title="Soruşturma Kapat · Takipsizlik"):
    reason_input = discord.ui.TextInput(
        label="Takipsizlik / Kapatma Gerekçesi",
        style=discord.TextStyle.paragraph,
        placeholder="Yetersiz delil, uzlaşma veya yetkisizlik gerekçesini yazın...",
        required=True,
        max_length=800,
    )

    def __init__(self, inq_id: str):
        super().__init__()
        self.inq_id = inq_id

    async def on_submit(self, interaction: discord.Interaction):
        if not _is_prosecutor_or_admin(interaction.user):
            return await interaction.response.send_message("⛔ Yalnızca Savcılık Makamı işlem yapabilir.", ephemeral=True)
        inq = prosecution_storage.get(self.inq_id)
        if not inq:
            return await interaction.response.send_message("❌ Soruşturma bulunamadı.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        inq["status"] = "📁 Takipsizlik / Kapatıldı · Closed"
        inq["close_reason"] = self.reason_input.value.strip()
        inq["closed_by"] = interaction.user.display_name
        inq["closed_at"] = datetime.utcnow().strftime("%d.%m.%Y %H:%M UTC")
        prosecution_storage.set(self.inq_id, inq)

        msg_ref = getattr(interaction, "message", None)
        if msg_ref and hasattr(msg_ref, "edit"):
            try:
                await msg_ref.edit(embed=build_inquiry_embed(inq), view=None)
            except Exception:
                pass
        await _refresh_prosecution_panel_if_present(interaction.guild)
        await interaction.followup.send(f"✅ `{self.inq_id}` takipsizlik kararıyla kapatıldı.", ephemeral=True)


class InvestigationActionView(discord.ui.View):
    """Persistent prosecutorial action buttons on open SAV-YYYY/XXX inquiries."""

    def __init__(self, inq_id: str):
        super().__init__(timeout=None)
        self.inq_id = inq_id

        specs = [
            ("İfade / Delil Çağrısı · Summon", discord.ButtonStyle.primary, "📩", f"mc_sav_summon:{inq_id}", self._on_summon),
            ("İddianameyle Divana Sevk · Indict", discord.ButtonStyle.danger, "⚖️", f"mc_sav_indict:{inq_id}", self._on_indict),
            ("Takipsizlik / Kapat · Close", discord.ButtonStyle.secondary, "📁", f"mc_sav_close:{inq_id}", self._on_close),
        ]
        for label, style, emoji, cid, cb in specs:
            btn = discord.ui.Button(label=label, style=style, emoji=emoji, custom_id=cid)
            btn.callback = cb
            self.add_item(btn)

    async def _require_prosecutor(self, interaction: discord.Interaction) -> bool:
        if _is_prosecutor_or_admin(interaction.user):
            return True
        await interaction.response.send_message(
            "⛔ Yalnızca **Cemiyet Başsavcısı (`BSV`)**, **Savcılar (`SVC`)** veya Yöneticiler soruşturma dosyası yönetebilir.",
            ephemeral=True,
        )
        return False

    async def _on_summon(self, interaction: discord.Interaction):
        if not await self._require_prosecutor(interaction):
            return
        inq = prosecution_storage.get(self.inq_id) or {}
        await interaction.response.send_modal(InvestigationSummonModal(self.inq_id, inq.get("suspect", "")))

    async def _on_indict(self, interaction: discord.Interaction):
        if not await self._require_prosecutor(interaction):
            return
        inq = prosecution_storage.get(self.inq_id)
        if not inq:
            return await interaction.response.send_message("❌ Soruşturma bulunamadı.", ephemeral=True)
        if inq.get("indicted_case_id"):
            return await interaction.response.send_message(
                f"ℹ️ Bu dosya zaten **`{inq['indicted_case_id']}`** ile Divana sevk edilmiş.",
                ephemeral=True,
            )
        await interaction.response.defer(ephemeral=True, thinking=True)
        case, msg, issue_no = await _file_indictment_to_court(
            guild=interaction.guild,
            bot=interaction.client,
            actor=interaction.user,
            defendant=inq["suspect"],
            subject=inq["subject"],
            evidence=inq["evidence"],
            demand=inq.get("demand", ""),
            prosecution_ref=self.inq_id,
            fallback_channel=interaction.channel,
        )
        inq["status"] = f"⚖️ İddianameyle Divana Sevk Edildi ({case['id']})"
        inq["indicted_case_id"] = case["id"]
        prosecution_storage.set(self.inq_id, inq)

        msg_ref = getattr(interaction, "message", None)
        if msg_ref and hasattr(msg_ref, "edit"):
            try:
                await msg_ref.edit(embed=build_inquiry_embed(inq), view=None)
            except Exception:
                pass
        await _refresh_prosecution_panel_if_present(interaction.guild)
        url_part = f" {msg.jump_url}" if getattr(msg, "jump_url", None) else ""
        gaz_part = f" · Resmî Gazete: `{issue_no}`" if issue_no else ""
        await interaction.followup.send(
            f"✅ `{self.inq_id}` iddianameye dönüştürülüp Divana sevk edildi: **`{case['id']}`**{url_part}{gaz_part}",
            ephemeral=True,
        )

    async def _on_close(self, interaction: discord.Interaction):
        if not await self._require_prosecutor(interaction):
            return
        await interaction.response.send_modal(InquiryCloseModal(self.inq_id))


class InvestigationModal(discord.ui.Modal, title="Soruşturma Aç · Open Inquiry"):
    suspect_input = discord.ui.TextInput(
        label="Şüpheli Devlet veya Üye / Suspect",
        placeholder="Örn / e.g.: Nova Roma veya @Üye",
        required=True,
        max_length=100,
    )
    subject_input = discord.ui.TextInput(
        label="Soruşturma Konusu / Inquiry Subject",
        placeholder="Örn / e.g.: Şart Md. 10 İhlali / İzinsiz Saldırı",
        required=True,
        max_length=150,
    )
    evidence_input = discord.ui.TextInput(
        label="Bulgular & İhlal Dayanağı / Findings",
        style=discord.TextStyle.paragraph,
        placeholder="Somut bulgular, tarih, ilgili Şart maddesi ve varsa EMS-YYYY/XXX emsali...",
        required=True,
        max_length=1200,
    )
    demand_input = discord.ui.TextInput(
        label="Ön Görüş / Yaptırım İstemi (Opsiyonel)",
        placeholder="Örn / e.g.: Tazminat veya geçici askıya alma istemi",
        required=False,
        max_length=250,
    )

    async def on_submit(self, interaction: discord.Interaction):
        if not _is_prosecutor_or_admin(interaction.user):
            return await interaction.response.send_message(
                "⛔ Yalnızca **Cemiyet Başsavcısı (`BSV`)**, **Savcılar (`SVC`)** veya Yöneticiler soruşturma açabilir.",
                ephemeral=True,
            )
        await interaction.response.defer(ephemeral=True)
        sid = _next_inquiry_id()
        evidence = self.evidence_input.value.strip()
        subject = self.subject_input.value.strip()
        demand = (self.demand_input.value or "").strip()
        cited = _increment_precedent_citations(_extract_precedent_refs(f"{subject}\n{evidence}\n{demand}"))
        inq = {
            "id": sid,
            "guild_id": interaction.guild_id,
            "suspect": self.suspect_input.value.strip(),
            "subject": subject,
            "evidence": evidence,
            "demand": demand,
            "cited_precedents": cited,
            "prosecutor_uid": str(interaction.user.id),
            "prosecutor_name": interaction.user.display_name,
            "status": "🔍 Soruşturma Açık · Active Inquiry",
            "summon_notes": [],
            "created_at": datetime.utcnow().strftime("%d.%m.%Y %H:%M UTC"),
        }
        prosecution_storage.set(sid, inq)

        view = InvestigationActionView(sid)
        if hasattr(interaction.client, "add_view"):
            interaction.client.add_view(view)
        dest = resolve_panel_channel(interaction.guild, None, "savcilik-makami-prosecution", interaction.channel)
        msg = None
        if dest and hasattr(dest, "send"):
            msg = await dest.send(embed=build_inquiry_embed(inq), view=view)
            inq["office_channel_id"] = dest.id
            inq["office_message_id"] = msg.id
            prosecution_storage.set(sid, inq)
        await _refresh_prosecution_panel_if_present(interaction.guild)
        url_part = f": {msg.jump_url}" if getattr(msg, "jump_url", None) else ""
        await interaction.followup.send(f"✅ Savcılık soruşturması açıldı (`{sid}`){url_part}", ephemeral=True)


class DirectIndictmentModal(discord.ui.Modal, title="İddianame Sun · Indict to ICJ"):
    suspect_input = discord.ui.TextInput(
        label="Sanık Devlet veya Üye / Accused",
        placeholder="Örn / e.g.: Nova Roma",
        required=True,
        max_length=100,
    )
    subject_input = discord.ui.TextInput(
        label="İddianame Konusu / Indictment Subject",
        placeholder="Örn / e.g.: Antlaşma İhlali ve Tazminat Talebi",
        required=True,
        max_length=150,
    )
    evidence_input = discord.ui.TextInput(
        label="İddianame Metni & Deliller / Indictment",
        style=discord.TextStyle.paragraph,
        placeholder="Deliller, ihlal edilen Şart/antlaşma maddesi ve EMS-YYYY/XXX emsal atıfları...",
        required=True,
        max_length=1400,
    )
    demand_input = discord.ui.TextInput(
        label="Talep Edilen Yaptırım / Penalty Demand",
        placeholder="Örn / e.g.: 1.500 Gold tazminat veya geçici askıya alma",
        required=False,
        max_length=250,
    )

    async def on_submit(self, interaction: discord.Interaction):
        if not _is_prosecutor_or_admin(interaction.user):
            return await interaction.response.send_message(
                "⛔ Yalnızca **Cemiyet Başsavcısı (`BSV`)**, **Savcılar (`SVC`)** veya Yöneticiler iddianame sunabilir.",
                ephemeral=True,
            )
        await interaction.response.defer(ephemeral=True, thinking=True)
        case, msg, issue_no = await _file_indictment_to_court(
            guild=interaction.guild,
            bot=interaction.client,
            actor=interaction.user,
            defendant=self.suspect_input.value.strip(),
            subject=self.subject_input.value.strip(),
            evidence=self.evidence_input.value.strip(),
            demand=(self.demand_input.value or "").strip(),
            fallback_channel=interaction.channel,
        )
        url_part = f": {msg.jump_url}" if getattr(msg, "jump_url", None) else ""
        gaz_part = f" · Resmî Gazete: `{issue_no}`" if issue_no else ""
        await interaction.followup.send(
            f"✅ Savcılık iddianamesi Divana sunuldu (`{case['id']}`){url_part}{gaz_part}",
            ephemeral=True,
        )


def _format_inquiry_lines() -> tuple[list[str], int, int]:
    all_inq = [r for r in prosecution_storage.read().values() if isinstance(r, dict) and r.get("id")]
    active = [r for r in all_inq if not r.get("indicted_case_id") and not r.get("close_reason")]
    closed = len(all_inq) - len(active)
    lines = [
        f"• **`{r['id']}`** — `{r['suspect']}` · **{r['subject'][:45]}** ({r.get('status', '-')})"
        for r in list(reversed(active))[:12]
    ]
    return lines, len(active), closed


class ProsecutionPanelView(discord.ui.View):
    """Persistent control panel for League Prosecution Office inside #savcilik-makami-prosecution."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Soruşturma Başlat · Open Inquiry", style=discord.ButtonStyle.primary, emoji="📁", custom_id="mc_sav_open", row=0)
    async def btn_open(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if not _is_prosecutor_or_admin(interaction.user):
            return await interaction.response.send_message(
                "⛔ Yalnızca **Cemiyet Başsavcısı (`BSV`)**, **Savcılar (`SVC`)** veya Yöneticiler soruşturma açabilir.",
                ephemeral=True,
            )
        await interaction.response.send_modal(InvestigationModal())

    @discord.ui.button(label="Doğrudan İddianame · Indict to ICJ", style=discord.ButtonStyle.danger, emoji="⚖️", custom_id="mc_sav_direct_indict", row=0)
    async def btn_indict(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if not _is_prosecutor_or_admin(interaction.user):
            return await interaction.response.send_message(
                "⛔ Yalnızca **Cemiyet Başsavcısı (`BSV`)**, **Savcılar (`SVC`)** veya Yöneticiler iddianame sunabilir.",
                ephemeral=True,
            )
        await interaction.response.send_modal(DirectIndictmentModal())

    @discord.ui.button(label="Açık Soruşturmalar · Inquiries", style=discord.ButtonStyle.secondary, emoji="📋", custom_id="mc_sav_list", row=1)
    async def btn_list(self, interaction: discord.Interaction, _button: discord.ui.Button):
        lines, _act, _cls = _format_inquiry_lines()
        await interaction.response.send_message(
            embed=discord.Embed(
                title="📜 AÇIK SAVCILIK SORUŞTURMALARI",
                description="\n".join(lines) if lines else "*Şu an açık soruşturma yok.*",
                colour=0x991B1B,
            ),
            ephemeral=True,
        )

    @discord.ui.button(label="Emsal İçtihatlar · Precedents", style=discord.ButtonStyle.secondary, emoji="📕", custom_id="mc_sav_precedents", row=1)
    async def btn_prec(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.send_message(embed=build_precedents_list_embed(), ephemeral=True)

    @discord.ui.button(label="Büroyu Yenile · Refresh", style=discord.ButtonStyle.success, emoji="🔄", custom_id="mc_sav_refresh", row=1)
    async def btn_refresh(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if not _is_prosecutor_or_admin(interaction.user):
            return await interaction.response.send_message("⛔ Yetkiniz yok.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        emb, vw = build_prosecution_panel(interaction.guild)
        if interaction.message and hasattr(interaction.message, "edit"):
            await interaction.message.edit(embed=emb, view=vw)
        await interaction.followup.send("✅ Savcılık Makamı paneli güncellendi.", ephemeral=True)


def build_prosecution_panel(guild: discord.Guild | None = None) -> tuple[discord.Embed, discord.ui.View]:
    chiefs, prosecutors = _collect_prosecutors(guild)
    _lines, active_cnt, closed_cnt = _format_inquiry_lines()
    court_ch = resolve_panel_channel(guild, None, "yargi-divani-icj-court", None) if guild else None
    audit_ch = resolve_panel_channel(guild, None, "denetim-kaydi-audit-log", None) if guild else None

    embed = discord.Embed(
        title="📜 SAVCILIK MAKAMI & SORUŞTURMA BÜROSU · PROSECUTION CHAMBERS",
        description=(
            "Milletler Cemiyeti adına Şart (`MC-KS-001`) ve antlaşma ihlallerini resen veya şikâyet üzerine soruşturan **kamu iddia makamıdır**.\n\n"
            "• **1) Soruşturma (`SAV-YYYY/XXX`):** **`📁 Soruşturma Başlat`** veya `/sorusturma-ac` ile gizli ön soruşturma başlatıp ifade/delil toplayın.\n"
            f"• **2) İddianame & Mütalaa:** Yeterli delil oluştuğunda dosyayı {court_ch.mention if court_ch else '`#⚖️│yargi-divani-icj-court`'} salonuna **`⚖️ İddianame`** ile sevk edin veya açık davalara **`📜 Savcılık Mütalaası`** sunun.\n"
            f"• **3) Denetim Kaydı & Kuvvetler Ayrılığı:** {audit_ch.mention if audit_ch else '`#🛡️│denetim-kaydi-audit-log`'} ihlallerini inceleyebilirsiniz; ancak hüküm yetkisi yalnızca bağımsız Divan Yargıçlarına aittir."
        ),
        colour=0x991B1B,
    )
    embed.add_field(name="⚖️ Cemiyet Başsavcısı (`BSV`)", value=", ".join(chiefs) or "*Atama Bekleniyor (`/kurum-ata`)*", inline=True)
    embed.add_field(name="📜 Cemiyet Savcıları (`SVC`)", value=", ".join(prosecutors) or "*Atama Bekleniyor (`/kurum-ata`)*", inline=True)
    embed.add_field(name="📊 Soruşturma Sicili", value=f"Açık Dosya: `{active_cnt}` · Sevk / Kapatılan: `{closed_cnt}`", inline=False)
    embed.set_footer(text="MC-KS-001 Cemiyet Başsavcılığı · Bağımsız İddia Makamı")
    return embed, ProsecutionPanelView()


class GazetteBulletinModal(discord.ui.Modal, title="Resmî Gazete · Official Gazette"):
    b_tag = discord.ui.TextInput(label="Kategori / Category Tag", placeholder="Örn / e.g.: ACİL KRİZ / CRISIS ALERT, ZİRVE / SUMMIT...", required=True, max_length=60)
    b_title = discord.ui.TextInput(label="Manşet / Headline", placeholder="Örn / e.g.: Olağanüstü Oturum / Emergency Session", required=True, max_length=150)
    b_body = discord.ui.TextInput(label="Resmî Metin / Official Body", style=discord.TextStyle.paragraph, placeholder="Metin / Text...", required=True, max_length=2000)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        issue_no = await publish_to_gazette(
            interaction.client,
            interaction.guild_id,
            self.b_tag.value.strip().upper(),
            self.b_title.value.strip(),
            self.b_body.value.strip(),
            issuer=f"{interaction.user.display_name} · Cemiyet Genel Sekreterliği",
        )
        await interaction.followup.send(
            f"✅ Resmi Gazete'de yayınlandı: **`{issue_no}`**" if issue_no else "⚠️ Yayınlandı ancak `#mc-resmi-gazete` kanalı bulunamadı.",
            ephemeral=True,
        )


class TreatiesICJCog(commands.Cog):
    """International Treaties, ICJ Court, and UN Official Gazette commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(TreatyPanelView())
        self.bot.add_view(ICJPanelView())
        self.bot.add_view(ICJChambersPanelView())
        self.bot.add_view(ProsecutionPanelView())
        for tid in treaties_storage.read():
            if isinstance(tid, str):
                self.bot.add_view(TreatyActionView(tid))
        for sid, sdata in prosecution_storage.read().items():
            if isinstance(sid, str) and isinstance(sdata, dict) and not sdata.get("indicted_case_id") and not sdata.get("close_reason"):
                self.bot.add_view(InvestigationActionView(sid))
        for cid, cdata in icj_storage.read().items():
            if not (isinstance(cid, str) and isinstance(cdata, dict)):
                continue
            if not cdata.get("archived") and not cdata.get("verdict"):
                self.bot.add_view(ICJActionView(cid))
            else:
                self.bot.add_view(ICJArchivedVerdictView(cid, precedent_id=cdata.get("precedent_id")))

    @app_commands.command(name="antlasma-paneli-kur", description="İlgili resmi kanala Bretton-Woods tarzı butonlu Antlaşma İmza Masası kurar")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(kanal="İsteğe bağlı hedef kanal (boş bırakılırsa otomatik resmi kanala kurulur)")
    async def cmd_treaty_panel(self, interaction: discord.Interaction, kanal: discord.TextChannel = None):
        target = resolve_panel_channel(interaction.guild, kanal, "antlasmalar-treaties", interaction.channel)
        embed, view = build_treaty_panel()
        st = await upsert_panel_message(target, ["📜 ANTLAŞMALAR", "📜 MİLLETLER CEMİYETİ · ANTLAŞMALAR"], embed, view)
        await interaction.response.send_message(f"✅ Butonlu Antlaşma Masası {target.mention} kanalında güncellendi (`{st}`).", ephemeral=True)

    @app_commands.command(name="divan-paneli-kur", description="İlgili resmi kanala Uluslararası Yargı Divanı başvuru paneli kurar")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(kanal="İsteğe bağlı hedef kanal (boş bırakılırsa otomatik #yargi-divani-icj-court kanalına kurulur)")
    async def cmd_icj_panel(self, interaction: discord.Interaction, kanal: discord.TextChannel = None):
        target = resolve_panel_channel(interaction.guild, kanal, "yargi-divani-icj-court", interaction.channel)
        embed, view = build_icj_panel(interaction.guild)
        st = await upsert_panel_message(target, ["⚖️ ULUSLARARASI YARGI DİVANI"], embed, view)
        await interaction.response.send_message(f"✅ Uluslararası Yargı Divanı başvuru paneli {target.mention} kanalında güncellendi (`{st}`).", ephemeral=True)

    @app_commands.command(name="divan-ofisi-kur", description="Divan Başkanı ve Yargıçlar Ofisi (#divan-makami-chambers) panelini kurar")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(kanal="İsteğe bağlı hedef kanal (boş bırakılırsa #divan-makami-chambers)")
    async def cmd_icj_chambers_panel(self, interaction: discord.Interaction, kanal: discord.TextChannel = None):
        target = resolve_panel_channel(interaction.guild, kanal, "divan-makami-chambers", interaction.channel)
        embed, view = build_chambers_panel(interaction.guild)
        st = await upsert_panel_message(target, ["⚖️ DİVAN MAKAMI & YARGIÇLAR OFİSİ"], embed, view)
        await interaction.response.send_message(f"✅ Divan Makamı Ofisi {target.mention} kanalında güncellendi (`{st}`).", ephemeral=True)

    @app_commands.command(name="savcilik-paneli-kur", description="Cemiyet Başsavcılığı & Soruşturma Bürosu (#savcilik-makami-prosecution) panelini kurar")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(kanal="İsteğe bağlı hedef kanal (boş bırakılırsa #savcilik-makami-prosecution)")
    async def cmd_prosecution_panel(self, interaction: discord.Interaction, kanal: discord.TextChannel = None):
        target = resolve_panel_channel(interaction.guild, kanal, "savcilik-makami-prosecution", interaction.channel)
        embed, view = build_prosecution_panel(interaction.guild)
        st = await upsert_panel_message(target, ["📜 SAVCILIK MAKAMI & SORUŞTURMA"], embed, view)
        await interaction.response.send_message(f"✅ Savcılık Makamı Bürosu {target.mention} kanalında güncellendi (`{st}`).", ephemeral=True)

    @app_commands.command(name="sorusturma-ac", description="Cemiyet Başsavcılığı adına yeni gizli soruşturma (SAV-YYYY/XXX) başlatır (Savcı/Yönetici)")
    async def cmd_open_inquiry(self, interaction: discord.Interaction):
        if not _is_prosecutor_or_admin(interaction.user):
            return await interaction.response.send_message(
                "⛔ Yalnızca **Cemiyet Başsavcısı (`BSV`)**, **Cemiyet Savcıları (`SVC`)** veya Yöneticiler soruşturma başlatabilir.",
                ephemeral=True,
            )
        await interaction.response.send_modal(InvestigationModal())

    @app_commands.command(name="iddianame-sun", description="Cemiyet Başsavcılığı adına doğrudan Yargı Divanına İddianame sunar (Savcı/Yönetici)")
    async def cmd_file_indictment(self, interaction: discord.Interaction):
        if not _is_prosecutor_or_admin(interaction.user):
            return await interaction.response.send_message(
                "⛔ Yalnızca **Cemiyet Başsavcısı (`BSV`)**, **Cemiyet Savcıları (`SVC`)** veya Yöneticiler iddianame sunabilir.",
                ephemeral=True,
            )
        await interaction.response.send_modal(DirectIndictmentModal())

    @app_commands.command(name="antlasma-sun", description="Devletlerin imzalayıp onaylayabileceği uluslararası bir antlaşma sunar")
    @app_commands.describe(tur="Antlaşma türü (opsiyonel)")
    @app_commands.choices(tur=[
        app_commands.Choice(name="📦 Serbest Ticaret & Gümrük", value="trade"),
        app_commands.Choice(name="🛡️ İttifak & Karşılıklı Savunma", value="alliance"),
        app_commands.Choice(name="🕊️ Saldırmazlık & Tarafsızlık", value="nonaggression"),
        app_commands.Choice(name="⚖️ Barış & Sınır Antlaşması", value="peace"),
        app_commands.Choice(name="📜 Çok Taraflı Cemiyet Konvansiyonu", value="multilateral"),
    ])
    async def cmd_propose_treaty(self, interaction: discord.Interaction, tur: app_commands.Choice[str] = None):
        user_data = delegates_storage.get(str(interaction.user.id))
        if not user_data or not user_data.get("nation"):
            return await interaction.response.send_message("❌ Antlaşma sunabilmek için önce Cemiyet Akreditasyonu almalısınız.", ephemeral=True)
        if not can_member_sign_for_state(interaction.user, user_data):
            return await interaction.response.send_message(
                "⛔ **İmza Yetkisi Yetersiz:** Antlaşma taslağı sunma yetkisi yalnızca **👑 Devlet Başkanı** veya **🌐 Dışişleri Bakanı**na aittir.",
                ephemeral=True,
            )
        await interaction.response.send_modal(TreatyModal(user_data["nation"], tur.value if tur else "multilateral", interaction.channel_id))

    @app_commands.command(name="emsal-karar", description="Uluslararası Yargı Divanı adına bağlayıcı Emsal Karar (EMS-YYYY/XXX) ilân eder (Yargıç/Yönetici)")
    @app_commands.describe(
        hukuk_dali="Emsal kararın ilgili olduğu hukuk dalı",
        kaynak_dosya="İsteğe bağlı kaynak dava veya zirve no (ör. ICJ-2026/002, ZRV-2026/001)",
    )
    @app_commands.choices(hukuk_dali=[
        app_commands.Choice(name="📜 Antlaşma & Yazılı Şekil · Treaty Law", value="treaty"),
        app_commands.Choice(name="📎 Delil & İspat Hukuku · Evidence Standard", value="evidence"),
        app_commands.Choice(name="💳 Tazminat & Yaptırım · Reparations", value="reparations"),
        app_commands.Choice(name="🛡️ Egemenlik & Sınır · Sovereignty", value="sovereignty"),
        app_commands.Choice(name="⚖️ Yargılama Usulü & İçtihat · Procedural", value="procedure"),
    ])
    async def cmd_issue_precedent(
        self,
        interaction: discord.Interaction,
        hukuk_dali: app_commands.Choice[str] | None = None,
        kaynak_dosya: str = "",
    ):
        if not _is_icj_or_admin(interaction.user):
            return await interaction.response.send_message(
                "⛔ Yalnızca **Divan Başkanı (`DB`)**, **Divan Yargıçları (`UYD`)** veya Yöneticiler Emsal Karar ilân edebilir.",
                ephemeral=True,
            )
        cat_val = hukuk_dali.value if hukuk_dali else "procedure"
        ref = kaynak_dosya.strip()
        attach_id = ref if (ref.upper().startswith("ICJ-") and isinstance(icj_storage.get(ref.upper()), dict)) else None
        await interaction.response.send_modal(ICJPrecedentModal(cat_val, default_ref=ref, attach_case_id=attach_id))

    @app_commands.command(name="emsal-listesi", description="Divanın yürürlükteki Emsal Kararlar (EMS-YYYY/XXX) sicilini gösterir")
    @app_commands.describe(
        hukuk_dali="İsteğe bağlı hukuk dalı filtresi",
        arama="Başlık, ilke veya kaynak no araması",
        mulgalari_goster="Yürürlükten kaldırılan (mülga) kararları da göster",
    )
    @app_commands.choices(hukuk_dali=[
        app_commands.Choice(name="📜 Antlaşma & Yazılı Şekil", value="treaty"),
        app_commands.Choice(name="📎 Delil & İspat Hukuku", value="evidence"),
        app_commands.Choice(name="💳 Tazminat & Yaptırım", value="reparations"),
        app_commands.Choice(name="🛡️ Egemenlik & Sınır", value="sovereignty"),
        app_commands.Choice(name="⚖️ Yargılama Usulü & İçtihat", value="procedure"),
    ])
    async def cmd_list_precedents(
        self,
        interaction: discord.Interaction,
        hukuk_dali: app_commands.Choice[str] | None = None,
        arama: str = "",
        mulgalari_goster: bool = False,
    ):
        emb = build_precedents_list_embed(
            category=hukuk_dali.value if hukuk_dali else None,
            query=arama,
            include_repealed=mulgalari_goster,
        )
        await interaction.response.send_message(embed=emb, ephemeral=True)

    @app_commands.command(name="emsal-kaldir", description="Yürürlükteki bir Emsal Kararı gerekçeli kararla yürürlükten (mülga) kaldırır (Yargıç/Yönetici)")
    @app_commands.describe(emsal_no="Kaldırılacak emsal karar numarası (ör. EMS-2026/001)", gerekce="İlga / kaldırma gerekçesi")
    async def cmd_repeal_precedent(self, interaction: discord.Interaction, emsal_no: str, gerekce: str):
        if not _is_icj_or_admin(interaction.user):
            return await interaction.response.send_message(
                "⛔ Yalnızca **Divan Başkanı (`DB`)**, **Divan Yargıçları (`UYD`)** veya Yöneticiler Emsal Karar ilga edebilir.",
                ephemeral=True,
            )
        pid = emsal_no.strip().upper()
        prec = precedents_storage.get(pid)
        if not isinstance(prec, dict):
            return await interaction.response.send_message(f"❌ `{pid}` numaralı emsal karar bulunamadı.", ephemeral=True)
        if prec.get("status") != "ACTIVE":
            return await interaction.response.send_message(f"ℹ️ `{pid}` zaten yürürlükten kaldırılmış (`MÜLGA`).", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        reason_clean = gerekce.strip() or "Yeni içtihatla yürürlükten kaldırıldı."
        prec["status"] = "REPEALED"
        prec["repeal_reason"] = reason_clean
        prec["repealed_by"] = interaction.user.display_name
        prec["repealed_at"] = datetime.utcnow().strftime("%d.%m.%Y %H:%M UTC")
        precedents_storage.set(pid, prec)

        if interaction.guild and prec.get("archive_channel_id") and prec.get("archive_message_id"):
            ach = interaction.guild.get_channel(int(prec["archive_channel_id"]))
            if isinstance(ach, discord.TextChannel):
                try:
                    amsg = await ach.fetch_message(int(prec["archive_message_id"]))
                    await amsg.edit(embed=build_precedent_embed(prec))
                except Exception:
                    pass
        issue_no = None
        if interaction.guild:
            issue_no = await publish_to_gazette(
                interaction.client,
                interaction.guild_id,
                "EMSAL İÇTİHAT İLGASI · PRECEDENT REPEALED",
                f"{pid}: {prec.get('title', '-')}",
                f"**Karar Makamı:** <@{interaction.user.id}>\n**Gerekçe:** {reason_clean}",
                color=0x6B7280,
                issuer=f"{interaction.user.display_name} · Uluslararası Yargı Divanı",
            )
        await _refresh_icj_and_chambers_panels(interaction.guild)
        await interaction.followup.send(
            f"✅ `{pid}` numaralı emsal karar yürürlükten kaldırıldı (`MÜLGA`)." + (f" Resmî Gazete: `{issue_no}`" if issue_no else ""),
            ephemeral=True,
        )

    @app_commands.command(name="dava-ac", description="Uluslararası Yargı Divanı'nda (Madde 23-28) başka bir devlete dava açar")
    @app_commands.describe(davali_ulke="Şikâyet edilen davalı ülke")
    async def cmd_open_icj(self, interaction: discord.Interaction, davali_ulke: str):
        user_data = delegates_storage.get(str(interaction.user.id))
        if not user_data or not user_data.get("nation"):
            return await interaction.response.send_message("❌ Dava açabilmek için önce Cemiyet Akreditasyonu almalısınız.", ephemeral=True)
        if not (can_member_represent_state_diplomacy(interaction.user, user_data, allow_mp_icj=True) or _is_icj_or_admin(interaction.user)):
            return await interaction.response.send_message(
                "⛔ **Yetki Yetersiz · Official State Authority Required**\n"
                "Uluslararası Yargı Divanı'nda devletlerarası dava açma yetkisi yalnızca yetkili devlet/meclis makamlarına aittir; "
                "sade **Vatandaşlar (`Vatandaş`)** devlet davası açamaz. Bireysel başvurular için `#🎫│destek-masasi-tickets` kullanın.",
                ephemeral=True,
            )
        await interaction.response.send_modal(ICJModal(user_data["nation"], davali_ulke.strip()))

    @app_commands.command(name="resmi-gazete-yayinla", description="Cemiyet Resmi Gazetesi'nde özel bülten veya kriz duyurusu yayınlar")
    async def cmd_publish_gazette(self, interaction: discord.Interaction):
        if not _is_secretariat_or_spokesperson(interaction.user):
            return await interaction.response.send_message(
                "❌ Yalnızca Genel Sekreterlik (`SG`, `DSG`, `GKB`, `GKB-V`), Cemiyet Sözcüsü veya Yöneticiler Resmi Gazete yayını yapabilir.",
                ephemeral=True,
            )
        await interaction.response.send_modal(GazetteBulletinModal())

    @app_commands.command(name="resmi-gazete-kanali", description="Cemiyet Resmi Gazetesi yayın kanalını belirler (Yönetici)")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(kanal="Resmi Gazete yayın kanalı")
    async def cmd_set_gazette_channel(self, interaction: discord.Interaction, kanal: discord.TextChannel):
        gazette_storage.set(f"config_{interaction.guild_id}", {"channel_id": kanal.id})
        await interaction.response.send_message(f"✅ Resmi Gazete yayın kanalı {kanal.mention} olarak ayarlandı.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(TreatiesICJCog(bot))
