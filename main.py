import os
import re
import asyncio
from pathlib import Path

import discord
from dotenv import load_dotenv

# Screenshot에서 확인된 기존 Discord 서버 / welcome 채널
GUILD_ID = 1528990080914424009
WELCOME_CHANNEL_ID = 1529041045407666336

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN", "").strip()

if not TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN이 없습니다. 같은 폴더의 .env 파일에 "
        "DISCORD_TOKEN=기존_Lang_bot_토큰 형식으로 넣어주세요."
    )

intents = discord.Intents.default()
intents.message_content = False

client = discord.Client(intents=intents)

old_message_id = None
old_button_map = {}  # custom_id -> {"label":..., "emoji":..., "lang":..., "role_id":...}

LANG_ALIASES = {
    "en": ["english", "英語", "영어", "en", "eng"],
    "ja": ["japanese", "日本語", "日本", "일본어", "ja", "jp"],
    "ko": ["korean", "한국어", "韓国語", "ko", "kr"],
    "zh": ["chinese", "中文", "中国語", "중국어", "zh", "cn", "tw"],
    "es": ["spanish", "español", "espanol", "スペイン語", "스페인어", "es"],
    "fr": ["french", "français", "francais", "フランス語", "프랑스어", "fr"],
    "de": ["german", "deutsch", "ドイツ語", "독일어", "de"],
    "pt": ["portuguese", "português", "portugues", "ポルトガル語", "포르투갈어", "pt"],
    "ru": ["russian", "русский", "ロシア語", "러시아어", "ru"],
}

FLAG_LANG = {
    "🇬🇧": "en", "🇺🇸": "en",
    "🇯🇵": "ja",
    "🇰🇷": "ko",
    "🇨🇳": "zh", "🇹🇼": "zh",
    "🇪🇸": "es",
    "🇫🇷": "fr",
    "🇩🇪": "de",
    "🇵🇹": "pt", "🇧🇷": "pt",
    "🇷🇺": "ru",
}

def n(s):
    return re.sub(r"[^a-z0-9\u3040-\u30ff\u3400-\u9fff\uac00-\ud7a3]+", "", (s or "").lower())

def infer_lang(label, emoji=""):
    if emoji in FLAG_LANG:
        return FLAG_LANG[emoji]
    text = n((label or "") + " " + (emoji or ""))
    for code, aliases in LANG_ALIASES.items():
        for a in aliases:
            aa = n(a)
            if aa and aa in text:
                return code
    return None

def channel_lang(name):
    text = n(name)
    for code, aliases in LANG_ALIASES.items():
        for a in aliases:
            aa = n(a)
            if aa and aa in text:
                return code
    return None

def role_name_score(role, lang):
    text = n(role.name)
    best = 0
    for alias in LANG_ALIASES.get(lang, []):
        a = n(alias)
        if not a:
            continue
        if text == a:
            best = max(best, 100)
        elif a in text:
            best = max(best, 70)
    if lang in text:
        best = max(best, 40)
    return best

def general_language_channels(guild):
    chans = []
    for ch in guild.text_channels:
        cat = (ch.category.name if ch.category else "")
        # 스크린샷의 [General / 一般] 카테고리 우선
        if "general" in cat.lower() or "一般" in cat:
            chans.append(ch)
    return chans

def discover_role_for_lang(guild, lang):
    candidates = []

    # 1) General 카테고리의 해당 언어 채널을 실제로 열어주는 role 탐색
    for ch in general_language_channels(guild):
        if channel_lang(ch.name) != lang:
            continue
        for role, overwrite in ch.overwrites.items():
            if not isinstance(role, discord.Role):
                continue
            if role.is_default() or role.managed:
                continue
            if overwrite.view_channel is True:
                score = 300 + role_name_score(role, lang)
                candidates.append((score, role, f"#{ch.name} view_channel overwrite"))

    # 2) role 이름으로 보조 탐색
    for role in guild.roles:
        if role.is_default() or role.managed:
            continue
        score = role_name_score(role, lang)
        if score:
            candidates.append((score, role, "role name"))

    if not candidates:
        return None, "not found"

    candidates.sort(key=lambda x: (x[0], x[1].position), reverse=True)
    return candidates[0][1], candidates[0][2]

def read_buttons(message):
    found = []
    for row in message.components:
        for item in getattr(row, "children", []):
            if getattr(item, "type", None) != discord.ComponentType.button:
                continue
            cid = getattr(item, "custom_id", None)
            if not cid:
                continue
            label = getattr(item, "label", None) or ""
            emoji_obj = getattr(item, "emoji", None)
            emoji = str(emoji_obj) if emoji_obj else ""
            found.append((cid, label, emoji))
    return found

async def find_original_message(channel):
    # 기존 Lang_bot이 직접 쓴 component 메시지를 찾음.
    async for msg in channel.history(limit=300):
        if msg.author.id != client.user.id:
            continue
        buttons = read_buttons(msg)
        if buttons:
            # 스크린샷에서 보인 English 버튼이 있으면 이 메시지가 확실함.
            if any("english" in (label or "").lower() or emoji in ("🇬🇧", "🇺🇸")
                   for _, label, emoji in buttons):
                return msg
    return None

def discover_all_language_roles(guild):
    result = {}
    for code in LANG_ALIASES:
        role, reason = discover_role_for_lang(guild, code)
        if role:
            result[code] = (role, reason)
    return result

async def toggle_role(interaction, role):
    member = interaction.user
    if not isinstance(member, discord.Member):
        member = await interaction.guild.fetch_member(interaction.user.id)

    if role in member.roles:
        await member.remove_roles(role, reason="Lang_bot language toggle")
        return f"✅ **{role.name}** 역할을 제거했습니다."
    else:
        await member.add_roles(role, reason="Lang_bot language toggle")
        return f"✅ **{role.name}** 역할을 추가했습니다."

class PickerButton(discord.ui.Button):
    def __init__(self, code, role):
        label = role.name
        emoji = {
            "en":"🇬🇧","ja":"🇯🇵","ko":"🇰🇷","zh":"🇨🇳",
            "es":"🇪🇸","fr":"🇫🇷","de":"🇩🇪","pt":"🇵🇹","ru":"🇷🇺"
        }.get(code)
        super().__init__(
            label=label[:80],
            emoji=emoji,
            style=discord.ButtonStyle.secondary,
            custom_id=f"recovery_picker:{role.id}"
        )
        self.role_id = role.id

    async def callback(self, interaction):
        role = interaction.guild.get_role(self.role_id)
        if role is None:
            await interaction.response.send_message("역할을 찾을 수 없습니다.", ephemeral=True)
            return
        try:
            text = await toggle_role(interaction, role)
            await interaction.response.send_message(text, ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ Lang_bot에 `역할 관리` 권한이 없거나, Lang_bot 역할이 대상 역할보다 아래에 있습니다.",
                ephemeral=True
            )

class PickerView(discord.ui.View):
    def __init__(self, guild):
        super().__init__(timeout=180)
        roles = discover_all_language_roles(guild)
        for code, (role, reason) in list(roles.items())[:20]:
            self.add_item(PickerButton(code, role))

@client.event
async def on_ready():
    global old_message_id, old_button_map

    print("=" * 70)
    print(f"[ONLINE] {client.user} ({client.user.id})")
    print("이 계정이 기존 Lang_bot이면 Discord에서도 바로 온라인으로 표시됩니다.")

    guild = client.get_guild(GUILD_ID)
    if guild is None:
        print(f"[ERROR] 서버 {GUILD_ID} 를 찾지 못했습니다.")
        print("기존 Lang_bot 계정이 아직 그 서버에 들어가 있는지 확인하세요.")
        return

    channel = guild.get_channel(WELCOME_CHANNEL_ID)
    if channel is None:
        print(f"[ERROR] welcome 채널 {WELCOME_CHANNEL_ID} 를 찾지 못했습니다.")
        return

    me = guild.me
    if me:
        print(f"[PERMISSION] Manage Roles = {me.guild_permissions.manage_roles}")
        print(f"[BOT ROLE] 최고 역할 = {me.top_role.name} / position {me.top_role.position}")

    msg = await find_original_message(channel)
    if msg is None:
        print("[ERROR] 기존 Lang_bot 버튼 메시지를 찾지 못했습니다.")
        print("하지만 봇 자체는 온라인입니다. 이 CMD 화면을 캡처해서 보내주세요.")
        return

    old_message_id = msg.id
    print(f"[FOUND] 기존 Lang_bot 메시지 발견: {msg.id}")
    print(f"[FOUND] 링크: https://discord.com/channels/{guild.id}/{channel.id}/{msg.id}")

    buttons = read_buttons(msg)
    for cid, label, emoji in buttons:
        lang = infer_lang(label, emoji)

        # custom_id 안에 실제 role ID가 박혀 있는 경우 최우선
        role = None
        why = ""
        for number in re.findall(r"\d{17,20}", cid):
            maybe = guild.get_role(int(number))
            if maybe:
                role = maybe
                why = "custom_id role id"
                break

        if role is None and lang:
            role, why = discover_role_for_lang(guild, lang)

        old_button_map[cid] = {
            "label": label,
            "emoji": emoji,
            "lang": lang,
            "role_id": role.id if role else None
        }

        print(
            f"[BUTTON] label={label!r} emoji={emoji!r} custom_id={cid!r} "
            f"=> lang={lang!r} role={(role.name if role else None)!r} {why}"
        )

    print("[READY] 기존 메시지를 그대로 사용합니다.")
    print("[READY] Discord에서 예전 English 버튼을 눌러보세요.")
    print("[READY] `•••` 버튼은 다른 언어 역할 선택창으로 복구합니다.")
    print("=" * 70)

@client.event
async def on_interaction(interaction):
    try:
        if interaction.type != discord.InteractionType.component:
            return
        data = interaction.data or {}
        cid = data.get("custom_id")
        if not cid:
            return

        # Recovery picker에서 만들어진 임시 버튼은 View callback이 처리.
        if cid.startswith("recovery_picker:"):
            return

        # 기존 Lang_bot 메시지에서 발생한 interaction만 처리
        if interaction.message and old_message_id:
            if interaction.message.id != old_message_id:
                return

        info = old_button_map.get(cid)
        if not info:
            return

        # role 매핑이 된 언어 버튼
        role_id = info.get("role_id")
        if role_id:
            role = interaction.guild.get_role(role_id)
            if role:
                try:
                    text = await toggle_role(interaction, role)
                    await interaction.response.send_message(text, ephemeral=True)
                except discord.Forbidden:
                    await interaction.response.send_message(
                        "❌ Lang_bot에 `역할 관리` 권한이 없거나 역할 순서가 잘못되어 있습니다. "
                        "서버 설정 → 역할에서 Lang_bot 역할을 언어 역할보다 위로 올려주세요.",
                        ephemeral=True
                    )
                return

        # 스크린샷의 `•••` 같은 보조 버튼:
        # 현재 General 카테고리에서 발견되는 언어 역할을 선택하게 함.
        view = PickerView(interaction.guild)
        if len(view.children) == 0:
            await interaction.response.send_message(
                "언어 역할을 자동으로 찾지 못했습니다. 관리자에게 알려주세요.",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "🌐 사용할 언어를 선택하세요. 다시 누르면 역할이 제거됩니다.",
                view=view,
                ephemeral=True
            )

    except Exception as e:
        print(f"[INTERACTION ERROR] {type(e).__name__}: {e}")
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    f"오류가 발생했습니다: `{type(e).__name__}`",
                    ephemeral=True
                )
        except Exception:
            pass

client.run(TOKEN)
