import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils.constants import (
    ROLE_AMBASSADOR,
    ROLE_FM,
    ROLE_PRESIDENT,
    ROLE_SG,
    ROLE_SUSPENDED,
)
from utils.storage import (
    ambassador_appointments_storage,
    ambassador_rooms_storage,
    delegates_storage,
    sticky_roles_storage,
)
from utils.un_gazette import countries_storage, normalize_country_key
from utils.un_gazette import publish_to_gazette


log = logging.getLogger("InternationalDiplomaticBot.Ambassadors")
PROTECTION_PERIOD = timedelta(days=14)
ACTIVE_STATUSES = {"active"}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    return _utc_now().isoformat()


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _resolve_registered_country(raw_name: str) -> tuple[str, str] | None:
    needle = normalize_country_key(raw_name)
    if not needle:
        return None
    for storage_key, country in countries_storage.read().items():
        if not isinstance(country, dict):
            continue
        canonical_name = str(country.get("name", "")).strip()
        candidates = {
            normalize_country_key(str(storage_key)),
            normalize_country_key(str(country.get("key", ""))),
            normalize_country_key(canonical_name),
        }
        if needle in candidates and canonical_name:
            return canonical_name, normalize_country_key(canonical_name)
    return None


def _office_is(record: dict, code: str, role_name: str) -> bool:
    return record.get("office_code") == code or record.get("office_role_name") == role_name


def _append_event(record: dict, event_type: str, actor_id: int, **details):
    events = record.setdefault("events", [])
    events.append(
        {
            "type": event_type,
            "actor_id": str(actor_id),
            "at": _iso_now(),
            **details,
        }
    )


class AppointmentPanelView(discord.ui.View):
    """Persistent button shown in the private appointment room."""

    def __init__(self, cog: "AmbassadorsCog"):
        super().__init__(timeout=None)
        self.cog = cog
        button = discord.ui.Button(
            label="Büyükelçi Ata · Appoint Ambassador",
            style=discord.ButtonStyle.primary,
            emoji="🏛️",
            custom_id="ambassador:appointment:start",
        )
        button.callback = self.start_appointment
        self.add_item(button)

    async def start_appointment(self, interaction: discord.Interaction):
        if not self.cog._appointment_room_and_office_allowed(interaction):
            return await interaction.response.send_message(
                "⛔ Atama odası yalnızca kayıtlı Devlet Başkanları, Dışişleri Bakanları ve Genel Sekreter içindir.",
                ephemeral=True,
            )
        await interaction.response.send_message(
            "Önce atayacağınız kayıtlı adayı seçin. Adayın devlet ve görev uygunluğunu bot kontrol edecek.",
            view=CandidateSelectView(self.cog, interaction.user.id),
            ephemeral=True,
        )


class CandidateSelectView(discord.ui.View):
    def __init__(self, cog: "AmbassadorsCog", owner_id: int):
        super().__init__(timeout=300)
        self.cog = cog
        self.owner_id = owner_id
        self.candidate_select = discord.ui.UserSelect(
            placeholder="Büyükelçi adayını seçin · Select nominee",
            min_values=1,
            max_values=1,
        )
        self.candidate_select.callback = self.select_candidate
        self.add_item(self.candidate_select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Bu atama formu başka bir yetkilinin oturumuna ait.", ephemeral=True
            )
            return False
        return True

    async def select_candidate(self, interaction: discord.Interaction):
        candidate = self.candidate_select.values[0]
        if not isinstance(candidate, discord.Member) or candidate.bot:
            return await interaction.response.send_message(
                "❌ Aday sunucuda kayıtlı bir insan üye olmalıdır.", ephemeral=True
            )

        actor_office = self.cog._appointment_office(interaction)
        candidate_record = delegates_storage.get(str(candidate.id), {})
        candidate_home = _resolve_registered_country(
            str(candidate_record.get("nation", ""))
        )
        if (
            actor_office is None
            or candidate_home is None
            or candidate_record.get("office_code") == "Suspended"
            or any(role.name == ROLE_SUSPENDED for role in candidate.roles)
        ):
            return await interaction.response.send_message(
                "❌ Aday kayıtlı ve görev için uygun bir üye olmalıdır.", ephemeral=True
            )

        home_nation, home_key = candidate_home
        if actor_office != "SG":
            actor_home = _resolve_registered_country(
                str(self.cog._actor_record(interaction).get("nation", ""))
            )
            if actor_home is None or actor_home[1] != home_key:
                return await interaction.response.send_message(
                    "❌ Başkan veya Bakan yalnızca kendi devletinde kayıtlı üyeyi atayabilir.",
                    ephemeral=True,
                )

        host_countries = self.cog._registered_host_countries(home_key)
        if not host_countries:
            return await interaction.response.send_message(
                "❌ Adayın devleti dışında seçilebilecek kayıtlı hedef ülke yok.",
                ephemeral=True,
            )

        view = HostCountrySelectView(
            self.cog,
            owner_id=self.owner_id,
            candidate=candidate,
            home_nation=home_nation,
            host_countries=host_countries,
        )
        await interaction.response.edit_message(
            content=(
                f"Aday: {candidate.mention} · Gönderen devlet: **{home_nation}**\n"
                "Şimdi hedef ülkeyi seçin; sonraki adımda atamayı onaylayabilirsiniz."
            ),
            view=view,
        )


class HostCountrySelectView(discord.ui.View):
    """Paginated registered-country picker with an explicit final confirmation."""

    PAGE_SIZE = 25

    def __init__(
        self,
        cog: "AmbassadorsCog",
        *,
        owner_id: int,
        candidate: discord.Member,
        home_nation: str,
        host_countries: list[str],
    ):
        super().__init__(timeout=600)
        self.cog = cog
        self.owner_id = owner_id
        self.candidate = candidate
        self.home_nation = home_nation
        self.host_countries = host_countries
        self.page = 0
        self.selected_host: str | None = None
        self._render_components()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Bu atama formu başka bir yetkilinin oturumuna ait.", ephemeral=True
            )
            return False
        return True

    def _render_components(self):
        page_count = max(1, (len(self.host_countries) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        start = self.page * self.PAGE_SIZE
        page_countries = self.host_countries[start : start + self.PAGE_SIZE]
        self.clear_items()

        selector = discord.ui.Select(
            placeholder="Hedef ülkeyi seçin · Select destination",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=name[:100],
                    value=name[:100],
                    default=(name == self.selected_host),
                )
                for name in page_countries
            ],
            row=0,
        )
        selector.callback = self.select_host
        self.add_item(selector)

        previous = discord.ui.Button(
            label="Önceki · Previous",
            style=discord.ButtonStyle.secondary,
            disabled=self.page == 0,
            row=1,
        )
        previous.callback = self.previous_page
        self.add_item(previous)

        next_page = discord.ui.Button(
            label="Sonraki · Next",
            style=discord.ButtonStyle.secondary,
            disabled=self.page >= page_count - 1,
            row=1,
        )
        next_page.callback = self.next_page
        self.add_item(next_page)

        confirm = discord.ui.Button(
            label="Atamayı Onayla · Confirm Appointment",
            style=discord.ButtonStyle.success,
            disabled=self.selected_host is None,
            row=2,
        )
        confirm.callback = self.confirm_appointment
        self.add_item(confirm)

        cancel = discord.ui.Button(
            label="İptal · Cancel",
            style=discord.ButtonStyle.danger,
            row=2,
        )
        cancel.callback = self.cancel_appointment
        self.add_item(cancel)

    def _content(self) -> str:
        page_count = max(1, (len(self.host_countries) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        selected = f"\nSeçim: **{self.selected_host}**" if self.selected_host else ""
        return (
            f"Aday: {self.candidate.mention} · **{self.home_nation}** → hedef ülke seçin. "
            f"Sayfa {self.page + 1}/{page_count}.{selected}"
        )

    async def select_host(self, interaction: discord.Interaction):
        selector = next(
            item for item in self.children if isinstance(item, discord.ui.Select)
        )
        self.selected_host = selector.values[0]
        self._render_components()
        await interaction.response.edit_message(content=self._content(), view=self)

    async def previous_page(self, interaction: discord.Interaction):
        self.page = max(0, self.page - 1)
        self._render_components()
        await interaction.response.edit_message(content=self._content(), view=self)

    async def next_page(self, interaction: discord.Interaction):
        page_count = max(1, (len(self.host_countries) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        self.page = min(page_count - 1, self.page + 1)
        self._render_components()
        await interaction.response.edit_message(content=self._content(), view=self)

    async def cancel_appointment(self, interaction: discord.Interaction):
        self.stop()
        await interaction.response.edit_message(
            content="Atama işlemi iptal edildi. · Appointment cancelled.", view=None
        )

    async def confirm_appointment(self, interaction: discord.Interaction):
        if self.selected_host is None:
            return await interaction.response.send_message(
                "Önce hedef ülkeyi seçin.", ephemeral=True
            )
        self.stop()
        await interaction.response.edit_message(
            content="⏳ Atama işleniyor… · Processing appointment…", view=None
        )
        await self.cog._perform_appointment(
            interaction,
            self.candidate,
            self.selected_host,
            response_acknowledged=True,
        )


class AmbassadorsCog(commands.Cog):
    """State-authorized ambassador nominations, permits, and recall enforcement."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._transition_lock = asyncio.Lock()

    async def cog_load(self):
        self.bot.add_view(AppointmentPanelView(self))
        if not self.reconcile_ambassador_roles.is_running():
            self.reconcile_ambassador_roles.start()

    def cog_unload(self):
        self.reconcile_ambassador_roles.cancel()

    @tasks.loop(minutes=10)
    async def reconcile_ambassador_roles(self):
        records = ambassador_appointments_storage.read()
        members_by_guild: dict[int, set[int]] = {}
        for record in records.values():
            if not isinstance(record, dict):
                continue
            if (
                record.get("status") not in ACTIVE_STATUSES
                and record.get("role_added_by_appointment") is not True
            ):
                continue
            try:
                guild_id = int(record["guild_id"])
                candidate_id = int(record["candidate_id"])
            except (KeyError, TypeError, ValueError):
                continue
            members_by_guild.setdefault(guild_id, set()).add(candidate_id)

        for guild_id, candidate_ids in members_by_guild.items():
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                continue
            for candidate_id in candidate_ids:
                try:
                    await self._sync_member_role(guild, candidate_id)
                except discord.NotFound:
                    log.warning(
                        "Ambassador is no longer in guild (guild=%s, member=%s)",
                        guild_id,
                        candidate_id,
                    )
                except discord.HTTPException:
                    log.exception(
                        "Could not reconcile ambassador role (guild=%s, member=%s)",
                        guild_id,
                        candidate_id,
                    )

    @staticmethod
    def _update_sticky_ambassador_role(
        guild_id: int, candidate_id: int, role: discord.Role, *, active: bool
    ):
        key = f"{guild_id}_{candidate_id}"
        snapshot = sticky_roles_storage.get(key)
        if not isinstance(snapshot, dict):
            return

        role_ids = {int(value) for value in snapshot.get("role_ids", []) if str(value).isdigit()}
        role_names = list(snapshot.get("role_names", []))
        resigned_ids = {
            int(value)
            for value in snapshot.get("resigned_role_ids", [])
            if str(value).isdigit()
        }
        if active:
            role_ids.add(role.id)
            if role.name not in role_names:
                role_names.append(role.name)
            resigned_ids.discard(role.id)
        else:
            role_ids.discard(role.id)
            role_names = [name for name in role_names if name != role.name]
            resigned_ids.add(role.id)

        snapshot["role_ids"] = sorted(role_ids)
        snapshot["role_names"] = role_names
        snapshot["resigned_role_ids"] = sorted(resigned_ids)
        sticky_roles_storage.set(key, snapshot)

    async def _sync_member_role(self, guild: discord.Guild, candidate_id: int):
        async with self._transition_lock:
            records = ambassador_appointments_storage.read()
            candidate_key = str(candidate_id)
            member_records = [
                record
                for record in records.values()
                if isinstance(record, dict)
                and str(record.get("guild_id")) == str(guild.id)
                and str(record.get("candidate_id")) == candidate_key
            ]
            if not member_records:
                return
            has_active_posting = any(
                record.get("status") in ACTIVE_STATUSES for record in member_records
            )
            workflow_owned = any(
                record.get("role_added_by_appointment") is True
                for record in member_records
            )
            independently_accredited = _office_is(
                delegates_storage.get(candidate_key, {}), "Amb.", ROLE_AMBASSADOR
            )
            role = discord.utils.get(guild.roles, name=ROLE_AMBASSADOR)
            if role is None:
                log.error("Ambassador role missing in guild %s", guild.id)
                return

            member = await guild.fetch_member(candidate_id)
            if has_active_posting:
                if role not in member.roles:
                    await member.add_roles(
                        role, reason="Active ambassador posting role reconciliation"
                    )
                    log.info(
                        "Restored ambassador role (guild=%s, member=%s)",
                        guild.id,
                        candidate_id,
                    )
                    if not workflow_owned:
                        owner_record = next(
                            record
                            for record in member_records
                            if record.get("status") in ACTIVE_STATUSES
                        )
                        owner_record["role_added_by_appointment"] = True
                        self._save(owner_record)
                self._update_sticky_ambassador_role(
                    guild.id, candidate_id, role, active=True
                )
            elif workflow_owned and not independently_accredited:
                if role in member.roles:
                    await member.remove_roles(
                        role, reason="No active ambassador postings remain"
                    )
                self._update_sticky_ambassador_role(
                    guild.id, candidate_id, role, active=False
                )

    @reconcile_ambassador_roles.before_loop
    async def before_reconcile_ambassador_roles(self):
        await self.bot.wait_until_ready()

    @staticmethod
    def _actor_record(interaction: discord.Interaction) -> dict:
        return delegates_storage.get(str(interaction.user.id), {})

    @staticmethod
    def _save(record: dict):
        ambassador_appointments_storage.set(record["id"], record)

    @staticmethod
    def _get_record(atama_no: str) -> dict | None:
        return ambassador_appointments_storage.get(atama_no.strip().upper())

    @staticmethod
    def _appointment_room_id(guild_id: int | str) -> str:
        configured = ambassador_rooms_storage.get(str(guild_id))
        if isinstance(configured, dict):
            configured = configured.get("channel_id")
        return str(configured) if configured else ""

    @staticmethod
    def _is_secretary_general(
        interaction: discord.Interaction,
        member: discord.Member | None = None,
    ) -> bool:
        member = member or interaction.user
        return isinstance(member, discord.Member) and any(
            role.name == ROLE_SG for role in member.roles
        )

    def _appointment_office(
        self,
        interaction: discord.Interaction,
        *,
        member: discord.Member | None = None,
        record: dict | None = None,
    ) -> str | None:
        actor = record if isinstance(record, dict) else self._actor_record(interaction)
        actor_member = member or interaction.user
        if self._is_secretary_general(interaction, actor_member):
            return "SG"
        if _office_is(actor, "Pres.", ROLE_PRESIDENT):
            return "Pres."
        if _office_is(actor, "FM", ROLE_FM):
            return "FM"
        return None

    def _appointment_room_and_office_allowed(
        self, interaction: discord.Interaction
    ) -> bool:
        return bool(
            interaction.guild_id
            and self._appointment_room_id(interaction.guild_id)
            == str(interaction.channel_id)
            and self._appointment_office(interaction) in {"Pres.", "FM", "SG"}
        )

    @staticmethod
    def _registered_host_countries(home_key: str) -> list[str]:
        names: dict[str, str] = {}
        for storage_key, country in countries_storage.read().items():
            if not isinstance(country, dict):
                continue
            resolved = _resolve_registered_country(
                str(country.get("name") or storage_key)
            )
            if resolved and resolved[1] != home_key:
                names[resolved[1]] = resolved[0]
        return sorted(names.values(), key=str.casefold)

    async def _maybe_remove_role(self, guild: discord.Guild, candidate_id: str):
        try:
            await self._sync_member_role(guild, int(candidate_id))
        except discord.NotFound:
            return
        except discord.HTTPException:
            log.exception(
                "Could not reconcile ambassador role (guild=%s, member=%s)",
                guild.id,
                candidate_id,
            )

    async def _perform_appointment(
        self,
        interaction: discord.Interaction,
        aday: discord.Member,
        ev_sahibi_ulke: str,
        *,
        response_acknowledged: bool = False,
    ):
        if not response_acknowledged:
            await interaction.response.defer(ephemeral=True)
        async with self._transition_lock:
            guild = interaction.guild
            current_room = self._appointment_room_id(interaction.guild_id)
            if not current_room or current_room != str(interaction.channel_id):
                return await interaction.followup.send(
                    "⛔ Atama yalnızca özel Diplomatik Atama Odasında yapılabilir.",
                    ephemeral=True,
                )

            actor = self._actor_record(interaction)
            candidate_record = delegates_storage.get(str(aday.id), {})

            def office_of(record: dict, member: discord.Member) -> str | None:
                if self._is_secretary_general(interaction, member):
                    return "SG"
                if _office_is(record, "Pres.", ROLE_PRESIDENT):
                    return "Pres."
                if _office_is(record, "FM", ROLE_FM):
                    return "FM"
                return None

            actor_office = office_of(actor, interaction.user)
            if actor_office is None:
                return await interaction.followup.send(
                    "⛔ Bu odada yalnızca kayıtlı Devlet Başkanları, Dışişleri Bakanları ve Genel Sekreter atama yapabilir.",
                    ephemeral=True,
                )

            candidate_home = _resolve_registered_country(
                str(candidate_record.get("nation", ""))
            )
            if candidate_home is None:
                return await interaction.followup.send(
                    "❌ Adayın kayıtlı gönderen devleti bulunamadı.", ephemeral=True
                )
            home_nation, home_key = candidate_home
            if actor_office != "SG":
                actor_home = _resolve_registered_country(str(actor.get("nation", "")))
                if actor_home is None or actor_home[1] != home_key:
                    return await interaction.followup.send(
                        "❌ Aday, atama yapan Başkan veya Bakanın kendi devletinde kayıtlı olmalıdır.",
                        ephemeral=True,
                    )

            resolved_host = _resolve_registered_country(ev_sahibi_ulke)
            if resolved_host is None:
                return await interaction.followup.send(
                    "❌ Hedef ülke kayıtlı ülkeler arasından seçilmelidir.",
                    ephemeral=True,
                )
            host_nation, host_key = resolved_host
            if host_key == home_key:
                return await interaction.followup.send(
                    "❌ Bir devlet kendi ülkesine büyükelçi atayamaz.", ephemeral=True
                )

            records = ambassador_appointments_storage.read()
            member_records = [
                record
                for record in records.values()
                if isinstance(record, dict)
                and str(record.get("guild_id")) == str(guild.id)
                and str(record.get("candidate_id")) == str(aday.id)
            ]
            if any(
                record.get("status") in ACTIVE_STATUSES
                and record.get("home_key") == home_key
                and record.get("host_key") == host_key
                for record in records.values()
                if isinstance(record, dict)
                and str(record.get("guild_id")) == str(guild.id)
            ):
                return await interaction.followup.send(
                    "❌ Bu devlet çifti için zaten etkin bir büyükelçi ataması var.",
                    ephemeral=True,
                )

            role = discord.utils.get(guild.roles, name=ROLE_AMBASSADOR)
            if role is None:
                return await interaction.followup.send(
                    "❌ Mevcut Büyükelçi rolü bulunamadı; atama yapılmadı.",
                    ephemeral=True,
                )
            try:
                candidate = await guild.fetch_member(aday.id)
            except discord.NotFound:
                return await interaction.followup.send(
                    "❌ Aday sunucuda bulunamadığı için atama yapılmadı.",
                    ephemeral=True,
                )
            except discord.HTTPException:
                log.exception("Could not fetch ambassador candidate %s", aday.id)
                return await interaction.followup.send(
                    "❌ Adayın güncel durumu doğrulanamadı; atama yapılmadı.",
                    ephemeral=True,
                )

            def candidate_is_eligible(member: discord.Member) -> bool:
                latest_candidate_record = delegates_storage.get(str(aday.id), {})
                latest_home = _resolve_registered_country(
                    str(latest_candidate_record.get("nation", ""))
                )
                return (
                    latest_home is not None
                    and latest_home[1] == home_key
                    and latest_candidate_record.get("office_code") != "Suspended"
                    and not any(r.name == ROLE_SUSPENDED for r in member.roles)
                )

            if not candidate_is_eligible(candidate):
                return await interaction.followup.send(
                    "❌ Aday akredite değil veya askıya alınmış; atama yapılmadı.",
                    ephemeral=True,
                )

            workflow_owned = any(
                record.get("role_added_by_appointment") is True
                for record in member_records
            )
            had_active_posting = any(
                record.get("status") in ACTIVE_STATUSES for record in member_records
            )
            # Re-read the nominator and nominee immediately before persisting the appointment.
            try:
                refreshed_candidate = await guild.fetch_member(aday.id)
            except discord.NotFound:
                refreshed_candidate = None
            except discord.HTTPException:
                refreshed_candidate = None
            try:
                refreshed_actor = await guild.fetch_member(interaction.user.id)
            except discord.HTTPException:
                refreshed_actor = None
            if refreshed_actor is None:
                return await interaction.followup.send(
                    "❌ Atama yapan makamın güncel sunucu üyeliği doğrulanamadı; atama yapılmadı.",
                    ephemeral=True,
                )
            fresh_actor = self._actor_record(interaction)
            fresh_office = office_of(fresh_actor, refreshed_actor)
            latest_room = self._appointment_room_id(interaction.guild_id)
            fresh_candidate_record = delegates_storage.get(str(aday.id), {})
            fresh_home = _resolve_registered_country(
                str(fresh_candidate_record.get("nation", ""))
            )
            fresh_actor_home = _resolve_registered_country(
                str(fresh_actor.get("nation", ""))
            )
            still_authorized = (
                fresh_office in {"Pres.", "FM", "SG"}
                and latest_room == str(interaction.channel_id)
                and (
                    fresh_office == "SG"
                    or (
                        fresh_actor_home is not None
                        and fresh_actor_home[1] == home_key
                    )
                )
            )
            if (
                not still_authorized
                or fresh_home is None
                or fresh_home[1] != home_key
                or not refreshed_candidate
                or not candidate_is_eligible(refreshed_candidate)
            ):
                return await interaction.followup.send(
                    "❌ Yetki veya aday uygunluğu işlem sırasında değişti; atama yapılmadı.",
                    ephemeral=True,
                )

            had_role = role in refreshed_candidate.roles
            now = _utc_now()
            record_id = f"ELC-{uuid.uuid4().hex[:8].upper()}"
            record = {
                "id": record_id,
                "guild_id": guild.id,
                "home_nation": home_nation,
                "home_key": home_key,
                "host_nation": host_nation,
                "host_key": host_key,
                "candidate_id": str(aday.id),
                "candidate_name": aday.display_name,
                "nominated_by": str(interaction.user.id),
                "nominated_at": now.isoformat(),
                "appointed_by": str(interaction.user.id),
                "appointed_by_office": fresh_office,
                "appointed_at": now.isoformat(),
                "status": "active",
                "protected_until": (now + PROTECTION_PERIOD).isoformat(),
                "work_permit_active": True,
                "residence_permit_active": True,
                "role_added_by_appointment": (not had_role) or workflow_owned,
                "events": [],
            }
            _append_event(record, "appointed", interaction.user.id, office=fresh_office)
            self._save(record)
            if not had_role:
                try:
                    await refreshed_candidate.add_roles(
                        role, reason=f"Ambassador appointed by {fresh_office}"
                    )
                except discord.HTTPException:
                    log.exception("Could not grant ambassador role to %s", aday.id)
                    record.update(
                        {
                            "status": "failed",
                            "work_permit_active": False,
                            "residence_permit_active": False,
                            "failed_at": _iso_now(),
                        }
                    )
                    _append_event(record, "role_grant_failed", interaction.user.id)
                    self._save(record)
                    independently_accredited = _office_is(
                        delegates_storage.get(str(aday.id), {}),
                        "Amb.",
                        ROLE_AMBASSADOR,
                    )
                    if (
                        record["role_added_by_appointment"]
                        and not had_active_posting
                        and not independently_accredited
                    ):
                        self._update_sticky_ambassador_role(
                            guild.id, aday.id, role, active=False
                        )
                        try:
                            await refreshed_candidate.remove_roles(
                                role, reason=f"Failed ambassador appointment {record_id}"
                            )
                        except discord.HTTPException:
                            log.exception("Could not clean up ambassador role for %s", aday.id)
                    return await interaction.followup.send(
                        "❌ Büyükelçi rolü verilemedi; atama iptal edildi.", ephemeral=True
                    )
            self._update_sticky_ambassador_role(
                guild.id, aday.id, role, active=True
            )

        gazette_issue_no = None
        try:
            authority_names = {
                "Pres.": "Devlet Başkanlığı · Presidency",
                "FM": "Dışişleri Bakanlığı · Ministry of Foreign Affairs",
                "SG": "Genel Sekreterlik · Secretariat",
            }
            gazette_issue_no = await publish_to_gazette(
                self.bot,
                guild.id,
                "BÜYÜKELÇİ ATAMASI · AMBASSADOR APPOINTMENT",
                f"{host_nation} — Büyükelçi Atandı · Ambassador Appointed",
                (
                    f"**Gönderen devlet / Sending state:** `{home_nation}`\n"
                    f"**Ev sahibi devlet / Host state:** `{host_nation}`\n"
                    f"**Atanan büyükelçi / Appointed ambassador:** <@{aday.id}> (`{aday.display_name}`)\n"
                    f"**Atayan makam / Appointing authority:** <@{interaction.user.id}> "
                    f"({authority_names.get(fresh_office, fresh_office)})\n"
                    f"**Atanma tarihi / Appointed:** {now:%d.%m.%Y %H:%M UTC}\n"
                    "**Görev ve izinler / Posting and permits:** Geri çağırma veya istifaya kadar; "
                    "çalışma ve ikamet izinleri aktiftir. / Active until recall or resignation.\n"
                    f"**14 günlük koruma / Protection:** {now + PROTECTION_PERIOD:%d.%m.%Y %H:%M UTC} tarihine kadar "
                    "Dışişleri Bakanı geri çağıramaz; gönderen devletin Başkanı geri çağırabilir. "
                    "/ The Foreign Minister cannot recall during this period; the sending state's President may recall."
                ),
                fields=[
                    ("Gönderen → Ev sahibi", f"`{home_nation}` → `{host_nation}`", True),
                    ("Büyükelçi / Ambassador", f"<@{aday.id}>", True),
                    ("Atama sicili / Appointment record", f"`{record_id}`", True),
                ],
                color=0x1D4ED8,
                issuer=authority_names.get(fresh_office, fresh_office),
            )
            if gazette_issue_no:
                record["gazette_issue_no"] = gazette_issue_no
                record["gazette_published_at"] = _iso_now()
                self._save(record)
        except Exception:
            log.exception(
                "Ambassador appointment succeeded but Official Gazette publication failed (record=%s)",
                record_id,
            )

        gazette_note = (
            f"\n📰 Resmî Gazete / Official Gazette: **{gazette_issue_no}**"
            if gazette_issue_no
            else "\n⚠️ Atama etkin; Resmî Gazete kaydı oluşturulamadı."
        )
        await interaction.followup.send(
            f"✅ **{record_id}** ataması etkinleştirildi: {aday.mention} → **{host_nation}**. "
            "Çalışma ve ikamet izinleri geri çağırılana kadar aktiftir. "
            f"İlk 14 günde geri çağırma koruması **{(now + PROTECTION_PERIOD):%d.%m.%Y %H:%M UTC}** tarihine kadar sürer."
            f"{gazette_note}",
            ephemeral=True,
        )

    @app_commands.command(
        name="elci-geri-cagir",
        description="Büyükelçiyi geri çağırır; Bakan için ilk 14 gün koruma vardır / Recall an ambassador",
    )
    @app_commands.guild_only()
    @app_commands.describe(atama_no="Etkin atama sicil numarası / Active appointment ID")
    async def recall_ambassador(
        self,
        interaction: discord.Interaction,
        atama_no: str,
    ):
        await interaction.response.defer(ephemeral=True)
        async with self._transition_lock:
            actor = self._actor_record(interaction)
            actor_home_key = normalize_country_key(str(actor.get("nation", "")))
            actor_is_president = _office_is(actor, "Pres.", ROLE_PRESIDENT)
            actor_is_fm = _office_is(actor, "FM", ROLE_FM)
            record = self._get_record(atama_no)
            if (
                not isinstance(record, dict)
                or record.get("status") != "active"
                or str(record.get("guild_id")) != str(interaction.guild_id)
            ):
                return await interaction.followup.send(
                    "❌ Etkin büyükelçi ataması bulunamadı.", ephemeral=True
                )
            if actor_home_key != record.get("home_key") or not (actor_is_president or actor_is_fm):
                return await interaction.followup.send(
                    "⛔ Geri çağırmayı yalnızca gönderen devletin Devlet Başkanı veya Dışişleri Bakanı yapabilir.",
                    ephemeral=True,
                )

            now = _utc_now()
            protected_until = _parse_datetime(record["protected_until"])
            if actor_is_fm and not actor_is_president and now < protected_until:
                _append_event(
                    record,
                    "early_recall_denied",
                    interaction.user.id,
                    protected_until=protected_until.isoformat(),
                )
                self._save(record)
                return await interaction.followup.send(
                    "⛔ Geri çağırma reddedildi: atamadan sonraki ilk 14 gün içinde bunu yalnızca büyükelçinin istifası "
                    "veya gönderen devletin Devlet Başkanı görevden alabilir. Deneme sicile işlendi. "
                    f"Bakanın geri çağırma yetkisi **{protected_until:%d.%m.%Y %H:%M UTC}** tarihinde açılır.",
                    ephemeral=True,
                )

            record.update(
                {
                    "status": "recalled",
                    "work_permit_active": False,
                    "residence_permit_active": False,
                    "ended_at": now.isoformat(),
                    "ended_by": str(interaction.user.id),
                }
            )
            _append_event(record, "recalled", interaction.user.id)
            self._save(record)
        await self._maybe_remove_role(interaction.guild, str(record["candidate_id"]))
        await interaction.followup.send(
            f"✅ **{record['id']}** geri çağrıldı. Çalışma ve ikamet izinleri sona erdi.",
            ephemeral=True,
        )

    @app_commands.command(
        name="elci-istifa",
        description="Etkin büyükelçi görevinden istifa eder / Resign from an active ambassador posting",
    )
    @app_commands.guild_only()
    @app_commands.describe(atama_no="Etkin atama sicil numarası / Active appointment ID")
    async def resign_ambassador(
        self,
        interaction: discord.Interaction,
        atama_no: str,
    ):
        await interaction.response.defer(ephemeral=True)
        async with self._transition_lock:
            record = self._get_record(atama_no)
            if (
                not isinstance(record, dict)
                or record.get("status") != "active"
                or str(record.get("guild_id")) != str(interaction.guild_id)
            ):
                return await interaction.followup.send(
                    "❌ Etkin büyükelçi ataması bulunamadı.", ephemeral=True
                )
            if str(record.get("candidate_id")) != str(interaction.user.id):
                return await interaction.followup.send(
                    "⛔ Yalnızca atanan büyükelçi kendi görevinden istifa edebilir.",
                    ephemeral=True,
                )

            record.update(
                {
                    "status": "resigned",
                    "work_permit_active": False,
                    "residence_permit_active": False,
                    "ended_at": _iso_now(),
                    "ended_by": str(interaction.user.id),
                }
            )
            _append_event(record, "resigned", interaction.user.id)
            self._save(record)
        await self._maybe_remove_role(interaction.guild, str(record["candidate_id"]))
        await interaction.followup.send(
            f"✅ **{record['id']}** siciline istifanız işlendi. Çalışma ve ikamet izinleriniz sona erdi.",
            ephemeral=True,
        )

    @app_commands.command(
        name="buyukelciler",
        description="Etkin büyükelçi atamalarını ve izin durumlarını listeler / List active ambassadors",
    )
    @app_commands.guild_only()
    @app_commands.describe(sayfa="Gösterilecek sayfa numarası / Page number")
    async def list_ambassadors(
        self, interaction: discord.Interaction, sayfa: app_commands.Range[int, 1, 100000] = 1
    ):
        active = [
            record
            for record in ambassador_appointments_storage.read().values()
            if isinstance(record, dict)
            and record.get("status") == "active"
            and str(record.get("guild_id")) == str(interaction.guild_id)
        ]
        if not active:
            return await interaction.response.send_message(
                "ℹ️ Şu anda etkin büyükelçi ataması yok.", ephemeral=True
            )
        page_size = 12
        page_count = (len(active) + page_size - 1) // page_size
        if sayfa > page_count:
            return await interaction.response.send_message(
                f"❌ Bu listede yalnızca {page_count} sayfa var.", ephemeral=True
            )
        start = (sayfa - 1) * page_size
        embed = discord.Embed(
            title="🏛️ Etkin Büyükelçi Atamaları · Active Ambassador Postings",
            colour=0x1E3A8A,
        )
        for record in active[start:start + page_size]:
            embed.add_field(
                name=f"{record['home_nation']} → {record['host_nation']} · {record['id']}",
                value=(
                    f"<@{record['candidate_id']}>\n"
                    "Çalışma ve ikamet izni: **aktif / active**\n"
                    f"Bakan koruması / FM protection until: {record['protected_until'][:10]}"
                ),
                inline=False,
            )
        embed.set_footer(text=f"Sayfa {sayfa}/{page_count} · Toplam {len(active)} atama")
        await interaction.response.send_message(embed=embed, ephemeral=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(AmbassadorsCog(bot))
