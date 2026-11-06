"""WAZZR BOT: To'liq admin paneli va Mini App integratsiyasi bilan.
"""
import asyncio, logging, os, sqlite3, secrets, string, time, random, hmac, hashlib, json, re
from urllib.parse import parse_qsl
from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart, Command, CommandObject
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from html import escape as hesc
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import (Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
                           WebAppInfo, ChatMemberUpdated, ReplyKeyboardMarkup, KeyboardButton)
from aiogram.filters.chat_member_updated import ChatMemberUpdatedFilter, KICKED, MEMBER

# ===================== SOZLAMALAR =====================
BOT_TOKEN   = os.getenv("BOT_TOKEN", "9185114954:AAHrJk0G0teFH8ec3bLRHabFNZ6WezHRkUE")
SUPER_ADMIN = int(os.getenv("SUPER_ADMIN_ID", 8520429829))
WEBAPP_URL  = os.getenv("WEBAPP_URL", "https://wazzr-bot.onrender.com/")
CARD_NUMBER = os.getenv("CARD_NUMBER", "8600000000000000")
CARD_HOLDER = os.getenv("CARD_HOLDER", "WZR PAY")
SMS_SECRET  = os.getenv("SMS_SECRET", "uzun_tasodifiy_maxfiy_soz")
PORT        = int(os.environ.get("PORT", 8082))
# =======================================================

TOKEN = BOT_TOKEN
HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)

DB = "wazzr.db"

def conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c

def db_init():
    with conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            tg_id INTEGER PRIMARY KEY,
            usr_id TEXT UNIQUE,
            username TEXT,
            full_name TEXT,
            phone TEXT,
            started_at INTEGER,
            last_seen INTEGER,
            blocked_bot INTEGER DEFAULT 0,
            block_count INTEGER DEFAULT 0,
            warn_count INTEGER DEFAULT 0,
            banned INTEGER DEFAULT 0,
            balance INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS admins(
            tg_id INTEGER PRIMARY KEY,
            can_edit_settings INTEGER DEFAULT 1,
            hidden INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS settings(
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """)
        defaults = {
            "news_url": "https://t.me/WazzR_VIP",
            "support_id": "8520429829",
            "isbot_url": "https://t.me/WazzR_Isbot",
            "maintenance": "0",
            "maintenance_reason": "",
            "maintenance_until": "0",
            "welcome_image": "",
            "btn_shop": "Xaridga o'tish 🚀",
            "btn_news": "⚡ Yangiliklar",
            "btn_support": "🥷 Qo'llab-quvvatlash",
            "terms_text": "Assalomu Aleykum👋 Bizning Wazzr BOTimizdan foydalanayotganingizdan xursandmiz!\n\n1. WAZZR BOT servisi barcha o'yinchilar uchun xar xil o'yinlar uchun donat taqdim qiladi.\n2. ID raqamni to'g'ri kiritish majburiy. Xato kiritilsa biz javob bermaymiz!\n3. To'lov asosan Uzcard/Humo orqali amalga oshiriladi."
        }
        for k, v in defaults.items():
            c.execute("INSERT OR IGNORE INTO settings VALUES(?,?)", (k, v))

def get_setting(key):
    with conn() as c:
        r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r["value"] if r else None

def set_setting(key, value):
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (key, str(value)))

def _new_usr_id(c):
    alphabet = string.ascii_lowercase + string.digits
    while True:
        uid = "usr_" + "".join(secrets.choice(alphabet) for _ in range(8))
        if not c.execute("SELECT 1 FROM users WHERE usr_id=?", (uid,)).fetchone():
            return uid

def upsert_user(tg_id, username, full_name):
    now = int(time.time())
    with conn() as c:
        r = c.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,)).fetchone()
        if r:
            c.execute("UPDATE users SET username=?, full_name=?, last_seen=?, blocked_bot=0 WHERE tg_id=?",
                      (username, full_name, now, tg_id))
        else:
            c.execute("INSERT INTO users(tg_id,usr_id,username,full_name,started_at,last_seen) VALUES(?,?,?,?,?,?)",
                      (tg_id, _new_usr_id(c), username, full_name, now, now))
        return c.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,)).fetchone()

def mark_blocked(tg_id, blocked: bool):
    with conn() as c:
        if blocked:
            c.execute("UPDATE users SET blocked_bot=1, block_count=block_count+1 WHERE tg_id=?", (tg_id,))
        else:
            c.execute("UPDATE users SET blocked_bot=0 WHERE tg_id=?", (tg_id,))

def is_admin(tg_id, super_id):
    if tg_id == super_id:
        return True
    with conn() as c:
        return c.execute("SELECT 1 FROM admins WHERE tg_id=?", (tg_id,)).fetchone() is not None

PAY_TTL = 300
NUMERIC = {"free_fire", "pubg", "mlbb", "brawl", "eafc", "lords", "steam", "telegram"}

def shop_init():
    with conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS cards(id INTEGER PRIMARY KEY AUTOINCREMENT, number TEXT, holder TEXT, active INTEGER DEFAULT 1);
        CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY AUTOINCREMENT, game TEXT, title TEXT, price INTEGER, icon TEXT DEFAULT '💎', active INTEGER DEFAULT 1);
        CREATE TABLE IF NOT EXISTS payments(id INTEGER PRIMARY KEY AUTOINCREMENT, tg_id INTEGER, base INTEGER, exact INTEGER, card_id INTEGER, status TEXT, created INTEGER, expires INTEGER, paid_at INTEGER);
        CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY AUTOINCREMENT, tg_id INTEGER, product_id INTEGER, game TEXT, title TEXT, price INTEGER, player_id TEXT, status TEXT, created INTEGER);
        CREATE TABLE IF NOT EXISTS carts(tg_id INTEGER PRIMARY KEY, product_id INTEGER, player_id TEXT);
        """)
        if not c.execute("SELECT 1 FROM products").fetchone():
            games = [
                ("free_fire", "Free Fire", [("110 💎", 10500), ("341 💎", 32000), ("572 💎", 53000)]),
                ("pubg", "PUBG Mobile", [("60 UC", 11000), ("325 UC", 55000), ("660 UC", 110000)]),
                ("mlbb", "Mobile Legends", [("56 Diamonds", 11000), ("296 Diamonds", 54000)]),
                ("brawl", "Brawl Stars", [("30 Gems", 12000), ("80 Gems", 31000)]),
                ("telegram", "Telegram Premium", [("1 oy", 28000), ("1 yil", 250000)])
            ]
            for g_key, g_name, prods in games:
                for t, p in prods:
                    c.execute("INSERT INTO products(game,title,price,icon) VALUES(?,?,?,?)", (g_key, t, p, '💎'))
        try:
            c.execute("ALTER TABLE payments ADD COLUMN claimed INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        num, holder = CARD_NUMBER, CARD_HOLDER
        if num and not c.execute("SELECT 1 FROM cards").fetchone():
            c.execute("INSERT INTO cards(number,holder) VALUES(?,?)", (num.replace(" ", ""), holder))

def verify_init(init_data, token):
    d = dict(parse_qsl(init_data, keep_blank_values=True))
    h = d.pop("hash", None)
    s = "\n".join(f"{k}={v}" for k, v in sorted(d.items()))
    key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    if not h or not hmac.compare_digest(hmac.new(key, s.encode(), hashlib.sha256).hexdigest(), h):
        return None
    if time.time() - int(d.get("auth_date", 0)) > 86400:
        return None
    return json.loads(d["user"])

def verify_id(game, uid):
    uid = (uid or "").strip()
    if game in NUMERIC:
        return bool(len(uid) >= 3)
    return 2 < len(uid) <= 64

def create_payment(tg_id, base):
    now = int(time.time())
    with conn() as c:
        c.execute("UPDATE payments SET status='expired' WHERE status='pending' AND expires<?", (now - 120,))
        c.execute("UPDATE payments SET status='cancelled' WHERE status='pending' AND tg_id=?", (tg_id,))
        cards = c.execute("SELECT * FROM cards WHERE active=1").fetchall()
        if not cards:
            return None
        random.shuffle(cards)
        for card in cards:
            used = {r[0] for r in c.execute("SELECT exact FROM payments WHERE card_id=? AND status IN ('pending','expired') AND expires>?", (card["id"], now - 3600))}
            free = [o for o in range(1, 100) if base + o not in used]
            if free:
                exact = base + random.choice(free)
                cur = c.execute("INSERT INTO payments(tg_id,base,exact,card_id,status,created,expires) VALUES(?,?,?,?,?,?,?)",
                                (tg_id, base, exact, card["id"], "pending", now, now + PAY_TTL))
                return {"id": cur.lastrowid, "card": card["number"], "holder": card["holder"], "exact": exact, "left": PAY_TTL}
    return None

def pending_of(tg_id):
    now = int(time.time())
    with conn() as c:
        p = c.execute("SELECT p.*, c.number, c.holder FROM payments p JOIN cards c ON c.id=p.card_id WHERE p.tg_id=? AND p.status='pending' AND p.expires>? ORDER BY p.id DESC", (tg_id, now)).fetchone()
        if p:
            return {"id": p["id"], "card": p["number"], "holder": p["holder"], "exact": p["exact"], "left": p["expires"] - now}

def payment_status(tg_id, pid):
    with conn() as c:
        p = c.execute("SELECT * FROM payments WHERE id=? AND tg_id=?", (pid, tg_id)).fetchone()
        if not p:
            return "none"
        if p["status"] == "pending" and p["expires"] < int(time.time()) - 120:
            c.execute("UPDATE payments SET status='expired' WHERE id=?", (pid,))
            return "expired"
        return p["status"]

def cancel_payment(tg_id, pid):
    with conn() as c:
        c.execute("UPDATE payments SET status='cancelled' WHERE id=? AND tg_id=? AND status='pending'", (pid, tg_id))

def confirm_payment(pid):
    with conn() as c:
        cur = c.execute("UPDATE payments SET status='paid', paid_at=? WHERE id=? AND status NOT IN ('paid','cancelled')", (int(time.time()), pid))
        if cur.rowcount == 0:
            return None
        p = c.execute("SELECT * FROM payments WHERE id=?", (pid,)).fetchone()
        c.execute("UPDATE users SET balance=balance+? WHERE tg_id=?", (p["exact"], p["tg_id"]))
        return dict(p)

def place_order(tg_id, pid, player_id):
    with conn() as c:
        p = c.execute("SELECT * FROM products WHERE id=? AND active=1", (pid,)).fetchone()
        if not p:
            return {"error": "Mahsulot topilmadi"}
        if not verify_id(p["game"], player_id):
            return {"error": "ID noto‘g‘ri"}
        bal = c.execute("SELECT balance FROM users WHERE tg_id=?", (tg_id,)).fetchone()["balance"]
        if bal < p["price"]:
            return {"error": "insufficient", "balance": bal, "need": p["price"]}
        c.execute("UPDATE users SET balance=balance-? WHERE tg_id=?", (p["price"], tg_id))
        cur = c.execute("INSERT INTO orders(tg_id,product_id,game,title,price,player_id,status,created) VALUES(?,?,?,?,?,?,?,?)",
                        (tg_id, pid, p["game"], p["title"], p["price"], player_id.strip(), "new", int(time.time())))
        c.execute("DELETE FROM carts WHERE tg_id=?", (tg_id,))
        return {"ok": True, "balance": bal - p["price"],
                "order": {"id": cur.lastrowid, "title": p["title"], "game": p["game"], "price": p["price"], "player_id": player_id.strip()}}

def set_done(oid):
    with conn() as c:
        o = c.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
        if o:
            c.execute("UPDATE orders SET status='done' WHERE id=?", (oid,))
        return dict(o) if o else None

def history(tg_id):
    with conn() as c:
        h = [{"t": "order", "id": r["id"], "title": r["title"], "game": r["game"], "amount": r["price"],
              "pid": r["player_id"], "status": r["status"], "ts": r["created"]}
             for r in c.execute("SELECT * FROM orders WHERE tg_id=? ORDER BY id DESC LIMIT 100", (tg_id,))]
        h += [{"t": "topup", "id": r["id"], "title": "Hisob to‘ldirish", "game": "", "amount": r["exact"],
               "pid": "", "status": "done", "ts": r["paid_at"]}
              for r in c.execute("SELECT * FROM payments WHERE tg_id=? AND status='paid' ORDER BY id DESC LIMIT 100", (tg_id,))]
    return sorted(h, key=lambda x: -x["ts"])

HERE = os.path.dirname(os.path.abspath(__file__))
fmt = lambda n: f"{n:,}".replace(",", " ")

def admin_ids(super_admin):
    with conn() as c:
        return {super_admin} | {r[0] for r in c.execute("SELECT tg_id FROM admins")}

def make_app(bot, token, super_admin, webapp_url):
    async def tell_admins(text, kb=None):
        for a in admin_ids(super_admin):
            try:
                await bot.send_message(a, text, parse_mode="HTML", reply_markup=kb)
            except Exception:
                pass

    async def notify_paid(p):
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Davom etish ▶️", web_app=WebAppInfo(url=webapp_url))],
            [InlineKeyboardButton(text="Bekor qilish ✖️", callback_data="home")]])
        try:
            await bot.send_message(p["tg_id"],
                f"✅ <b>TO’LOV QABUL QILINDI!</b>\n\n+{fmt(p['exact'])} UZS balansingizga tushdi.\n"
                "Bundan oldingi savdoni davom ettiramizmi?", parse_mode="HTML", reply_markup=kb)
        except Exception:
            pass
        await tell_admins(f"💰 To‘lov qabul qilindi: {fmt(p['exact'])} UZS (user {p['tg_id']})")

    @web.middleware
    async def auth(request, handler):
        if not request.path.startswith("/api/") or request.path == "/api/sms":
            return await handler(request)
        u = verify_init(request.headers.get("X-Init", ""), token)
        if not u:
            return web.json_response({"error": "auth"}, status=401)
        row = upsert_user(u["id"], u.get("username"), (u.get("first_name", "") + " " + u.get("last_name", "")).strip())
        if row["banned"]:
            return web.json_response({"error": "Siz bloklangansiz"}, status=403)
        if get_setting("maintenance") == "1" and not is_admin(u["id"], super_admin):
            return web.json_response({"maintenance": get_setting("maintenance_reason") or "Sabab ko‘rsatilmagan"}, status=503)
        request["tg"], request["u"] = u, row
        return await handler(request)

    async def body(r):
        try:
            return await r.json()
        except Exception:
            return {}

    async def index(r):
        file_path = os.path.join(HERE, "index.html")
        return web.FileResponse(file_path, headers={"Cache-Control": "no-store"})

    async def state(r):
        u, tg = r["u"], r["tg"]
        with conn() as c:
            prods = [dict(x) for x in c.execute("SELECT id,game,title,price,icon FROM products WHERE active=1 ORDER BY price")]
            cart = c.execute("SELECT product_id,player_id FROM carts WHERE tg_id=?", (u["tg_id"],)).fetchone()
            ids = [x[0] for x in c.execute("SELECT player_id FROM orders WHERE tg_id=? GROUP BY player_id ORDER BY MAX(id) DESC LIMIT 3", (u["tg_id"],))]
            n = c.execute("SELECT COUNT(*) FROM orders WHERE tg_id=?", (u["tg_id"],)).fetchone()[0]
            bal = c.execute("SELECT balance FROM users WHERE tg_id=?", (u["tg_id"],)).fetchone()[0]
        sup = get_setting("support_id")
        return web.json_response({
            "user": {"usr_id": u["usr_id"], "name": u["full_name"] or "Mijoz", "tg_id": u["tg_id"], "balance": bal,
                     "joined": u["started_at"], "orders": n},
            "products": prods, "cart": dict(cart) if cart else None, "ids": ids,
            "links": {"news": get_setting("news_url"), "isbot": get_setting("isbot_url"),
                      "support": f"tg://user?id={sup}" if sup.isdigit() else f"https://t.me/{sup.lstrip('@')}"},
            "history": history(u["tg_id"]), "pending": pending_of(u["tg_id"]), "terms": get_setting("terms_text")})

    async def checkid(r):
        b = await body(r)
        return web.json_response({"ok": verify_id(b.get("game", ""), b.get("uid", ""))})

    async def cart(r):
        b = await body(r)
        with conn() as c:
            c.execute("INSERT OR REPLACE INTO carts VALUES(?,?,?)", (r["u"]["tg_id"], int(b.get("product_id", 0)), str(b.get("player_id", ""))))
        return web.json_response({"ok": True})

    async def order(r):
        b = await body(r)
        res = place_order(r["u"]["tg_id"], int(b.get("product_id", 0)), str(b.get("player_id", "")))
        if res.get("ok"):
            o, tg = res["order"], r["tg"]
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Bajarildi", callback_data=f"done:{o['id']}")]])
            await tell_admins(f"🛒 <b>Yangi buyurtma #{o['id']}</b>\nMahsulot: {o['title']} ({o['game']})\nSumma: {fmt(o['price'])} UZS\n"
                              f"Player ID: <code>{o['player_id']}</code>\nMijoz: {tg.get('first_name','')} @{tg.get('username','-')} ({tg['id']})", kb)
        return web.json_response(res)

    async def topup(r):
        b = await body(r)
        try:
            amt = int(b.get("amount", 0))
        except Exception:
            amt = 0
        if amt < 1000 or amt > 5_000_000:
            return web.json_response({"error": "Summa 1 000 dan 5 000 000 gacha bo‘lishi kerak"})
        p = create_payment(r["u"]["tg_id"], amt)
        return web.json_response(p or {"error": "Hozircha to‘lov qabul qilinmaydi (karta kiritilmagan)"})

    async def topup_status(r):
        b = await body(r)
        return web.json_response({"status": payment_status(r["u"]["tg_id"], int(b.get("id", 0)))})

    async def topup_cancel(r):
        b = await body(r)
        cancel_payment(r["u"]["tg_id"], int(b.get("id", 0)))
        return web.json_response({"ok": True})

    async def topup_claim(r):
        b = await body(r)
        pid, tg = int(b.get("id", 0)), r["tg"]
        with conn() as c:
            p = c.execute("SELECT p.*, c.number FROM payments p JOIN cards c ON c.id=p.card_id WHERE p.id=? AND p.tg_id=? AND p.status!='paid'", (pid, tg["id"])).fetchone()
            if p and not p["claimed"]:
                c.execute("UPDATE payments SET claimed=1 WHERE id=?", (pid,))
            else:
                p = None
        if p:
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"pay_ok:{pid}"),
                                                        InlineKeyboardButton(text="❌ Rad etish", callback_data=f"pay_no:{pid}")]])
            await tell_admins(f"🔔 <b>To‘lov tekshiruvi #{pid}</b>\nMijoz: {hesc(tg.get('first_name',''))} @{tg.get('username','-')} ({tg['id']})\n"
                              f"Summa: <b>{fmt(p['exact'])} UZS</b>\nKarta: ••{p['number'][-4:]}\n\nMijoz «To‘lov qildim» bosdi. Kartaga shu summa tushganini tekshirib tasdiqlang.", kb)
        return web.json_response({"ok": True})

    app = web.Application(middlewares=[auth])
    app.add_routes([web.get("/", index)] +
                   [web.post(f"/api/{n}", h) for n, h in [("state", state), ("checkid", checkid), ("cart", cart), ("order", order),
                    ("topup", topup), ("topup_status", topup_status), ("topup_cancel", topup_cancel), ("topup_claim", topup_claim)]])
    return app

router = Router()

def user_keyboard():
    support = get_setting("support_id")
    support_url = f"https://t.me/{support.lstrip('@')}" if not support.isdigit() else f"tg://user?id={support}"
    rows = [
        [InlineKeyboardButton(text=get_setting("btn_shop"), web_app=WebAppInfo(url=WEBAPP_URL))],
        [InlineKeyboardButton(text=get_setting("btn_news"), url=get_setting("news_url")),
         InlineKeyboardButton(text=get_setting("btn_support"), url=support_url)],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)

USER_TEXT = (
    "🦖 Xush kelibsiz, <b>{name}</b>!\n\n"
    "🛡 <b>Wazzr BOT</b> orqali endi o'yinlar va xizmatlarga tez va oson donat qilishingiz mumkin.\n\n"
    "🟢 Qulay interfeys\n🟢 Ishonchli to'lov\n🟢 Avtomatlashtirilgan xizmat\n\n"
    "🟢 Pastdagi tugmani bosing va boshlang 👇"
)

ADMIN_TEXT = (
    "👑 Assalomu alaykum, <b>{name}</b>!\n\n"
    "✨ Siz <b>adminsiz</b>. Boshqaruv menyusi pastda 👇"
)

@router.message(CommandStart())
async def start(m: Message):
    u = upsert_user(m.from_user.id, m.from_user.username, m.from_user.full_name)
    if get_setting("maintenance") == "1" and not is_admin(m.from_user.id, SUPER_ADMIN):
        reason = get_setting("maintenance_reason") or "Sabab ko'rsatilmagan"
        await m.answer(f"🛠 Bot vaqtincha tuzatuvda.\nSabab: {reason}\nOchilish vaqti belgilanmagan.")
        return
    kb = admin_kb() if is_admin(m.from_user.id, SUPER_ADMIN) else user_keyboard()
    txt = ADMIN_TEXT.format(name=hesc(m.from_user.full_name)) if is_admin(m.from_user.id, SUPER_ADMIN) else USER_TEXT.format(name=hesc(m.from_user.full_name))
    img = get_setting("welcome_image")
    if img:
        await m.answer_photo(img, caption=txt, parse_mode="HTML", reply_markup=kb)
    else:
        await m.answer(txt, parse_mode="HTML", reply_markup=kb)

@router.message(Command("help"))
async def help_(m: Message):
    support = get_setting("support_id")
    url = f"tg://user?id={support}" if support.isdigit() else f"https://t.me/{support.lstrip('@')}"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Admin bilan bog'lanish", url=url)]])
    await m.answer("Savol yoki muammo bo'lsa admin bilan bog'laning 👇", reply_markup=kb)

@router.message(Command("cancel"))
async def cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("Hamma narsa bekor qilindi. /start ni bosing.")

@router.callback_query(F.data == "home")
async def home(cb: CallbackQuery):
    await cb.message.answer(USER_TEXT.format(name=cb.from_user.full_name), parse_mode="HTML", reply_markup=user_keyboard())
    await cb.answer()

@router.callback_query(F.data.startswith("done:"))
async def done(cb: CallbackQuery):
    if not is_admin(cb.from_user.id, SUPER_ADMIN):
        return await cb.answer("Ruxsat yo'q", show_alert=True)
    o = set_done(int(cb.data.split(":")[1]))
    if o:
        try:
            await cb.bot.send_message(o["tg_id"], f"✅ Buyurtma #{o['id']} bajarildi: {o['title']}. Xaridingiz uchun rahmat!")
        except Exception:
            pass
        await cb.message.edit_text(cb.message.text + "\n\n✅ Bajarildi")
    await cb.answer("Bajarildi")

@router.my_chat_member(ChatMemberUpdatedFilter(KICKED))
async def blocked(e: ChatMemberUpdated):
    mark_blocked(e.from_user.id, True)

@router.my_chat_member(ChatMemberUpdatedFilter(MEMBER))
async def unblocked(e: ChatMemberUpdated):
    mark_blocked(e.from_user.id, False)

adm = Router()
adm.message.filter(lambda m: is_admin(m.from_user.id, SUPER_ADMIN))
adm.callback_query.filter(lambda c: is_admin(c.from_user.id, SUPER_ADMIN))
money = lambda n: f"{int(n):,}".replace(",", " ")
tdt = lambda ts: time.strftime("%d.%m %H:%M", time.gmtime((ts or 0) + 18000))
BG = set()

def admin_kb():
    rows = [
        ["📊 Statistika", "💳 To'lovlar"],
        ["🛒 Buyurtmalar", "🎴 Kartalar"],
        ["📢 Reklama", "🛠 Tuzatuv"],
        ["👥 Foydalanuvchilar", "⚙️ Sozlamalar"]
    ]
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=t) for t in r] for r in rows], resize_keyboard=True)

def ikb(rows):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d) for t, d in r] for r in rows])

class AddCard(StatesGroup):
    number = State()
    holder = State()

class Maint(StatesGroup):
    reason = State()

class Bcast(StatesGroup):
    msg = State()

async def tell_user_paid(bot, p):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Davom etish ▶️", web_app=WebAppInfo(url=WEBAPP_URL))],
                                               [InlineKeyboardButton(text="Bekor qilish ✖️", callback_data="home")]])
    try:
        await bot.send_message(p["tg_id"], f"✅ <b>TO'LOV QABUL QILINDI!</b>\n\n+{money(p['exact'])} UZS balansingizga tushdi.\n"
                               "Bundan oldingi savdoni davom ettiramizmi?", parse_mode="HTML", reply_markup=kb)
    except Exception:
        pass

@adm.message(Command("admin"))
async def a_menu(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("👑 <b>Admin paneli</b>\nKerakli bo'limni pastdagi menyudan tanlang 👇", parse_mode="HTML", reply_markup=admin_kb())

@adm.message(F.text == "📊 Statistika")
async def a_stats(m: Message, state: FSMContext):
    await state.clear()
    now = int(time.time()); t0 = (now + 18000) // 86400 * 86400 - 18000
    with conn() as c:
        q = lambda sql, *a: c.execute(sql, a).fetchone()[0]
        d = dict(users=q("SELECT COUNT(*) FROM users"), blocked=q("SELECT COUNT(*) FROM users WHERE blocked_bot=1"),
                 online=q("SELECT COUNT(*) FROM users WHERE last_seen>?", now - 300),
                 today=q("SELECT COALESCE(SUM(exact),0) FROM payments WHERE status='paid' AND paid_at>=?", t0),
                 allp=q("SELECT COALESCE(SUM(exact),0) FROM payments WHERE status='paid'"),
                 pend=q("SELECT COUNT(*) FROM payments WHERE status='pending' AND expires>?", now),
                 onew=q("SELECT COUNT(*) FROM orders WHERE status='new'"), odone=q("SELECT COUNT(*) FROM orders WHERE status='done'"),
                 sales=q("SELECT COALESCE(SUM(price),0) FROM orders"), bal=q("SELECT COALESCE(SUM(balance),0) FROM users"))
    await m.answer(f"📊 <b>Statistika</b>\n\n👥 Foydalanuvchilar: <b>{d['users']}</b>\n🟢 Faol: {d['online']}\n🚫 Bloklagan: {d['blocked']}\n\n"
                   f"💰 Bugungi to'lovlar: <b>{money(d['today'])} UZS</b>\n💰 Jami to'lovlar: {money(d['allp'])} UZS\n⏳ Kutilayotgan: {d['pend']}\n"
                   f"👛 Mijozlar balansi: {money(d['bal'])} UZS\n\n🛒 Yangi buyurtmalar: <b>{d['onew']}</b>\n✅ Bajarilgan: {d['odone']}\n💵 Savdo: {money(d['sales'])} UZS", parse_mode="HTML")

@adm.message(F.text == "💳 To'lovlar")
async def a_pay(m: Message, state: FSMContext):
    await state.clear()
    with conn() as c:
        rows = c.execute("SELECT p.*, u.username, u.full_name FROM payments p LEFT JOIN users u ON u.tg_id=p.tg_id ORDER BY p.id DESC LIMIT 10").fetchall()
    if not rows:
        return await m.answer("Hali to'lovlar yo'q.")
    ico = {"paid": "✅", "pending": "⏳", "expired": "⌛", "cancelled": "✖️"}
    lines = [f"{ico.get(r['status'], '•')} #{r['id']} · {money(r['exact'])} UZS · {hesc(r['full_name'] or str(r['tg_id']))} · {tdt(r['created'])}" for r in rows]
    await m.answer("💳 <b>Oxirgi to'lovlar</b>\n\n" + "\n".join(lines), parse_mode="HTML")
    for r in [r for r in rows if r["status"] in ("pending", "expired")][:5]:
        flag = "🔔 mijoz «To'lov qildim» bosdi\n" if r["claimed"] else ""
        await m.answer(f"{flag}#{r['id']} · <b>{money(r['exact'])} UZS</b> · @{r['username'] or '-'} ({r['tg_id']})\nHolat: {r['status']}",
                       parse_mode="HTML", reply_markup=ikb([[("✅ Tasdiqlash", f"pay_ok:{r['id']}"), ("❌ Rad etish", f"pay_no:{r['id']}")]]))

@adm.callback_query(F.data.startswith("pay_ok:"))
async def pay_ok(cb: CallbackQuery):
    p = confirm_payment(int(cb.data.split(":")[1]))
    if not p:
        return await cb.answer("Allaqachon tasdiqlangan", show_alert=True)
    await tell_user_paid(cb.bot, p)
    await cb.message.edit_text(cb.message.text + "\n\n✅ TASDIQLANDI")
    await cb.answer("Tasdiqlandi")

@adm.callback_query(F.data.startswith("pay_no:"))
async def pay_no(cb: CallbackQuery):
    pid = int(cb.data.split(":")[1])
    with conn() as c:
        p = c.execute("SELECT * FROM payments WHERE id=? AND status!='paid'", (pid,)).fetchone()
        if p:
            c.execute("UPDATE payments SET status='cancelled' WHERE id=?", (pid,))
    if p:
        try:
            await cb.bot.send_message(p["tg_id"], f"❌ #{pid} to'lov tasdiqlanmadi.")
        except Exception:
            pass
    await cb.message.edit_text(cb.message.text + "\n\n❌ RAD ETILDI")
    await cb.answer()

@adm.message(Command("balans"))
async def a_balans(m: Message, command: CommandObject):
    try:
        uid, amt = [int(x) for x in (command.args or "").split()]
    except Exception:
        return await m.answer("Format: /balans <telegram_id> <summa>")
    with conn() as c:
        c.execute("UPDATE users SET balance=balance+? WHERE tg_id=?", (amt, uid))
        nb = c.execute("SELECT balance FROM users WHERE tg_id=?", (uid,)).fetchone()["balance"]
    await m.answer(f"✅ Yangi balans: {money(nb)} UZS")

@adm.message(F.text == "🛒 Buyurtmalar")
async def a_orders(m: Message, state: FSMContext):
    await state.clear()
    with conn() as c:
        rows = c.execute("SELECT o.*, u.username FROM orders o LEFT JOIN users u ON u.tg_id=o.tg_id WHERE o.status='new' ORDER BY o.id LIMIT 10").fetchall()
    if not rows:
        return await m.answer("✅ Kutilayotgan buyurtma yo'q.")
    for o in rows:
        await m.answer(f"🛒 <b>Buyurtma #{o['id']}</b>\n{hesc(o['title'])} ({o['game']})\nSumma: {money(o['price'])} UZS\nPlayer ID: <code>{hesc(o['player_id'])}</code>\n"
                       f"Mijoz: @{o['username'] or '-'} ({o['tg_id']})", parse_mode="HTML", reply_markup=ikb([[("✅ Bajarildi", f"done:{o['id']}")]]))

def cards_view():
    with conn() as c:
        cards = c.execute("SELECT * FROM cards").fetchall()
    text = "🎴 <b>Kartalar</b> (5 tagacha, navbat bilan beriladi)\n\n" + ("\n".join(f"{'🟢' if x['active'] else '🔴'} #{x['id']} <code>{x['number']}</code> · {hesc(x['holder'])}" for x in cards) or "Karta yo'q.")
    rows = [[(f"⏯ #{x['id']} yoq/o'ch", f"card_t:{x['id']}"), (f"🗑 #{x['id']}", f"card_d:{x['id']}")] for x in cards] + [[("➕ Karta qo'shish", "card_add")]]
    return text, ikb(rows)

@adm.message(F.text == "🎴 Kartalar")
async def a_cards(m: Message, state: FSMContext):
    await state.clear()
    t, kb = cards_view()
    await m.answer(t, parse_mode="HTML", reply_markup=kb)

@adm.callback_query(F.data.startswith("card_t:") | F.data.startswith("card_d:"))
async def card_edit(cb: CallbackQuery):
    act, cid = cb.data.split(":")
    with conn() as c:
        c.execute("UPDATE cards SET active=1-active WHERE id=?", (int(cid),)) if act == "card_t" else c.execute("DELETE FROM cards WHERE id=?", (int(cid),))
    t, kb = cards_view()
    await cb.message.edit_text(t, parse_mode="HTML", reply_markup=kb)
    await cb.answer("Bajarildi")

@adm.callback_query(F.data == "card_add")
async def card_add(cb: CallbackQuery, state: FSMContext):
    with conn() as c:
        n = c.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
    if n >= 5:
        return await cb.answer("Ko'pi bilan 5 ta karta", show_alert=True)
    await state.set_state(AddCard.number)
    await cb.message.answer("Karta raqamini yuboring (16 ta raqam):")
    await cb.answer()

@adm.message(AddCard.number)
async def card_num(m: Message, state: FSMContext):
    n = re.sub(r"\D", "", m.text or "")
    if len(n) != 16:
        return await m.answer("16 ta raqam bo'lishi kerak:")
    await state.update_data(number=n)
    await state.set_state(AddCard.holder)
    await m.answer("Karta egasining F.I.Sh ni yuboring:")

@adm.message(AddCard.holder)
async def card_holder(m: Message, state: FSMContext):
    d = await state.get_data()
    await state.clear()
    with conn() as c:
        c.execute("INSERT INTO cards(number,holder) VALUES(?,?)", (d["number"], (m.text or "").upper()[:40]))
    t, kb = cards_view()
    await m.answer("✅ Karta qo'shildi.\n\n" + t, parse_mode="HTML", reply_markup=kb)

@adm.message(F.text == "🛠 Tuzatuv")
async def a_maint(m: Message, state: FSMContext):
    await state.clear()
    if get_setting("maintenance") == "1":
        await m.answer(f"🛠 Tuzatuv <b>YOQILGAN</b>.\nSabab: {hesc(get_setting('maintenance_reason'))}", parse_mode="HTML", reply_markup=ikb([[("🟢 O'chirish", "mt_off")]]))
    else:
        await m.answer("🛠 Tuzatuv o'chiq. Yoqsangiz, foydalanuvchilar botdan foydalana olmaydi.", reply_markup=ikb([[("🔴 Yoqish", "mt_on")]]))

@adm.callback_query(F.data == "mt_on")
async def mt_on(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Maint.reason)
    await cb.message.answer("Tuzatuv sababini kiriting:")
    await cb.answer()

@adm.message(Maint.reason)
async def mt_reason(m: Message, state: FSMContext):
    set_setting("maintenance_reason", m.text or "Sabab ko'rsatilmagan")
    set_setting("maintenance", "1")
    await state.clear()
    await m.answer("🛠 Tuzatuv yoqildi.")

@adm.callback_query(F.data == "mt_off")
async def mt_off(cb: CallbackQuery):
    set_setting("maintenance", "0")
    await cb.message.edit_text("🟢 Tuzatuv o'chirildi.")
    await cb.answer()

@adm.message(F.text == "👥 Foydalanuvchilar")
async def a_users(m: Message, state: FSMContext):
    await state.clear()
    with conn() as c:
        rows = c.execute("SELECT u.*, (SELECT COUNT(*) FROM orders o WHERE o.tg_id=u.tg_id) AS oc FROM users u ORDER BY started_at DESC LIMIT 10").fetchall()
        total = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    lines = [f"{hesc(r['full_name'] or '-')} @{r['username'] or '-'} · <code>{r['tg_id']}</code>\n   {tdt(r['started_at'])} · 💰{money(r['balance'])}" + (" · 🚫" if r["blocked_bot"] else "") for r in rows]
    await m.answer(f"👥 <b>Foydalanuvchilar</b> (jami {total})\n\n" + "\n".join(lines), parse_mode="HTML")

@adm.message(F.text == "📢 Reklama")
async def a_bcast(m: Message, state: FSMContext):
    await state.set_state(Bcast.msg)
    await m.answer("📢 Reklama xabarini yuboring (matn, rasm, video va h.k.):")

@adm.message(Bcast.msg)
async def bc_preview(m: Message, state: FSMContext):
    with conn() as c:
        n = c.execute("SELECT COUNT(*) FROM users WHERE banned=0 AND blocked_bot=0").fetchone()[0]
    await state.update_data(cid=m.chat.id, mid=m.message_id)
    await m.answer("👆 Reklama ko'rinishi.")
    await m.copy_to(m.chat.id)
    await m.answer(f"Tasdiqlaysizmi? Yetkaziladi: <b>{n}</b> ta foydalanuvchiga.", parse_mode="HTML", reply_markup=ikb([[("✅ Yuborish", "bc_go"), ("❌ Bekor", "bc_no")]]))

@adm.callback_query(F.data == "bc_no")
async def bc_no(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.edit_text("Bekor qilindi.")
    await cb.answer()

@adm.callback_query(F.data == "bc_go")
async def bc_go(cb: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    await state.clear()
    t = asyncio.create_task(do_broadcast(cb.bot, cb.from_user.id, d["cid"], d["mid"]))
    BG.add(t); t.add_done_callback(BG.discard)
    await cb.message.edit_text("📤 Reklama yuborilmoqda...")
    await cb.answer()

async def do_broadcast(bot, admin_id, cid, mid):
    with conn() as c:
        ids = [r[0] for r in c.execute("SELECT tg_id FROM users WHERE banned=0 AND blocked_bot=0")]
    ok = fail = 0
    for uid in ids:
        try:
            await bot.copy_message(uid, cid, mid)
            ok += 1
        except TelegramForbiddenError:
            mark_blocked(uid, True); fail += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05)
    await bot.send_message(admin_id, f"📢 Reklama tugadi.\n✅ Yetkazildi: {ok}\n🚫 Bloklagan: {fail}")

@adm.message(F.text == "⚙️ Sozlamalar")
async def a_settings(m: Message):
    await m.answer("⚙️ Sozlamalar menyusi:\n\nKanallar va havolalarni o'zgartirish uchun bot bazasidan foydalanishingiz mumkin.")

async def main():
    logging.basicConfig(level=logging.INFO)
    if not TOKEN:
        raise SystemExit("BOT_TOKEN topilmadi!")
    db_init(); shop_init()
    bot = Bot(TOKEN)
    runner = web.AppRunner(make_app(bot, TOKEN, SUPER_ADMIN, WEBAPP_URL))
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', PORT).start()
    print("=" * 50)
    print(f"Bot va Mini App serveri ishga tushdi: http://0.0.0.0:{PORT}")
    print("=" * 50)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(adm)
    dp.include_router(router)
    await dp.start_polling(bot, allowed_updates=["message", "callback_query", "my_chat_member"])

if __name__ == "__main__":
    asyncio.run(main())
