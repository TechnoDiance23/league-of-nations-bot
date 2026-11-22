import logging
import re
import time
from urllib.parse import urlparse

import discord
from discord.ext import commands

from utils.constants import slug_match as _slug_match

logger = logging.getLogger("InternationalDiplomaticBot.ChatGuard")

MEDIA_ALLOWED_SLUGS = ("medya-media", "mizah-memes")
LOUNGE_VOICE_SLUGS = ("delege dinlenme · lounge", "delege dinlenme")

MEDIA_FILE_EXTS = (
    ".gif",
    ".gifv",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".bmp",
    ".tiff",
    ".heic",
    ".avif",
    ".svg",
    ".mp4",
    ".mov",
    ".webm",
)

GIF_PROVIDER_HOSTS = (
    "tenor.com",
    "giphy.com",
    "klipy.com",
    "gfycat.com",
    "redgifs.com",
)

DISCORD_CDN_HOSTS = (
    "cdn.discordapp.com",
    "media.discordapp.net",
    "images-ext-1.discordapp.net",
    "images-ext-2.discordapp.net",
)

_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


def is_guarded_chat_channel(channel) -> bool:
    """Return True if GIFs/photos are prohibited in this channel or its parent thread."""
    if channel is None:
        return False
    target = getattr(channel, "parent", None) if isinstance(channel, discord.Thread) else channel
    if target is None:
        return False

    name = getattr(target, "name", "") or ""
    if _slug_match(name, list(MEDIA_ALLOWED_SLUGS)):
        return False

    cat = getattr(target, "category", None)
    cat_name = (getattr(cat, "name", "") or "").lower()

    if isinstance(target, (discord.VoiceChannel, discord.StageChannel)):
        return (
            "ses kanalları" in cat_name
            or "voice channels" in cat_name
            or _slug_match(name, list(LOUNGE_VOICE_SLUGS))
        )

    if isinstance(target, discord.TextChannel):
        return "sohbet" in cat_name or "chat" in cat_name

    # Duck-typed / mock channel fallback for unit checks
    ch_type = getattr(target, "type", None)
    if ch_type in (discord.ChannelType.voice, discord.ChannelType.stage_voice):
        return (
            "ses kanalları" in cat_name
            or "voice channels" in cat_name
            or _slug_match(name, list(LOUNGE_VOICE_SLUGS))
        )
    return "sohbet" in cat_name or "chat" in cat_name


def _is_media_url(url: str) -> bool:
    if not url:
        return False
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower().strip(".")
    path = (parsed.path or "").lower()
    if any(host == dom or host.endswith(f".{dom}") for dom in GIF_PROVIDER_HOSTS):
        return True
    if host in DISCORD_CDN_HOSTS and ("/attachments/" in path or "/external/" in path or "/ephemeral-attachments/" in path):
        return True
    if host in ("imgur.com", "i.imgur.com") and (path.endswith(MEDIA_FILE_EXTS) or path.startswith("/a/") or path.startswith("/gallery/")):
        return True
    return path.endswith(MEDIA_FILE_EXTS)


def _container_has_blocked_media(obj) -> bool:
    if obj is None:
        return False

    for att in getattr(obj, "attachments", None) or ():
        ctype = (getattr(att, "content_type", None) or "").lower()
        fname = (getattr(att, "filename", None) or "").lower()
        if ctype.startswith(("image/", "video/")) or fname.endswith(MEDIA_FILE_EXTS):
            return True

    for emb in getattr(obj, "embeds", None) or ():
        etype = (getattr(emb, "type", None) or "").lower()
        if etype in ("gifv", "image"):
            return True
        for candidate in (
            getattr(emb, "url", None),
            getattr(getattr(emb, "image", None), "url", None),
            getattr(getattr(emb, "video", None), "url", None),
        ):
            if candidate and _is_media_url(candidate):
                return True

    content = getattr(obj, "content", None) or ""
    for url in _URL_RE.findall(content):
        if _is_media_url(url):
            return True

    return False


def message_has_blocked_media(message) -> bool:
    """Detect uploaded images/GIFs, embeds, forwarded snapshots, and Tenor/Giphy links."""
    if _container_has_blocked_media(message):
        return True
    for snap in getattr(message, "message_snapshots", None) or ():
        if _container_has_blocked_media(snap) or _container_has_blocked_media(getattr(snap, "cached_message", None)):
            return True
    return False


class ChatMediaGuardCog(commands.Cog):
    """Blocks GIFs, image uploads, and Tenor/Giphy links in guarded chat & voice text rooms."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._warned_at: dict[tuple[int, int], float] = {}

    async def _enforce(self, message: discord.Message):
        if not message.guild or message.author.bot or message.webhook_id:
            return
        if message.type not in (discord.MessageType.default, discord.MessageType.reply):
            return
        if not is_guarded_chat_channel(message.channel):
            return
        if not message_has_blocked_media(message):
            return

        try:
            await message.delete()
        except (discord.NotFound, discord.Forbidden):
            return
        except discord.HTTPException as exc:
            logger.warning("Failed deleting guarded media in %s: %s", message.channel.id, exc)
            return

        now = time.monotonic()
        key = (message.channel.id, message.author.id)
        if now - self._warned_at.get(key, 0.0) < 6.0:
            return
        self._warned_at[key] = now

        media_ch = discord.utils.find(
            lambda c: _slug_match(c.name, ["medya-media"]),
            message.guild.text_channels,
        )
        hint = media_ch.mention if media_ch else "`#📸・medya-media`"
        try:
            await message.channel.send(
                f"🚫 {message.author.mention} Sohbet kanallarında GIF ve fotoğraf paylaşımı kapalıdır; medya için {hint} kanalını kullanın.\n"
                f"*GIFs and images are disabled in chat rooms; please use {hint}.*",
                delete_after=6.0,
            )
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        await self._enforce(message)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        await self._enforce(after)


async def setup(bot: commands.Bot):
    await bot.add_cog(ChatMediaGuardCog(bot))
