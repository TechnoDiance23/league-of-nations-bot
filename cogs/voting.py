import uuid
from datetime import datetime, timedelta
import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils.storage import votes_storage, resolutions_storage, delegates_storage
from utils.constants import (
    ROLE_ADMIN,
    ROLE_COUNCIL_CHAIR,
    ROLE_GA_PRESIDENT,
    ROLE_HEAD_OF_DELEGATION,
    ROLE_ICJ_PRESIDENT,
    ROLE_PRESIDENT,
    ROLE_SG,
    can_member_speak_in_assembly,
    can_member_vote_for_state,
    resolve_panel_channel,
)
from utils.embed_factory import voting_embed, resolution_archive_embed, compute_vote_outcome
from utils.un_gazette import (
    get_country_record,
    save_country_record,
    normalize_country_key,
    publish_to_gazette,
    upsert_panel_message,
)
from cogs.delegation import (
    assign_head_of_delegation,
    assign_institutional_chair,
    resolve_member,
)

DURATIONS = {
    "1m": ("⚡ 1 Dk (Hızlı)", 60),
    "5m": ("⚡ 5 Dk (Oturum)", 300),
    "15m": ("⏱️ 15 Dk (Kısa Oturum)", 900),
    "6h": ("🚨 6 Saat (Acil Durum · Md. 18.6)", 21600),
    "24h": ("📅 24 Saat (Olağan · Md. 18.5)", 86400),
    "manual": ("🔒 Manuel (Genel Kurul Başkanı Kapatır)", 0),
}

ELECTION_OFFICES = {
    "GA_PRES": {
        "label": "🪑 Genel Kurul Başkanı · Assembly President",
        "office_code": "GKB",
        "role_name": ROLE_GA_PRESIDENT,
        "kind": "chair",
        "desc": "Genel Kurul oturum ve oylama başkanı (Md. 13)",
    },
    "COUNCIL_CHAIR": {
        "label": "🏛️ Şûra Başkanı · Council Chair",
        "office_code": "ŞB",
        "role_name": ROLE_COUNCIL_CHAIR,
        "kind": "chair",
        "desc": "Yüksek Şûra / Konsey Başkanı",
    },
    "ICJ_PRES": {
        "label": "⚖️ Divan Başkanı · ICJ President",
        "office_code": "DB",
        "role_name": ROLE_ICJ_PRESIDENT,
        "kind": "chair",
        "desc": "Uluslararası Yargı Divanı Başkanı",
    },
    "HEAD_DEL": {
        "label": "🎖️ Delege Başkanı · Head of Delegation",
        "office_code": "Del.B.",
        "role_name": ROLE_HEAD_OF_DELEGATION,
        "kind": "head_del",
        "desc": "Ülke delegasyon başkanı (Genel Kurul seçimi)",
    },
}


def can_manage_vote(member: discord.Member, author_id: str) -> bool:
    mid = getattr(member, "id", None)
    if (mid is not None and str(mid) == str(author_id)) or getattr(getattr(member, "guild_permissions", None), "administrator", False):
        return True
    if {r.name for r in getattr(member, "roles", ())} & {ROLE_SG, ROLE_GA_PRESIDENT, ROLE_COUNCIL_CHAIR, ROLE_ADMIN}:
        return True
    rec = delegates_storage.get(str(mid), {}) if mid is not None else {}
    return rec.get("office_code") in {"SG", "DSG", "GKB", "GKB-V", "ŞB"}


def _can_launch_election(member: discord.Member, office_key: str) -> bool:
    if can_manage_vote(member, ""):
        return True
    role_names = {r.name for r in getattr(member, "roles", ())}
    rec = delegates_storage.get(str(member.id), {}) or {}
    if office_key == "HEAD_DEL":
        return can_member_vote_for_state(member, rec)
    return ROLE_PRESIDENT in role_names or rec.get("office_code") == "Pres."


def find_state_head_of_delegation(nation: str) -> dict | None:
    """Return active accredited Head of Delegation (Del.B.) for nation, if appointed or elected."""
    target_key = normalize_country_key(nation)
    if not target_key:
        return None
    for rec in delegates_storage.read().values():
        if not isinstance(rec, dict):
            continue
        if rec.get("status", "APPROVED") != "APPROVED":
            continue
        if normalize_country_key(rec.get("nation", "")) != target_key:
            continue
        if rec.get("office_code") == "Del.B." or rec.get("office_role_name") == ROLE_HEAD_OF_DELEGATION:
            return rec
    return None


def check_state_voter_eligibility(
    user_id: str,
    member,
    user_data: dict | None,
    vote_data: dict,
    country_rec: dict | None = None,
) -> tuple[bool, tuple[str, str, str] | None, str]:
    """Validate 1-State-1-Vote rules: Del.B. if appointed, otherwise first voting delegate counts."""
    if not user_data or not user_data.get("nation"):
        return (
            False,
            None,
            "❌ Oy kullanmak için önce `#🚪│kayit-masasi-register` üzerinden kayıt olup akredite olmalısınız.\n"
            "*Please complete registration and accreditation before casting a state vote.*",
        )
    if not can_member_vote_for_state(member, user_data):
        return (
            False,
            None,
            "⛔ **Oylama Yetkisi Yetersiz · Delegate / Cabinet Authority Required**\n"
            "Devlet adına oy kullanma yetkisi yalnızca o ülkenin **Kabine üyelerine (Devlet Başkanı & Bakanlar)**, "
            "**Delege Başkanına** veya **Asil / Yedek Temsilcisine** aittir; sade **Vatandaşlar** veya **Gözlemciler** oy veremez.\n"
            "*Only Cabinet members, Head of Delegation, or Primary/Alternate Delegates may vote for their State.*",
        )

    nation = user_data["nation"]
    crec = country_rec if country_rec is not None else get_country_record(nation)
    status = crec.get("status", "MEMBER")
    if status == "OBSERVER":
        return False, None, "👁️ Gözlemci statüsünün oy hakkı yoktur. / Observers have no voting rights."
    if status == "SUSPENDED":
        return False, None, "⛔ Ülkenizin oy hakkı askıda (Md. 10). / Country vote suspended (Art. 10)."

    voter_key = f"state_{normalize_country_key(nation)}"
    existing_vote = vote_data.get("votes", {}).get(voter_key)
    office_code = user_data.get("office_code", "Asil")
    designated_delb = find_state_head_of_delegation(nation)

    if designated_delb:
        is_self_delb = office_code == "Del.B." or str(designated_delb.get("user_id")) == str(user_id)
        if not is_self_delb:
            return (
                False,
                None,
                f"⚖️ **{nation}** adına yetkili **Delege Başkanı** (<@{designated_delb['user_id']}>) atanmış/seçilmiştir; "
                f"1 Devlet = 1 Oy kuralı gereği devlet oyunu **Delege Başkanı** kullanır.\n"
                f"*Head of Delegation (<@{designated_delb['user_id']}>) is registered for {nation} and casts its state vote.*",
            )
        return True, (voter_key, nation, "Del.B."), ""

    if existing_vote and existing_vote.get("user_id") != str(user_id):
        prev_code = existing_vote.get("office_code")
        if office_code == "Del.B." and prev_code != "Del.B.":
            return True, (voter_key, nation, "Del.B."), ""
        return (
            False,
            None,
            f"⚖️ **{nation}** için kayıtlı bir **Delege Başkanı** bulunmadığından temsilciniz "
            f"<@{existing_vote['user_id']}>'in kullandığı oy ülkenin resmî oyu olarak sayılmıştır *(1 Ülke = 1 Oy)*.\n"
            f"*No Head of Delegation is appointed for {nation}; the first delegate vote (<@{existing_vote['user_id']}>) counts as your State's vote.*",
        )
    return True, (voter_key, nation, office_code), ""


def check_individual_voter_eligibility(
    user_id: str,
    member,
    user_data: dict | None,
    country_rec: dict | None = None,
) -> tuple[bool, tuple[str, str, str] | None, str]:
    """Validate 1-Delegate = 1-Person voting eligibility for accredited delegates/representatives."""
    is_chair = can_manage_vote(member, "")
    if (not user_data or not user_data.get("nation")) and not is_chair:
        return (
            False,
            None,
            "❌ Genel Kurulda oy kullanmak için önce `#🚪│kayit-masasi-register` üzerinden kayıt olup delege akreditasyonu almalısınız.\n"
            "*Please complete delegate accreditation before voting.*",
        )
    if not (can_member_speak_in_assembly(member, user_data) or can_member_vote_for_state(member, user_data) or is_chair):
        return (
            False,
            None,
            "⛔ **Delege Yetkisi Gerekli · Accredited Delegate Only**\n"
            "Genel Kurulda yazma ve oy kullanma hakkı yalnızca akredite **delegelere / temsilcilere** aittir "
            "*(her delege 1 kişiyi temsil eder)*; **Vatandaş (`Vatandaş`)**, **Gözlemci** veya **Basın** oy kullanamaz.\n"
            "*Only accredited delegates/representatives may speak or vote (1 delegate = 1 person); Citizens and Observers cannot.*",
        )
    nation = (user_data or {}).get("nation") or "Cemiyet Divanı / Assembly Chair"
    if user_data and user_data.get("nation"):
        crec = country_rec if country_rec is not None else get_country_record(nation)
        status = crec.get("status", "MEMBER")
        if status == "OBSERVER":
            return False, None, "👁️ Gözlemci statüsünün oy hakkı yoktur. / Observers have no voting rights."
        if status == "SUSPENDED":
            return False, None, "⛔ Ülkenizin oy hakkı askıda (Md. 10). / Country vote suspended (Art. 10)."
    office_code = (user_data or {}).get("office_code", "Del.")
    return True, (str(user_id), nation, office_code), ""


async def _validate_state_voter(
    interaction: discord.Interaction,
    vote_data: dict,
) -> tuple[str, str, str] | None:
    """Validate 1-State-1-Vote eligibility & Del.B./single-delegate rule; sends error and returns None if blocked."""
    user_id = str(interaction.user.id)
    user_data = delegates_storage.get(user_id)
    ok, result, err = check_state_voter_eligibility(user_id, interaction.user, user_data, vote_data)
    if not ok:
        await interaction.response.send_message(err, ephemeral=True)
        return None
    return result


async def _validate_individual_voter(interaction: discord.Interaction) -> tuple[str, str, str] | None:
    """Validate accredited delegate 1-person vote eligibility; sends error and returns None if blocked."""
    user_id = str(interaction.user.id)
    user_data = delegates_storage.get(user_id)
    ok, result, err = check_individual_voter_eligibility(user_id, interaction.user, user_data)
    if not ok:
        await interaction.response.send_message(err, ephemeral=True)
        return None
    return result


class VoteModal(discord.ui.Modal, title="Önerge Sun · Submit Assembly Motion"):
    vote_title = discord.ui.TextInput(
        label="Önerge Başlığı / Motion Title",
        placeholder="Örn / e.g.: Barış & Ticaret Karar Tasarısı / Peace & Trade Resolution",
        required=True,
        max_length=150,
    )
    vote_desc = discord.ui.TextInput(
        label="Gerekçe & Maddeler / Grounds & Articles",
        style=discord.TextStyle.paragraph,
        placeholder="Gerekçe ve karar maddelerini yazın / State the grounds and operative clauses...",
        required=True,
        max_length=2000,
    )

    def __init__(self, organ: str, threshold: str, duration_key: str):
        super().__init__()
        self.organ = organ  # 'GA', 'INDIVIDUAL'
        self.threshold = threshold
        self.duration_key = duration_key

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        user_id = str(interaction.user.id)
        user_data = delegates_storage.get(user_id, {})
        author_nation = user_data.get("nation") or "Bağımsız Temsilci / Independent"
        mode = "individual" if self.organ == "INDIVIDUAL" else "state"

        if mode == "state":
            if not user_data or not user_data.get("nation"):
                return await interaction.followup.send(
                    "❌ Önerge sunmak için önce `#🚪│kayit-masasi-register` üzerinden kayıt olup akredite olmalısınız.\n"
                    "*Please complete registration and accreditation first before submitting a state motion.*",
                    ephemeral=True,
                )
            if not can_member_vote_for_state(interaction.user, user_data):
                return await interaction.followup.send(
                    "⛔ **Yetki Yetersiz · Cabinet / Delegate Authority Required**\n"
                    "Devlet adına Genel Kurul önergesi sunma yetkisi yalnızca **Kabine üyelerine (Devlet Başkanı & Bakanlar)** ve **Asil / Yedek Temsilcilere** aittir.\n"
                    "*Only Cabinet members (Head of State & Ministers) and Primary/Alternate Delegates may submit state motions.*",
                    ephemeral=True,
                )
            country_rec = get_country_record(author_nation)
            if country_rec.get("status") in {"OBSERVER", "SUSPENDED"}:
                return await interaction.followup.send(
                    f"⛔ **{author_nation}** mevcut statüsü (`{country_rec.get('status')}`) nedeniyle önerge sunamaz / cannot submit motions.",
                    ephemeral=True,
                )
        else:
            if not (can_member_vote_for_state(interaction.user, user_data) or can_manage_vote(interaction.user, "")):
                return await interaction.followup.send(
                    "⛔ **Yetki Yetersiz · Official Authority Required**\n"
                    "Meclis oylaması / anketi başlatma yetkisi yalnızca yetkili devlet/kabine temsilcilerine veya Divan/Genel Sekreterliğe aittir; "
                    "sade **Vatandaşlar (`Vatandaş`)** oylama açamaz.\n"
                    "*Only accredited state officials or Assembly/Secretariat officers may launch parliamentary polls; Citizens cannot.*",
                    ephemeral=True,
                )

        vote_id = str(uuid.uuid4())[:8]
        label, dur_secs = DURATIONS.get(self.duration_key, ("24h", 86400))
        expires_at = (datetime.utcnow() + timedelta(seconds=dur_secs)).isoformat() if dur_secs > 0 else None

        vote_data = {
            "id": vote_id,
            "title": self.vote_title.value.strip(),
            "description": self.vote_desc.value.strip(),
            "mode": mode,
            "organ": self.organ,
            "threshold": self.threshold,
            "author_id": user_id,
            "author_name": interaction.user.display_name,
            "author_nation": author_nation,
            "sponsors": [],
            "created_at": datetime.utcnow().isoformat(),
            "expires_at": expires_at,
            "votes": {},
            "is_closed": False,
            "channel_id": interaction.channel_id,
            "guild_id": interaction.guild_id,
        }

        embed = voting_embed(vote_data, is_closed=False)
        view = VoteActionView(vote_id)
        interaction.client.add_view(view)

        msg = await interaction.channel.send(embed=embed, view=view)
        vote_data["message_id"] = msg.id
        votes_storage.set(vote_id, vote_data)

        await interaction.followup.send(
            f"✅ Önerge ve oylama başlatıldı / Motion & Vote started ({label}):\n{msg.jump_url}",
            ephemeral=True,
        )


class VoteActionView(discord.ui.View):
    def __init__(self, vote_id: str):
        super().__init__(timeout=None)
        self.vote_id = vote_id

        buttons = [
            ("Kabul · Yes", discord.ButtonStyle.success, "🟩", f"mc_vt_yes:{vote_id}", self.btn_yes),
            ("Ret · No", discord.ButtonStyle.danger, "🟥", f"mc_vt_no:{vote_id}", self.btn_no),
            ("Çekimser · Abstain", discord.ButtonStyle.secondary, "⬜", f"mc_vt_abs:{vote_id}", self.btn_abstain),
            ("Sponsor Ol · Co-Sponsor", discord.ButtonStyle.primary, "🤝", f"mc_vt_spon:{vote_id}", self.btn_sponsor),
            ("Kapat · Close Vote", discord.ButtonStyle.primary, "🔒", f"mc_vt_cls:{vote_id}", self.btn_close),
        ]
        for label, style, emoji, cid, cb in buttons:
            btn = discord.ui.Button(label=label, style=style, emoji=emoji, custom_id=cid)
            btn.callback = cb
            self.add_item(btn)

    async def _cast_vote(self, interaction: discord.Interaction, choice: str):
        vote_data = votes_storage.get(self.vote_id)
        if not vote_data or vote_data.get("is_closed"):
            return await interaction.response.send_message(
                "⚠️ Bu oylama zaten kapanmış. / This vote is already closed.",
                ephemeral=True,
            )

        user_id = str(interaction.user.id)
        mode = vote_data.get("mode", "state")
        user_data = delegates_storage.get(user_id)

        if mode == "state":
            res = await _validate_state_voter(interaction, vote_data)
            if not res:
                return
            voter_key, nation, office_code = res
        else:
            res = await _validate_individual_voter(interaction)
            if not res:
                return
            voter_key, nation, office_code = res

        vote_data["votes"][voter_key] = {
            "choice": choice,
            "user_id": user_id,
            "nation": nation,
            "office_code": office_code,
        }
        votes_storage.set(self.vote_id, vote_data)

        embed = voting_embed(vote_data, is_closed=False)
        await interaction.response.edit_message(embed=embed)

    async def btn_yes(self, interaction: discord.Interaction):
        await self._cast_vote(interaction, "yes")

    async def btn_no(self, interaction: discord.Interaction):
        await self._cast_vote(interaction, "no")

    async def btn_abstain(self, interaction: discord.Interaction):
        await self._cast_vote(interaction, "abstain")

    async def btn_sponsor(self, interaction: discord.Interaction):
        vote_data = votes_storage.get(self.vote_id)
        if not vote_data or vote_data.get("is_closed"):
            return await interaction.response.send_message("⚠️ Oylama kapanmış. / Vote closed.", ephemeral=True)
        user_data = delegates_storage.get(str(interaction.user.id))
        if not user_data or not user_data.get("nation"):
            return await interaction.response.send_message("❌ Ortak sponsorluk için akredite olmalısınız. / Accreditation required.", ephemeral=True)
        if vote_data.get("mode", "state") == "state" and not can_member_vote_for_state(interaction.user, user_data):
            return await interaction.response.send_message(
                "⛔ Ortak sponsorluk yetkisi yalnızca **Kabine üyelerine** ve **Asil / Yedek Temsilcilere** aittir.",
                ephemeral=True,
            )

        nation = user_data["nation"]
        sponsors = vote_data.setdefault("sponsors", [])
        if nation in sponsors or nation == vote_data.get("author_nation"):
            return await interaction.response.send_message("ℹ️ Ülkeniz zaten sponsor. / Already a sponsor.", ephemeral=True)
        sponsors.append(nation)
        votes_storage.set(self.vote_id, vote_data)
        await interaction.response.edit_message(embed=voting_embed(vote_data, is_closed=False))

    async def btn_close(self, interaction: discord.Interaction):
        vote_data = votes_storage.get(self.vote_id)
        if not vote_data:
            return await interaction.response.send_message("❌ Oylama bulunamadı. / Vote not found.", ephemeral=True)

        if not can_manage_vote(interaction.user, vote_data.get("author_id", "")):
            return await interaction.response.send_message(
                "❌ Oylamayı yalnızca Genel Kurul Başkanı, Sekreterlik, Yönetici veya önerge sahibi kapatabilir.\n*Chair, Secretariat, Admin, or author only.*",
                ephemeral=True,
            )

        await interaction.response.defer()
        await finalize_vote(interaction.client, self.vote_id)


def _compute_election_winner(vote_data: dict) -> dict | None:
    """Return winning candidate dict if strictly ahead (tie or zero votes returns None)."""
    candidates = vote_data.get("candidates", [])
    if not candidates:
        return None
    votes = vote_data.get("votes", {})
    counts = {c["user_id"]: 0 for c in candidates}
    reject_c = 0
    for v in votes.values():
        ch = v.get("choice")
        if ch in counts:
            counts[ch] += 1
        elif ch == "reject":
            reject_c += 1

    if len(candidates) == 1:
        only = candidates[0]
        return only if counts[only["user_id"]] > reject_c else None

    ranked = sorted(((counts[c["user_id"]], c) for c in candidates), key=lambda x: x[0], reverse=True)
    if ranked[0][0] <= 0:
        return None
    if len(ranked) > 1 and ranked[1][0] == ranked[0][0]:
        return None
    return ranked[0][1]


def election_embed(vote_data: dict, is_closed: bool = False) -> discord.Embed:
    """Build live or closed General Assembly election ballot embed."""
    office = ELECTION_OFFICES.get(vote_data.get("office_key", "GA_PRES"), ELECTION_OFFICES["GA_PRES"])
    candidates = vote_data.get("candidates", [])
    votes = vote_data.get("votes", {})
    counts = {c["user_id"]: 0 for c in candidates}
    voters_by_cand: dict[str, list[str]] = {c["user_id"]: [] for c in candidates}
    reject_voters: list[str] = []
    abstain_voters: list[str] = []

    for v in votes.values():
        ch = v.get("choice")
        nation = v.get("nation", "?")
        if ch in counts:
            counts[ch] += 1
            voters_by_cand[ch].append(nation)
        elif ch == "reject":
            reject_voters.append(nation)
        elif ch == "abstain":
            abstain_voters.append(nation)

    prefix = "🔒 [SEÇİM SONUÇLANDI / CLOSED]" if is_closed else "🗳️ [BAŞKANLIK & DELEGE SEÇİMİ · ACTIVE]"
    winner = _compute_election_winner(vote_data) if is_closed else None
    status_line = (
        (f"🏆 **KAZANAN / ELECTED:** <@{winner['user_id']}> (`{winner['name']}`)" if winner else "❌ **SONUÇSUZ / NO WINNER (Eşitlik veya Yetersiz Oy)**")
        if is_closed
        else "⏳ **Genel Kurul Seçimi Sürüyor / Voting Open (1 Devlet = 1 Oy)**"
    )
    target_nation_note = (
        f"**İlgili Ülke / State:** `{vote_data['target_nation']}`\n"
        if vote_data.get("target_nation")
        else ""
    )

    emb = discord.Embed(
        title=f"{prefix} {office['label']}",
        description=(
            f"**Makam / Office:** `{office['role_name']}`\n"
            f"**Açıklama:** {office['desc']}\n"
            f"{target_nation_note}"
            f"**Durum / Status:** {status_line}\n"
            + (f"**Not:** {vote_data.get('description')}\n" if vote_data.get("description") else "")
            + "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ),
        colour=0x10B981 if (is_closed and winner) else (0xEF4444 if is_closed else 0xD97706),
    )

    for idx, c in enumerate(candidates, start=1):
        cid = c["user_id"]
        n_txt = f" ({c['nation']})" if c.get("nation") else ""
        voted_states = ", ".join(f"**{n}**" for n in voters_by_cand[cid][:12]) or "*Henüz oy yok*"
        emb.add_field(
            name=f"`{idx}.` {c['name']}{n_txt} — 🗳️ {counts[cid]} Oy",
            value=f"<@{cid}> · **Oy Verenler:** {voted_states}"[:1024],
            inline=False,
        )

    if len(candidates) == 1:
        emb.add_field(name=f"🟥 Ret / Reject — {len(reject_voters)}", value=", ".join(reject_voters[:12]) or "*Yok*", inline=True)
    emb.add_field(name=f"⬜ Çekimser / Abstain — {len(abstain_voters)}", value=", ".join(abstain_voters[:12]) or "*Yok*", inline=True)
    emb.set_footer(text=f"Açan / Opened by: {vote_data.get('author_name', '-')} · Seçim ID: {vote_data.get('id', '-')}")
    return emb


class ElectionActionView(discord.ui.View):
    def __init__(self, vote_id: str, candidates: list[dict] | None = None):
        super().__init__(timeout=None)
        self.vote_id = vote_id
        cands = candidates if candidates is not None else (votes_storage.get(vote_id, {}) or {}).get("candidates", [])
        if len(cands) == 1:
            only = cands[0]
            btn_yes = discord.ui.Button(
                label=f"Seç · {only['name'][:55]}",
                style=discord.ButtonStyle.success,
                emoji="✅",
                custom_id=f"mc_el_vote:{vote_id}:{only['user_id']}",
                row=0,
            )
            btn_yes.callback = self._make_vote_cb(only["user_id"])
            self.add_item(btn_yes)

            btn_rej = discord.ui.Button(
                label="Ret · Reject",
                style=discord.ButtonStyle.danger,
                emoji="🟥",
                custom_id=f"mc_el_vote:{vote_id}:reject",
                row=0,
            )
            btn_rej.callback = self._make_vote_cb("reject")
            self.add_item(btn_rej)
        else:
            for cand in cands[:4]:
                btn = discord.ui.Button(
                    label=cand["name"][:70],
                    style=discord.ButtonStyle.primary,
                    emoji="🗳️",
                    custom_id=f"mc_el_vote:{vote_id}:{cand['user_id']}",
                    row=0,
                )
                btn.callback = self._make_vote_cb(cand["user_id"])
                self.add_item(btn)

        btn_abs = discord.ui.Button(
            label="Çekimser · Abstain",
            style=discord.ButtonStyle.secondary,
            emoji="⬜",
            custom_id=f"mc_el_vote:{vote_id}:abstain",
            row=1,
        )
        btn_abs.callback = self._make_vote_cb("abstain")
        self.add_item(btn_abs)

        btn_cls = discord.ui.Button(
            label="Seçimi Bitir & Rol Ver · Close",
            style=discord.ButtonStyle.secondary,
            emoji="🔒",
            custom_id=f"mc_el_cls:{vote_id}",
            row=1,
        )
        btn_cls.callback = self.btn_close
        self.add_item(btn_cls)

    def _make_vote_cb(self, choice_val: str):
        async def _cb(interaction: discord.Interaction):
            vote_data = votes_storage.get(self.vote_id)
            if not vote_data or vote_data.get("is_closed"):
                return await interaction.response.send_message("⚠️ Bu seçim zaten kapanmış.", ephemeral=True)
            res = await _validate_state_voter(interaction, vote_data)
            if not res:
                return
            voter_key, nation, office_code = res
            vote_data["votes"][voter_key] = {
                "choice": choice_val,
                "user_id": str(interaction.user.id),
                "nation": nation,
                "office_code": office_code,
            }
            votes_storage.set(self.vote_id, vote_data)
            await interaction.response.edit_message(embed=election_embed(vote_data, is_closed=False))

        return _cb

    async def btn_close(self, interaction: discord.Interaction):
        vote_data = votes_storage.get(self.vote_id)
        if not vote_data:
            return await interaction.response.send_message("❌ Seçim bulunamadı.", ephemeral=True)
        if not can_manage_vote(interaction.user, vote_data.get("author_id", "")):
            return await interaction.response.send_message(
                "❌ Seçimi yalnızca Genel Kurul / Şûra Başkanı, Sekreterlik, Yönetici veya seçimi açan kapatabilir.",
                ephemeral=True,
            )
        await interaction.response.defer()
        await finalize_vote(interaction.client, self.vote_id)


async def _create_and_post_election(
    interaction: discord.Interaction,
    *,
    office_key: str,
    candidate_members: list[discord.Member],
    duration_key: str,
    target_nation: str | None = None,
    note: str = "",
):
    office = ELECTION_OFFICES[office_key]
    unique_cands: list[discord.Member] = []
    seen: set[int] = set()
    for m in candidate_members:
        if m.bot or m.id in seen:
            continue
        seen.add(m.id)
        unique_cands.append(m)

    if not unique_cands:
        msg = "❌ En az 1 geçerli (bot olmayan) aday seçilmelidir."
        if interaction.response.is_done():
            return await interaction.followup.send(msg, ephemeral=True)
        return await interaction.response.send_message(msg, ephemeral=True)

    candidates_data: list[dict] = []
    inferred_nation = target_nation
    for m in unique_cands[:4]:
        crec = delegates_storage.get(str(m.id)) or {}
        c_nation = crec.get("nation", "")
        if office_key == "HEAD_DEL" and not inferred_nation and c_nation:
            inferred_nation = c_nation
        candidates_data.append(
            {
                "user_id": str(m.id),
                "name": crec.get("rep_name") or m.display_name,
                "nation": c_nation,
            }
        )

    if office_key == "HEAD_DEL" and not inferred_nation:
        msg = "❌ **Delege Başkanı** seçimindeki adayın bir ülke kaydı olmalıdır (`ulke` girin veya aday önce kayıt olsun)."
        if interaction.response.is_done():
            return await interaction.followup.send(msg, ephemeral=True)
        return await interaction.response.send_message(msg, ephemeral=True)

    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)

    vote_id = f"EL-{str(uuid.uuid4())[:6]}"
    label, dur_secs = DURATIONS.get(duration_key, ("📅 24 Saat", 86400))
    expires_at = (datetime.utcnow() + timedelta(seconds=dur_secs)).isoformat() if dur_secs > 0 else None
    user_data = delegates_storage.get(str(interaction.user.id), {}) or {}

    vote_data = {
        "id": vote_id,
        "kind": "election",
        "office_key": office_key,
        "target_nation": inferred_nation,
        "title": office["label"],
        "description": note.strip(),
        "mode": "state",
        "organ": "GA",
        "threshold": "majority",
        "candidates": candidates_data,
        "author_id": str(interaction.user.id),
        "author_name": interaction.user.display_name,
        "author_nation": user_data.get("nation") or "Yönetim",
        "created_at": datetime.utcnow().isoformat(),
        "expires_at": expires_at,
        "votes": {},
        "is_closed": False,
        "channel_id": interaction.channel_id,
        "guild_id": interaction.guild_id,
    }

    embed = election_embed(vote_data, is_closed=False)
    view = ElectionActionView(vote_id, candidates_data)
    interaction.client.add_view(view)
    target_ch = resolve_panel_channel(interaction.guild, None, "onergeler-motions-votes", interaction.channel)
    vote_data["channel_id"] = target_ch.id
    msg = await target_ch.send(embed=embed, view=view)
    vote_data["message_id"] = msg.id
    votes_storage.set(vote_id, vote_data)

    await interaction.followup.send(
        f"✅ **{office['label']}** seçim sandığı açıldı ({label}): {msg.jump_url}",
        ephemeral=True,
    )


class ElectionLauncherView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        self.office_key = "GA_PRES"
        self.duration_key = "24h"
        self.candidates: list[discord.Member] = []

        office_sel = discord.ui.Select(
            placeholder="Seçilecek makamı seçin / Select office...",
            min_values=1,
            max_values=1,
            row=0,
            options=[
                discord.SelectOption(
                    label=meta["label"][:100],
                    value=key,
                    description=meta["desc"][:100],
                    default=(key == "GA_PRES"),
                )
                for key, meta in ELECTION_OFFICES.items()
            ],
        )
        office_sel.callback = self._on_office
        self.add_item(office_sel)

        cand_sel = discord.ui.UserSelect(
            placeholder="Adayları seçin (1–4 kişi) / Select candidates...",
            min_values=1,
            max_values=4,
            row=1,
        )
        cand_sel.callback = self._on_cands
        self.add_item(cand_sel)

        dur_sel = discord.ui.Select(
            placeholder="Seçim süresi / Duration...",
            min_values=1,
            max_values=1,
            row=2,
            options=[
                discord.SelectOption(label=lbl[:100], value=k, default=(k == "24h"))
                for k, (lbl, _) in DURATIONS.items()
            ],
        )
        dur_sel.callback = self._on_dur
        self.add_item(dur_sel)

        btn_go = discord.ui.Button(label="Seçim Sandığını Kur · Launch Election", style=discord.ButtonStyle.success, emoji="🗳️", row=3)
        btn_go.callback = self._on_launch
        self.add_item(btn_go)

    async def _on_office(self, interaction: discord.Interaction):
        self.office_key = interaction.data["values"][0]
        await interaction.response.defer(ephemeral=True)

    async def _on_cands(self, interaction: discord.Interaction):
        self.candidates = []
        for uid_str in interaction.data.get("values", []):
            m = await resolve_member(interaction.guild, int(uid_str))
            if m:
                self.candidates.append(m)
        await interaction.response.defer(ephemeral=True)

    async def _on_dur(self, interaction: discord.Interaction):
        self.duration_key = interaction.data["values"][0]
        await interaction.response.defer(ephemeral=True)

    async def _on_launch(self, interaction: discord.Interaction):
        if not _can_launch_election(interaction.user, self.office_key):
            return await interaction.response.send_message(
                "⛔ Bu makam için seçim başlatma yetkiniz bulunmuyor.",
                ephemeral=True,
            )
        if not self.candidates:
            return await interaction.response.send_message("❌ Lütfen yukarıdan en az 1 aday seçin.", ephemeral=True)
        await _create_and_post_election(
            interaction,
            office_key=self.office_key,
            candidate_members=self.candidates,
            duration_key=self.duration_key,
        )


async def finalize_vote(bot: commands.Bot, vote_id: str):
    """Close vote or election, grant roles or archive resolution, and publish to Official Gazette."""
    vote_data = votes_storage.get(vote_id)
    if not vote_data or vote_data.get("is_closed"):
        return

    vote_data["is_closed"] = True
    is_election = vote_data.get("kind") == "election"

    channel = bot.get_channel(vote_data.get("channel_id"))
    if is_election:
        office = ELECTION_OFFICES.get(vote_data.get("office_key", "GA_PRES"), ELECTION_OFFICES["GA_PRES"])
        winner = _compute_election_winner(vote_data)
        guild = bot.get_guild(vote_data.get("guild_id")) if vote_data.get("guild_id") else (getattr(channel, "guild", None))
        assigned_note = ""
        if winner and guild:
            w_member = await resolve_member(guild, int(winner["user_id"]))
            vote_data["winner_id"] = winner["user_id"]
            vote_data["winner_name"] = winner["name"]
            if w_member:
                if office["kind"] == "chair":
                    await assign_institutional_chair(
                        guild,
                        w_member,
                        role_name=office["role_name"],
                        office_code=office["office_code"],
                        actor_id="ELECTION",
                    )
                    assigned_note = f"`{office['role_name']}` rolü otomatik tanımlandı."
                else:
                    win_nation = vote_data.get("target_nation") or winner.get("nation") or (delegates_storage.get(str(winner["user_id"]), {}) or {}).get("nation", "")
                    if win_nation:
                        res = await assign_head_of_delegation(
                            guild,
                            w_member,
                            nation_clean=win_nation,
                            actor_id="ELECTION",
                        )
                        assigned_note = f"**{res['nation']}** adına `{office['role_name']}` rolü tanımlandı (`{res['nick']}`)."
        votes_storage.set(vote_id, vote_data)
        if channel and vote_data.get("message_id"):
            try:
                msg = await channel.fetch_message(vote_data["message_id"])
                await msg.edit(embed=election_embed(vote_data, is_closed=True), view=discord.ui.View())
            except Exception:
                pass
        if channel:
            try:
                if winner:
                    await channel.send(
                        f"🏆 **SEÇİM SONUCU ({office['label']}):** <@{winner['user_id']}> (`{winner['name']}`) seçildi! {assigned_note}"
                    )
                else:
                    await channel.send(
                        f"⚠️ **SEÇİM SONUÇSUZ ({office['label']}):** Yeterli oy sağlanamadı veya eşitlik oluştu (`{vote_id}`)."
                    )
            except Exception:
                pass
        if winner:
            await publish_to_gazette(
                bot,
                vote_data.get("guild_id"),
                "GENEL KURUL SEÇİM SONUCU · ELECTION RESULT",
                f"{office['label']} — {winner['name']}",
                f"**Seçilen / Elected:** <@{winner['user_id']}> (`{winner['name']}`)\n**Makam / Office:** `{office['role_name']}`\n{assigned_note}",
                color=0x10B981,
            )
        return

    votes_storage.set(vote_id, vote_data)

    if channel and vote_data.get("message_id"):
        try:
            msg = await channel.fetch_message(vote_data["message_id"])
            embed = voting_embed(vote_data, is_closed=True)
            await msg.edit(embed=embed, view=discord.ui.View())
        except Exception:
            pass

    votes = vote_data.get("votes", {})
    yes_c = sum(1 for v in votes.values() if v["choice"] == "yes")
    no_c = sum(1 for v in votes.values() if v["choice"] == "no")
    abs_c = sum(1 for v in votes.values() if v["choice"] == "abstain")
    passed = compute_vote_outcome(vote_data)

    if vote_data.get("mode") == "state":
        author_nation = vote_data.get("author_nation")
        if author_nation and author_nation != "Bağımsız Temsilci":
            arec = get_country_record(author_nation)
            arec["stats"]["proposals"] += 1
            if passed:
                arec["stats"]["passed_proposals"] += 1
            save_country_record(arec)

        for v in votes.values():
            vnation = v.get("nation")
            if vnation:
                crec = get_country_record(vnation)
                c = v["choice"]
                if c in crec["stats"]:
                    crec["stats"][c] += 1
                save_country_record(crec)

    if passed:
        existing_res = resolutions_storage.read()
        res_num = len(existing_res) + 1
        year = datetime.utcnow().year
        res_id = f"MC-KARAR/{year}/{res_num:03d}"

        res_record = {
            "res_id": res_id,
            "title": vote_data["title"],
            "text": vote_data["description"],
            "mode_text": "Milletler Cemiyeti Genel Kurulu (Madde 15)" if vote_data.get("organ") == "GA" else "Bireysel Oylama",
            "author": f"{vote_data['author_name']} ({vote_data['author_nation']})",
            "timestamp": datetime.utcnow().strftime("%d.%m.%Y %H:%M UTC"),
            "yes": yes_c,
            "no": no_c,
            "abstain": abs_c,
        }
        resolutions_storage.set(res_id, res_record)

        if channel:
            try:
                await channel.send(
                    content=f"📜 **ÖNERGE KABUL EDİLDİ VE RESMÎ KARAR SİCİLİNE İŞLENDİ (`{res_id}`)**",
                    embed=resolution_archive_embed(res_record),
                )
            except Exception:
                pass

        roll_yes = ", ".join(v.get("nation", "?") for v in votes.values() if v["choice"] == "yes") or "Yok"
        roll_no = ", ".join(v.get("nation", "?") for v in votes.values() if v["choice"] == "no") or "Yok"
        roll_abs = ", ".join(v.get("nation", "?") for v in votes.values() if v["choice"] == "abstain") or "Yok"

        await publish_to_gazette(
            bot,
            vote_data.get("guild_id"),
            "GENEL KURUL KARARI YÜRÜRLÜKTE",
            vote_data["title"],
            vote_data["description"][:1000],
            fields=[
                ("Karar No", f"`{res_id}`", True),
                ("Sunan", res_record["author"], True),
                ("🟩 Kabul Edenler", roll_yes, True),
                ("🟥 Ret Verenler", roll_no, True),
                ("⬜ Çekimserler", roll_abs, True),
            ],
            color=0x10B981,
        )


class VotingPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Usul Önergesi · Majority (1/2)", style=discord.ButtonStyle.primary, emoji="🏛️", custom_id="panel_ga_majority", row=0)
    async def btn_ga_maj(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(VoteModal(organ="GA", threshold="majority", duration_key="24h"))

    @discord.ui.button(label="Önemli Karar · Supermajority (2/3)", style=discord.ButtonStyle.danger, emoji="⚖️", custom_id="panel_ga_two_thirds", row=0)
    async def btn_ga_23(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(VoteModal(organ="GA", threshold="two_thirds", duration_key="24h"))

    @discord.ui.button(label="Acil Önerge · Urgent (6h)", style=discord.ButtonStyle.secondary, emoji="🚨", custom_id="panel_ga_urgent", row=0)
    async def btn_ga_urgent(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(VoteModal(organ="GA", threshold="majority", duration_key="6h"))

    @discord.ui.button(label="Başkanlık & Delege Seçimi · Election", style=discord.ButtonStyle.success, emoji="🗳️", custom_id="panel_ga_election", row=1)
    async def btn_ga_election(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _can_launch_election(interaction.user, "HEAD_DEL"):
            return await interaction.response.send_message(
                "⛔ Seçim sandığı açmak için yetkili Kabine / Temsilci veya Başkan / Yönetici olmalısınız.",
                ephemeral=True,
            )
        await interaction.response.send_message(
            "**🗳️ Genel Kurul Başkanlık & Ülke Delege Başkanı Seçim Kurulumu:**\n"
            "• Aşağıdan makamı (`Genel Kurul Başkanı`, `Şûra Başkanı`, `Divan Başkanı` veya `Delege Başkanı`), adayları (`1–4` kişi) ve süreyi seçip **`Seçim Sandığını Kur`** butonuna basın.\n"
            "• Seçim kapandığında kazanan adaya ilgili **rol otomatik tanımlanır**.",
            view=ElectionLauncherView(),
            ephemeral=True,
        )


def build_voting_panel() -> tuple[discord.Embed, discord.ui.View]:
    embed = discord.Embed(
        title="🏛️ GENEL KURUL OYLAMA MASASI · ASSEMBLY VOTING DESK",
        description=(
            "**🇹🇷 Türkçe (MC-KS-001 Madde 13–18):**\n"
            "• **Delegeler & Bireysel Oy:** Genel Kurul'da yalnızca akredite **delegeler ve temsilciler** yazıp oy verebilir *(her delege 1 kişiyi temsil eder)*.\n"
            "• **Eşit Devlet Oyu (1 Devlet = 1 Oy):** Atanmış **Delege Başkanı (`Del.B.`)** varsa devlet oyunu o kullanır; "
            "Delege Başkanı seçemeyen veya aralarında karar bildirip bildirmeyen devletlerden yetkili **1 delegenin** verdiği oy ülkenin oyu sayılır.\n"
            "• **Yeter Sayı:** Usul önergesi salt çoğunluk (**1/2**); yaptırım ve tüzük değişiklikleri **2/3 çoğunluk** iledir.\n"
            "• **Seçimler (`🗳️ Başkanlık & Delege Seçimi`):** **Genel Kurul Başkanı**, **Şûra Başkanı**, **Divan Başkanı** ve **Ülke Delege Başkanı** seçilir; kazananın rolü otomatik tanımlanır.\n\n"
            "**🇬🇧 English (MC-KS-001 Articles 13–18):**\n"
            "• **Delegates:** Only accredited **delegates & representatives** may write and vote in the Assembly *(1 delegate = 1 person)*.\n"
            "• **1 State = 1 Equal Vote:** If a state has a registered **Head of Delegation (`Del.B.`)**, they cast the state vote; "
            "otherwise, any **1 authorized delegate** casting a vote counts as that country's official vote."
        ),
        colour=0x1E3A8A,
    )
    embed.set_footer(text="Milletler Cemiyeti · League of Nations General Assembly")
    return embed, VotingPanelView()


class VotingCog(commands.Cog):
    """MC-KS-001 General Assembly Voting & Elections Cog."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.check_expired_votes.start()

    async def cog_load(self):
        self.bot.add_view(VotingPanelView())
        for vid, vdata in votes_storage.read().items():
            if isinstance(vdata, dict) and not vdata.get("is_closed"):
                if vdata.get("kind") == "election":
                    self.bot.add_view(ElectionActionView(vid, vdata.get("candidates", [])))
                else:
                    self.bot.add_view(VoteActionView(vid))

    def cog_unload(self):
        self.check_expired_votes.cancel()

    @tasks.loop(seconds=15)
    async def check_expired_votes(self):
        now = datetime.utcnow()
        all_votes = votes_storage.read()
        for vote_id, vdata in list(all_votes.items()):
            if vdata.get("is_closed"):
                continue
            exp_str = vdata.get("expires_at")
            if exp_str:
                try:
                    if now >= datetime.fromisoformat(exp_str):
                        await finalize_vote(self.bot, vote_id)
                except Exception:
                    pass

    @check_expired_votes.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="oylama", description="Milletler Cemiyeti Genel Kurulu'nda önerge ve oylama başlatır (Madde 15 & 18)")
    @app_commands.describe(organ="Oylama türü", kural="Çoğunluk şartı", sure="Oylama süresi")
    @app_commands.choices(
        organ=[
            app_commands.Choice(name="🏛️ Genel Kurul (1 Ülke = 1 Eşit Oy)", value="GA"),
            app_commands.Choice(name="👤 Bireysel Delege Oylaması (1 Delege = 1 Kişi)", value="INDIVIDUAL"),
        ],
        kural=[
            app_commands.Choice(name="Salt Çoğunluk (Madde 15.2 · Usul)", value="majority"),
            app_commands.Choice(name="Üçte İki Çoğunluk (Madde 15.3-4 · Önemli Karar)", value="two_thirds"),
            app_commands.Choice(name="Oybirliği / Consensus", value="unanimity"),
        ],
        sure=[
            app_commands.Choice(name="⚡ 1 Dakika", value="1m"),
            app_commands.Choice(name="⚡ 5 Dakika", value="5m"),
            app_commands.Choice(name="⏱️ 15 Dakika", value="15m"),
            app_commands.Choice(name="🚨 6 Saat (Acil Durum · Md. 18.6)", value="6h"),
            app_commands.Choice(name="📅 24 Saat (Olağan · Md. 18.5)", value="24h"),
            app_commands.Choice(name="🔒 Manuel (Başkan Kapatır)", value="manual"),
        ],
    )
    async def cmd_oylama(
        self,
        interaction: discord.Interaction,
        organ: app_commands.Choice[str],
        kural: app_commands.Choice[str],
        sure: app_commands.Choice[str],
    ):
        await interaction.response.send_modal(VoteModal(organ.value, kural.value, sure.value))

    @app_commands.command(name="oylama-paneli-kur", description="İlgili resmi kanala kalıcı Genel Kurul önerge & oylama paneli kurar (Yönetici)")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(kanal="İsteğe bağlı hedef kanal (boş bırakılırsa otomatik oylama kanalına kurulur)")
    async def cmd_setup_panel(self, interaction: discord.Interaction, kanal: discord.TextChannel = None):
        target = resolve_panel_channel(interaction.guild, kanal, "onergeler-motions-votes", interaction.channel)
        embed, view = build_voting_panel()
        st = await upsert_panel_message(target, ["🏛️ GENEL KURUL OYLAMA", "🏛️ MİLLETLER CEMİYETİ GENEL KURUL ÖNERGE"], embed, view)
        await interaction.response.send_message(f"✅ Oylama paneli {target.mention} kanalında güncellendi (`{st}`).", ephemeral=True)

    @app_commands.command(name="secim-baslat", description="Genel Kurul oylamasıyla Şûra / Genel Kurul / Divan / Delege Başkanı seçimi açar")
    @app_commands.describe(
        makam="Seçilecek başkanlık veya delegasyon makamı",
        aday1="1. Aday (tek aday seçilirse güvenoyu / Kabul-Ret açılır)",
        aday2="2. Aday (isteğe bağlı)",
        aday3="3. Aday (isteğe bağlı)",
        aday4="4. Aday (isteğe bağlı)",
        sure="Seçim süresi",
        ulke="Sadece Delege Başkanı seçimi için hedef ülke (boşsa adayın kayıtlı ülkesi)",
        not_metni="Adaylar / dönem hakkında kısa açıklama",
    )
    @app_commands.choices(
        makam=[
            app_commands.Choice(name="🪑 Genel Kurul Başkanı · Assembly President", value="GA_PRES"),
            app_commands.Choice(name="🏛️ Şûra Başkanı · Council Chair", value="COUNCIL_CHAIR"),
            app_commands.Choice(name="⚖️ Divan Başkanı · ICJ President", value="ICJ_PRES"),
            app_commands.Choice(name="🎖️ Delege Başkanı · Head of Delegation", value="HEAD_DEL"),
        ],
        sure=[
            app_commands.Choice(name="⚡ 1 Dakika", value="1m"),
            app_commands.Choice(name="⚡ 5 Dakika", value="5m"),
            app_commands.Choice(name="⏱️ 15 Dakika", value="15m"),
            app_commands.Choice(name="🚨 6 Saat", value="6h"),
            app_commands.Choice(name="📅 24 Saat", value="24h"),
            app_commands.Choice(name="🔒 Manuel (Başkan Kapatır)", value="manual"),
        ],
    )
    async def cmd_start_election(
        self,
        interaction: discord.Interaction,
        makam: app_commands.Choice[str],
        aday1: discord.Member,
        sure: app_commands.Choice[str],
        aday2: discord.Member = None,
        aday3: discord.Member = None,
        aday4: discord.Member = None,
        ulke: str | None = None,
        not_metni: str = "",
    ):
        if not _can_launch_election(interaction.user, makam.value):
            return await interaction.response.send_message(
                "⛔ Bu makam için seçim başlatma yetkiniz bulunmuyor (Başkan / Sekreterlik / Devlet Başkanı / Yönetici gerekir).",
                ephemeral=True,
            )
        cands = [m for m in (aday1, aday2, aday3, aday4) if m is not None]
        await _create_and_post_election(
            interaction,
            office_key=makam.value,
            candidate_members=cands,
            duration_key=sure.value,
            target_nation=ulke.strip() if ulke else None,
            note=not_metni,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(VotingCog(bot))
