# ══════════════════════════════════════════════════════════════
#   SMS BOT v15.3 — FINAL NIJWM 💎
#   By NIJWM
# ══════════════════════════════════════════════════════════════

import asyncio, json, os, re, time, logging, zipfile, io, random
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
BOT_TOKEN = "8824607713:AAFp9rW2E-aH9axjS3re5tFN0v3zjaLMyFo"
OWNER_ID  = 7165783614
DEFAULT_ADMINS = [7165783614]

_DATA_FILE = "bot_data.json"
_VERSION   = "v15.3"
_CREDITS   = "NIJWM"
_OWNER_UN  = "@nijwmz"

FB_FETCH_CONCURRENT = 100
SEND_CONCURRENT     = 40
HTTP_TIMEOUT        = 10
PROGRESS_INTERVAL   = 2.5
JOB_KEEP_SECONDS    = 300
BULK_ADD_LIMIT      = 200
SMS_RANDOM_MIN      = 5
SMS_RANDOM_MAX      = 10

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S")
log = logging.getLogger("SMSBot")
logging.getLogger("aiogram.event").setLevel(logging.WARNING)

_fb_sem   = asyncio.Semaphore(FB_FETCH_CONCURRENT)
_send_sem = asyncio.Semaphore(SEND_CONCURRENT)

# ══════════════════════════════════════════════
#  PHONE NORMALIZER
# ══════════════════════════════════════════════
def normalize_phone(raw):
    if not raw: return None
    s = re.sub(r"[\s\-\(\)\.\,\_]", "", raw.strip())
    if s.startswith("00"): s = "+" + s[2:]
    if s.startswith("+"):
        d = s[1:]
        if d.isdigit() and 6 <= len(d) <= 15: return "+" + d
        return None
    if not s.isdigit(): return None
    if len(s) == 10:                        return "+91" + s
    if len(s) == 12 and s.startswith("91"): return "+" + s
    if 6 <= len(s) <= 15:                   return "+" + s
    return None

# ══════════════════════════════════════════════
#  FSM
# ══════════════════════════════════════════════
class W(StatesGroup):
    fb_url         = State()
    fb_bulk        = State()
    test_to        = State()
    test_msg       = State()
    adm_add        = State()
    ban_id         = State()
    usr_add_id     = State()
    usr_add_exp    = State()
    prem_add_id    = State()
    fj_add         = State()
    broadcast      = State()
    prem_price     = State()
    prem_upi       = State()
    prem_qr        = State()
    prem_binance   = State()
    prem_crypto    = State()
    pay_screenshot = State()

# ══════════════════════════════════════════════
#  STORAGE
# ══════════════════════════════════════════════
def _default_plans():
    return {
        "7day":     {"price": 20,  "days": 7,    "label": "7 Days"},
        "30day":    {"price": 79,  "days": 30,   "label": "30 Days"},
        "lifetime": {"price": 299, "days": None, "label": "Lifetime"},
    }

def _default_payment():
    return {"upi": "", "qr": None, "binance": "", "crypto": ""}

def _default_premium():
    return {"enabled": False, "plans": _default_plans(),
            "payment": _default_payment(),
            "users": {}, "requests": [], "history": []}

def _new_user():
    return {"stats": {"sent":0, "failed":0, "last":"—"}}

def _defs():
    return {
        "firebase_pool": [], "admins": list(DEFAULT_ADMINS),
        "free": True, "users": {}, "timed_users": {},
        "force_join": [], "banned": [], "premium": _default_premium(),
    }

_cache = None; _cache_mtime = 0

def _deep_merge_defaults(d):
    for k, v in _defs().items():
        if k not in d: d[k] = v
    prem_defaults = _default_premium()
    if not isinstance(d.get("premium"), dict):
        d["premium"] = prem_defaults
    else:
        for k, v in prem_defaults.items():
            if k not in d["premium"]: d["premium"][k] = v
        for pk, pv in _default_plans().items():
            if pk not in d["premium"]["plans"]: d["premium"]["plans"][pk] = pv
        for kk, vv in _default_payment().items():
            if kk not in d["premium"]["payment"]: d["premium"]["payment"][kk] = vv
    return d

def load() -> dict:
    global _cache, _cache_mtime
    if not os.path.exists(_DATA_FILE): return _defs()
    try:
        mt = os.path.getmtime(_DATA_FILE)
        if _cache is not None and mt == _cache_mtime: return _cache
        with open(_DATA_FILE) as f: d = json.load(f)
        d = _deep_merge_defaults(d)
        for aid in DEFAULT_ADMINS:
            if aid not in d["admins"]: d["admins"].append(aid)
        _cache = d; _cache_mtime = mt
        return d
    except Exception as e:
        log.error(f"load: {e}"); return _defs()

def save(d):
    global _cache, _cache_mtime
    try:
        tmp = _DATA_FILE + ".tmp"
        with open(tmp, "w") as f: json.dump(d, f, indent=1)
        os.replace(tmp, _DATA_FILE)
        _cache = d; _cache_mtime = os.path.getmtime(_DATA_FILE)
    except Exception as e: log.error(f"save: {e}")

def usr(uid, d) -> dict:
    k = str(uid)
    if k not in d["users"]: d["users"][k] = _new_user()
    u = d["users"][k]
    for key, val in _new_user().items():
        if key not in u: u[key] = val
    return u

# ══════════════════════════════════════════════
#  PERMISSIONS
# ══════════════════════════════════════════════
def is_owner(uid):     return uid == OWNER_ID
def is_admin(uid, d):  return is_owner(uid) or uid in d.get("admins", [])
def is_banned(uid, d): return uid in d.get("banned", [])

def is_premium_user(uid, d) -> bool:
    if is_admin(uid, d): return True
    pu = d.get("premium", {}).get("users", {}).get(str(uid))
    if pu:
        exp = pu.get("expires")
        if exp is None: return True
        if time.time() < exp: return True
    return False

def premium_mode_on(d) -> bool:
    return bool(d.get("premium", {}).get("enabled"))

def can_use(uid, d) -> bool:
    if is_banned(uid, d): return False
    if is_admin(uid, d):  return True
    if premium_mode_on(d):
        return is_premium_user(uid, d)
    if d.get("free", True): return True
    tu = d.get("timed_users", {}).get(str(uid))
    if tu:
        if tu.get("expires") is None:   return True
        if time.time() < tu["expires"]: return True
    return False

def role_label(uid, d) -> str:
    if is_owner(uid):    return "Owner"
    if is_admin(uid, d): return "Admin"
    if premium_mode_on(d):
        pu = d.get("premium", {}).get("users", {}).get(str(uid))
        if pu:
            exp = pu.get("expires")
            if exp is None: return "Premium · Lifetime"
            rem = exp - time.time()
            if rem > 0:
                days = int(rem // 86400)
                if days >= 1: return f"Premium · {days}d left"
                h = int(rem // 3600); m = int((rem % 3600) // 60)
                return f"Premium · {h}h {m}m"
            return "Expired"
    return "Lifetime Pro"

def role_badge(uid, d) -> str:
    if is_owner(uid):    return "👑"
    if is_admin(uid, d): return "🛡"
    if premium_mode_on(d) and is_premium_user(uid, d):
        return "💎"
    return "✨"

def is_public_callback(c) -> bool:
    """Only these run even without access. Admin sub-callbacks MUST fall through."""
    return c in ("home", "fj:check", "help:show", "about:show")

# ══════════════════════════════════════════════
#  FORCE JOIN
# ══════════════════════════════════════════════
_JR_CACHE: dict[str, set] = {}
_JR_MAX = 5000

async def check_force_join(bot, uid, d):
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
    btns.append([InlineKeyboardButton(text="✅  Verify", callback_data="fj:check")])
    return InlineKeyboardMarkup(inline_keyboard=btns)

# ══════════════════════════════════════════════
#  UI HELPERS
# ══════════════════════════════════════════════
def pbar_solid(done, total, w=14):
    if total == 0: return "░" * w
    filled = round(done / total * w)
    return "█" * filled + "░" * (w - filled)

def pct(done, total): return f"{round(done/total*100)}%" if total else "0%"

def fmt_time(sec):
    sec = int(sec)
    h = sec // 3600; m = (sec % 3600) // 60; s = sec % 60
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

def line(): return "━━━━━━━━━━━━━━━━━━━━━━━"
def fmt_dt(ts):   return datetime.fromtimestamp(ts).strftime("%d/%m/%Y %H:%M")
def fmt_date(ts): return datetime.fromtimestamp(ts).strftime("%d/%m/%Y")

def is_payment_set(pay) -> bool:
    return bool(pay.get("upi") or pay.get("qr") or pay.get("binance") or pay.get("crypto"))

def kb(*rows):
    """Bulletproof keyboard builder — accepts tuples OR InlineKeyboardButton."""
    ikb = []
    for row in rows:
        out_row = []
        for item in row:
            if isinstance(item, InlineKeyboardButton):
                out_row.append(item)
            elif isinstance(item, (tuple, list)) and len(item) == 2:
                out_row.append(InlineKeyboardButton(
                    text=str(item[0]), callback_data=str(item[1])))
        if out_row: ikb.append(out_row)
    return InlineKeyboardMarkup(inline_keyboard=ikb)

# ══════════════════════════════════════════════
#  TEXTS
# ══════════════════════════════════════════════
def home_text(uid, d, u):
    return (
        f"<b>⚡  SMS BOT  ·  PREMIUM</b>\n"
        f"<code>{_VERSION}</code>   ·   <i>By {_CREDITS}</i>\n"
        f"{line()}\n\n"
        f"{role_badge(uid, d)}  <b>{role_label(uid, d)}</b>"
    )

HELP_TEXT = f"""
<b>📖  HELP CENTER</b>
{line()}

<b>🚀  Shuru kaise karein</b>

<b>1.</b>  <b>🧪 Test SMS</b> pe tap karo
<b>2.</b>  Target number daalo
<b>3.</b>  Message likho
<b>4.</b>  Live progress dekho
<b>5.</b>  🛑 Stop — jab mann bhar jaye 😄

{line()}

<b>💎  Premium Access</b>

Plan chuno → Payment karo →
Screenshot bhejo → Admin verify karega →
Premium unlock 🎉

{line()}

<b>📞  Number Formats</b>

<code>+91 98765 43210</code>
<code>+919876543210</code>
<code>9876543210</code>

<{line()}>

<b>👨‍💻  Credits</b>

Developed by <b>{_CREDITS}</b>
"""

ABOUT_TEXT = f"""
<b>👨‍💻  DEVELOPER</b>
{line()}

<b>Name</b>      : <b>{_CREDITS}</b>
<b>Username</b>  : <code>{_OWNER_UN}</code>
<b>Version</b>   : <code>{_VERSION}</code>

{line()}
<i>Premium SMS Infrastructure — built with ☕</i>
"""

def premium_landing_text(d):
    return (
        f"<b>💎  PREMIUM ACCESS</b>\n"
        f"{line()}\n\n"
        f"Bot free hai but premium ka alag hi maza hai 😎\n\n"
        f"<b>Kya milta hai</b>\n"
        f"  ⚡  Unlimited SMS sending\n"
        f"  🚀  Fastest delivery speed\n"
        f"  📊  Live tracking & reports\n"
        f"  🛑  Real-time stop control\n"
        f"  💎  Lifetime Pro badge\n\n"
        f"<i>Starting from just ₹20 — chai se sasta ☕</i>"
    )

# ══════════════════════════════════════════════
#  KEYBOARDS
# ══════════════════════════════════════════════
def main_menu(uid, d):
    rows = [
        [("🧪  Test SMS",     "test:go"),   ("📊  Dashboard",  "dash:show")],
        [("💎  Premium",      "prem:menu"), ("📖  Help",       "help:show")],
        [("👨‍💻  Developer",   "about:show")],
    ]
    if is_admin(uid, d):
        rows.append([("🛡  Admin Panel", "adm:menu")])
    return kb(*rows)

def premium_cta_kb():
    return kb(
        [("💎  View Plans", "prem:plans")],
        [("📖  Help", "help:show"), ("👨‍💻  Developer", "about:show")],
    )

def plans_kb(d):
    plans = d.get("premium", {}).get("plans", {})
    rows = []
    for key, p in plans.items():
        rows.append([(f"💎  {p['label']}   ·   ₹{p['price']}", f"prem:plan:{key}")])
    rows.append([("◀️  Back", "home")])
    return kb(*rows)

def methods_kb(plan_key, pay):
    rows = []
    if pay.get("upi"):     rows.append([("🏦  UPI",          f"prem:m:{plan_key}:upi")])
    if pay.get("qr"):      rows.append([("📱  QR Code",      f"prem:m:{plan_key}:qr")])
    if pay.get("binance"): rows.append([("🟡  Binance Pay",  f"prem:m:{plan_key}:binance")])
    if pay.get("crypto"):  rows.append([("🪙  Crypto",       f"prem:m:{plan_key}:crypto")])
    rows.append([("◀️  Back", "prem:plans")])
    return kb(*rows)

def pay_confirm_kb(plan_key, method):
    return kb(
        [("📸  Send Payment Screenshot", f"prem:pay:{plan_key}:{method}")],
        [("◀️  Back", f"prem:plan:{plan_key}")],
    )

def adm_menu_kb(uid, d):
    prem_status = "🟢" if premium_mode_on(d) else "🔴"
    rows = [
        [("🔥  Firebase Manager","fbm:menu"),  ("👥  Users","adm:users")],
        [("➕  Add User","adm:adduser"),      ("📊  Global Stats","adm:stats")],
        [("🚫  Ban","ban:do"),                ("✅  Unban","unban:do")],
        [("📢  Broadcast","bcast:do"),        ("📢  Force Join","fj:menu")],
        [(f"💎  Premium  {prem_status}","adm:prem"), ("🗑  Reset Stats","adm:resetdash")],
    ]
    if is_owner(uid):
        rows.append([("🟢  Free Mode","adm:free"), ("➕  Add Admin","adm:addadmin")])
        rows.append([("📦  Export ZIP","adm:zip"), ("💥  Reset ALL","adm:resetall")])
    rows.append([("◀️  Back","home")])
    return kb(*rows)

def prem_admin_kb(d):
    prem = d.get("premium", {})
    on = prem.get("enabled")
    toggle = "🔴  Disable" if on else "🟢  Enable"
    reqs = len([r for r in prem.get("requests", []) if r.get("status") == "pending"])
    req_label = f"📥  Requests ({reqs})" if reqs else "📥  Requests"
    users_count = len(prem.get("users", {}))
    rows = [
        [(toggle, "prem:toggle")],
        [("➕  Add Premium User", "prem:add")],
        [(f"👥  Premium Users ({users_count})", "prem:users")],
        [("📝  Edit Plans", "prem:editplans"), ("💳  Payment", "prem:editpay")],
        [(req_label, "prem:reqs"), ("📜  History", "prem:hist")],
        [("◀️  Back", "adm:menu")],
    ]
    return kb(*rows)

def prem_plans_edit_kb(d):
    rows = []
    for key, p in d.get("premium", {}).get("plans", {}).items():
        rows.append([(f"✏️  {p['label']}  —  ₹{p['price']}", f"prem:editprice:{key}")])
    rows.append([("◀️  Back", "adm:prem")])
    return kb(*rows)

def prem_pay_edit_kb(d):
    pay = d.get("premium", {}).get("payment", {})
    def stat(v): return "✅" if v else "❌"
    upi_short = (pay.get("upi", "") or "not set")[:14]
    binance_short = (pay.get("binance", "") or "not set")[:14]
    crypto_short = (pay.get("crypto", "") or "not set")[:14]
    qr_stat = "Set" if pay.get("qr") else "not set"
    rows = [
        [(f"🏦  UPI     {stat(pay.get('upi'))}  {upi_short}", "prem:setupi")],
        [(f"📱  QR      {stat(pay.get('qr'))}  {qr_stat}",     "prem:setqr")],
        [(f"🟡  Binance {stat(pay.get('binance'))}  {binance_short}", "prem:setbinance")],
        [(f"🪙  Crypto  {stat(pay.get('crypto'))}  {crypto_short}", "prem:setcrypto")],
        [("◀️  Back", "adm:prem")],
    ]
    return kb(*rows)

def prem_plan_select_kb(target_uid):
    plans = load().get("premium", {}).get("plans", {})
    rows = []
    for key, p in plans.items():
        rows.append([(f"💎  {p['label']}   ·   ₹{p['price']}",
                      f"prem:addp:{target_uid}:{key}")])
    rows.append([("❌  Cancel", "adm:prem")])
    return kb(*rows)

def prem_users_kb(users):
    rows = []
    for k, v in list(users.items())[:30]:
        rows.append([
            (f"💎 {k} · {v.get('label','?')}", "<i>noop</i>"),
            ("🗑", f"prem:rm:{k}"),
        ])
    rows.append([("◀️  Back","adm:prem")])
    return kb(*rows)

def prem_requests_kb(reqs):
    rows = []
    for r in reqs[:20]:
        method_lbl = {"upi":"🏦","qr":"📱","binance":"🟡","crypto":"🪙"}.get(r.get("method",""), "💳")
        rows.append([(f"{method_lbl}  {r['name'][:16]}  ·  {r['label']}  ·  ₹{r['price']}",
                      f"prem:view:{r['id']}")])
    rows.append([("◀️  Back","adm:prem")])
    return kb(*rows)

def unban_kb(banned):
    rows = [[(f"🔓 {bid}", f"unban:uid:{bid}")] for bid in banned[:20]]
    rows.append([("❌  Cancel","adm:menu")])
    return kb(*rows)

def stop_kb(uid):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🛑   STOP", callback_data=f"stop:{uid}")
    ]])

def timed_kb():
    return kb(
        [("⚡  1 Hour","tacc:3600"),    ("🕕  6 Hours","tacc:21600")],
        [("📅  24 Hours","tacc:86400"), ("📆  7 Days","tacc:604800")],
        [("📅  Custom","tacc:custom"),  ("♾  Permanent","tacc:0")],
        [("◀️  Back","adm:menu")],
    )

def fj_menu_kb(uid, d):
    fj = d.get("force_join", [])
    rows = [[("➕  Add Force Join","fj:add")]]
    for ch in fj:
        rows.append([(f"📢  {ch.get('title','?')[:20]}", "<i>noop</i>"),
                     ("🗑", f"fj:del:{ch['id']}")])
    rows.append([("◀️  Back","adm:menu")])
    return kb(*rows)

def fb_manager_kb(page=0):
    d = load()
    pool = d.get("firebase_pool", [])
    per = 10; start = page * per
    chunk = pool[start:start+per]
    rows = []
    for i, fb in enumerate(chunk, start=start+1):
        short = (fb["url"].replace("https://", "").replace(".firebaseio.com", "")
                        .split(".")[0][:16])
        added_by = fb.get("added_by", "?")
        if added_by == OWNER_ID:              tag = "👑"
        elif added_by in d.get("admins",[]):  tag = "🛡"
        else:                                  tag = "👤"
        rows.append([(f"{i:02d}. {tag}  {short}", "<i>noop</i>"),
                     ("🗑", f"fbr:del:{fb['id']}")])
    nav = []
    total_pages = max(1, (len(pool) + per - 1) // per)
    if page > 0:                nav.append(("◀️", f"fbm:pg:{page-1}"))
    nav.append((f"  {page+1} / {total_pages}  ", "<i>noop</i>"))
    if start + per < len(pool): nav.append(("▶️", f"fbm:pg:{page+1}"))
    if nav: rows.append(nav)
    rows.append([("➕  Add Single","wiz:start"), ("📥  Add Bulk","fbb:start")])
    if pool:
        rows.append([("💥  Reset Pool","fbr:reset:confirm")])
    rows.append([("◀️  Back","adm:menu")])
    return kb(*rows)

# ══════════════════════════════════════════════
#  FIREBASE CORE
# ══════════════════════════════════════════════
async def fb_get(base, path):
    url = base.rstrip("/") + path
    async with _fb_sem:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT)) as r:
                    if r.status == 200:
                        txt = (await r.text()).strip()
                        if txt == "null": return {}
                        return json.loads(txt)
                    return None
        except Exception:
            return None

async def fb_put(base, path, payload):
    url = base.rstrip("/") + path
    async with _send_sem:
        for attempt in range(2):
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.put(url, json=payload,
                                     timeout=aiohttp.ClientTimeout(total=8)) as r:
                        if 200 <= r.status < 300: return True
            except Exception: pass
            if attempt == 0: await asyncio.sleep(0.3)
        return False

def dev_online(dd):
    try:
        return any([dd.get("isOnline"), dd.get("online"), dd.get("connected"),
                    dd.get("status") in ("online","active",True,1)])
    except Exception: return False

async def send_via_fb(fb, dev, sim, to, msg):
    return await fb_put(fb, f"/clients/{dev}/webhookEvent/sendSms.json", {
        "from": sim, "to": to.strip(), "message": msg.strip(),
        "isSended": False, "timestamp": int(time.time() * 1000)
    })

async def verify_fb(url):
    devs = await fb_get(url, "/clients.json")
    if devs is None: return "bad", 0
    if not devs: return "empty", 0
    return "ok", len(devs)

async def fetch_pool_devices():
    d = load()
    pool = d.get("firebase_pool", [])
    if not pool: return [], 0
    async def _one(fb):
        try: return fb, await fb_get(fb["url"], "/clients.json")
        except Exception: return fb, None
    results = await asyncio.gather(*[_one(fb) for fb in pool])
    devices = []; dead_ids = set()
    for fb, devs in results:
        if devs is None: continue
        if not devs:
            dead_ids.add(fb["id"]); continue
        for did, dd in devs.items():
            if dev_online(dd):
                devices.append({
                    "fb_url": fb["url"], "device_id": did,
                    "name": dd.get("deviceName") or dd.get("name") or did[:20],
                    "sims": dd.get("sims", []),
                })
    removed = 0
    if dead_ids:
        d2 = load()
        before = len(d2.get("firebase_pool", []))
        d2["firebase_pool"] = [fb for fb in d2["firebase_pool"] if fb["id"] not in dead_ids]
        removed = before - len(d2["firebase_pool"])
        if removed:
            save(d2); log.info(f"auto-removed {removed} dead firebases")
    return devices, removed

# ══════════════════════════════════════════════
#  SEND JOB
# ══════════════════════════════════════════════
class SendJob:
    def __init__(self, uid, to, msg):
        self.uid = uid; self.to = to; self.msg = msg
        self.total_devices = 0; self.done_devices = 0
        self.sent = 0; self.failed = 0; self.total_sms = 0
        self.start_time = time.time()
        self.cancel_event = asyncio.Event()
        self.progress_msg = None
        self.task = None; self.updater = None
        self.state = "running"
        self.lock = asyncio.Lock()
        self.device_counts = {}; self.auto_removed = 0

    def count_for(self, dev_id):
        if dev_id not in self.device_counts:
            self.device_counts[dev_id] = random.randint(SMS_RANDOM_MIN, SMS_RANDOM_MAX)
        return self.device_counts[dev_id]

    @property
    def elapsed(self): return time.time() - self.start_time

    @property
    def speed(self):
        e = self.elapsed
        return (self.sent + self.failed) / e if e > 0 else 0.0

    def snapshot(self):
        return {"done": self.done_devices, "total": self.total_devices,
                "sent": self.sent, "failed": self.failed,
                "total_sms": self.total_sms, "elapsed": self.elapsed,
                "speed": self.speed, "state": self.state}

_active_jobs: dict[int, SendJob] = {}

def render_progress(job):
    s = job.snapshot()
    done = s["sent"] + s["failed"]
    bar = pbar_solid(done, s["total_sms"]); p = pct(done, s["total_sms"])
    icon = {"running":"🚀","done":"✅","stopped":"🛑"}[s["state"]]
    title = {"running":"SENDING","done":"DONE","stopped":"STOPPED"}[s["state"]]
    return (
        f"<b>{icon}   {title}</b>\n{line()}\n"
        f"<code>{bar}</code>   <b>{p}</b>\n{line()}\n\n"
        f"📱  <b>Devices</b>  <code>{s['done']}/{s['total']}</code>\n"
        f"✅  <b>Sent</b>     <code>{s['sent']}</code>\n"
        f"❌  <b>Failed</b>   <code>{s['failed']}</code>\n\n"
        f"⏱  <b>Time</b>     <code>{fmt_time(s['elapsed'])}</code>\n"
        f"⚡  <b>Speed</b>    <code>{s['speed']:.1f}/s</code>"
    )

def render_final(job):
    s = job.snapshot()
    done = s["sent"] + s["failed"]
    bar = pbar_solid(s["sent"], done if done else 1)
    rate = pct(s["sent"], done if done else 1)
    icon = {"done":"✅","stopped":"🛑"}.get(s["state"], "✅")
    title = {"done":"COMPLETE","stopped":"STOPPED"}.get(s["state"], "DONE")
    msg_show = job.msg if len(job.msg) <= 55 else job.msg[:55] + "…"
    return (
        f"<b>{icon}   {title}</b>\n{line()}\n"
        f"<code>{bar}</code>   <b>{rate}</b>\n{line()}\n\n"
        f"📞  <b>Target</b>    <code>{job.to}</code>\n"
        f"📱  <b>Devices</b>   <code>{s['total']}</code>\n"
        f"✅  <b>Sent</b>      <code>{s['sent']}</code>\n"
        f"❌  <b>Failed</b>    <code>{s['failed']}</code>\n\n"
        f"⏱  <b>Time</b>      <code>{fmt_time(s['elapsed'])}</code>\n"
        f"⚡  <b>Speed</b>     <code>{s['speed']:.1f}/s</code>"
        + f"\n\n💬  <i>{msg_show}</i>"
    )

async def _edit_safe(msg, text, markup=None):
    try:
        await msg.edit_text(text, reply_markup=markup, parse_mode="HTML"); return True
    except TelegramBadRequest: return False
    except Exception: return False

async def _progress_updater(job):
    while not job.cancel_event.is_set() and job.state == "running":
        await asyncio.sleep(PROGRESS_INTERVAL)
        if job.state != "running": break
        if job.progress_msg:
            await _edit_safe(job.progress_msg, render_progress(job), stop_kb(job.uid))

async def _run_job(bot, job):
    try:
        devs, removed = await fetch_pool_devices()
        job.auto_removed = removed
        job.total_devices = len(devs)
        for dv in devs: job.count_for(dv["device_id"])
        job.total_sms = sum(job.device_counts.values())

        if job.cancel_event.is_set(): job.state = "stopped"; return
        if job.total_devices == 0: job.state = "done"; return

        if job.progress_msg:
            await _edit_safe(job.progress_msg, render_progress(job), stop_kb(job.uid))
        job.updater = asyncio.create_task(_progress_updater(job))

        async def _one_device(dv):
            if job.cancel_event.is_set(): return 0, 0
            sims = dv.get("sims", []); sim = 0
            if sims:
                try: sim = int(sims[0].get("simSlotIndex", 0))
                except Exception: sim = 0
            n = job.count_for(dv["device_id"]); ok = 0
            for _ in range(n):
                if job.cancel_event.is_set(): break
                if await send_via_fb(dv["fb_url"], dv["device_id"], sim, job.to, job.msg):
                    ok += 1
            return n, ok

        tasks = [asyncio.create_task(_one_device(dv)) for dv in devs]
        for coro in asyncio.as_completed(tasks):
            if job.cancel_event.is_set():
                for t_ in tasks:
                    if not t_.done(): t_.cancel()
                break
            try: r = await coro
            except (asyncio.CancelledError, Exception): r = (0, 0)
            async with job.lock:
                job.done_devices += 1
                if isinstance(r, tuple):
                    n, ok = r
                    job.sent += ok; job.failed += (n - ok)

        job.state = "stopped" if job.cancel_event.is_set() else "done"
    except asyncio.CancelledError:
        job.state = "stopped"; raise
    except Exception as e:
        log.error(f"job crash uid={job.uid}: {e}"); job.state = "stopped"
    finally:
        if job.updater and not job.updater.done(): job.updater.cancel()
        try:
            d2 = load(); u2 = usr(job.uid, d2)
            u2["stats"]["sent"]   = u2["stats"].get("sent", 0) + job.sent
            u2["stats"]["failed"] = u2["stats"].get("failed", 0) + job.failed
            u2["stats"]["last"]   = datetime.now().strftime("%H:%M:%S")
            save(d2)
        except Exception as e: log.error(f"stats save: {e}")
        if job.progress_msg:
            final_kb = kb([("🧪  Test Again", "test:go"), ("🏠  Home", "home")])
            await _edit_safe(job.progress_msg, render_final(job), final_kb)
        asyncio.create_task(_cleanup_job(job.uid))

async def _cleanup_job(uid):
    await asyncio.sleep(JOB_KEEP_SECONDS)
    job = _active_jobs.get(uid)
    if job and job.state != "running": _active_jobs.pop(uid, None)

# ══════════════════════════════════════════════
#  STARTUP BROADCAST  (with /start button)
# ══════════════════════════════════════════════
async def notify_all_users_online(bot, bot_username):
    try:
        await asyncio.sleep(2)
        d = load()
        uids = list(d.get("users", {}).keys())
        if not uids: return

        start_url = f"https://t.me/{bot_username}?start=1"
        launch_kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🚀  Let's Start", url=start_url)
        ]])

        text = (
            f"<b>🚀  BOT IS ONLINE</b>\n"
            f"{line()}\n\n"
            f"🟢  Status    : <b>Online</b>\n"
            f"⚡  Version   : <code>{_VERSION}</code>\n"
            f"👨‍💻  By        : <b>{_CREDITS}</b>\n\n"
            f"<i>Tap below to begin ⤵️</i>"
        )
        ok = fail = 0
        for k in uids:
            try:
                await bot.send_message(int(k), text,
                    parse_mode="HTML", reply_markup=launch_kb)
                ok += 1
            except Exception:
                fail += 1
            await asyncio.sleep(0.05)
        log.info(f"startup broadcast: ok={ok} fail={fail} total={len(uids)}")
    except Exception as e:
        log.error(f"startup broadcast: {e}")

# ══════════════════════════════════════════════
#  BROADCAST
# ══════════════════════════════════════════════
async def _do_broadcast(bot, text, progress_msg=None):
    d = load()
    uids = list(d.get("users", {}).keys())
    ok = fail = 0; total = len(uids)
    for idx, k in enumerate(uids):
        try:
            await bot.send_message(int(k), text, parse_mode="HTML")
            ok += 1
        except Exception:
            fail += 1
        if progress_msg and (idx % 10 == 0 or idx == total-1):
            bar = pbar_solid(idx+1, total)
            try:
                await progress_msg.edit_text(
                    f"<b>📢  Sending…</b>\n{line()}\n"
                    f"<code>{bar}</code>  <b>{pct(idx+1,total)}</b>\n\n"
                    f"✅  <code>{ok}</code>   ❌  <code>{fail}</code>   📊  <code>{total}</code>",
                    parse_mode="HTML")
            except TelegramBadRequest: pass
        await asyncio.sleep(0.05)
    return ok, fail

# ══════════════════════════════════════════════
#  NOTIFY ADMINS
# ══════════════════════════════════════════════
async def notify_admins_payment(bot, req):
    d = load()
    targets = set(d.get("admins", [])) | {OWNER_ID}
    method_lbl = {"upi":"🏦 UPI","qr":"📱 QR","binance":"🟡 Binance","crypto":"🪙 Crypto"}.get(req.get("method",""), "—")
    caption = (
        f"<b>💎  NEW PAYMENT</b>\n{line()}\n\n"
        f"👤  <b>{req.get('name','User')}</b>\n"
        f"🆔  <code>{req['uid']}</code>\n"
        f"📦  {req['label']}  ·  ₹{req['price']}\n"
        f"💳  {method_lbl}\n"
        f"🕐  {fmt_dt(req['created_at'])}"
    )
    btns = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅  Approve", callback_data=f"prem:approve:{req['id']}"),
         InlineKeyboardButton(text="❌  Reject",  callback_data=f"prem:reject:{req['id']}")]
    ])
    for tid in targets:
        try:
            await bot.send_photo(tid, req["screenshot"],
                caption=caption, reply_markup=btns, parse_mode="HTML")
        except Exception as e:
            log.error(f"notify admin {tid}: {e}")

# ══════════════════════════════════════════════
#  ZIP / UTILS
# ══════════════════════════════════════════════
def make_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        if os.path.exists(_DATA_FILE): z.write(_DATA_FILE)
        z.write(__file__, "bot.py")
    buf.seek(0); return buf.read()

async def sedit(cq, text, markup=None):
    try: await cq.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    except TelegramBadRequest: pass
    except Exception: pass

# ══════════════════════════════════════════════
#  ROUTER
# ══════════════════════════════════════════════
R = Router()

@R.message(Command("start"))
async def c_start(msg: Message, state: FSMContext):
    try:
        await state.clear()
        d = load(); uid = msg.from_user.id
        if is_banned(uid, d):
            await msg.answer("🚫 Access denied."); return
        u = usr(uid, d); save(d)
        ok, not_joined = await check_force_join(msg.bot, uid, d)
        if not ok:
            await msg.answer("📢  <b>Join Required</b>\n\nPlease join to continue:",
                reply_markup=_fj_keyboard(not_joined), parse_mode="HTML"); return
        if not can_use(uid, d):
            if premium_mode_on(d):
                await msg.answer(premium_landing_text(d),
                    reply_markup=premium_cta_kb(), parse_mode="HTML")
            else:
                await msg.answer("🚫 Access denied. Contact admin.")
            return
        await msg.answer(home_text(uid, d, u),
            reply_markup=main_menu(uid, d), parse_mode="HTML")
    except Exception as e: log.error(f"/start: {e}")

@R.message(Command("menu"))
async def c_menu(msg: Message, state: FSMContext):
    try:
        await state.clear(); d = load(); uid = msg.from_user.id
        if not can_use(uid, d):
            if premium_mode_on(d):
                await msg.answer(premium_landing_text(d),
                    reply_markup=premium_cta_kb(), parse_mode="HTML")
            else:
                await msg.answer("🚫 Access denied.")
            return
        u = usr(uid, d)
        await msg.answer(home_text(uid, d, u),
            reply_markup=main_menu(uid, d), parse_mode="HTML")
    except Exception as e: log.error(f"/menu: {e}")

@R.message(Command("stop"))
async def c_stop(msg: Message):
    try:
        uid = msg.from_user.id
        job = _active_jobs.get(uid)
        if not job or job.state != "running":
            await msg.answer("Arey, koi kaam chal hi nahi raha 😅"); return
        job.cancel_event.set()
        if job.updater and not job.updater.done(): job.updater.cancel()
        await msg.answer("🛑  Ruk gaya!")
    except Exception as e: log.error(f"/stop: {e}")

@R.message(Command("cancel"))
async def c_cancel(msg, state):
    try:
        await state.clear()
        d = load()
        await msg.answer("✅ Cancelled.", reply_markup=main_menu(msg.from_user.id, d))
    except Exception: pass

@R.chat_join_request()
async def h_join_req(update: ChatJoinRequest):
    try:
        s = _JR_CACHE.setdefault(str(update.chat.id), set())
        if len(s) < _JR_MAX: s.add(update.from_user.id)
    except Exception: pass

# ══════════════════════════════════════════════
#  FSM HANDLERS
# ══════════════════════════════════════════════
@R.message(W.fb_url)
async def f_fb_url(msg, state):
    try:
        uid = msg.from_user.id
        d = load()
        if not is_admin(uid, d): await state.clear(); return
        text = msg.text.strip()
        if not text.startswith("http"):
            await msg.answer("❌ URL must start with <code>https://</code>",
                parse_mode="HTML"); return
        fb_url = text.rstrip("/")

        if any(fb["url"].rstrip("/") == fb_url for fb in d.get("firebase_pool", [])):
            await state.clear()
            await msg.answer(f"<b>🔒 Already Added</b>\n{line()}\n\nThis URL is already in the pool.",
                reply_markup=kb([("🏠  Home","home")]), parse_mode="HTML"); return

        await state.clear()
        wait = await msg.answer("🔍 Verifying…")
        status, n = await verify_fb(fb_url)
        try: await wait.delete()
        except: pass

        if status == "bad":
            await msg.answer(f"<b>❌ Connection Failed</b>\n{line()}\n\nURL or rules issue.",
                reply_markup=kb([("🔙  Retry","wiz:start"), ("🏠  Home","home")]),
                parse_mode="HTML"); return
        if status == "empty":
            await msg.answer(f"<b>❌ Empty</b>\n{line()}\n\n0 devices found.",
                reply_markup=kb([("🔙  Retry","wiz:start"), ("🏠  Home","home")]),
                parse_mode="HTML"); return

        d2 = load()
        d2["firebase_pool"].append({
            "id": str(int(time.time()*1000)) + "_" + str(random.randint(100,999)),
            "url": fb_url, "api_key": "",
            "added_by": uid, "added_at": int(time.time()),
        })
        save(d2)

        devs = await fb_get(fb_url, "/clients.json") or {}
        online = sum(1 for v in devs.values() if dev_online(v))

        await msg.answer(
            f"<b>✅ Added</b>\n{line()}\n\n"
            f"📱 Online : <code>{online}</code>\n"
            f"📦 Total  : <code>{len(devs)}</code>\n"
            f"🔥 Pool   : <code>{len(d2['firebase_pool'])}</code>",
            reply_markup=kb(
                [("➕  Add Single","wiz:start"), ("📥  Add Bulk","fbb:start")],
                [("🔥  Manager","fbm:menu")]),
            parse_mode="HTML")
    except Exception as e: log.error(f"fb_url: {e}"); await state.clear()

@R.message(W.fb_bulk)
async def f_fb_bulk(msg, state):
    try:
        uid = msg.from_user.id
        d = load()
        if not is_admin(uid, d): await state.clear(); return
        text = msg.text.strip()
        if not text: await msg.answer("❌ Empty."); return
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        if not lines: await msg.answer("❌ No URLs found."); return
        if len(lines) > BULK_ADD_LIMIT:
            await msg.answer(f"⚠️ Max {BULK_ADD_LIMIT} URLs per batch."); return

        await state.clear()
        wait = await msg.answer(
            f"<b>📥 Bulk Add</b>\n{line()}\n\n"
            f"🔍 Verifying <b>{len(lines)}</b> URLs…",
            parse_mode="HTML")

        d2 = load()
        existing = {fb["url"].rstrip("/") for fb in d2.get("firebase_pool", [])}
        added = dup = bad = empty = 0
        sem = asyncio.Semaphore(20)

        async def _check(line):
            url = line.strip().rstrip("/")
            if not url.startswith("http"): return ("bad", url)
            if url in existing: return ("dup", url)
            async with sem:
                status, _n = await verify_fb(url)
            return (status, url)

        results = await asyncio.gather(*[_check(l) for l in lines], return_exceptions=True)

        for r in results:
            if isinstance(r, Exception) or not isinstance(r, tuple):
                bad += 1; continue
            status, url = r
            if status == "ok":
                d3 = load()
                d3["firebase_pool"].append({
                    "id": str(int(time.time()*1000)) + "_" + str(random.randint(1000,9999)),
                    "url": url, "api_key": "",
                    "added_by": uid, "added_at": int(time.time()),
                })
                save(d3); existing.add(url); added += 1
            elif status == "empty": empty += 1
            elif status == "dup":   dup += 1
            else:                    bad += 1

        try: await wait.delete()
        except: pass

        total = len(load().get("firebase_pool", []))
        await msg.answer(
            f"<b>✅ Bulk Add Complete</b>\n{line()}\n\n"
            f"✅  Added    : <code>{added}</code>\n"
            f"🔒  Duplicate: <code>{dup}</code>\n"
            f"😴  Empty    : <code>{empty}</code>\n"
            f"❌  Invalid  : <code>{bad}</code>\n\n"
            f"📊  Total    : <code>{len(lines)}</code>\n"
            f"🔥  Pool now : <code>{total}</code>",
            reply_markup=kb(
                [("📥  Add More","fbb:start"), ("🔥  Manager","fbm:menu")],
                [("🏠  Home","home")]),
            parse_mode="HTML")
    except Exception as e: log.error(f"fb_bulk: {e}"); await state.clear()

@R.message(W.test_to)
async def f_test_to(msg, state):
    try:
        to = normalize_phone(msg.text.strip())
        if not to:
            await msg.answer(
                f"❌ Number samajh nahi aaya 😅\n\n<code>+919876543210</code>\n<code>+91 98765 43210</code>\n<code>9876543210</code>",
                parse_mode="HTML"); return
        await state.update_data(test_to=to)
        await state.set_state(W.test_msg)
        await msg.answer(f"✅  Target : <code>{to}</code>\n\n💬 Now enter your message:",
            parse_mode="HTML")
    except Exception as e: log.error(f"test_to: {e}"); await state.clear()

@R.message(W.test_msg)
async def f_test_msg(msg, state):
    try:
        sms_text = msg.text.strip()
        if not sms_text: await msg.answer("❌ Message required."); return
        await state.update_data(test_msg=sms_text)
        await _begin_send(msg.bot, msg.from_user.id, state, msg)
    except Exception as e: log.error(f"test_msg: {e}"); await state.clear()

@R.message(W.adm_add)
async def f_adm_add(msg, state):
    try:
        if not is_owner(msg.from_user.id): await state.clear(); return
        d = load()
        nid = int(msg.text.strip())
        if nid not in d["admins"]: d["admins"].append(nid)
        save(d); await state.clear()
        await msg.answer(f"✅ Admin: <code>{nid}</code>",
            reply_markup=adm_menu_kb(msg.from_user.id, d), parse_mode="HTML")
        try: await msg.bot.send_message(nid, "🎉 You're now <b>Admin</b>! /start", parse_mode="HTML")
        except: pass
    except: await msg.answer("❌ Invalid ID."); await state.clear()

@R.message(W.ban_id)
async def f_ban_id(msg, state):
    try:
        if not is_admin(msg.from_user.id, load()): await state.clear(); return
        d = load()
        bid = int(msg.text.strip())
        if is_admin(bid, d): await msg.answer("❌ Cannot ban admins."); return
        if bid not in d.setdefault("banned", []): d["banned"].append(bid)
        save(d); await state.clear()
        await msg.answer(f"🚫 Banned: <code>{bid}</code>",
            reply_markup=adm_menu_kb(msg.from_user.id, d), parse_mode="HTML")
        try: await msg.bot.send_message(bid, "🚫 You have been banned.")
        except: pass
    except: await msg.answer("❌ Invalid ID."); await state.clear()

@R.message(W.usr_add_id)
async def f_usr_add_id(msg, state):
    try:
        uid2 = int(msg.text.strip())
        await state.update_data(new_uid=uid2)
        await state.set_state(W.usr_add_exp)
        await msg.answer(f"✅ User ID: <code>{uid2}</code>\n\n⏱ Select duration:",
            reply_markup=timed_kb(), parse_mode="HTML")
    except: await msg.answer("❌ Invalid ID.")

@R.message(W.usr_add_exp)
async def f_usr_add_exp(msg, state):
    try:
        d = load(); uid = msg.from_user.id; text = msg.text.strip()
        fsmd = await state.get_data(); uid2 = fsmd.get("new_uid"); await state.clear()
        for fmt in ("%d/%m/%Y","%Y-%m-%d","%d-%m-%Y"):
            try:
                dt = datetime.strptime(text, fmt); exp = dt.timestamp(); break
            except: pass
        else: await msg.answer("❌ Use DD/MM/YYYY"); return
        d.setdefault("timed_users", {})[str(uid2)] = {
            "expires": exp, "added_by": uid, "added_at": int(time.time())}
        save(d)
        await msg.answer(f"✅ User <code>{uid2}</code> until <code>{text}</code>",
            reply_markup=adm_menu_kb(uid, d), parse_mode="HTML")
        try: await msg.bot.send_message(uid2, f"✅ Access until <code>{text}</code>. /start",
            parse_mode="HTML")
        except: pass
    except Exception as e: log.error(f"usr_add_exp: {e}")

@R.message(W.prem_add_id)
async def f_prem_add_id(msg, state):
    try:
        uid = msg.from_user.id
        if not is_admin(uid, load()): await state.clear(); return
        try: uid2 = int(msg.text.strip())
        except: await msg.answer("❌ Invalid user ID."); return
        await state.clear()
        await msg.answer(
            f"<b>💎 Add Premium</b>\n{line()}\n\n"
            f"👤  User : <code>{uid2}</code>\n\nSelect plan:",
            parse_mode="HTML",
            reply_markup=prem_plan_select_kb(uid2))
    except Exception as e: log.error(f"prem_add_id: {e}"); await state.clear()

@R.message(W.broadcast)
async def f_broadcast(msg, state):
    try:
        if not is_admin(msg.from_user.id, load()): await state.clear(); return
        text = msg.text.strip()
        if not text: await msg.answer("❌ Empty message."); return
        await state.clear()
        wait = await msg.answer(f"📢 Broadcasting…", parse_mode="HTML")
        ok, fail = await _do_broadcast(msg.bot, text, wait)
        try: await wait.delete()
        except: pass
        await msg.answer(
            f"<b>✅ Broadcast Complete</b>\n{line()}\n\n"
            f"✅ <code>{ok}</code>   ❌ <code>{fail}</code>   📊 <code>{ok+fail}</code>",
            reply_markup=adm_menu_kb(msg.from_user.id, load()), parse_mode="HTML")
    except Exception as e: log.error(f"bcast: {e}"); await state.clear()

@R.message(W.prem_price)
async def f_prem_price(msg, state):
    try:
        if not is_admin(msg.from_user.id, load()): await state.clear(); return
        fsmd = await state.get_data()
        plan_key = fsmd.get("edit_plan")
        if not plan_key: await msg.answer("⚠️ Session expired."); await state.clear(); return
        try:
            new_price = int(msg.text.strip())
            if new_price < 0 or new_price > 100000: raise ValueError
        except: await msg.answer("❌ Valid price (0–100000)."); return
        d = load()
        d["premium"]["plans"][plan_key]["price"] = new_price
        save(d); await state.clear()
        await msg.answer(f"✅ Updated to ₹{new_price}",
            reply_markup=prem_plans_edit_kb(d), parse_mode="HTML")
    except Exception as e: log.error(f"prem_price: {e}"); await state.clear()

@R.message(W.prem_upi)
async def f_prem_upi(msg, state):
    try:
        if not is_admin(msg.from_user.id, load()): await state.clear(); return
        val = msg.text.strip()
        d = load()
        d["premium"]["payment"]["upi"] = val if val != "/clear" else ""
        save(d); await state.clear()
        await msg.answer("✅ UPI saved." if val != "/clear" else "🗑 Cleared.",
            reply_markup=prem_pay_edit_kb(d), parse_mode="HTML")
    except Exception as e: log.error(f"prem_upi: {e}"); await state.clear()

@R.message(W.prem_qr)
async def f_prem_qr(msg, state):
    try:
        if not is_admin(msg.from_user.id, load()): await state.clear(); return
        d = load()
        if msg.text and msg.text.strip() == "/clear":
            d["premium"]["payment"]["qr"] = None
            save(d); await state.clear()
            await msg.answer("🗑 QR cleared.", reply_markup=prem_pay_edit_kb(d), parse_mode="HTML"); return
        if msg.photo: file_id = msg.photo[-1].file_id
        elif msg.document: file_id = msg.document.file_id
        else: await msg.answer("❌ Send photo or /clear."); return
        d["premium"]["payment"]["qr"] = file_id
        save(d); await state.clear()
        await msg.answer("✅ QR saved.", reply_markup=prem_pay_edit_kb(d), parse_mode="HTML")
    except Exception as e: log.error(f"prem_qr: {e}"); await state.clear()

@R.message(W.prem_binance)
async def f_prem_binance(msg, state):
    try:
        if not is_admin(msg.from_user.id, load()): await state.clear(); return
        val = msg.text.strip()
        d = load()
        d["premium"]["payment"]["binance"] = val if val != "/clear" else ""
        save(d); await state.clear()
        await msg.answer("✅ Binance saved." if val != "/clear" else "🗑 Cleared.",
            reply_markup=prem_pay_edit_kb(d), parse_mode="HTML")
    except Exception as e: log.error(f"prem_binance: {e}"); await state.clear()

@R.message(W.prem_crypto)
async def f_prem_crypto(msg, state):
    try:
        if not is_admin(msg.from_user.id, load()): await state.clear(); return
        val = msg.text.strip()
        d = load()
        d["premium"]["payment"]["crypto"] = val if val != "/clear" else ""
        save(d); await state.clear()
        await msg.answer("✅ Crypto saved." if val != "/clear" else "🗑 Cleared.",
            reply_markup=prem_pay_edit_kb(d), parse_mode="HTML")
    except Exception as e: log.error(f"prem_crypto: {e}"); await state.clear()

@R.message(W.pay_screenshot)
async def f_pay_screenshot(msg, state):
    try:
        if not (msg.photo or msg.document):
            await msg.answer(f"📸 Please send a screenshot.\n\n<i>/cancel to abort</i>",
                parse_mode="HTML"); return
        fsmd = await state.get_data()
        plan_key = fsmd.get("pay_plan")
        method = fsmd.get("pay_method", "upi")
        if not plan_key:
            await state.clear(); await msg.answer("⚠️ Session expired."); return
        d = load()
        plan = d["premium"]["plans"].get(plan_key)
        if not plan:
            await state.clear(); await msg.answer("⚠️ Plan not found."); return
        file_id = msg.photo[-1].file_id if msg.photo else msg.document.file_id

        existing = next((r for r in d["premium"]["requests"]
                         if r.get("uid") == msg.from_user.id
                         and r.get("status") == "pending"), None)
        if existing:
            await state.clear()
            await msg.answer(
                f"⏳ A request is already pending.\n\n<i>Please wait for admin approval.</i>",
                parse_mode="HTML"); return

        req_id = "req_" + str(int(time.time())) + "_" + str(random.randint(1000, 9999))
        req = {
            "id": req_id, "uid": msg.from_user.id,
            "username": msg.from_user.username or "",
            "name": (msg.from_user.first_name or "User") +
                    (f" {msg.from_user.last_name}" if msg.from_user.last_name else ""),
            "plan": plan_key, "label": plan["label"], "price": plan["price"],
            "method": method, "screenshot": file_id,
            "status": "pending", "created_at": int(time.time()),
        }
        d2 = load()
        d2["premium"].setdefault("requests", []).append(req)
        save(d2); await state.clear()

        await msg.answer(
            f"<b>✅ Screenshot Received</b>\n{line()}\n\n"
            f"📦 Plan     : <b>{plan['label']}</b>\n"
            f"💰 Amount   : <b>₹{plan['price']}</b>\n\n"
            f"Admin will verify your payment shortly.\n"
            f"You'll be notified once approved.",
            parse_mode="HTML")
        await notify_admins_payment(msg.bot, req)
    except Exception as e:
        log.error(f"pay_screenshot: {e}"); await state.clear()

@R.message(W.fj_add)
async def f_fj_add(msg, state):
    try:
        if not is_admin(msg.from_user.id, load()): await state.clear(); return
        uid = msg.from_user.id; text = msg.text.strip()
        fsmd = await state.get_data()
        if fsmd.get("fj_step") == "awaiting_title":
            link = fsmd.get("fj_link", ""); chat_id = fsmd.get("fj_chat_id", "")
            d = load(); fj = d.setdefault("force_join", [])
            if not any(str(c["id"]) == str(chat_id) for c in fj):
                fj.append({"id": chat_id, "title": text, "link": link})
            save(d); await state.clear()
            await msg.answer(f"✅ Added: {text}",
                reply_markup=fj_menu_kb(uid, load()), parse_mode="HTML"); return
        if fsmd.get("fj_step") == "awaiting_chat_id":
            if text.lstrip("-").isdigit(): chat_id = int(text)
            elif text.startswith("@"): chat_id = text
            else:
                await msg.answer("❌ Send <code>-1001234567890</code> or <code>@username</code>",
                    parse_mode="HTML"); return
            await state.update_data(fj_step="awaiting_title", fj_chat_id=chat_id)
            await msg.answer(f"✅ Chat ID: <code>{chat_id}</code>\n\nNow send display name:",
                parse_mode="HTML", reply_markup=kb([("❌ Cancel","fj:menu")])); return
        tm = re.search(r"t\.me/([+\w]+)", text)
        if tm: link = f"https://t.me/{tm.group(1)}"
        elif text.startswith("@"): link = f"https://t.me/{text.lstrip('@')}"
        elif text.startswith("http"): link = text
        elif text.lstrip("-").isdigit():
            chat_id = int(text)
            await state.update_data(fj_step="awaiting_title", fj_link="", fj_chat_id=chat_id)
            await msg.answer(f"✅ Chat ID: <code>{chat_id}</code>\n\nNow send display name:",
                parse_mode="HTML", reply_markup=kb([("❌ Cancel","fj:menu")])); return
        else: link = f"https://t.me/{text.lstrip('@')}"
        await state.update_data(fj_step="awaiting_chat_id", fj_link=link)
        await msg.answer(f"✅ Link saved.\n\nNow send Chat ID:",
            parse_mode="HTML", reply_markup=kb([("❌ Cancel","fj:menu")]))
    except Exception as e: log.error(f"fj_add: {e}"); await state.clear()

# ══════════════════════════════════════════════
#  SEND STARTER
# ══════════════════════════════════════════════
async def _begin_send(bot, uid, state, msg):
    fsmd = await state.get_data()
    to = fsmd.get("test_to", ""); sms_text = fsmd.get("test_msg", "")
    await state.clear()
    d = load()
    if not to or not sms_text:
        await msg.answer("❌ Session expired.", reply_markup=main_menu(uid, d)); return
    existing = _active_jobs.get(uid)
    if existing and existing.state == "running":
        await msg.answer(f"⚠️ A job is already running.\n\n🛑 Stop it first.", parse_mode="HTML"); return
    if not d.get("firebase_pool"):
        await msg.answer(f"❌ Service unavailable. Try again later.",
            reply_markup=main_menu(uid, d), parse_mode="HTML"); return
    prog = await msg.answer("🔍 Preparing…")
    job = SendJob(uid, to, sms_text)
    job.progress_msg = prog
    _active_jobs[uid] = job
    job.task = asyncio.create_task(_run_job(bot, job))

@R.callback_query(F.data.startswith("stop:"))
async def cb_stop(cq):
    try:
        target_uid = int(cq.data.split(":")[1])
        if cq.from_user.id != target_uid:
            await cq.answer("🚫 Not your job.", show_alert=True); return
        job = _active_jobs.get(target_uid)
        if not job or job.state != "running":
            await cq.answer("Already stopped.", show_alert=True); return
        job.cancel_event.set()
        if job.updater and not job.updater.done(): job.updater.cancel()
        await cq.answer("🛑 Stopping…")
    except Exception as e:
        log.error(f"cb_stop: {e}"); await cq.answer("❌")

# ══════════════════════════════════════════════
#  PREMIUM CALLBACKS — user-facing
# ══════════════════════════════════════════════
@R.callback_query(F.data == "prem:menu")
async def cb_prem_menu(cq, state):
    try:
        d = load(); uid = cq.from_user.id
        if is_admin(uid, d):
            prem = d["premium"]
            on = prem.get("enabled")
            reqs = len([r for r in prem.get("requests", []) if r.get("status") == "pending"])
            await sedit(cq,
                f"<b>💎 Premium Control</b>\n{line()}\n\n"
                f"Status  : {'🟢 ON' if on else '🔴 OFF'}\n"
                f"Users   : <code>{len(prem.get('users',{}))}</code>\n"
                f"Pending : <code>{reqs}</code>",
                kb([("🛡  Open Panel","adm:prem"), ("🏠  Home","home")])); return
        if not premium_mode_on(d):
            await sedit(cq,
                f"<b>💎 Premium</b>\n{line()}\n\nBot is currently <b>FREE</b> 🎉\n\nEnjoy!",
                kb([("🏠  Home","home")])); return
        if is_premium_user(uid, d):
            pu = d["premium"]["users"].get(str(uid), {})
            exp = pu.get("expires")
            exp_txt = "Lifetime" if exp is None else fmt_date(exp)
            await sedit(cq,
                f"<b>💎 Your Subscription</b>\n{line()}\n\n"
                f"Plan     : <b>{pu.get('label','?')}</b>\n"
                f"Expires  : <code>{exp_txt}</code>",
                kb([("🏠  Home","home")])); return
        await sedit(cq, premium_landing_text(d), premium_cta_kb())
    except Exception as e: log.error(f"prem:menu: {e}")

@R.callback_query(F.data == "prem:plans")
async def cb_prem_plans(cq, state):
    try:
        d = load()
        await sedit(cq, f"<b>💎 Choose Your Plan</b>\n{line()}\n\nAll plans include full access:",
            plans_kb(d))
    except Exception as e: log.error(f"prem:plans: {e}")

@R.callback_query(F.data.startswith("prem:plan:"))
async def cb_prem_plan_detail(cq, state):
    try:
        plan_key = cq.data.split("prem:plan:", 1)[1]
        d = load()
        plan = d["premium"]["plans"].get(plan_key)
        if not plan: await cq.answer("❌", show_alert=True); return
        pay = d["premium"]["payment"]
        if not is_payment_set(pay):
            await cq.answer("⚠️ Payment methods not configured.", show_alert=True); return
        await sedit(cq,
            f"<b>💎 {plan['label']}  ·  ₹{plan['price']}</b>\n{line()}\n\n"
            f"Select your payment method:",
            methods_kb(plan_key, pay))
    except Exception as e: log.error(f"prem:plan_detail: {e}")

@R.callback_query(F.data.startswith("prem:m:"))
async def cb_prem_method(cq, state):
    try:
        parts = cq.data.split(":")
        if len(parts) < 4: await cq.answer("❌"); return
        plan_key, method = parts[2], parts[3]
        d = load()
        plan = d["premium"]["plans"].get(plan_key)
        if not plan: await cq.answer("❌", show_alert=True); return
        pay = d["premium"]["payment"]
        value = pay.get(method, "")
        if not value and method != "qr":
            await cq.answer("❌ Not configured", show_alert=True); return

        if method == "upi":
            await sedit(cq,
                f"<b>🏦 UPI PAYMENT</b>\n{line()}\n\n"
                f"📦 Plan     : <b>{plan['label']}</b>\n"
                f"💰 Amount   : <b>₹{plan['price']}</b>\n\n"
                f"<b>Send to this UPI ID</b>\n"
                f"<code>{value}</code>\n\n"
                f"<i>Tap the ID above to copy</i>\n\n"
                f"{line()}\n"
                f"✅ Any UPI app works — GPay · PhonePe · Paytm",
                pay_confirm_kb(plan_key, method))
        elif method == "binance":
            await sedit(cq,
                f"<b>🟡 BINANCE PAY</b>\n{line()}\n\n"
                f"📦 Plan     : <b>{plan['label']}</b>\n"
                f"💰 Amount   : <b>₹{plan['price']}</b>\n\n"
                f"<b>Send to this Binance ID</b>\n"
                f"<code>{value}</code>\n\n"
                f"<i>Tap the ID above to copy</i>\n\n"
                f"{line()}\n"
                f"✅ Use Binance app to transfer",
                pay_confirm_kb(plan_key, method))
        elif method == "crypto":
            await sedit(cq,
                f"<b>🪙 CRYPTO PAYMENT</b>\n{line()}\n\n"
                f"📦 Plan     : <b>{plan['label']}</b>\n"
                f"💰 Amount   : <b>₹{plan['price']}</b>\n\n"
                f"<b>Send to this wallet</b>\n"
                f"<code>{value}</code>\n\n"
                f"<i>Tap the address above to copy</i>\n\n"
                f"{line()}\n"
                f"✅ USDT · BTC · ETH accepted",
                pay_confirm_kb(plan_key, method))
        elif method == "qr":
            if not value:
                await cq.answer("❌ QR not set", show_alert=True); return
            await sedit(cq,
                f"<b>📱 QR PAYMENT</b>\n{line()}\n\n"
                f"📦 Plan     : <b>{plan['label']}</b>\n"
                f"💰 Amount   : <b>₹{plan['price']}</b>\n\n"
                f"Scan the QR code below to pay.\n"
                f"Works with any UPI app ✅",
                pay_confirm_kb(plan_key, method))
            try:
                await cq.message.answer_photo(
                    value,
                    caption=f"📱  <b>Scan & Pay  ·  ₹{plan['price']}</b>\n"
                            f"<i>GPay · PhonePe · Paytm · Any UPI app</i>",
                    parse_mode="HTML")
            except Exception as e:
                log.error(f"qr send: {e}")
    except Exception as e: log.error(f"prem:method: {e}")

@R.callback_query(F.data.startswith("prem:pay:"))
async def cb_prem_pay(cq, state):
    try:
        parts = cq.data.split(":")
        if len(parts) < 4: await cq.answer("❌"); return
        plan_key, method = parts[2], parts[3]
        d = load()
        plan = d["premium"]["plans"].get(plan_key)
        if not plan: await cq.answer("❌", show_alert=True); return
        await state.clear()
        await state.update_data(pay_plan=plan_key, pay_method=method)
        await state.set_state(W.pay_screenshot)
        await sedit(cq,
            f"<b>📸 Send Payment Screenshot</b>\n{line()}\n\n"
            f"📦 Plan     : <b>{plan['label']}</b>\n"
            f"💰 Amount   : <b>₹{plan['price']}</b>\n\n"
            f"Send a clear screenshot of your payment.\n"
            f"Admin will verify it manually.\n\n"
            f"<i>/cancel to abort</i>",
            kb([("❌  Cancel","home")]))
    except Exception as e: log.error(f"prem:pay: {e}")

# ── Admin: Add premium ─────────────────────────
@R.callback_query(F.data == "prem:add")
async def cb_prem_add(cq, state):
    try:
        d = load(); uid = cq.from_user.id
        if not is_admin(uid, d): await cq.answer("🚫", show_alert=True); return
        await state.clear()
        await state.set_state(W.prem_add_id)
        await sedit(cq,
            f"<b>💎 Add Premium User</b>\n{line()}\n\nSend user's Telegram ID:",
            kb([("❌  Cancel","adm:prem")]))
    except Exception as e: log.error(f"prem:add: {e}")

@R.callback_query(F.data.startswith("prem:addp:"))
async def cb_prem_addp(cq, state):
    try:
        d = load(); uid = cq.from_user.id
        if not is_admin(uid, d): await cq.answer("🚫", show_alert=True); return
        parts = cq.data.split(":")
        if len(parts) < 4: await cq.answer("❌"); return
        try: target_uid = int(parts[2])
        except: await cq.answer("❌ Invalid UID", show_alert=True); return
        plan_key = parts[3]
        plan = d["premium"]["plans"].get(plan_key)
        if not plan: await cq.answer("❌ Plan missing", show_alert=True); return

        prem_users = d["premium"].setdefault("users", {})
        existing = prem_users.get(str(target_uid), {})
        now = time.time()
        if plan["days"] is None: new_exp = None
        else:
            base = now
            if existing.get("expires") and existing["expires"] > now:
                base = existing["expires"]
            new_exp = base + plan["days"] * 86400

        prem_users[str(target_uid)] = {
            "plan": plan_key, "label": plan["label"],
            "expires": new_exp, "added_at": int(now), "added_by": uid,
            "source": "admin_grant",
        }
        save(d)
        await cq.answer("✅ Premium granted!", show_alert=True)

        exp_txt = "Lifetime" if new_exp is None else fmt_date(new_exp)
        try:
            await cq.bot.send_message(target_uid,
                f"<b>🎉 Premium Activated</b>\n{line()}\n\n"
                f"💎 Plan     : <b>{plan['label']}</b>\n"
                f"📅 Expires  : <code>{exp_txt}</code>\n\n"
                f"<i>Enjoy full access 🚀</i>",
                parse_mode="HTML")
        except Exception as e: log.error(f"notify premium grant: {e}")

        await sedit(cq,
            f"<b>✅ Premium Added</b>\n{line()}\n\n"
            f"👤 User     : <code>{target_uid}</code>\n"
            f"💎 Plan     : <b>{plan['label']}</b>\n"
            f"📅 Expires  : <code>{exp_txt}</code>",
            kb([("👥  View Users","prem:users"), ("◀️  Back","adm:prem")]))
    except Exception as e:
        log.error(f"prem:addp: {e}"); await cq.answer("❌")

# ── Admin: Remove premium ──────────────────────
@R.callback_query(F.data.startswith("prem:rmok:"))
async def cb_prem_rmok(cq, state):
    try:
        d = load(); uid = cq.from_user.id
        if not is_admin(uid, d): await cq.answer("🚫", show_alert=True); return
        target = cq.data.split("prem:rmok:", 1)[1]
        d2 = load()
        if target in d2["premium"]["users"]:
            del d2["premium"]["users"][target]
            save(d2)
            await cq.answer("🗑 Removed.", show_alert=True)
            try:
                await cq.bot.send_message(int(target),
                    f"<b>⚠️ Premium Removed</b>\n{line()}\n\n"
                    f"Your premium access has been removed.\n"
                    f"<i>Contact admin for details.</i>",
                    parse_mode="HTML")
            except: pass
        else:
            await cq.answer("❌ Not found", show_alert=True)

        d3 = load()
        users = d3["premium"].get("users", {})
        if not users:
            await sedit(cq, f"<b>👥 Users</b>\n{line()}\n\nNo premium users yet.",
                kb([("◀️  Back","adm:prem")]))
        else:
            await sedit(cq,
                f"<b>👥 Premium Users ({len(users)})</b>\n{line()}\n\nTap 🗑 to remove:",
                prem_users_kb(users))
    except Exception as e:
        log.error(f"prem:rmok: {e}"); await cq.answer("❌")

@R.callback_query(F.data.startswith("prem:rm:"))
async def cb_prem_rm(cq, state):
    try:
        d = load(); uid = cq.from_user.id
        if not is_admin(uid, d): await cq.answer("🚫", show_alert=True); return
        target = cq.data.split("prem:rm:", 1)[1]
        pu = d["premium"]["users"].get(target)
        if not pu:
            await cq.answer("❌ Not premium", show_alert=True); return
        exp_txt = "Lifetime" if pu.get("expires") is None else fmt_date(pu.get("expires", 0))
        await sedit(cq,
            f"<b>⚠️ Remove Premium?</b>\n{line()}\n\n"
            f"🆔 <code>{target}</code>\n"
            f"📦 {pu.get('label','?')}\n"
            f"📅 {exp_txt}\n\n"
            f"<i>User will lose premium access.</i>",
            kb([("🗑  Yes, Remove", f"prem:rmok:{target}"),
                ("❌  Cancel", "prem:users")]))
    except Exception as e: log.error(f"prem:rm: {e}")

# ── Payment approve / reject ───────────────────
@R.callback_query(F.data.startswith("prem:approve:"))
async def cb_prem_approve(cq, state):
    try:
        d = load(); uid = cq.from_user.id
        if not is_admin(uid, d): await cq.answer("🚫", show_alert=True); return
        req_id = cq.data.split("prem:approve:", 1)[1]
        d2 = load()
        req = next((r for r in d2["premium"].get("requests", []) if r["id"] == req_id), None)
        if not req: await cq.answer("❌ Not found", show_alert=True); return
        plan_key = req["plan"]
        plan = d2["premium"]["plans"].get(plan_key)
        if not plan: await cq.answer("❌ Plan missing", show_alert=True); return
        user_uid = req["uid"]
        prem_users = d2["premium"].setdefault("users", {})
        existing = prem_users.get(str(user_uid), {})
        now = time.time()
        if plan["days"] is None: new_exp = None
        else:
            base = now
            if existing.get("expires") and existing["expires"] > now:
                base = existing["expires"]
            new_exp = base + plan["days"] * 86400
        prem_users[str(user_uid)] = {
            "plan": plan_key, "label": plan["label"],
            "expires": new_exp, "added_at": int(now), "added_by": uid,
            "source": "payment",
        }
        d2["premium"]["requests"] = [r for r in d2["premium"]["requests"] if r["id"] != req_id]
        req["status"] = "approved"; req["resolved_at"] = int(now); req["resolved_by"] = uid
        d2["premium"].setdefault("history", []).append(req)
        save(d2)
        await cq.answer("✅ Approved!")
        try:
            await cq.message.edit_caption(
                caption=(cq.message.caption or "") + f"\n\n✅  <b>APPROVED</b>",
                parse_mode="HTML")
        except: pass
        try:
            exp_txt = "Lifetime" if new_exp is None else fmt_date(new_exp)
            await cq.bot.send_message(user_uid,
                f"<b>🎉 Payment Verified</b>\n{line()}\n\n"
                f"💎 Plan     : <b>{plan['label']}</b>\n"
                f"📅 Expires  : <code>{exp_txt}</code>\n\n"
                f"<i>Full access unlocked 🚀</i>",
                parse_mode="HTML")
        except Exception as e: log.error(f"notify: {e}")

        d3 = load()
        reqs = [r for r in d3["premium"].get("requests", []) if r.get("status") == "pending"]
        if reqs:
            await _render_requests_list(cq, d3)
        else:
            await sedit(cq, f"<b>✅ All Clear</b>\n{line()}\n\nNo pending requests.",
                kb([("◀️  Back","adm:prem")]))
    except Exception as e:
        log.error(f"prem_approve: {e}"); await cq.answer("❌")

@R.callback_query(F.data.startswith("prem:reject:"))
async def cb_prem_reject(cq, state):
    try:
        d = load(); uid = cq.from_user.id
        if not is_admin(uid, d): await cq.answer("🚫", show_alert=True); return
        req_id = cq.data.split("prem:reject:", 1)[1]
        d2 = load()
        req = next((r for r in d2["premium"].get("requests", []) if r["id"] == req_id), None)
        if not req: await cq.answer("❌", show_alert=True); return
        now = time.time()
        d2["premium"]["requests"] = [r for r in d2["premium"]["requests"] if r["id"] != req_id]
        req["status"] = "rejected"; req["resolved_at"] = int(now); req["resolved_by"] = uid
        d2["premium"].setdefault("history", []).append(req)
        save(d2)
        await cq.answer("❌ Rejected!")
        try:
            await cq.message.edit_caption(
                caption=(cq.message.caption or "") + f"\n\n❌  <b>REJECTED</b>",
                parse_mode="HTML")
        except: pass
        try:
            await cq.bot.send_message(req["uid"],
                f"<b>❌ Payment Rejected</b>\n{line()}\n\n"
                f"Could not verify your payment.\n"
                f"Contact admin or try again.",
                parse_mode="HTML")
        except: pass
        d3 = load()
        reqs = [r for r in d3["premium"].get("requests", []) if r.get("status") == "pending"]
        if reqs:
            await _render_requests_list(cq, d3)
        else:
            await sedit(cq, f"<b>✅ All Clear</b>\n{line()}\n\nNo pending requests.",
                kb([("◀️  Back","adm:prem")]))
    except Exception as e:
        log.error(f"prem_reject: {e}"); await cq.answer("❌")

async def _render_requests_list(cq, d):
    reqs = [r for r in d["premium"].get("requests", []) if r.get("status") == "pending"]
    if not reqs:
        await sedit(cq, f"<b>📥 No Pending</b>\n{line()}\n\nAll clear.",
            kb([("◀️  Back","adm:prem")]))
        return
    await sedit(cq,
        f"<b>📥 Pending Requests ({len(reqs)})</b>\n{line()}\n\nTap to view:",
        prem_requests_kb(reqs))

@R.callback_query(F.data.startswith("prem:view:"))
async def cb_prem_view(cq, state):
    try:
        d = load(); uid = cq.from_user.id
        if not is_admin(uid, d): await cq.answer("🚫", show_alert=True); return
        req_id = cq.data.split("prem:view:", 1)[1]
        req = next((r for r in d["premium"].get("requests", []) if r["id"] == req_id), None)
        if not req: await cq.answer("❌", show_alert=True); return
        method_lbl = {"upi":"🏦 UPI","qr":"📱 QR","binance":"🟡 Binance","crypto":"🪙 Crypto"}.get(req.get("method",""), "—")
        caption = (
            f"<b>💎 Payment Request</b>\n{line()}\n\n"
            f"👤 {req['name']}\n"
            f"🆔 <code>{req['uid']}</code>\n"
            f"📦 {req['label']}  ·  ₹{req['price']}\n"
            f"💳 {method_lbl}\n"
            f"🕐 {fmt_dt(req['created_at'])}"
        )
        btns = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅  Approve", callback_data=f"prem:approve:{req_id}"),
             InlineKeyboardButton(text="❌  Reject",  callback_data=f"prem:reject:{req_id}")],
            [InlineKeyboardButton(text="◀️  Back", callback_data="prem:reqs")],
        ])
        try:
            await cq.message.answer_photo(req["screenshot"],
                caption=caption, reply_markup=btns, parse_mode="HTML")
            await cq.answer()
        except Exception as e:
            log.error(f"view: {e}"); await cq.answer("❌")
    except Exception as e: log.error(f"prem:view: {e}")

# ══════════════════════════════════════════════
#  GENERAL CALLBACKS  (catch-all — MUST be last)
# ══════════════════════════════════════════════
@R.callback_query()
async def cb(cq, state):
    try:
        d = load(); uid = cq.from_user.id; c = cq.data
        if is_banned(uid, d):
            await cq.answer("🚫 Banned", show_alert=True); return

        # ── Public: home / force join check / help / about
        if c in ("home","fj:check"):
            await state.clear()
            ok, not_joined = await check_force_join(cq.bot, uid, d)
            if not ok:
                await sedit(cq, "📢  <b>Join Required</b>", _fj_keyboard(not_joined))
                if c == "fj:check": await cq.answer("❌ Still missing", show_alert=True)
                return
            if not can_use(uid, d):
                if premium_mode_on(d):
                    await sedit(cq, premium_landing_text(d), premium_cta_kb())
                else:
                    await sedit(cq, "🚫 Access denied", kb([("🏠 Home","home")]))
                if c == "fj:check": await cq.answer("✅ Verified", show_alert=True)
                return
            u = usr(uid, d)
            await sedit(cq, home_text(uid, d, u), main_menu(uid, d))
            if c == "fj:check": await cq.answer("✅ Verified", show_alert=True)
            return

        if c == "help:show":
            await sedit(cq, HELP_TEXT, kb([("◀️  Back","home")])); return
        if c == "about:show":
            await sedit(cq, ABOUT_TEXT, kb([("◀️  Back","home")])); return

        # ── Access gate for everything else
        if not can_use(uid, d):
            if premium_mode_on(d):
                await sedit(cq, premium_landing_text(d), premium_cta_kb())
            else:
                await cq.answer("🚫", show_alert=True)
            return

        u = usr(uid, d)

        if c == "test:go":
            existing = _active_jobs.get(uid)
            if existing and existing.state == "running":
                await cq.answer("⚠️ Job already running.", show_alert=True); return
            if not d.get("firebase_pool"):
                await cq.answer("❌ Service unavailable.", show_alert=True); return
            await state.set_state(W.test_to)
            await sedit(cq,
                f"<b>🧪 Test SMS</b>\n{line()}\n\n"
                f"Send target number:\n"
                f"<code>+919876543210</code>\n"
                f"<code>+91 98765 43210</code>\n"
                f"<code>9876543210</code>",
                kb([("❌  Cancel","home")]))

        elif c == "dash:show":
            d2 = load(); u2 = usr(uid, d2)
            s = u2.get("stats", {})
            sent = s.get("sent", 0); fail = s.get("failed", 0)
            total = sent + fail
            bar = pbar_solid(sent, total) if total else "░"*14
            rate = pct(sent, total) if total else "0%"
            badge = role_badge(uid, d2)
            live = ""
            job = _active_jobs.get(uid)
            if job and job.state == "running":
                js = job.snapshot()
                live = (f"\n{line()}\n🚀  <code>{js['done']}/{js['total']}</code>  "
                        f"✅<code>{js['sent']}</code>  ❌<code>{js['failed']}</code>")
            await sedit(cq,
                f"<b>📊 Dashboard</b>\n{line()}\n\n"
                f"{badge} <b>{role_label(uid, d2)}</b>\n\n"
                f"📨 Total  : <code>{total}</code>\n{line()}\n"
                f"<code>{bar}</code>  <b>{rate}</b>\n{line()}\n\n"
                f"✅ <code>{sent}</code>   ❌ <code>{fail}</code>   🕐 <code>{str(s.get('last','—'))[:10]}</code>"
                f"{live}",
                kb([("🔄  Refresh","dash:show"), ("🏠  Home","home")]))

        # ── Admin Panel ────────────────────────────
        elif c == "adm:menu":
            if not is_admin(uid, d): await cq.answer("🚫", show_alert=True); return
            await sedit(cq,
                f"<b>🛡 Admin Panel</b>\n{line()}\n\n<i>Full control</i>",
                adm_menu_kb(uid, d))

        elif c == "adm:prem":
            if not is_admin(uid, d): await cq.answer("🚫", show_alert=True); return
            prem = d.get("premium", {})
            on = prem.get("enabled")
            reqs = len([r for r in prem.get("requests", []) if r.get("status") == "pending"])
            users_n = len(prem.get("users", {}))
            plans = prem.get("plans", {})
            pay = prem.get("payment", {})
            plans_txt = "\n".join([f"   {p['label']:10s} ₹{p['price']}" for p in plans.values()])
            pay_txt = (
                f"   UPI     {'✅' if pay.get('upi') else '❌'}\n"
                f"   QR      {'✅' if pay.get('qr') else '❌'}\n"
                f"   Binance {'✅' if pay.get('binance') else '❌'}\n"
                f"   Crypto  {'✅' if pay.get('crypto') else '❌'}"
            )
            await sedit(cq,
                f"<b>💎 Premium Control</b>\n{line()}\n\n"
                f"Status   : {'🟢 ON' if on else '🔴 OFF'}\n"
                f"Requests : <code>{reqs}</code>\n"
                f"Users    : <code>{users_n}</code>\n\n"
                f"<b>📦 Plans</b>\n{plans_txt}\n\n"
                f"<b>💳 Payment</b>\n{pay_txt}",
                prem_admin_kb(d))

        elif c == "prem:toggle":
            if not is_admin(uid, d): await cq.answer("🚫", show_alert=True); return
            d2 = load()
            d2["premium"]["enabled"] = not d2["premium"].get("enabled", False)
            save(d2)
            on = d2["premium"]["enabled"]
            await cq.answer(f"Premium: {'🟢 ON' if on else '🔴 OFF'}", show_alert=True)
            await sedit(cq, f"<b>💎 Premium</b>\n{line()}\n\n{'🟢 ON' if on else '🔴 OFF'}",
                prem_admin_kb(d2))

        elif c == "prem:editplans":
            if not is_admin(uid, d): await cq.answer("🚫", show_alert=True); return
            await sedit(cq,
                f"<b>📝 Edit Plans</b>\n{line()}\n\nTap to edit:",
                prem_plans_edit_kb(d))

        elif c.startswith("prem:editprice:"):
            if not is_admin(uid, d): await cq.answer("🚫", show_alert=True); return
            plan_key = c.split("prem:editprice:", 1)[1]
            plan = d["premium"]["plans"].get(plan_key)
            if not plan: await cq.answer("❌", show_alert=True); return
            await state.clear()
            await state.update_data(edit_plan=plan_key)
            await state.set_state(W.prem_price)
            await sedit(cq,
                f"<b>✏️ {plan['label']}</b>\n{line()}\n\n"
                f"Current: ₹{plan['price']}\n\nEnter new price:",
                kb([("❌  Cancel","prem:editplans")]))

        elif c == "prem:editpay":
            if not is_admin(uid, d): await cq.answer("🚫", show_alert=True); return
            await sedit(cq,
                f"<b>💳 Payment Methods</b>\n{line()}\n\n"
                f"Empty methods stay hidden from users.\n"
                f"Tap any to set / change / clear.",
                prem_pay_edit_kb(d))

        elif c == "prem:setupi":
            if not is_admin(uid, d): await cq.answer("🚫", show_alert=True); return
            await state.clear()
            await state.set_state(W.prem_upi)
            await sedit(cq,
                f"<b>🏦 Set UPI ID</b>\n{line()}\n\n"
                f"Example: <code>example@ybl</code>\n\n"
                f"Clear: <code>/clear</code>\n"
                f"Cancel: button below",
                kb([("❌  Cancel","prem:editpay")]))

        elif c == "prem:setqr":
            if not is_admin(uid, d): await cq.answer("🚫", show_alert=True); return
            await state.clear()
            await state.set_state(W.prem_qr)
            await sedit(cq,
                f"<b>📱 Set QR Code</b>\n{line()}\n\n"
                f"Send QR code photo.\n\n"
                f"Clear: <code>/clear</code>",
                kb([("❌  Cancel","prem:editpay")]))

        elif c == "prem:setbinance":
            if not is_admin(uid, d): await cq.answer("🚫", show_alert=True); return
            await state.clear()
            await state.set_state(W.prem_binance)
            await sedit(cq,
                f"<b>🟡 Set Binance ID</b>\n{line()}\n\n"
                f"Send Binance Pay ID / UID.\n\n"
                f"Clear: <code>/clear</code>",
                kb([("❌  Cancel","prem:editpay")]))

        elif c == "prem:setcrypto":
            if not is_admin(uid, d): await cq.answer("🚫", show_alert=True); return
            await state.clear()
            await state.set_state(W.prem_crypto)
            await sedit(cq,
                f"<b>🪙 Set Crypto Wallet</b>\n{line()}\n\n"
                f"Send wallet address.\n\n"
                f"Clear: <code>/clear</code>",
                kb([("❌  Cancel","prem:editpay")]))

        elif c == "prem:reqs":
            if not is_admin(uid, d): await cq.answer("🚫", show_alert=True); return
            await _render_requests_list(cq, d)

        elif c == "prem:users":
            if not is_admin(uid, d): await cq.answer("🚫", show_alert=True); return
            users = d["premium"].get("users", {})
            if not users:
                await sedit(cq, f"<b>👥 Users</b>\n{line()}\n\nNo premium users.",
                    kb([("◀️  Back","adm:prem")])); return
            await sedit(cq,
                f"<b>👥 Premium Users ({len(users)})</b>\n{line()}\n\nTap 🗑 to remove:",
                prem_users_kb(users))

        elif c == "prem:hist":
            if not is_admin(uid, d): await cq.answer("🚫", show_alert=True); return
            hist = d["premium"].get("history", [])
            if not hist:
                await sedit(cq, f"<b>📜 History</b>\n{line()}\n\nEmpty.",
                    kb([("◀️  Back","adm:prem")])); return
            lines = [f"<b>📜 History ({len(hist)})</b>", line(), ""]
            for r in hist[-25:][::-1]:
                emoji = "✅" if r["status"] == "approved" else "❌"
                lines.append(f"{emoji} <b>{r['name'][:16]}</b>  {r['label']}  ₹{r['price']}")
            await sedit(cq, "\n".join(lines), kb([("◀️  Back","adm:prem")]))

        # ── Firebase Manager ───────────────────────
        elif c == "fbm:menu":
            if not is_admin(uid, d): await cq.answer("🚫", show_alert=True); return
            pool = d.get("firebase_pool", [])
            await sedit(cq,
                f"<b>🔥 Firebase Manager</b>\n{line()}\n\n"
                f"📦 Pool: <code>{len(pool)}</code>\n\n"
                f"<i>👑 Owner · 🛡 Admin · 👤 User</i>",
                fb_manager_kb())

        elif c.startswith("fbm:pg:"):
            if not is_admin(uid, d): await cq.answer("🚫", show_alert=True); return
            page = int(c.split(":")[-1])
            await sedit(cq, f"<i>Pool: {len(d.get('firebase_pool',[]))} · Page {page+1}</i>",
                fb_manager_kb(page))

        elif c.startswith("fbr:del:"):
            if not is_admin(uid, d): await cq.answer("🚫", show_alert=True); return
            fid = c.split("fbr:del:", 1)[1]
            d2 = load()
            before = len(d2["firebase_pool"])
            d2["firebase_pool"] = [f for f in d2["firebase_pool"] if f["id"] != fid]
            removed = before - len(d2["firebase_pool"])
            save(d2)
            await cq.answer(f"🗑 {removed} removed")
            await sedit(cq, f"<b>🔥 Pool</b>\n{line()}\n\n<code>{len(d2['firebase_pool'])}</code>",
                fb_manager_kb())

        elif c == "fbr:reset:confirm":
            if not is_admin(uid, d): await cq.answer("🚫", show_alert=True); return
            n = len(d.get("firebase_pool", []))
            await sedit(cq,
                f"<b>⚠️ Reset Pool?</b>\n{line()}\n\n"
                f"<b>{n}</b> entries will be removed.\n<i>Cannot be undone.</i>",
                kb([("💥  Yes","fbr:reset:yes"), ("❌  Cancel","fbm:menu")]))

        elif c == "fbr:reset:yes":
            if not is_admin(uid, d): await cq.answer("🚫", show_alert=True); return
            for j in list(_active_jobs.values()):
                if j.state == "running": j.cancel_event.set()
            d2 = load(); n = len(d2.get("firebase_pool", []))
            d2["firebase_pool"] = []; save(d2)
            await cq.answer(f"🗑 {n} removed")
            await sedit(cq, f"<b>✅ Reset</b>\n{line()}\n\n<b>{n}</b> removed.",
                kb([("➕  Add Single","wiz:start"), ("◀️  Admin","adm:menu")]))

        elif c == "wiz:start":
            if not is_admin(uid, d): await cq.answer("🚫", show_alert=True); return
            await state.clear()
            await state.set_state(W.fb_url)
            await sedit(cq,
                f"<b>🧙 Add Firebase</b>\n{line()}\n\n"
                f"Send Firebase URL:\n"
                f"<i>https://xxx.firebaseio.com</i>\n\n"
                f"💡 Direct add — just send the URL",
                kb([("❌  Cancel","fbm:menu")]))

        elif c == "fbb:start":
            if not is_admin(uid, d): await cq.answer("🚫", show_alert=True); return
            await state.clear()
            await state.set_state(W.fb_bulk)
            await sedit(cq,
                f"<b>📥 Add Firebase (Bulk)</b>\n{line()}\n\n"
                f"Send one URL per line:\n\n"
                f"<code>https://xxx.firebaseio.com\n"
                f"https://yyy.firebaseio.com</code>\n\n"
                f"<i>Max {BULK_ADD_LIMIT} · Auto-dedupe · Auto-verify 🔍</i>",
                kb([("❌  Cancel","fbm:menu")]))

        elif c == "adm:users":
            if not is_admin(uid, d): await cq.answer("🚫", show_alert=True); return
            lines = [f"<b>👥 Users</b>", line(), ""]
            for k, v in list(d.get("users", {}).items())[:40]:
                s = v.get("stats", {})
                lines.append(f"<code>{k}</code>  ✅{s.get('sent',0)}  ❌{s.get('failed',0)}")
            await sedit(cq, "\n".join(lines) or "No users.", kb([("◀️  Back","adm:menu")]))

        elif c == "adm:adduser":
            if not is_admin(uid, d): await cq.answer("🚫", show_alert=True); return
            await state.set_state(W.usr_add_id)
            await sedit(cq, f"<b>👤 Add User</b>\n{line()}\n\nSend user ID:",
                kb([("❌  Cancel","adm:menu")]))

        elif c.startswith("tacc:"):
            val = c.split(":")[1]
            fsmd = await state.get_data(); uid2 = fsmd.get("new_uid")
            if not uid2: await cq.answer("Session expired", show_alert=True); return
            if val == "custom":
                await state.set_state(W.usr_add_exp)
                await sedit(cq, "📅 Send DD/MM/YYYY:", kb([("❌  Cancel","adm:menu")])); return
            secs = int(val); exp = None if secs == 0 else time.time() + secs
            d2 = load()
            d2.setdefault("timed_users", {})[str(uid2)] = {
                "expires": exp, "added_by": uid, "added_at": int(time.time())}
            save(d2); await state.clear()
            label = "Permanent" if secs == 0 else f"{secs//3600}h"
            await sedit(cq, f"✅ <code>{uid2}</code> — {label}", adm_menu_kb(uid, d2))
            try:
                m2 = "♾ Permanent" if secs == 0 else f"⏱ {secs//3600}h"
                await cq.bot.send_message(uid2, f"✅ Access: <b>{m2}</b>. /start",
                    parse_mode="HTML")
            except: pass

        elif c == "adm:stats":
            if not is_admin(uid, d): await cq.answer("🚫", show_alert=True); return
            users = d.get("users", {})
            ts = tf = 0
            for v in users.values():
                s = v.get("stats", {})
                ts += s.get("sent", 0); tf += s.get("failed", 0)
            bar = pbar_solid(ts, ts+tf) if ts+tf else "░"*14
            pool_n = len(d.get("firebase_pool", []))
            active = len([j for j in _active_jobs.values() if j.state == "running"])
            prem_users = len(d["premium"].get("users", {}))
            await sedit(cq,
                f"<b>📊 Global Stats</b>\n{line()}\n\n"
                f"<code>{bar}</code>  <b>{pct(ts,ts+tf)}</b>\n{line()}\n\n"
                f"👥 Users    : <code>{len(users)}</code>\n"
                f"💎 Premium  : <code>{prem_users}</code>\n"
                f"🔥 Pool     : <code>{pool_n}</code>\n"
                f"✅ Sent     : <code>{ts}</code>\n"
                f"❌ Failed   : <code>{tf}</code>\n"
                f"🚀 Active   : <code>{active}</code>",
                kb([("◀️  Back","adm:menu")]))

        elif c == "adm:resetdash":
            if not is_admin(uid, d): await cq.answer("🚫", show_alert=True); return
            await sedit(cq,
                f"<b>⚠️ Reset Stats?</b>\n{line()}\n\nAll users' counters will reset.",
                kb([("💥  Yes","adm:resetdash:yes"), ("❌  Cancel","adm:menu")]))

        elif c == "adm:resetdash:yes":
            if not is_admin(uid, d): await cq.answer("🚫", show_alert=True); return
            d2 = load(); count = 0
            for k, v in d2.get("users", {}).items():
                v["stats"] = {"sent":0, "failed":0, "last":"—"}
                count += 1
            save(d2)
            await cq.answer(f"✅ {count} reset")
            await sedit(cq, f"✅ <b>{count}</b> users cleared.",
                kb([("◀️  Admin","adm:menu")]))

        elif c == "bcast:do":
            if not is_admin(uid, d): await cq.answer("🚫", show_alert=True); return
            await state.set_state(W.broadcast)
            await sedit(cq,
                f"<b>📢 Broadcast</b>\n{line()}\n\n"
                f"👥 <code>{len(d.get('users',{}))}</code> recipients\n\n"
                f"Enter message:",
                kb([("❌  Cancel","adm:menu")]))

        elif c == "adm:free":
            if not is_owner(uid): await cq.answer("🚫", show_alert=True); return
            d["free"] = not d.get("free", False); save(d)
            await cq.answer(f"Free: {'✅' if d['free'] else '🔴'}")
            await sedit(cq, "<b>🛡 Panel</b>", adm_menu_kb(uid, d))

        elif c == "adm:addadmin":
            if not is_owner(uid): await cq.answer("🚫", show_alert=True); return
            await state.set_state(W.adm_add)
            await sedit(cq, f"<b>➕ Add Admin</b>\n{line()}\n\nSend user ID:",
                kb([("❌  Cancel","adm:menu")]))

        elif c == "adm:zip":
            if not is_owner(uid): await cq.answer("🚫", show_alert=True); return
            await cq.answer("📦…")
            zdata = make_zip()
            fname = f"smsbot_{datetime.now().strftime('%Y%m%d_%H%M')}.zip"
            await cq.bot.send_document(uid, BufferedInputFile(zdata, filename=fname),
                caption=f"📦 <code>{fname}</code>", parse_mode="HTML")

        elif c == "adm:resetall":
            if not is_owner(uid): await cq.answer("🚫", show_alert=True); return
            await sedit(cq,
                f"<b>💥 Reset ALL?</b>\n{line()}\n\nEverything will be cleared. Cannot be undone.",
                kb([("✅  Yes","adm:resetall:yes"), ("❌  Cancel","adm:menu")]))

        elif c == "adm:resetall:yes":
            if not is_owner(uid): await cq.answer("🚫", show_alert=True); return
            for j in list(_active_jobs.values()):
                if j.state == "running": j.cancel_event.set()
            save(_defs())
            await sedit(cq, "<b>💥 Reset complete.</b>", adm_menu_kb(uid, load()))

        elif c == "ban:do":
            if not is_admin(uid, d): await cq.answer("🚫", show_alert=True); return
            await state.set_state(W.ban_id)
            await sedit(cq, f"<b>🚫 Ban</b>\n{line()}\n\nSend user ID:",
                kb([("❌  Cancel","adm:menu")]))

        elif c == "unban:do":
            if not is_admin(uid, d): await cq.answer("🚫", show_alert=True); return
            banned = d.get("banned", [])
            if not banned:
                await cq.answer("No banned users.", show_alert=True); return
            await sedit(cq, f"<b>✅ Unban</b>\n{line()}", unban_kb(banned))

        elif c.startswith("unban:uid:"):
            if not is_admin(uid, d): await cq.answer("🚫", show_alert=True); return
            bid = int(c.split("unban:uid:", 1)[1])
            d2 = load()
            if bid in d2.get("banned", []): d2["banned"].remove(bid)
            save(d2)
            await cq.answer(f"✅ {bid} unbanned")
            await sedit(cq, f"✅ <code>{bid}</code> unbanned", adm_menu_kb(uid, d2))
            try: await cq.bot.send_message(bid, "✅ Ban lifted. /start", parse_mode="HTML")
            except: pass

        elif c == "fj:menu":
            if not is_admin(uid, d): await cq.answer("🚫", show_alert=True); return
            await sedit(cq, f"<b>📢 Force Join</b>\n{line()}", fj_menu_kb(uid, d))

        elif c == "fj:add":
            if not is_admin(uid, d): await cq.answer("🚫", show_alert=True); return
            await state.set_state(W.fj_add)
            await sedit(cq,
                f"<b>📢 Add Force Join — 1/3</b>\n{line()}\n\nSend channel link:",
                kb([("❌  Cancel","fj:menu")]))

        elif c.startswith("fj:del:"):
            if not is_admin(uid, d): await cq.answer("🚫", show_alert=True); return
            cid_str = c.split("fj:del:", 1)[1]
            d2 = load()
            d2["force_join"] = [x for x in d2.get("force_join", []) if str(x["id"]) != cid_str]
            save(d2); await cq.answer("🗑 Removed")
            await sedit(cq, f"<b>📢 Force Join</b>\n{line()}", fj_menu_kb(uid, d2))

        elif c == "<i>noop</i>": pass

        await cq.answer()
    except Exception as e:
        log.error(f"cb crash: {e}")
        try: await cq.answer("❌", show_alert=True)
        except: pass

# ══════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════
async def _on_error(event):
    log.error(f"dispatcher error: {event.exception}")

async def main():
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(R)
    dp.errors.register(_on_error)

    me = await bot.get_me()
    log.info(f"✅ @{me.username} started ({_VERSION}) | by {_CREDITS}")

    try:
        await bot.send_message(OWNER_ID,
            f"🚀 <b>SMS Bot {_VERSION}</b>\n"
            f"@{me.username}\n"
            f"<i>By {_CREDITS}</i>\n"
            f"<code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>",
            parse_mode="HTML")
    except Exception as e: log.warning(f"owner notify: {e}")

    asyncio.create_task(notify_all_users_online(bot, me.username))

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Bot stopped.")
