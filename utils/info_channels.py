import discord

from utils.constants import CHANNEL_ALIASES, slug_match as _slug_match

SILENT_EVERYONE_SPACER = "_ _\n@everyone\n_ _"
_ALLOW_EVERYONE = discord.AllowedMentions(everyone=True)
DISCORD_TEXT_LIMIT = 1950


def _split_balanced_paragraphs(header: str, paragraphs: list[str], limit: int = DISCORD_TEXT_LIMIT) -> list[str]:
    """Pack paragraphs under Discord's 2000-char content limit; split balanced halves if >1 chunk needed."""
    full = f"{header}\n\n" + "\n\n".join(paragraphs) if header else "\n\n".join(paragraphs)
    if len(full) <= limit:
        return [full]

    mid = max(1, len(paragraphs) // 2)
    first_chunk = (f"{header}\n\n" if header else "") + "\n\n".join(paragraphs[:mid])
    second_chunk = "\n\n".join(paragraphs[mid:])
    if len(first_chunk) <= limit and len(second_chunk) <= limit:
        return [first_chunk, second_chunk]

    chunks: list[str] = []
    cur = header
    for p in paragraphs:
        cand = f"{cur}\n\n{p}" if cur else p
        if len(cand) <= limit:
            cur = cand
        else:
            if cur:
                chunks.append(cur)
            cur = p
    if cur:
        chunks.append(cur)
    return chunks


async def _purge_channel(channel: discord.TextChannel, limit: int = 100):
    """Delete previous messages in an info/rules/announcements channel cleanly."""
    try:
        await channel.purge(limit=limit, bulk=True)
        return
    except Exception:
        pass
    async for msg in channel.history(limit=limit):
        try:
            await msg.delete()
        except Exception:
            pass


async def _send_separated_texts(channel: discord.TextChannel, messages: list[str]) -> int:
    """Send each plain-text message individually with silent @everyone spacers between them."""
    sent = 0
    for idx, content in enumerate(messages):
        if idx > 0:
            await channel.send(
                SILENT_EVERYONE_SPACER,
                silent=True,
                allowed_mentions=_ALLOW_EVERYONE,
            )
            sent += 1
        await channel.send(content=content, allowed_mentions=discord.AllowedMentions.none())
        sent += 1
    return sent


def _ch_ref(guild: discord.Guild | None, aliases: list[str], fallback: str, voice: bool = False) -> str:
    if guild is None:
        return fallback
    pool = guild.voice_channels if voice else guild.text_channels
    ch = discord.utils.find(lambda c: _slug_match(c.name, aliases), pool)
    return ch.mention if ch else fallback


def build_languages_messages(guild: discord.Guild | None = None) -> list[str]:
    """Build plain-text header + 6 individual language messages (IT, RU, EN, ES, DE, TR)."""
    acc_ref = _ch_ref(guild, CHANNEL_ALIASES["akreditasyon-accreditation"], "#🚪│kayit-masasi-register")
    nat_ref = _ch_ref(guild, ["uluslar-nations", "milletler-nations"], "#🌍│uluslar-nations")
    it_t, it_v = _ch_ref(guild, ["it-chat"], "#💬・it-chat"), _ch_ref(guild, ["it-chat", "it chat"], "🔉・IT Chat", voice=True)
    ru_t, ru_v = _ch_ref(guild, ["ru-chat"], "#💬・ru-chat"), _ch_ref(guild, ["ru-chat", "ru chat"], "🔉・RU Chat", voice=True)
    en_t, en_v = _ch_ref(guild, ["en-chat"], "#💬・en-chat"), _ch_ref(guild, ["en-chat", "en chat"], "🔉・EN Chat", voice=True)
    es_t, es_v = _ch_ref(guild, ["es-chat"], "#💬・es-chat"), _ch_ref(guild, ["es-chat", "es chat"], "🔉・ES Chat", voice=True)
    de_t, de_v = _ch_ref(guild, ["de-chat"], "#💬・de-chat"), _ch_ref(guild, ["de-chat", "de chat"], "🔉・DE Chat", voice=True)
    tr_t, tr_v = _ch_ref(guild, ["tr-sohbet"], "#💬・tr-sohbet"), _ch_ref(guild, ["tr-sohbet", "tr sohbet"], "🔉・TR Sohbet", voice=True)
    gl_t = _ch_ref(guild, ["global-chat"], "#💬・global-chat")

    return [
        (
            "🌐 **RESMÎ SUNUCU DİLLERİ | OFFICIAL SERVER LANGUAGES**\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"Sunucuda aktif sohbet ve ses odaları aşağıdaki 6 dilde hizmet vermektedir.\n"
            f"Dil ve milliyet rolü almak için: {acc_ref} veya {nat_ref} *(Global İngilizce: {gl_t})*"
        ),
        f"🇮🇹 • **İtalyanca · Italiano**\nMetin: {it_t} | Ses: {it_v} | Rol: `🇮🇹 Italiano`",
        f"🇷🇺 • **Rusça · Русский**\nMetin: {ru_t} | Ses: {ru_v} | Roller: `🇷🇺 Русский`, `🇺🇦 Український`",
        f"🇬🇧 • **İngilizce · English**\nMetin: {en_t} · {gl_t} | Ses: {en_v} | Roller: `🇬🇧 British · English`, `🇺🇸 American`",
        f"🇪🇸 • **İspanyolca · Español**\nMetin: {es_t} | Ses: {es_v} | Rol: `🇪🇸 Español`",
        f"🇩🇪 • **Almanca · Deutsch**\nMetin: {de_t} | Ses: {de_v} | Rol: `🇩🇪 Deutsch`",
        f"🇹🇷 • **Türkçe · Turkish**\nMetin: {tr_t} | Ses: {tr_v} | Roller: `🇹🇷 Türk` *(ve `🇦🇿`, `🇰🇿`, `🇺🇿`, `🇰🇬`, `🇹🇲`)*",
    ]


def build_rules_messages() -> list[str]:
    """Build plain-text rules messages across TR, EN, ES, IT, RU, DE under 2000 chars each."""
    tr_paragraphs = [
        "**1. Saygı ve Nezaket**\nTüm üyelere karşı saygılı olunmalıdır. **Irkçılık, nefret söylemi, ayrımcılık ve ağır hakaret** kesinlikle yasaktır. Siyasi görüşlere ve oyun içi kararlara yönelik eleştiri serbesttir; ancak kişisel saldırıya dönüşmemelidir.",
        "**2. +18 ve Uygunsuz İçerik (NSFW)**\nSunucunun tüm kanallarında, profil fotoğraflarında, kullanıcı adlarında ve paylaşılan bağlantılarda **+18, cinsel içerik, aşırı şiddet ve rahatsız edici paylaşımlar** yasaktır.",
        "**3. Oyun İçi ve Oyun Dışı Ayrımı (IC / OOC)**\nOyun içerisinde yaşanan savaşlar, siyasi anlaşmazlıklar ve diplomatik krizler oyun dışında kişisel husumete dönüştürülmemelidir. **Gerçek hayat siyaseti üzerinden kışkırtma ve kişisel saldırı** yasaktır.",
        "**4. Spam ve Reklam**\nSohbet kanallarında gereksiz mesaj tekrarı, spam, toplu etiketleme (ping), izinsiz özel mesaj reklamcılığı ve yetkili izni olmadan başka sunucu veya platformların reklamını yapmak yasaktır.",
        "**5. Dil Kanalları**\nHer kanalda yalnızca belirlenen dil kullanılmalıdır. Örneğin, `tr-sohbet` kanalında Türkçe, `global-chat` kanalında İngilizce kullanılmalıdır. Yetkililer gerekli gördüklerinde istisna yapabilir.",
        "**6. Hile ve Metagaming**\nOyun mekaniklerini suistimal etmek, açıkları kötüye kullanmak veya oyun dışında edinilen gizli bilgileri oyun içerisinde avantaj sağlamak amacıyla kullanmak (metagaming) yasaktır.",
        "**7. Yetkili Talimatları ve Moderasyon**\nSunucu düzenini korumak amacıyla yöneticiler ve moderatörler tarafından verilen makul talimatlara uyulmalıdır. Yetkili kararlarına yönelik itirazlar ilgili kanallardan veya yönetim ekibine özel olarak iletilmelidir.",
        "**8. Tehdit ve Kişisel Taciz**\nGerçek hayat tehditleri, hedef gösterme, doxxing (kişisel bilgileri izinsiz paylaşma), üyeleri sürekli rahatsız etme ve kişisel bilgileri kötüye kullanma kesinlikle yasaktır.",
        "**9. Sahte Hesap ve Kimlik Taklidi**\nBaşka üyeleri, devlet yetkililerini veya sunucu yönetimini taklit ederek insanları yanıltmak ve sahte kimliklerle topluluğu manipüle etmek yasaktır.",
        "**10. Diplomatik Gizlilik**\nÖzel diplomatik görüşmelerin, kapalı toplantıların ve tarafların izni olmadan paylaşılan özel yazışmaların izinsiz yayımlanması yasaktır.",
        "**11. Ceza ve İtiraz Sistemi**\nKural ihlallerinde ihlalin ağırlığına ve tekrarına göre sözlü veya yazılı uyarı, susturma, geçici uzaklaştırma ya da kalıcı yasak uygulanabilir. Üyeler verilen cezalara karşı yönetim ekibine itiraz etme hakkına sahiptir.",
    ]

    en_paragraphs = [
        "**1. Respect & Civility**\nAll members must treat one another with respect. **Racism, hate speech, discrimination, and severe insults** are strictly prohibited. Criticism of political views and in-game decisions is allowed, as long as it does not become a personal attack.",
        "**2. No +18 / NSFW Content**\n+18 content, sexually explicit material, excessive violence, gore, and disturbing content are strictly forbidden across all channels, profile pictures, usernames, and shared links.",
        "**3. IC vs. OOC Separation**\nIn-game wars, political disputes, and diplomatic conflicts must not turn into personal hostility outside the game. **Real-life political provocation and personal attacks** are prohibited.",
        "**4. No Spam or Advertising**\nUnnecessary message repetition, spamming, mass-pinging, unsolicited advertising through private messages, and promoting other servers or platforms without staff permission are prohibited.",
        "**5. Language Channels**\nOnly the designated language should be used in each channel. For example, Turkish in `tr-sohbet` and English in `global-chat`. Staff may make exceptions when necessary.",
        "**6. Cheating & Metagaming**\nAbusing game mechanics, exploiting bugs, or using secret information obtained outside the game to gain an in-game advantage (metagaming) is strictly prohibited.",
        "**7. Staff Instructions & Moderation**\nMembers must follow reasonable instructions given by administrators and moderators to maintain order. Appeals against staff decisions should be submitted through the appropriate channels or directly to the administration team.",
        "**8. Threats & Personal Harassment**\nReal-life threats, targeted harassment, doxxing (sharing someone's personal information without permission), repeatedly harassing members, and misusing personal information are strictly prohibited.",
        "**9. Impersonation & Fake Accounts**\nImpersonating other members, government officials, or server staff, as well as using fake identities to mislead or manipulate the community, is prohibited.",
        "**10. Diplomatic Confidentiality**\nPublishing private diplomatic discussions, closed meetings, or private correspondence without the consent of the involved parties is prohibited.",
        "**11. Penalties & Appeals**\nDepending on the severity and repetition of an offense, penalties may include verbal or written warnings, mutes, temporary suspensions, or permanent bans. Members have the right to appeal disciplinary actions to the administration team.",
    ]

    es_paragraphs = [
        "**1. Respeto y Civismo**\nTodos los miembros deben tratarse con respeto. **El racismo, los discursos de odio, la discriminación y los insultos graves** están estrictamente prohibidos. Se permite criticar opiniones políticas y decisiones dentro del juego, siempre que no se convierta en un ataque personal.",
        "**2. Prohibido el Contenido +18 / NSFW**\nEstá estrictamente prohibido compartir contenido +18, sexualmente explícito, violencia extrema, gore o material perturbador en cualquier canal, foto de perfil, nombre de usuario o enlace compartido.",
        "**3. Separación entre IC y OOC**\nLas guerras, disputas políticas y crisis diplomáticas dentro del juego no deben convertirse en conflictos personales fuera de él. **Las provocaciones políticas relacionadas con la vida real y los ataques personales** están prohibidos.",
        "**4. Spam y Publicidad**\nEstá prohibido repetir mensajes innecesariamente, hacer spam, mencionar masivamente a otros miembros, enviar publicidad no solicitada por mensajes privados o promocionar otros servidores y plataformas sin autorización del equipo de moderación.",
        "**5. Canales de Idiomas**\nEn cada canal debe utilizarse únicamente el idioma correspondiente. Por ejemplo, turco en `tr-sohbet` e inglés en `global-chat`. El equipo de moderación podrá hacer excepciones cuando lo considere necesario.",
        "**6. Trampas y Metagaming**\nEstá prohibido abusar de las mecánicas del juego, aprovecharse de errores o utilizar información secreta obtenida fuera del juego para conseguir ventajas dentro de él (metagaming).",
        "**7. Instrucciones del Equipo y Moderación**\nLos miembros deben respetar las instrucciones razonables de los administradores y moderadores para mantener el orden del servidor. Las apelaciones contra las decisiones del equipo deben presentarse en los canales correspondientes o directamente a la administración.",
        "**8. Amenazas y Acoso Personal**\nEstán estrictamente prohibidas las amenazas en la vida real, el acoso dirigido, el doxxing (compartir información personal sin consentimiento), el acoso repetido y el uso indebido de información personal.",
        "**9. Suplantación de Identidad y Cuentas Falsas**\nEstá prohibido hacerse pasar por otros miembros, funcionarios gubernamentales o miembros del equipo del servidor, así como utilizar identidades falsas para engañar o manipular a la comunidad.",
        "**10. Confidencialidad Diplomática**\nEstá prohibido publicar conversaciones diplomáticas privadas, reuniones cerradas o correspondencia privada sin el consentimiento de las partes involucradas.",
        "**11. Sanciones y Apelaciones**\nSegún la gravedad y la reincidencia de la infracción, las sanciones pueden incluir advertencias verbales o escritas, silenciamiento, suspensión temporal o expulsión permanente. Los miembros tienen derecho a apelar las sanciones ante el equipo de administración.",
    ]

    it_paragraphs = [
        "**1. Rispetto e Cortesia**\nTutti i membri devono trattarsi con rispetto. **Razzismo, incitamento all'odio, discriminazione e insulti gravi** sono severamente vietati. Le critiche alle opinioni politiche e alle decisioni di gioco sono consentite, purché non si trasformino in attacchi personali.",
        "**2. Divieto di Contenuti +18 / NSFW**\nÈ severamente vietata la condivisione di contenuti +18, sessualmente espliciti, violenza estrema, gore o materiale disturbante in tutti i canali, nelle immagini del profilo, nei nomi utente e nei link condivisi.",
        "**3. Separazione tra IC e OOC**\nLe guerre, le controversie politiche e le crisi diplomatiche all'interno del gioco non devono trasformarsi in ostilità personali al di fuori di esso. **Le provocazioni politiche legate alla vita reale e gli attacchi personali** sono vietati.",
        "**4. Spam e Pubblicità**\nÈ vietato ripetere inutilmente i messaggi, fare spam, menzionare in massa gli altri membri, inviare pubblicità non richiesta tramite messaggi privati o promuovere altri server e piattaforme senza l'autorizzazione dello staff.",
        "**5. Canali Linguistici**\nIn ogni canale deve essere utilizzata esclusivamente la lingua designata. Ad esempio, il turco in `tr-sohbet` e l'inglese in `global-chat`. Lo staff può concedere eccezioni quando lo ritiene necessario.",
        "**6. Barare e Metagaming**\nÈ vietato abusare delle meccaniche di gioco, sfruttare bug o utilizzare informazioni segrete ottenute al di fuori del gioco per ottenere vantaggi al suo interno (metagaming).",
        "**7. Istruzioni dello Staff e Moderazione**\nI membri devono rispettare le ragionevoli istruzioni fornite dagli amministratori e dai moderatori per mantenere l'ordine del server. I ricorsi contro le decisioni dello staff devono essere presentati nei canali appropriati o direttamente all'amministrazione.",
        "**8. Minacce e Molestie Personali**\nSono severamente vietate le minacce nella vita reale, le molestie mirate, il doxxing (condivisione di informazioni personali senza autorizzazione), le molestie ripetute e l'uso improprio di informazioni personali.",
        "**9. Furto d'Identità e Account Falsi**\nÈ vietato impersonare altri membri, funzionari governativi o membri dello staff del server, così come utilizzare identità false per ingannare o manipolare la comunità.",
        "**10. Riservatezza Diplomatica**\nÈ vietato pubblicare conversazioni diplomatiche private, riunioni riservate o corrispondenza privata senza il consenso delle parti coinvolte.",
        "**11. Sanzioni e Ricorsi**\nA seconda della gravità e della reiterazione della violazione, le sanzioni possono includere avvertimenti verbali o scritti, silenziamento, sospensione temporanea o ban permanente. I membri hanno il diritto di presentare ricorso contro le sanzioni al team di amministrazione.",
    ]

    ru_paragraphs = [
        "**1. Уважение и вежливость**\nВсе участники обязаны уважительно относиться друг к другу. **Расизм, разжигание ненависти, дискриминация и грубые оскорбления** строго запрещены. Критика политических взглядов и игровых решений допускается, если она не переходит в личные нападки.",
        "**2. Запрет контента 18+ / NSFW**\nМатериалы 18+, откровенный сексуальный контент, чрезмерная жестокость и шокирующий контент строго запрещены во всех каналах, на аватарах, в никнеймах и ссылках.",
        "**3. Разделение IC и OOC**\nИгровые войны, политические споры и дипломатические кризисы не должны переноситься в личную неприязнь вне игры. **Провокации на почве реальной политики и личные оскорбления** запрещены.",
        "**4. Спам и реклама**\nЗапрещены флуд, спам, массовые упоминания (пинг), несанкционированная реклама в личных сообщениях и продвижение сторонних серверов или платформ без разрешения администрации.",
        "**5. Языковые каналы**\nВ каждом канале должен использоваться только установленный язык. Например, турецкий в `tr-sohbet`, русский в `ru-chat`, английский в `global-chat`. Администрация может делать исключения при необходимости.",
        "**6. Читы и метагейминг**\nЗлоупотребление игровыми механиками, использование багов или применение секретной информации, полученной вне игры, для получения игрового преимущества (метагейминг) строго запрещены.",
        "**7. Требования администрации и модерация**\nУчастники обязаны соблюдать обоснованные указания администраторов и модераторов для поддержания порядка. Апелляции на решения модерации подаются через тикеты или напрямую администрации.",
        "**8. Угрозы и травля**\nУгрозы в реальной жизни, целенаправленная травля, доксинг (распространение личных данных без согласия) и злоупотребление чужой личной информацией категорически запрещены.",
        "**9. Выдача себя за других и фейковые аккаунты**\nЗапрещено выдавать себя за других участников, государственных деятелей или членов команды сервера, а также использовать поддельные личности для введения сообщества в заблуждение.",
        "**10. Дипломатическая конфиденциальность**\nЗапрещена публикация закрытых дипломатических переговоров, секретных встреч и личной переписки без согласия участвующих сторон.",
        "**11. Наказания и апелляции**\nВ зависимости от тяжести и повторности нарушения применяются устное или письменное предупреждение, мут, временная блокировка или перманентный бан. Участники имеют право обжаловать наказание у администрации.",
    ]

    de_paragraphs = [
        "**1. Respekt und Höflichkeit**\nAlle Mitglieder müssen respektvoll miteinander umgehen. **Rassismus, Hassrede, Diskriminierung und schwere Beleidigungen** sind strengstens verboten. Kritik an politischen Ansichten und Entscheidungen im Spiel ist erlaubt, solange sie nicht in persönliche Angriffe ausartet.",
        "**2. Keine +18 / NSFW-Inhalte**\n+18-Inhalte, sexuell explizites Material, übermäßige Gewalt und verstörende Inhalte sind in allen Kanälen, Profilbildern, Benutzernamen und geteilten Links strengstens untersagt.",
        "**3. Trennung von IC und OOC**\nKriege, politische Streitigkeiten und diplomatische Krisen im Spiel dürfen außerhalb des Spiels nicht zu persönlicher Feindseligkeit führen. **Provokationen mit Bezug auf reale Politik und persönliche Angriffe** sind verboten.",
        "**4. Kein Spam und keine Werbung**\nUnnötige Nachrichtenwiederholungen, Spam, Massen-Pings, unerwünschte Werbung per Direktnachricht sowie Werbung für andere Server oder Plattformen ohne Erlaubnis des Teams sind verboten.",
        "**5. Sprachkanäle**\nIn jedem Kanal darf nur die vorgesehene Sprache verwendet werden — zum Beispiel Türkisch in `tr-sohbet`, Deutsch in `de-chat` und Englisch in `global-chat`. Das Team kann bei Bedarf Ausnahmen zulassen.",
        "**6. Cheating und Metagaming**\nDas Ausnutzen von Spielmechaniken oder Fehlern (Bugs) sowie die Nutzung geheimer Informationen außerhalb des Spiels für spielerische Vorteile (Metagaming) sind strengstens verboten.",
        "**7. Anweisungen des Teams und Moderation**\nDen angemessenen Anweisungen von Administratoren und Moderatoren zur Wahrung der Ordnung ist Folge zu leisten. Einsprüche gegen Entscheidungen des Teams sind über die vorgesehenen Kanäle oder direkt an die Leitung zu richten.",
        "**8. Drohungen und persönliche Belästigung**\nDrohungen im echten Leben, gezielte Belästigung, Doxxing (Veröffentlichung persönlicher Daten ohne Einwilligung), wiederholtes Stören und Missbrauch persönlicher Daten sind strengstens verboten.",
        "**9. Identitätsmissbrauch und Fake-Accounts**\nEs ist verboten, sich als andere Mitglieder, Staatsvertreter oder Teammitglieder auszugeben sowie gefälschte Identitäten zur Täuschung oder Manipulation der Community zu verwenden.",
        "**10. Diplomatische Vertraulichkeit**\nDas Veröffentlichen privater diplomatischer Gespräche, geschlossener Sitzungen oder vertraulicher Korrespondenz ohne Zustimmung der beteiligten Parteien ist untersagt.",
        "**11. Strafen und Einsprüche**\nJe nach Schwere und Wiederholung eines Verstoßes können mündliche oder schriftliche Verwarnungen, Stummschaltungen, vorübergehende Sperren oder dauerhafte Banns verhängt werden. Mitglieder haben das Recht, gegen Sanktionen bei der Serverleitung Einspruch einzulegen.",
    ]

    messages = [
        (
            "📜 **MİLLETLER CEMİYETİ · SUNUCU KURALLARI | SERVER RULES**\n"
            "Sunucumuzda herkes için saygılı, güvenli ve düzenli bir ortam oluşturmayı amaçlıyoruz. Lütfen aşağıdaki kuralları dikkatlice okuyunuz.\n"
            "🇹🇷 Türkçe · 🇬🇧 English · 🇪🇸 Español · 🇮🇹 Italiano · 🇷🇺 Русский · 🇩🇪 Deutsch"
        )
    ]

    lang_sections = [
        ("━━━━━━━━━━━━━━━━━━━━\n🇹🇷 **TÜRKÇE KURALLAR**\n━━━━━━━━━━━━━━━━━━━━", tr_paragraphs),
        ("━━━━━━━━━━━━━━━━━━━━\n🇬🇧 **ENGLISH RULES**\n━━━━━━━━━━━━━━━━━━━━", en_paragraphs),
        ("━━━━━━━━━━━━━━━━━━━━\n🇪🇸 **REGLAS EN ESPAÑOL**\n━━━━━━━━━━━━━━━━━━━━", es_paragraphs),
        ("━━━━━━━━━━━━━━━━━━━━\n🇮🇹 **REGOLE IN ITALIANO**\n━━━━━━━━━━━━━━━━━━━━", it_paragraphs),
        ("━━━━━━━━━━━━━━━━━━━━\n🇷🇺 **ПРАВИЛА НА РУССКОМ**\n━━━━━━━━━━━━━━━━━━━━", ru_paragraphs),
        ("━━━━━━━━━━━━━━━━━━━━\n🇩🇪 **REGELN AUF DEUTSCH**\n━━━━━━━━━━━━━━━━━━━━", de_paragraphs),
    ]
    for hdr, paras in lang_sections:
        messages.extend(_split_balanced_paragraphs(hdr, paras))

    messages.append(
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "⚖️ **Sunucuya katılan herkes bu kuralları okumuş ve kabul etmiş sayılır.**\n"
        "⚖️ **By joining this server, you acknowledge that you have read and agreed to follow these rules.**\n"
        "⚖️ **Al unirte a este servidor, se considera que has leído y aceptado estas reglas.**\n"
        "⚖️ **Unendoti a questo server, dichiari di aver letto e accettato queste regole.**\n"
        "⚖️ **Присоединяясь к серверу, вы подтверждаете, что прочитали и принимаете данные правила.**\n"
        "⚖️ **Mit dem Beitritt zu diesem Server bestätigst du, dass du diese Regeln gelesen und akzeptiert hast.**"
    )
    return messages


def build_welcome_announcement_messages(guild: discord.Guild | None = None) -> list[str]:
    """Build initial separate plain-text bilingual announcements for #duyurular-announcements."""
    acc_ref = _ch_ref(guild, CHANNEL_ALIASES["akreditasyon-accreditation"], "#🚪│kayit-masasi-register")
    charter_ref = _ch_ref(guild, ["tuzuk-charter"], "#📜│tuzuk-charter")
    sig_ref = _ch_ref(guild, ["imzaci-devletler-signatories"], "#✍️│imzaci-devletler-signatories")
    lang_ref = _ch_ref(guild, ["diller-languages"], "#🌐・diller-languages")
    rules_ref = _ch_ref(guild, ["kurallar-rules"], "#📌・kurallar-rules")
    votes_ref = _ch_ref(guild, ["onergeler-motions-votes"], "#💡│onergeler-motions-votes")
    treaty_ref = _ch_ref(guild, ["antlasmalar-treaties"], "#📜│antlasmalar-treaties")
    icj_ref = _ch_ref(guild, ["yargi-divani-icj-court"], "#⚖️│yargi-divani-icj-court")
    ticket_ref = _ch_ref(guild, ["destek-masasi-tickets"], "#🎫│destek-masasi-tickets")
    media_ref = _ch_ref(guild, ["medya-media"], "#📸・medya-media")

    return [
        (
            "🏛️ **RESMÎ DUYURU · MİLLETLER CEMİYETİ / LEAGUE OF NATIONS ONLINE**\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "**🇹🇷 Türkçe:**\n"
            f"Milletler Cemiyeti resmi diplomasi sunucusu tüm organlarıyla faaliyete geçmiştir. "
            f"Kuruluş Şartı (**MC-KS-001**) ile egemen eşitlik (`1 Devlet = 1 Eşit Oy`) ilkesi geçerlidir.\n"
            f"• Tüzük & Şart İmza Odası / Charter & Signing: {charter_ref}\n"
            f"• Giriş Kayıt & Rol Kapısı / Registration Gate: {acc_ref}\n"
            f"• İmzacı Devletler Sicili: {sig_ref}\n\n"
            "**🇬🇧 English:**\n"
            f"The official League of Nations diplomatic server is now active under Charter **MC-KS-001** (`1 State = 1 Equal Vote`). "
            f"Complete registration in {acc_ref}, then read & sign the Charter in {charter_ref}."
        ),
        (
            "⚖️ **DİPLOMATİK ORGANLAR & DESTEK · ORGANS & SUPPORT**\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "**🇹🇷 Türkçe:**\n"
            f"• Önerge & Oylamalar: {votes_ref}\n"
            f"• Antlaşmalar & İmza Masası: {treaty_ref}\n"
            f"• Uluslararası Yargı Divanı (5 Yargıç): {icj_ref}\n"
            f"• Özel Başvuru & Yetkili Destek Masası: {ticket_ref}\n\n"
            "**🇬🇧 English:**\n"
            f"Submit motions in {votes_ref}, negotiate treaties in {treaty_ref}, file ICJ cases in {icj_ref}, and contact Secretariat support in {ticket_ref}."
        ),
        (
            "🌐 **DİLLER, KURALLAR & SOHBET DÜZENİ · LANGUAGES, RULES & CHAT POLICY**\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "**🇹🇷 Türkçe:**\n"
            f"• Desteklenen 6 dil (**İtalyanca, Rusça, İngilizce, İspanyolca, Almanca, Türkçe**) için {lang_ref}, kurallar için {rules_ref} kanalını inceleyin.\n"
            f"• Yazılı sohbet ve ses sohbeti odalarında GIF ve fotoğraf gönderimi kapalıdır; medya paylaşımları için {media_ref} kanalını kullanın.\n\n"
            "**🇬🇧 English:**\n"
            f"Check {lang_ref} for supported languages and {rules_ref} for server rules. Please share images and GIFs in {media_ref} only."
        ),
    ]


async def _already_synced(channel: discord.TextChannel, guild: discord.Guild, expected_texts: int) -> bool:
    expected_msgs = max(0, 2 * expected_texts - 1)
    msgs = [m async for m in channel.history(limit=expected_msgs + 5)]
    if len(msgs) != expected_msgs or not guild.me:
        return False
    return all(m.author.id == guild.me.id and not m.embeds for m in msgs)


async def rebuild_languages_channel(
    guild: discord.Guild,
    channel: discord.TextChannel | None = None,
    if_needed: bool = False,
) -> tuple[bool, int]:
    ch = channel or discord.utils.find(lambda c: _slug_match(c.name, ["diller-languages"]), guild.text_channels)
    if not ch:
        return False, 0
    texts = build_languages_messages(guild)
    if if_needed and await _already_synced(ch, guild, len(texts)):
        return False, 0
    await _purge_channel(ch)
    count = await _send_separated_texts(ch, texts)
    return True, count


async def rebuild_rules_channel(
    guild: discord.Guild,
    channel: discord.TextChannel | None = None,
    if_needed: bool = False,
) -> tuple[bool, int]:
    ch = channel or discord.utils.find(lambda c: _slug_match(c.name, ["kurallar-rules"]), guild.text_channels)
    if not ch:
        return False, 0
    texts = build_rules_messages()
    if if_needed and await _already_synced(ch, guild, len(texts)):
        return False, 0
    await _purge_channel(ch)
    count = await _send_separated_texts(ch, texts)
    return True, count


async def rebuild_announcements_channel(
    guild: discord.Guild,
    channel: discord.TextChannel | None = None,
    force: bool = False,
) -> tuple[bool, int]:
    ch = channel or discord.utils.find(
        lambda c: _slug_match(c.name, ["duyurular-announcements", "cemiyet-duyuru"]),
        guild.text_channels,
    )
    if not ch:
        return False, 0
    if not force:
        existing = [m async for m in ch.history(limit=3)]
        if existing:
            return False, 0
    await _purge_channel(ch)
    count = await _send_separated_texts(ch, build_welcome_announcement_messages(guild))
    return True, count


async def sync_info_and_rules_channels(
    guild: discord.Guild,
    force_announcements: bool = False,
    if_needed: bool = False,
) -> list[str]:
    """Rebuild languages & rules channels (and seed announcements if empty or forced)."""
    statuses: list[str] = []
    ok_lang, c_lang = await rebuild_languages_channel(guild, if_needed=if_needed)
    if ok_lang:
        statuses.append(f"Diller ({c_lang} msg)")
    ok_rules, c_rules = await rebuild_rules_channel(guild, if_needed=if_needed)
    if ok_rules:
        statuses.append(f"Kurallar ({c_rules} msg)")
    ok_ann, c_ann = await rebuild_announcements_channel(guild, force=force_announcements)
    if ok_ann:
        statuses.append(f"Duyurular ({c_ann} msg)")
    return statuses
