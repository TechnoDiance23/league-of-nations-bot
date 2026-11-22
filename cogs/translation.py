import asyncio
import io
import logging
import os
import re
import time
from collections import Counter, OrderedDict

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from cogs.translation_languages import NATURAL_LANGUAGE_NAMES, identify_language, normalize_language
from utils.storage import translation_channels_storage


log = logging.getLogger("InternationalDiplomaticBot.Translation")
TRANSLATE_URL = os.getenv("TRANSLATION_API_URL", "http://127.0.0.1:5000/translate")
DETECT_URL = TRANSLATE_URL.rsplit("/", 1)[0] + "/detect"
NATURAL_TRANSLATE_URL = "http://127.0.0.1:8092/completion"
LOCAL_REQUEST_ERRORS = (aiohttp.ClientError, asyncio.TimeoutError, ValueError)

LIBRE_LANGUAGES = {"az", "de", "en", "es", "fr", "it", "ja", "ko", "ky", "ru", "tr", "uk", "zh-Hans"}

# Keep Discord-specific syntax outside the text submitted for translation.
MARKER = re.compile(r"⟦\d+⟧")
TRAILING_MARKERS = re.compile(r"(?:[ \t]+⟦\d+⟧)+$")
FLAT_MARKER = re.compile(r"\[(\d+)\]")
PROTECTED_TEXT = re.compile(
    r"(?s:```.*?```)|``[^`\n]*``|`[^`\n]*`|"
    r"<(?:@!?|@&|#)\d+>|<a?:\w+:\d+>|@(?:everyone|here)\b|"
    r"<https?://[^\s>]+>|(?:https?://|www\.)[^\s<>()]+|"
    r"<[|｜][^<>\r\n]+[|｜]>|</?(?:source|think|bos|eos|pad|unk|mask|unused\d+|"
    r"(?:start|end)_of_(?:turn|image|audio)|(?:image|audio)_soft_token)>|"
    + MARKER.pattern + "|" + FLAT_MARKER.pattern,
    re.IGNORECASE,
)

LANGUAGE_LABELS = {
    **NATURAL_LANGUAGE_NAMES,
    "az": "Azərbaycan dili · Azerbaijani",
    "en": "English",
    "it": "Italiano",
    "ru": "Русский",
    "fr": "Français",
    "zh-Hans": "中文 · Chinese",
    "de": "Deutsch",
    "es": "Español",
    "uk": "Українська",
    "ja": "日本語",
    "ko": "한국어",
    "ky": "Кыргызча",
    "tr": "Türkçe",
}

DEFAULT_CHANNEL_LANGUAGES = {
    "1570443567824904242": "tr",  # tr-sohbet
    "1570444668221526066": "en",  # global-chat
    "1570444785016373368": "en",  # en-chat
    "1570444821141659719": "it",  # it-chat
    "1570444853635190784": "es",  # es-chat
    "1570836608079368262": "ru",  # ru-chat
    "1572648788626243798": "de",  # de-chat
    "1573054083638493275": "fr",  # fr-chat
}
CHAT_CATEGORY_ID = 1570443008183111720
WEBHOOK_NAME = "Le Haut-Commissaire Translator"
FIXED_ROOM_TARGETS = {
    "1570443567824904242": ("tr", "en"),      # #💬・tr-sohbet
    "1570444668221526066": ("tr", "en"),      # #💬・global-chat
    "1573054083638493275": ("fr", "tr", "en"), # #💬・fr-chat
}
LANG_BADGES = {"tr": "🇹🇷 TR", "en": "🇬🇧 EN", "fr": "🇫🇷 FR", "de": "🇩🇪 DE", "it": "🇮🇹 IT", "es": "🇪🇸 ES", "ru": "🇷🇺 RU"}
CHAT_ACTIVITY_SECONDS = 45 * 60
MAX_TRACKED_ROOMS = 128
MAX_TRACKED_SPEAKERS = 64
MAX_REPLY_LANGUAGES = 3

SHORT_GREETING_LANGUAGES = {
    "bonjour": "fr",
    "hello": "en",
    "hi": "en",
    "hola": "es",
    "merhaba": "tr",
    "selam": "tr",
    "hoş geldin": "tr",
    "hoş geldiniz": "tr",
    "welcome": "en",
    "welcome back": "en",
    "you're welcome": "en",
    "you’re welcome": "en",
}
COMMON_CHAT_TRANSLATIONS = {
    ("tr", "en"): {
        "gündüz": "Daytime", "merhaba": "Hello", "selam": "Hi",
        "hoş geldin": "Welcome", "hoş geldiniz": "Welcome",
    },
    ("en", "tr"): {
        "hello": "Merhaba", "hi": "Selam", "welcome": "Hoş geldin",
        "welcome back": "Tekrar hoş geldin", "you're welcome": "Rica ederim",
        "you’re welcome": "Rica ederim",
    },
}
TURKISH_NEGATIVE_REQUEST = re.compile(
    r"\bsen\s+((?:[^.!?;\n]{1,80}?\s+)?)(başlatma|bitirme|gönderme|yollama|açma"
    r"|kapatma|silme|yazma|arama|bekleme|gitme|gelme|söyleme|yapma|karışma|unutma)"
    r",?\s+olur\s+mu\b\??",
    re.IGNORECASE,
)


def _clarify_turkish_negative(match: re.Match) -> str:
    prefix, verb = match.groups()
    ending = "manı" if verb.casefold().endswith("ma") else "meni"
    request = f"{prefix}{verb[:-2]}{ending} istemiyorum."
    return request[:1].upper() + request[1:] if match[0][0].isupper() else request


def _detected_language(detection):
    if not isinstance(detection, dict):
        return None
    language = detection.get("language")
    if not isinstance(language, str):
        return None
    try:
        if float(detection.get("confidence", 1)) <= 0:
            return None
    except (TypeError, ValueError):
        return None
    language = normalize_language(language)
    return language if language in LANGUAGE_LABELS else None


def _protect_text(text):
    originals = {}

    def replace(match):
        marker = f"⟦{len(originals) + 1}⟧"
        originals[marker] = match.group()
        return marker

    return PROTECTED_TEXT.sub(replace, text), originals


def _restore_text(text, originals):
    if originals:
        text = FLAT_MARKER.sub(lambda match: f"⟦{match[1]}⟧", text)
    if Counter(MARKER.findall(text)) != Counter(originals.keys()):
        return None
    return MARKER.sub(lambda match: originals[match.group()], text)


def _room_settings(channel, config):
    room = channel.parent if isinstance(channel, discord.Thread) else channel
    if room is None:
        return False, ()
    language = config.get(str(room.id))
    if language == "off":
        return False, ()
    if isinstance(language, str) and language in LANGUAGE_LABELS:
        return True, (language,)
    if isinstance(room, discord.TextChannel) and room.category_id == CHAT_CATEGORY_ID:
        default_language = DEFAULT_CHANNEL_LANGUAGES.get(str(room.id))
        return True, (default_language,) if default_language else ("tr", "en")
    return False, ()


class TranslationCog(commands.Cog):
    """Translate chat messages for the languages being used in the conversation."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._session: aiohttp.ClientSession | None = None
        self._request_slots = asyncio.Semaphore(4)
        self._natural_slot = asyncio.Semaphore(1)
        self._webhooks: dict[int, discord.Webhook] = {}
        self._speaker_languages: OrderedDict[
            int, OrderedDict[int, tuple[str, int, float]]
        ] = OrderedDict()

    async def cog_load(self):
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=24)
        )
        config = translation_channels_storage.read()
        if not config.get("_initialized"):
            translation_channels_storage.write(
                {"_initialized": True, **DEFAULT_CHANNEL_LANGUAGES}
            )

    async def cog_unload(self):
        if self._session and not self._session.closed:
            await self._session.close()

    @staticmethod
    def _is_admin(interaction: discord.Interaction) -> bool:
        return bool(
            interaction.guild
            and isinstance(interaction.user, discord.Member)
            and interaction.user.guild_permissions.administrator
        )

    def _recent_speakers(self, channel_id):
        speakers = self._speaker_languages.get(channel_id)
        if speakers is None:
            return OrderedDict()
        cutoff = time.monotonic() - CHAT_ACTIVITY_SECONDS
        for author_id, (_, _, heard_at) in list(speakers.items()):
            if heard_at <= cutoff:
                del speakers[author_id]
        return speakers

    def _remember_language(self, message, language):
        channel_id = message.channel.id
        speakers = self._recent_speakers(channel_id)
        current = speakers.get(message.author.id)
        if current is not None and current[1] >= message.id:
            return
        speakers[message.author.id] = (language, message.id, time.monotonic())
        speakers.move_to_end(message.author.id)
        if len(speakers) > MAX_TRACKED_SPEAKERS:
            speakers.popitem(last=False)
        self._speaker_languages[channel_id] = speakers
        self._speaker_languages.move_to_end(channel_id)
        if len(self._speaker_languages) > MAX_TRACKED_ROOMS:
            self._speaker_languages.popitem(last=False)

    async def _reply_language(self, message, speakers):
        reference = message.reference
        original = reference.resolved if reference is not None else None
        if (
            not isinstance(original, discord.Message)
            or original.channel.id != message.channel.id
            or original.author.id == message.author.id
            or original.author.bot
            or original.webhook_id is not None
        ):
            return None
        speaker = speakers.get(original.author.id)
        if speaker is not None and speaker[1] == original.id:
            return speaker[0]
        return await self._detect_language(PROTECTED_TEXT.sub(" ", original.content))

    async def _get_webhook(self, channel: discord.TextChannel):
        cached = self._webhooks.get(channel.id)
        if cached is not None:
            return cached
        try:
            for wh in await channel.webhooks():
                if wh.name == WEBHOOK_NAME:
                    self._webhooks[channel.id] = wh
                    return wh
            created = await channel.create_webhook(name=WEBHOOK_NAME, reason="MC-KS-001 Otomatik Çeviri Webhook")
            self._webhooks[channel.id] = created
            return created
        except Exception as exc:
            log.warning("Could not get/create translator webhook in #%s: %s", channel.name, exc)
            return None

    async def _target_languages(self, message, source, defaults):
        room = message.channel.parent if isinstance(message.channel, discord.Thread) else message.channel
        fixed = FIXED_ROOM_TARGETS.get(str(getattr(room, "id", "")))
        if fixed:
            if fixed == ("tr", "en"):
                if source == "tr":
                    return ["en"]
                if source == "en":
                    return ["tr"]
                return ["tr", "en"]
            if fixed == ("fr", "tr", "en"):
                return ["tr", "en"] if source == "fr" else ["fr"]
        speakers = self._recent_speakers(message.channel.id)
        reply_language = await self._reply_language(message, speakers)
        # Reserve the room language and the Turkish bridge before using other
        # participant languages, even when everyone has spoken the source language.
        candidates = list(defaults) if len(defaults) == 1 else []
        candidates.extend(("tr", reply_language))
        candidates.extend(
            language
            for author_id, (language, _, _) in reversed(speakers.items())
            if author_id != message.author.id
        )
        targets = [
            language for language in dict.fromkeys(candidates)
            if language and language != source
        ]
        return (targets or ["en"])[:MAX_REPLY_LANGUAGES]

    async def _post_json(self, url, payload, timeout):
        async with self._request_slots:
            async with self._session.post(
                url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=timeout, sock_connect=2),
                allow_redirects=False,
            ) as response:
                response.raise_for_status()
                return await response.json()

    async def _natural_translation(self, text, source, target):
        source_name = NATURAL_LANGUAGE_NAMES[source]
        target_name = NATURAL_LANGUAGE_NAMES[target]
        source_code = source.split("-", 1)[0]
        target_code = target.split("-", 1)[0]
        # TranslateGemma expects a language-pair instruction in a Gemma user turn.
        # _protect_text masks literal control tokens before this prompt is built.
        if source == "tr":
            # Disambiguate frequent casual negatives such as "başlatma olur mu".
            text = TURKISH_NEGATIVE_REQUEST.sub(_clarify_turkish_negative, text)
        if (
            len(text) <= 100
            and "\n" not in text
            and not re.search(r"[.!?…]\s+\S", text)
            and not MARKER.search(text)
        ):
            instruction = (
                f"Translate this {source_name} ({source_code}) message into {target_name} "
                f"({target_code}). Preserve its meaning and tone. Output only the translation:"
            )
        else:
            instruction = (
                f"You are a professional {source_name} ({source_code}) to {target_name} "
                f"({target_code}) translator. Your goal is to accurately convey the meaning "
                f"and nuances of the original {source_name} text while adhering to {target_name} "
                "grammar, vocabulary, and cultural sensitivities.\n"
                f"Produce only the {target_name} translation, without any additional explanations "
                f"or commentary. Please translate the following {source_name} text into {target_name}:"
            )
        prompt = f"<start_of_turn>user\n{instruction}\n\n\n{text}<end_of_turn>\n<start_of_turn>model\n"
        payload = {
            "prompt": prompt,
            "temperature": 0,
            "repeat_penalty": 1.1,
            "n_predict": min(384, max(64, len(text) // 2 + 32)),
            "stop": ["<end_of_turn>"],
            "cache_prompt": True,  # Reuse the common instruction in the model's in-memory slot.
        }
        has_fallback = source in LIBRE_LANGUAGES and target in LIBRE_LANGUAGES
        try:
            await asyncio.wait_for(self._natural_slot.acquire(), timeout=22 if has_fallback else 65)
            try:
                result = await self._post_json(NATURAL_TRANSLATE_URL, payload, 40)
            finally:
                self._natural_slot.release()
        except LOCAL_REQUEST_ERRORS as error:
            log.debug("Natural translation unavailable (%s)", type(error).__name__)
            return None
        if not isinstance(result, dict):
            return None
        translated = result.get("content")
        if (
            result.get("stop_type") in {"eos", "word"}
            and not result.get("truncated")
            and isinstance(translated, str)
            and translated.strip()
            and not PROTECTED_TEXT.search(FLAT_MARKER.sub("", MARKER.sub("", translated)))
        ):
            return translated.strip()
        return None

    async def _detect_language(self, detection_text):
        if not any(character.isalpha() for character in detection_text):
            return None
        greeting = detection_text.casefold().strip(" !?.,…\t\r\n")
        if greeting in SHORT_GREETING_LANGUAGES:
            return SHORT_GREETING_LANGUAGES[greeting]
        language = identify_language(detection_text)
        if language in LANGUAGE_LABELS:
            return language
        try:
            detections = await self._post_json(DETECT_URL, {"q": detection_text}, 4)
            if isinstance(detections, list) and detections:
                return _detected_language(detections[0])
        except LOCAL_REQUEST_ERRORS as error:
            log.debug("Language detection unavailable (%s)", type(error).__name__)
        return None

    async def _translate_prepared(self, original, text, originals, source, target):
        if source == target:
            return None
        stripped = text.strip()
        word = stripped.rstrip("!?.,…")
        suffix = stripped[len(word):]
        trailing = TRAILING_MARKERS.search(word)
        if trailing:
            suffix = trailing[0] + suffix
            word = word[:trailing.start()]
        common = COMMON_CHAT_TRANSLATIONS.get((source, target), {}).get(word.casefold())
        if common:
            restored = _restore_text(common + suffix, originals)
            if restored:
                return restored, source
        if source in NATURAL_LANGUAGE_NAMES and target in NATURAL_LANGUAGE_NAMES:
            translated = await self._natural_translation(text, source, target)
            restored = _restore_text(translated, originals) if translated else None
            if restored and restored != original.strip():
                return restored, source

        if target not in LIBRE_LANGUAGES or (source and source not in LIBRE_LANGUAGES):
            return None
        payload = {
            "q": text,
            "source": source or "auto",
            "target": target,
            "format": "text",
        }
        result = await self._post_json(TRANSLATE_URL, payload, 12)
        if not isinstance(result, dict):
            return None
        source = source or _detected_language(result.get("detectedLanguage"))
        translated = result.get("translatedText")
        if not source or source == target or not isinstance(translated, str):
            return None
        restored = _restore_text(translated.strip(), originals)
        if restored and restored != original.strip():
            return restored, source
        return None

    async def _translate_message(self, message, defaults):
        original = message.content
        text, originals = _protect_text(original)
        detection_text = MARKER.sub(" ", text).strip()
        if not any(character.isalpha() for character in detection_text):
            return
        source = await self._detect_language(detection_text)
        if source:
            self._remember_language(message, source)
        targets = await self._target_languages(message, source, defaults)

        async def translate(rank, target):
            try:
                result = await self._translate_prepared(original, text, originals, source, target)
            except Exception as error:
                log.warning(
                    "Local translation failed (channel=%s, message=%s, target=%s, error=%s)",
                    message.channel.id, message.id, target, type(error).__name__,
                )
                return None
            if result is not None:
                translated, detected = result
                return rank, target, translated, detected
            return None

        tasks = [asyncio.create_task(translate(rank, target)) for rank, target in enumerate(targets)]
        try:
            for completion in asyncio.as_completed(tasks):
                result = await completion
                if result is not None:
                    yield result
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    @staticmethod
    def _render_translations(translations):
        # Discord combines the character count of every embed in a message.
        description_limit = min(3900, 5500 // len(translations))
        embeds, files = [], []
        for target, translated, detected in translations:
            is_long = len(translated) > description_limit
            embed = discord.Embed(
                title=f"🌐 {LANGUAGE_LABELS[target]} · {detected.upper()} → {target.upper()}",
                description=(translated[:description_limit - 1] + "…") if is_long else translated,
                colour=0x2563EB,
            )
            if is_long:
                files.append(discord.File(
                    io.BytesIO(translated.encode("utf-8")),
                    filename=f"translation-{target.lower()}.txt",
                ))
                embed.set_footer(text="Tam çeviri ekte · Full translation attached")
            embeds.append(embed)
        return embeds, files

    @staticmethod
    def _render_webhook_embed(translations):
        lines = []
        src_code = ""
        targets = []
        for target, translated, detected in translations:
            src_code = (detected or "").upper()
            targets.append(target.upper())
            badge = LANG_BADGES.get(target, f"🌐 {target.upper()}")
            lines.append(f"**{badge}:** {translated.strip()}")
        desc = "\n".join(lines)
        if len(desc) > 3900:
            desc = desc[:3897] + "…"
        emb = discord.Embed(description=desc, colour=0x3B82F6)
        if src_code:
            emb.set_footer(text=f"🌐 Otomatik Çeviri · {src_code} → {', '.join(targets)}")
        return emb

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if (
            message.guild is None
            or message.author.bot
            or message.webhook_id is not None
            or not message.content.strip()
            or self._session is None
        ):
            return

        try:
            ctx = await self.bot.get_context(message)
            if ctx.valid:
                return
        except Exception:
            log.exception("Could not inspect message context (message=%s)", message.id)
            return

        enabled, defaults = _room_settings(
            message.channel, translation_channels_storage.read()
        )
        if not enabled:
            return

        posted = None
        wh_msg = None
        orig_deleted = False
        completed = {}
        allowed_mentions = discord.AllowedMentions.none()
        use_webhook = isinstance(message.channel, discord.TextChannel) and not message.attachments and len(message.content) <= 1850
        wh = await self._get_webhook(message.channel) if use_webhook else None

        reply_prefix = ""
        if message.reference and isinstance(message.reference.resolved, discord.Message):
            ref_msg = message.reference.resolved
            ref_snip = " ".join((ref_msg.content or "").split())[:60]
            if ref_snip:
                reply_prefix = f"-# ↩️ **{ref_msg.author.display_name}:** {ref_snip}\n"
        orig_body = f"{reply_prefix}{message.content.strip()}"[:1950]

        try:
            translations = self._translate_message(message, defaults)
            try:
                async for rank, target, translated, detected in translations:
                    completed[rank] = (target, translated, detected)
                    ordered = [completed[key] for key in sorted(completed)]

                    if wh is not None and posted is None:
                        lines = [f"{reply_prefix}**Orijinal:** {message.content.strip()}"]
                        for t_lang, t_text, _det in ordered:
                            badge = LANG_BADGES.get(t_lang, t_lang.upper())
                            lines.append(f"**Oto Çeviri ({badge}):** {t_text.strip()}")
                        text_body = "\n".join(lines)
                        if len(text_body) > 1990:
                            text_body = text_body[:1987] + "..."
                        try:
                            if wh_msg is None:
                                wh_msg = await wh.send(
                                    content=text_body,
                                    username=message.author.display_name[:80],
                                    avatar_url=message.author.display_avatar.url,
                                    allowed_mentions=allowed_mentions,
                                    wait=True,
                                )
                                if not orig_deleted:
                                    orig_deleted = True
                                    try:
                                        await message.delete()
                                    except Exception:
                                        pass
                            else:
                                wh_msg = await wh_msg.edit(content=text_body, allowed_mentions=allowed_mentions)
                            continue
                        except Exception as exc:
                            self._webhooks.pop(message.channel.id, None)
                            log.warning("Webhook fallback in #%s: %s", message.channel.name, exc)
                            wh = None

                    embeds, files = self._render_translations(ordered)
                    if posted is None:
                        options = {"embeds": embeds, "mention_author": False, "allowed_mentions": allowed_mentions}
                        if files:
                            options["files"] = files
                        posted = await message.reply(**options)
                    else:
                        posted = await posted.edit(embeds=embeds, attachments=files, allowed_mentions=allowed_mentions)
            finally:
                await translations.aclose()
        except discord.HTTPException:
            log.warning(
                "Could not post translation (channel=%s, message=%s)",
                message.channel.id,
                message.id,
            )
        except Exception:
            log.exception(
                "Unexpected translation response (channel=%s, message=%s)",
                message.channel.id,
                message.id,
            )

    @app_commands.command(
        name="ceviri-kanali-ekle",
        description="Kanalı çeviriye açar ve varsayılan dil belirler / Enable chat translation",
    )
    @app_commands.guild_only()
    @app_commands.describe(
        kanal="Otomatik çevirinin açılacağı kanal",
        hedef_dil="Katılımcı dillerine ek olarak kullanılacak kanal dili / Default channel language",
    )
    async def add_translation_channel(
        self,
        interaction: discord.Interaction,
        kanal: discord.TextChannel,
        hedef_dil: str,
    ):
        if not self._is_admin(interaction):
            return await interaction.response.send_message(
                "❌ Bu ayarı yalnızca sunucu yöneticisi değiştirebilir. / Server Admin only.",
                ephemeral=True,
            )
        code = normalize_language(hedef_dil)
        if code not in LANGUAGE_LABELS:
            return await interaction.response.send_message(
                "❌ Desteklenen bir dil kodu veya listeden bir dil seç. / Choose a supported language.",
                ephemeral=True,
            )
        translation_channels_storage.set(str(kanal.id), code)
        await interaction.response.send_message(
            f"✅ {kanal.mention} için varsayılan dil **{LANGUAGE_LABELS[code]}** oldu.",
            ephemeral=True,
        )

    @add_translation_channel.autocomplete("hedef_dil")
    async def autocomplete_target_language(self, interaction: discord.Interaction, current: str):
        query = current.strip().casefold()
        return [
            app_commands.Choice(name=f"{label} ({code})", value=code)
            for code, label in LANGUAGE_LABELS.items()
            if query in code.casefold() or query in label.casefold()
            or query in NATURAL_LANGUAGE_NAMES.get(code, "").casefold()
        ][:25]

    @app_commands.command(
        name="ceviri-kanali-kaldir",
        description="Kanalın otomatik çevirisini kapatır / Disable automatic translation for a channel",
    )
    @app_commands.guild_only()
    @app_commands.describe(kanal="Çevirisi kapatılacak kanal")
    async def remove_translation_channel(
        self,
        interaction: discord.Interaction,
        kanal: discord.TextChannel,
    ):
        if not self._is_admin(interaction):
            return await interaction.response.send_message(
                "❌ Bu ayarı yalnızca sunucu yöneticisi değiştirebilir. / Server Admin only.",
                ephemeral=True,
            )
        enabled, _ = _room_settings(kanal, translation_channels_storage.read())
        if not enabled:
            return await interaction.response.send_message(
                "ℹ️ Bu kanalda otomatik çeviri açık değil.", ephemeral=True
            )
        translation_channels_storage.set(str(kanal.id), "off")
        self._speaker_languages.pop(kanal.id, None)
        await interaction.response.send_message(
            f"✅ {kanal.mention} kanalında otomatik çeviri kapatıldı.",
            ephemeral=True,
        )

    @app_commands.command(
        name="ceviri-kanallari",
        description="Otomatik çeviri açık kanalları listeler / List translated channels",
    )
    @app_commands.guild_only()
    @app_commands.describe(sayfa="Gösterilecek sayfa numarası / Page number")
    async def list_translation_channels(
        self, interaction: discord.Interaction, sayfa: app_commands.Range[int, 1, 100] = 1
    ):
        if not self._is_admin(interaction):
            return await interaction.response.send_message(
                "❌ Bu ayarı yalnızca sunucu yöneticisi görüntüleyebilir. / Server Admin only.",
                ephemeral=True,
            )
        config = translation_channels_storage.read()
        rows = []
        for channel in interaction.guild.text_channels:
            enabled, defaults = _room_settings(channel, config)
            if enabled:
                languages = " / ".join(LANGUAGE_LABELS[language] for language in defaults)
                default_label = f" (varsayılan {languages})" if languages else ""
                rows.append(f"• {channel.mention} → Katılımcı dilleri{default_label}")
        if not rows:
            return await interaction.response.send_message(
                "ℹ️ Otomatik çeviri açık kanal yok.", ephemeral=True
            )
        page_size = 20
        page_count = (len(rows) + page_size - 1) // page_size
        if sayfa > page_count:
            return await interaction.response.send_message(
                f"❌ Bu sunucuda yalnızca {page_count} sayfa var.", ephemeral=True
            )
        start = (sayfa - 1) * page_size
        embed = discord.Embed(
            title="🌐 Otomatik Çeviri Kanalları · Translation Channels",
            description="\n".join(rows[start:start + page_size]),
            colour=0x2563EB,
        )
        embed.set_footer(text=f"Sayfa {sayfa}/{page_count}")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(TranslationCog(bot))
