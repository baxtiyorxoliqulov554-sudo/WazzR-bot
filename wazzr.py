"""WAZZR BOT: To'liq boshqaruv va Mini App dinamik rasm/ikonka sozlamalari bilan.
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
            started_at INTEGER,
            last_seen INTEGER,
            blocked_bot INTEGER DEFAULT 0,
            banned INTEGER DEFAULT 0,
            ban_until INTEGER DEFAULT 0,
            ban_reason TEXT,
            balance INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS admins(tg_id INTEGER PRIMARY KEY, username TEXT);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
        """)
        defaults = {
            "news_url": "https://t.me/WazzR_VIP",
            "support_id": "8520429829",
            "isbot_url": "https://t.me/WazzR_Isbot",
            "maintenance": "0",
            "maintenance_reason": "",
            "banner_text": "⚡ Tezkor Donat Xizmati",
            "banner_desc": "O'yinlaringizni avtomatik va ishonchli to'ldiring!",
            "btn_shop": "Xaridga o'tish 🚀",
            "btn_news": "⚡ Yangiliklar",
            "btn_support": "🥷 Qo'llab-quvvatlash"
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
            c.execute("UPDATE users SET blocked_bot=1 WHERE tg_id=?", (tg_id,))
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
        CREATE TABLE IF NOT EXISTS categories(id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT UNIQUE, name TEXT, icon TEXT DEFAULT '💎');
        CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY AUTOINCREMENT, game TEXT, title TEXT, price INTEGER, icon TEXT DEFAULT '💎', active INTEGER DEFAULT 1);
        CREATE TABLE IF NOT EXISTS payments(id INTEGER PRIMARY KEY AUTOINCREMENT, tg_id INTEGER, base INTEGER, exact INTEGER, card_id INTEGER, status TEXT, created INTEGER, expires INTEGER, paid_at INTEGER, holder_name TEXT, claimed INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY AUTOINCREMENT, tg_id INTEGER, product_id INTEGER, game TEXT, title TEXT, price INTEGER, player_id TEXT, status TEXT, created INTEGER);
        CREATE TABLE IF NOT EXISTS carts(tg_id INTEGER PRIMARY KEY, product_id INTEGER, player_id TEXT);
        """)
        if not c.execute("SELECT 1 FROM categories").fetchone():
            cats = [
                ("free_fire", "Free Fire", "🔥"),
                ("pubg", "PUBG Mobile", "🎯"),
                ("mlbb", "Mobile Legends", "⚔️"),
                ("brawl", "Brawl Stars", "⭐"),
                ("telegram", "Telegram Premium", "💎")
            ]
            for k, n, i in cats:
                c.execute("INSERT OR IGNORE INTO categories(key,name,icon) VALUES(?,?,?)", (k, n, i))
        if not c.execute("SELECT 1 FROM products").fetchone():
            games = [
                ("free_fire", [("110 💎", 10500), ("341 💎", 32000), ("572 💎", 53000)]),
                ("pubg", [("60 UC", 11000), ("325 UC", 55000), ("660 UC", 110000)]),
                ("mlbb", [("56 Diamonds", 11000), ("296 Diamonds", 54000)]),
                ("brawl", [("30 Gems", 12000), ("80 Gems", 31000)]),
                ("telegram", [("1 oy", 28000), ("1 yil", 250000)])
            ]
            for g_key, prods in games:
                for t, p in prods:
                    c.execute("INSERT INTO products(game,title,price,icon) VALUES(?,?,?,?)", (g_key, t, p, '💎'))
        if not c.execute("SELECT 1 FROM cards").fetchone():
            c.execute("INSERT INTO cards(number,holder) VALUES(?,?)", ("8600123456789012", "WZR PAY"))

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
            return web.json_response({"maintenance": get_setting("maintenance_reason") or "Vaqtincha tuzatuvda"}, status=503)
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
            cats = [dict(x) for x in c.execute("SELECT key,name,icon FROM categories")]
            cart = c.execute("SELECT product_id,player_id FROM carts WHERE tg_id=?", (u["tg_id"],)).fetchone()
            ids = [x[0] for x in c.execute("SELECT player_id FROM orders WHERE tg_id=? GROUP BY player_id ORDER BY MAX(id) DESC LIMIT 3", (u["tg_id"],))]
            n = c.execute("SELECT COUNT(*) FROM orders WHERE tg_id=?", (u["tg_id"],)).fetchone()[0]
            bal = c.execute("SELECT balance FROM users WHERE tg_id=?", (u["tg_id"],)).fetchone()[0]
        sup = get_setting("support_id")
        return web.json_response({
            "user": {"usr_id": u["usr_id"], "name": u["full_name"] or "Mijoz", "tg_id": u["tg_id"], "balance": bal,
                     "joined": u["started_at"], "orders": n},
            "products": prods, "categories": cats, "cart": dict(cart) if cart else None, "ids": ids,
            "links": {"news": get_setting("news_url"), "isbot": get_setting("isbot_url"),
                      "support": f"tg://user?id={sup}" if sup.isdigit() else f"https://t.me/{sup.lstrip('@')}"},
            "history": history(u["tg_id"]), "pending": pending_of(u["tg_id"]),
            "banner_text": get_setting("banner_text"), "banner_desc": get_setting("banner_desc")})

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
        if amt < 1000 or amt > 500_000:
            return web.json_response({"error": "Summa 1 000 dan 500 000 UZS gacha bo‘lishi kerak"})
        p = create_payment(r["u"]["tg_id"], amt)
        return web.json_response(p or {"error": "To'lov qabul qilinmaydi"})

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
        holder_name = str(b.get("holder_name", "")).strip()
        with conn() as c:
            c.execute("UPDATE payments SET holder_name=? WHERE id=?", (holder_name, pid))
            p = c.execute("SELECT p.*, c.number FROM payments p JOIN cards c ON c.id=p.card_id WHERE p.id=? AND p.tg_id=?", (pid, tg["id"])).fetchone()
            u = c.execute("SELECT balance FROM users WHERE tg_id=?", (tg["id"],)).fetchone()
        if p:
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"pay_ok:{pid}"),
                                                        InlineKeyboardButton(text="❌ Rad etish", callback_data=f"pay_no:{pid}")]])
            await tell_admins(f"🔔 <b>To'lov tekshiruvi #{pid}</b>\n"
                              f"Mijoz: {hesc(tg.get('first_name',''))} @{tg.get('username','-')} (<code>{tg['id']}</code>)\n"
                              f"F.I.Sh: <b>{hesc(holder_name)}</b>\n"
                              f"Hisobdagi balans: <b>{fmt(u['balance'] if u else 0)} UZS</b>\n"
                              f"To'lov summasi: <b>{fmt(p['exact'])} UZS</b>\nKarta: ••{p['number'][-4:]}", kb)
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
        [InlineKeyboardButton(text="Xaridga o'tish 🚀", web_app=WebAppInfo(url=WEBAPP_URL))],
        [InlineKeyboardButton(text="⚡ Yangiliklar", url=get_setting("news_url")),
         InlineKeyboardButton(text="🥷 Qo'llab-quvvatlash", url=support_url)],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)

USER_TEXT = (
    "🦖 Xush kelibsiz, <b>{name}</b>!\n\n"
    "🛡 <b>Wazzr BOT</b> orqali o'yinlarga tez va ishonchli donat qilishingiz mumkin."
)

ADMIN_TEXT = (
    "👑 Assalomu alaykum, <b>{name}</b>!\n\n"
    "✨ Siz <b>adminsiz</b>. Boshqaruv menyusi pastda 👇"
)

@router.message(CommandStart())
async def start(m: Message):
    u = upsert_user(m.from_user.id, m.from_user.username, m.from_user.full_name)
    if u["banned"] and u["ban_until"] > time.time():
        await m.answer(f"🚫 Siz vaqtincha bloklangansiz.\nSabab: {u['ban_reason']}")
        return
    kb = admin_kb() if is_admin(m.from_user.id, SUPER_ADMIN) else user_keyboard()
    txt = ADMIN_TEXT.format(name=hesc(m.from_user.full_name)) if is_admin(m.from_user.id, SUPER_ADMIN) else USER_TEXT.format(name=hesc(m.from_user.full_name))
    await m.answer(txt, parse_mode="HTML", reply_markup=kb)

@router.message(Command("help"))
async def help_(m: Message):
    support = get_setting("support_id")
    url = f"tg://user?id={support}" if support.isdigit() else f"https://t.me/{support.lstrip('@')}"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Admin bilan bog'lanish", url=url)]])
    await m.answer("Savol yoki muammo bo'lsa admin bilan bog'laning 👇", reply_markup=kb)

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
            await cb.bot.send_message(o["tg_id"], f"✅ Buyurtma #{o['id']} bajarildi: {o['title']}.")
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
        ["📦 Mahsulotlar", "👥 Hisobot"],
        ["📢 Reklama", "⚙️ Sozlamalar"]
    ]
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=t) for t in r] for r in rows], resize_keyboard=True)

def ikb(rows):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d) for t, d in r] for r in rows])

class AddCard(StatesGroup):
    number = State()
    holder = State()

class AddProd(StatesGroup):
    game = State()
    title = State()
    price = State()
    icon = State()

class AddCat(StatesGroup):
    key = State()
    name = State()
    icon = State()

class Bcast(StatesGroup):
    msg = State()

async def tell_user_paid(bot, p):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Davom etish ▶️", web_app=WebAppInfo(url=WEBAPP_URL))],
                                               [InlineKeyboardButton(text="Bekor qilish ✖️", callback_data="home")]])
    try:
        await bot.send_message(p["tg_id"], f"✅ <b>TO'LOV TASDIQLANDI!</b>\n\n+{money(p['exact'])} UZS balansingizga qo'shildi.\n"
                               "F.I.Sh: <b>{}</b>\nSavdoni davom ettiramizmi?".format(hesc(p.get('holder_name','-'))), parse_mode="HTML", reply_markup=kb)
    except Exception:
        pass

@adm.message(Command("admin"))
async def a_menu(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("👑 <b>Admin paneli</b>", parse_mode="HTML", reply_markup=admin_kb())

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
                 onew=q("SELECT COUNT(*) FROM orders WHERE status='new'"), odone=q("SELECT COUNT(*) FROM orders WHERE status='done'"),
                 sales=q("SELECT COALESCE(SUM(price),0) FROM orders"), bal=q("SELECT COALESCE(SUM(balance),0) FROM users"))
    await m.answer(f"📊 <b>Statistika</b>\n\n👥 Foydalanuvchilar: <b>{d['users']}</b>\n🟢 Faol: {d['online']}\n🚫 Bloklagan: {d['blocked']}\n\n"
                   f"💰 Bugungi to'lovlar: <b>{money(d['today'])} UZS</b>\n💰 Jami: {money(d['allp'])} UZS\n"
                   f"👛 Balanslar: {money(d['bal'])} UZS\n\n🛒 Yangi buyurtmalar: <b>{d['onew']}</b>\n✅ Bajarilgan: {d['odone']}", parse_mode="HTML")

@adm.message(F.text == "💳 To'lovlar")
async def a_pay(m: Message, state: FSMContext):
    await state.clear()
    with conn() as c:
        rows = c.execute("SELECT p.*, u.username, u.full_name FROM payments p LEFT JOIN users u ON u.tg_id=p.tg_id ORDER BY p.id DESC LIMIT 10").fetchall()
    if not rows:
        return await m.answer("Hali to'lovlar yo'q.")
    for r in rows:
        flag = "🔔 Mijoz to'lov qildi!\n" if r["claimed"] else ""
        await m.answer(f"{flag}#{r['id']} · <b>{money(r['exact'])} UZS</b>\n"
                       f"F.I.Sh: {hesc(r['holder_name'] or 'Kiritilmagan')}\n"
                       f"User: @{r['username'] or '-'} ({r['tg_id']})\nHolat: {r['status']}",
                       parse_mode="HTML", reply_markup=ikb([[("✅ Tasdiqlash", f"pay_ok:{r['id']}"), ("❌ Rad etish", f"pay_no:{r['id']}")]]))

@adm.callback_query(F.data.startswith("pay_ok:"))
async def pay_ok(cb: CallbackQuery):
    pid = int(cb.data.split(":")[1])
    p = confirm_payment(pid)
    if not p:
        return await cb.answer("Allaqachon tasdiqlangan", show_alert=True)
    await tell_user_paid(cb.bot, p)
    await cb.message.edit_text(cb.message.text + f"\n\n✅ TASDIQLANDI ({tdt(time.time())})")
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
            await cb.bot.send_message(p["tg_id"], f"❌ #{pid} to'lov rad etildi.")
        except Exception:
            pass
    await cb.message.edit_text(cb.message.text + "\n\n❌ RAD ETILDI")
    await cb.answer()

@adm.message(F.text == "📦 Mahsulotlar")
async def a_products_menu(m: Message, state: FSMContext):
    await state.clear()
    with conn() as c:
        cats = c.execute("SELECT * FROM categories").fetchall()
        prods = c.execute("SELECT * FROM products").fetchall()
    text = f"📦 <b>Kategoriyalar va Mahsulotlar boshqaruvi</b>\n\nJami kategoriyalar: {len(cats)}\nJami mahsulotlar: {len(prods)}"
    kb = ikb([
        [("➕ Kategoriya qo'shish", "cat_add"), ("➕ Mahsulot qo'shish", "prod_add")],
        [("📋 Mahsulotlar ro'yxati", "prod_list"), ("📁 Kategoriyalar ro'yxati", "cat_list")]
    ])
    await m.answer(text, parse_mode="HTML", reply_markup=kb)

@adm.callback_query(F.data == "cat_add")
async def cat_add_start(cb: CallbackQuery, state: FSMContext):
    await state.set_state(AddCat.key)
    await cb.message.answer("Kategoriya kodini kiriting (masalan: `pubg`, `free_fire`):", parse_mode="HTML")
    await cb.answer()

@adm.message(AddCat.key)
async def cat_key(m: Message, state: FSMContext):
    await state.update_data(key=m.text.strip().lower())
    await state.set_state(AddCat.name)
    await m.answer("Kategoriya nomini kiriting (masalan: `PUBG Mobile`):")

@adm.message(AddCat.name)
async def cat_name(m: Message, state: FSMContext):
    await state.update_data(name=m.text.strip())
    await state.set_state(AddCat.icon)
    await m.answer("Kategoriya ikonkasini (emoji yoki rasm belgisi) yuboring (masalan: `🎯`):")

@adm.message(AddCat.icon)
async def cat_save(m: Message, state: FSMContext):
    d = await state.get_data()
    await state.clear()
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO categories(key,name,icon) VALUES(?,?,?)", (d["key"], d["name"], m.text.strip()))
    await m.answer("✅ Kategoriya muvaffaqiyatli qo'shildi!")

@adm.callback_query(F.data == "prod_add")
async def prod_add_start(cb: CallbackQuery, state: FSMContext):
    with conn() as c:
        cats = c.execute("SELECT key, name FROM categories").fetchall()
    if not cats:
        return await cb.answer("Avval kategoriya qo'shing!", show_alert=True)
    kb = ikb([[(c["name"], f"p_cat:{c['key']}")] for c in cats])
    await cb.message.answer("Mahsulot qaysi kategoriyaga tegishli ekanini tanlang:", reply_markup=kb)
    await cb.answer()

@adm.callback_query(F.data.startswith("p_cat:"))
async def prod_cat_chosen(cb: CallbackQuery, state: FSMContext):
    game = cb.data.split(":")[1]
    await state.update_data(game=game)
    await state.set_state(AddProd.title)
    await cb.message.answer("Mahsulot nomini kiriting (masalan: `60 UC` yoki `100 💎`):")
    await cb.answer()

@adm.message(AddProd.title)
async def prod_title(m: Message, state: FSMContext):
    await state.update_data(title=m.text.strip())
    await state.set_state(AddProd.price)
    await m.answer("Narxini so'mda kiriting (masalan: `11000`):")

@adm.message(AddProd.price)
async def prod_price(m: Message, state: FSMContext):
    try:
        price = int(m.text.strip())
    except ValueError:
        return await m.answer("Faqat raqam kiriting:")
    await state.update_data(price=price)
    await state.set_state(AddProd.icon)
    await m.answer("Mahsulot ikonkasini (emoji) yuboring (masalan: `💎`):")

@adm.message(AddProd.icon)
async def prod_save(m: Message, state: FSMContext):
    d = await state.get_data()
    await state.clear()
    with conn() as c:
        c.execute("INSERT INTO products(game,title,price,icon) VALUES(?,?,?,?)", (d["game"], d["title"], d["price"], m.text.strip()))
    await m.answer("✅ Mahsulot muvaffaqiyatli qo'shildi!")

@adm.callback_query(F.data == "prod_list")
async def prod_list(cb: CallbackQuery):
    with conn() as c:
        prods = c.execute("SELECT * FROM products ORDER BY game, price").fetchall()
    if not prods:
        return await cb.answer("Mahsulotlar yo'q", show_alert=True)
    lines = [f"{p['icon']} <b>{p['title']}</b> ({p['game']}) — {money(p['price'])} UZS" for p in prods[:20]]
    await cb.message.edit_text("📦 <b>Mahsulotlar:</b>\n\n" + "\n".join(lines), parse_mode="HTML")
    await cb.answer()

@adm.callback_query(F.data == "cat_list")
async def cat_list(cb: CallbackQuery):
    with conn() as c:
        cats = c.execute("SELECT * FROM categories").fetchall()
    lines = [f"{c['icon']} <b>{c['name']}</b> (<code>{c['key']}</code>)" for c in cats]
    await cb.message.edit_text("📁 <b>Kategoriyalar:</b>\n\n" + "\n".join(lines), parse_mode="HTML")
    await cb.answer()

@adm.message(F.text == "🎴 Kartalar")
async def a_cards(m: Message):
    with conn() as c:
        cards = c.execute("SELECT * FROM cards").fetchall()
    text = "🎴 <b>Kartalar</b> (5 tagacha)\n\n" + ("\n".join(f"#{x['id']} <code>{x['number']}</code> · {hesc(x['holder'])}" for x in cards) or "Karta yo'q.")
    await m.answer(text, parse_mode="HTML", reply_markup=ikb([[("➕ Karta qo'shish", "card_add")]] + [[(f"🗑 #{x['id']} o'chirish", f"card_d:{x['id']}")] for x in cards]))

@adm.callback_query(F.data.startswith("card_d:"))
async def card_del(cb: CallbackQuery):
    cid = int(cb.data.split(":")[1])
    with conn() as c:
        c.execute("DELETE FROM cards WHERE id=?", (cid,))
    await cb.message.edit_text("✅ Karta o'chirildi.")
    await cb.answer()

@adm.callback_query(F.data == "card_add")
async def card_add(cb: CallbackQuery, state: FSMContext):
    await state.set_state(AddCard.number)
    await cb.message.answer("16 xonali karta raqamini yuboring:")
    await cb.answer()

@adm.message(AddCard.number)
async def card_num(m: Message, state: FSMContext):
    n = re.sub(r"\D", "", m.text or "")
    if len(n) != 16:
        return await m.answer("16 ta raqam bo'lishi shart:")
    await state.update_data(number=n)
    await state.set_state(AddCard.holder)
    await m.answer("Karta egasining F.I.Sh ni yuboring:")

@adm.message(AddCard.holder)
async def card_holder(m: Message, state: FSMContext):
    d = await state.get_data()
    await state.clear()
    with conn() as c:
        c.execute("INSERT INTO cards(number,holder) VALUES(?,?)", (d["number"], (m.text or "").upper()[:40]))
    await m.answer("✅ Karta muvaffaqiyatli qo'shildi!")

@adm.message(F.text == "👥 Hisobot")
async def a_users_report(m: Message):
    with conn() as c:
        rows = c.execute("SELECT * FROM users ORDER BY started_at DESC LIMIT 10").fetchall()
        total = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    kb_list = [[(f"👤 {r['full_name'][:15]} (@{r['username'] or 'yoq'})", f"u_info:{r['tg_id']}") for r in rows]]
    kb_list.append([("🔄 Yangilash", "refresh_report")])
    await m.answer(f"👥 <b>Foydalanuvchilar hisoboti</b> (Jami: {total})", parse_mode="HTML", reply_markup=ikb(kb_list))

@adm.callback_query(F.data == "refresh_report")
async def refresh_rep(cb: CallbackQuery):
    with conn() as c:
        rows = c.execute("SELECT * FROM users ORDER BY started_at DESC LIMIT 10").fetchall()
    kb_list = [[(f"👤 {r['full_name'][:15]}", f"u_info:{r['tg_id']}") for r in rows]]
    kb_list.append([("🔄 Yangilash", "refresh_report")])
    await cb.message.edit_text("👥 <b>Yangilangan hisobot:</b>", parse_mode="HTML", reply_markup=ikb(kb_list))
    await cb.answer("Yangilandi!")

@adm.callback_query(F.data.startswith("u_info:"))
async def u_detail(cb: CallbackQuery):
    uid = int(cb.data.split(":")[1])
    with conn() as c:
        u = c.execute("SELECT * FROM users WHERE tg_id=?", (uid,)).fetchone()
    if not u:
        return await cb.answer("Topilmadi", show_alert=True)
    text = (f"👤 <b>Foydalanuvchi ma'lumoti:</b>\n\nF.I.Sh: {hesc(u['full_name'])}\n"
            f"Username: @{u['username'] or '-'}\nID: <code>{u['tg_id']}</code>\nBalans: {money(u['balance'])} UZS")
    await cb.message.edit_text(text, parse_mode="HTML")
    await cb.answer()

@adm.message(F.text == "📢 Reklama")
async def a_bcast(m: Message, state: FSMContext):
    await state.set_state(Bcast.msg)
    await m.answer("📢 Reklama xabarini yuboring (matn, rasm, video):")

@adm.message(Bcast.msg)
async def bc_preview(m: Message, state: FSMContext):
    with conn() as c:
        n = c.execute("SELECT COUNT(*) FROM users WHERE banned=0 AND blocked_bot=0").fetchone()[0]
    await state.update_data(cid=m.chat.id, mid=m.message_id)
    await m.answer("👆 Reklama korinishi.")
    await m.copy_to(m.chat.id)
    await m.answer(f"Hammaga yuborilsinmi? Yetkaziladi: <b>{n}</b> ta.", parse_mode="HTML", reply_markup=ikb([[("✅ Tasdiqlash", "bc_go"), ("❌ Bekor", "bc_no")]]))

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
    await cb.message.edit_text("📤 Yuborilmoqda...")
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
    await bot.send_message(admin_id, f"📢 Reklama yakunlandi.\n✅ Yetkazildi: {ok}\n🚫 Bloklagan: {fail}")

@adm.message(F.text == "⚙️ Sozlamalar")
async def a_settings(m: Message):
    await m.answer("⚙️ Sozlamalar menyusi:\nKanallar va havolalarni bot bazasidan boshqarishingiz mumkin.")

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
