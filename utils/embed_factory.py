import discord
from datetime import datetime
from typing import Dict, Any, List

BLUE_DIPLOMATIC = 0x1E3A8A
GOLD_DIPLOMATIC = 0xD97706
GREEN_ACCEPTED = 0x10B981
RED_REJECTED = 0xEF4444
PURPLE_CONFERENCE = 0x6D28D9


def create_progress_bar(yes_count: int, no_count: int, abstain_count: int, total_slots: int = 12) -> str:
    """Render a visual voting progress bar."""
    total = yes_count + no_count + abstain_count
    if total == 0:
        return "⬜" * total_slots

    yes_slots = round((yes_count / total) * total_slots)
    no_slots = round((no_count / total) * total_slots)
    abs_slots = max(0, total_slots - (yes_slots + no_slots))
    return ("🟩" * yes_slots) + ("🟥" * no_slots) + ("⬜" * abs_slots)


def compute_vote_outcome(vote_data: Dict[str, Any]) -> bool:
    """Compute vote result per MC-KS-001 Article 15 (abstentions excluded, tie = rejected)."""
    votes = vote_data.get("votes", {})
    yes_c = sum(1 for v in votes.values() if v["choice"] == "yes")
    no_c = sum(1 for v in votes.values() if v["choice"] == "no")
    threshold = vote_data.get("threshold", "majority")

    if yes_c == 0 or yes_c == no_c:
        return False

    if threshold == "two_thirds":
        return yes_c >= (2 * no_c)
    elif threshold == "unanimity":
        return no_c == 0
    else:
        return yes_c > no_c


def voting_embed(vote_data: Dict[str, Any], is_closed: bool = False) -> discord.Embed:
    """Generate live voting embed aligned with MC-KS-001 (League of Nations Charter)."""
    title_prefix = "🔒 [KAPANDI / CLOSED]" if is_closed else "🗳️ [AKTİF / ACTIVE]"
    mode = vote_data.get("mode", "state")
    organ = vote_data.get("organ", "GA")

    organ_labels = {
        "GA": "🏛️ Genel Kurul · General Assembly (Md./Art. 13 · 1 Devlet/State = 1 Oy/Vote)",
        "INDIVIDUAL": "👤 Bireysel Delege Oylaması · Individual Delegate Vote (1 Delege/Delegate = 1 Oy/Vote)",
    }
    mode_text = organ_labels.get(organ, "🏛️ Genel Kurul · General Assembly")

    votes = vote_data.get("votes", {})
    yes_votes = [k for k, v in votes.items() if v["choice"] == "yes"]
    no_votes = [k for k, v in votes.items() if v["choice"] == "no"]
    abstain_votes = [k for k, v in votes.items() if v["choice"] == "abstain"]

    yes_c = len(yes_votes)
    no_c = len(no_votes)
    abs_c = len(abstain_votes)
    total_c = yes_c + no_c + abs_c

    threshold = vote_data.get("threshold", "majority")
    threshold_labels = {
        "majority": "Salt Çoğunluk / Simple Majority (Md./Art. 15.2 · Tie = Rejected)",
        "two_thirds": "Üçte İki / Two-Thirds Majority (Md./Art. 15.3-4)",
        "unanimity": "Oybirliği / Consensus",
    }
    threshold_str = threshold_labels.get(threshold, "Salt Çoğunluk / Majority")

    if is_closed:
        passed = compute_vote_outcome(vote_data)
        color = GREEN_ACCEPTED if passed else RED_REJECTED
        result_str = "✅ **KABUL EDİLDİ / ADOPTED**" if passed else "❌ **REDDEDİLDİ / REJECTED**"
    else:
        color = GOLD_DIPLOMATIC if threshold == "two_thirds" else BLUE_DIPLOMATIC
        result_str = "⏳ **Oylama Sürüyor / Voting in Progress**"

    sponsors = vote_data.get("sponsors", [])
    sponsor_line = f"**Ortak Sponsorlar / Co-Sponsors:** {', '.join(sponsors)}\n" if sponsors else ""

    embed = discord.Embed(
        title=f"{title_prefix} {vote_data.get('title', 'Önerge / Motion')}",
        description=(
            f"**Organ:** {mode_text}\n"
            f"**Yeter Sayı / Threshold:** `{threshold_str}`\n"
            f"{sponsor_line}"
            f"**Durum / Status:** {result_str}\n\n"
            f"**Metin & Gerekçe / Text & Grounds (Md./Art. 18.4):**\n"
            f"> {vote_data.get('description', '-')}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ),
        colour=color,
        timestamp=datetime.fromisoformat(vote_data.get("created_at", datetime.utcnow().isoformat())),
    )

    bar = create_progress_bar(yes_c, no_c, abs_c, total_slots=14)
    embed.add_field(
        name="📊 Canlı Oy Dağılımı / Live Tally",
        value=(
            f"`{bar}`\n"
            f"🟩 **Kabul / Yes:** {yes_c} | 🟥 **Ret / No:** {no_c} | ⬜ **Çekimser / Abstain:** {abs_c} | 👥 **Toplam / Total:** {total_c}"
        ),
        inline=False,
    )

    if mode == "state":
        yes_names = ", ".join([f"**{v.get('nation', 'State')}**" for v in votes.values() if v["choice"] == "yes"]) or "*Yok / None*"
        no_names = ", ".join([f"**{v.get('nation', 'State')}**" for v in votes.values() if v["choice"] == "no"]) or "*Yok / None*"
        abs_names = ", ".join([f"**{v.get('nation', 'State')}**" for v in votes.values() if v["choice"] == "abstain"]) or "*Yok / None*"
    else:
        yes_names = ", ".join([f"<@{uid}>" for uid in yes_votes[:15]]) + (f" *+{len(yes_votes)-15}*" if len(yes_votes) > 15 else "") or "*Yok / None*"
        no_names = ", ".join([f"<@{uid}>" for uid in no_votes[:15]]) + (f" *+{len(no_votes)-15}*" if len(no_votes) > 15 else "") or "*Yok / None*"
        abs_names = ", ".join([f"<@{uid}>" for uid in abstain_votes[:15]]) + (f" *+{len(abstain_votes)-15}*" if len(abstain_votes) > 15 else "") or "*Yok / None*"

    embed.add_field(name="🟩 Kabul / In Favor", value=yes_names[:1024], inline=True)
    embed.add_field(name="🟥 Ret / Against", value=no_names[:1024], inline=True)
    embed.add_field(name="⬜ Çekimser / Abstain", value=abs_names[:1024], inline=True)

    author_name = vote_data.get("author_name", "Chair")
    author_nation = vote_data.get("author_nation", "League of Nations")
    embed.set_footer(text=f"Sunan / Sponsor: {author_name} ({author_nation}) · MC-KS-001 · ID: {vote_data.get('id', 'N/A')}")
    return embed


def speakers_list_embed(queue: List[Dict[str, Any]], current_speaker: Dict[str, Any] = None) -> discord.Embed:
    """Generate embed for conference floor / speakers queue."""
    embed = discord.Embed(
        title="🎙️ Konuşmacı Kürsüsü · Speakers' Floor & Queue",
        description=(
            "🏛️ **Genel Kurul Kürsüsü / General Assembly Speakers' List**\n"
            "• Devlet temsilcileri aşağıdaki butonlarla sıraya girebilir, ayrılabilir veya usul önergesi verebilir.\n"
            "• Accredited delegates may request the floor, leave the queue, or raise a Point of Order below.\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ),
        colour=PURPLE_CONFERENCE,
    )

    if current_speaker:
        embed.add_field(
            name="🎤 Kürsüdeki Konuşmacı / Current Speaker",
            value=f"👑 <@{current_speaker['user_id']}> — **{current_speaker.get('nation', '-')}** ({current_speaker.get('office', '-')})",
            inline=False,
        )
    else:
        embed.add_field(name="🎤 Kürsüdeki Konuşmacı / Current Speaker", value="*Kürsü serbest / Floor is open*", inline=False)

    if queue:
        lines = [
            f"`{idx}.` <@{item['user_id']}> — **{item.get('nation', '-')}** `({item.get('office', '-')})`"
            for idx, item in enumerate(queue, start=1)
        ]
        embed.add_field(name="📋 Bekleyen Sıra / Speakers Queue", value="\n".join(lines[:15]), inline=False)
    else:
        embed.add_field(name="📋 Bekleyen Sıra / Speakers Queue", value="*Sırada bekleyen yok / Queue is empty*", inline=False)

    embed.set_footer(text="Milletler Cemiyeti · League of Nations (MC-KS-001)")
    return embed


def resolution_archive_embed(res_data: Dict[str, Any]) -> discord.Embed:
    """Generate formal League of Nations resolution document for permanent record."""
    embed = discord.Embed(
        title=f"📜 RESMÎ KARAR / RESOLUTION · {res_data.get('res_id', 'MC-KARAR-2026/001')}",
        description=(
            f"**Konu / Subject:** {res_data.get('title', '-')}\n"
            f"**Organ / Body:** {res_data.get('mode_text', 'Genel Kurul · General Assembly')}\n"
            f"**Tarih / Date:** {res_data.get('timestamp', datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC'))}\n"
            f"**Sunan / Sponsor:** {res_data.get('author', '-')}\n\n"
            f"**KARAR METNİ / TEXT OF RESOLUTION:**\n"
            f"```text\n{res_data.get('text', '-')}\n```\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"**Oylama Tutanağı / Vote Tally:**\n"
            f"🟩 Kabul / Yes: {res_data.get('yes', 0)} | 🟥 Ret / No: {res_data.get('no', 0)} | ⬜ Çekimser / Abstain: {res_data.get('abstain', 0)}\n"
            f"**Hukuki Durum / Status:** ⚖️ Kabul Edildi & Arşivlendi / Adopted & Archived (Md./Art. 15.7)"
        ),
        colour=GREEN_ACCEPTED,
    )
    embed.set_footer(text="Resmî Karar Sicili · Official Resolution Register (MC-KS-001)")
    return embed
