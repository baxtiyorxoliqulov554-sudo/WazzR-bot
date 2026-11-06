"""WAZZR BOT: Admin chat tahrirlash va mukammallashtirilgan Mini App backend kodi.
"""
import asyncio, logging, os, sqlite3, secrets, string, time, random, hmac, hashlib, json, re
from urllib.parse import parse_qsl
from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart, Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from html import escape as hesc
from aiogram.types import (Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
                           WebAppInfo, ReplyKeyboardMarkup, KeyboardButton)

BOT_TOKEN   = os.getenv("BOT_TOKEN", "9185114954:AAHrJk0G0teFH8ec3bLRHabFNZ6WezHRkUE")
SUPER_ADMIN = int(os.getenv("SUPER_ADMIN_ID", 8520429829))
WEBAPP_URL  = os.getenv("WEBAPP_URL", "https://wazzr-bot.onrender.com/")
PORT        = int(os.environ.get("PORT", 8082))

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
            balance INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS admins(tg_id INTEGER PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
        """)
        defaults = {
            "news_url": "https://t.me/WazzR_VIP",
            "support_id": "8520429829",
            "isbot_url": "https://t.me/WazzR_Isbot",
            "maintenance": "0",
            "banner_text": "⚡ WAZZR DONAT STORE",
            "banner_desc": "O'yinlarga 24/7 tezkor va arzon donat!"
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

def upsert_user(tg_id, username, full_name):
    now = int(time.time())
    with conn() as c:
        r = c.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,)).fetchone()
        if r:
            c.execute("UPDATE users SET username=?, full_name=?, last_seen=? WHERE tg_id=?", (username, full_name, now, tg_id))
        else:
            uid = "usr_" + "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(8))
            c.execute("INSERT INTO users(tg_id,usr_id,username,full_name,started_at,last_seen) VALUES(?,?,?,?,?,?)",
                      (tg_id, uid, username, full_name, now, now))
        return c.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,)).fetchone()

def is_admin(tg_id, super_id):
    return tg_id == super_id

def shop_init():
    with conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS cards(id INTEGER PRIMARY KEY AUTOINCREMENT, number TEXT, holder TEXT, active INTEGER DEFAULT 1);
        CREATE TABLE IF NOT EXISTS categories(id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT UNIQUE, name TEXT, icon TEXT DEFAULT '🔥');
        CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY AUTOINCREMENT, game TEXT, title TEXT, price INTEGER, icon TEXT DEFAULT '💎', active INTEGER DEFAULT 1);
        CREATE TABLE IF NOT EXISTS payments(id INTEGER PRIMARY KEY AUTOINCREMENT, tg_id INTEGER, base INTEGER, exact INTEGER, card_id INTEGER, status TEXT, created INTEGER, expires INTEGER, paid_at INTEGER, holder_name TEXT, claimed INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY AUTOINCREMENT, tg_id INTEGER, product_id INTEGER, game TEXT, title TEXT, price INTEGER, player_id TEXT, status TEXT, created INTEGER);
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
            prods = [
                ("free_fire", "110 💎", 10500, "💎"), ("free_fire", "341 💎", 32000, "💎"),
                ("pubg", "60 UC", 11000, "🪙"), ("pubg", "325 UC", 55000, "🪙"),
                ("telegram", "1 Oy Premium", 28000, "🌟")
            ]
            for g, t, p, i in prods:
                c.execute("INSERT INTO products(game,title,price,icon) VALUES(?,?,?,?)", (g, t, p, i))
        if not c.execute("SELECT 1 FROM cards").fetchone():
            c.execute("INSERT INTO cards(number,holder) VALUES(?,?)", ("8600123456789012", "WAZZR BOT"))

def verify_init(init_data, token):
    d = dict(parse_qsl(init_data, keep_blank_values=True))
    h = d.pop("hash", None)
    s = "\n".join(f"{k}={v}" for k, v in sorted(d.items()))
    key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    if not h or not hmac.compare_digest(hmac.new(key, s.encode(), hashlib.sha256).hexdigest(), h):
        return None
    return json.loads(d["user"])

fmt = lambda n: f"{n:,}".replace(",", " ")

def make_app(bot, token, super_admin):
    @web.middleware
    async def auth(request, handler):
        if not request.path.startswith("/api/"):
            return await handler(request)
        init = request.headers.get("X-Init", "")
        u = verify_init(init, token)
        if not u:
            return web.json_response({"error": "Avtorizatsiya xatosi"}, status=401)
        row = upsert_user(u["id"], u.get("username"), (u.get("first_name", "") + " " + u.get("last_name", "")).strip())
        request["tg"], request["u"] = u, row
        return await handler(request)

    async def index(r):
        return web.FileResponse(os.path.join(HERE, "index.html"), headers={"Cache-Control": "no-store"})

    async def state(r):
        u = r["u"]
        with conn() as c:
            prods = [dict(x) for x in c.execute("SELECT id,game,title,price,icon FROM products WHERE active=1 ORDER BY price")]
            cats = [dict(x) for x in c.execute("SELECT key,name,icon FROM categories")]
            bal = c.execute("SELECT balance FROM users WHERE tg_id=?", (u["tg_id"],)).fetchone()[0]
        sup = get_setting("support_id")
        return web.json_response({
            "user": {"usr_id": u["usr_id"], "name": u["full_name"] or "Mijoz", "tg_id": u["tg_id"], "balance": bal},
            "products": prods, "categories": cats,
            "banner_text": get_setting("banner_text"), "banner_desc": get_setting("banner_desc")
        })

    async def order(r):
        b = await r.json()
        pid, player_id = int(b.get("product_id", 0)), str(b.get("player_id", "")).strip()
        tg = r["tg"]
        with conn() as c:
            p = c.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
            if not p: return web.json_response({"error": "Mahsulot topilmadi"})
            bal = c.execute("SELECT balance FROM users WHERE tg_id=?", (tg["id"],)).fetchone()[0]
            if bal < p["price"]: return web.json_response({"error": "insufficient", "balance": bal, "need": p["price"]})
            c.execute("UPDATE users SET balance=balance-? WHERE tg_id=?", (p["price"], tg["id"]))
            cur = c.execute("INSERT INTO orders(tg_id,product_id,game,title,price,player_id,status,created) VALUES(?,?,?,?,?,?,?,?)",
                            (tg["id"], pid, p["game"], p["title"], p["price"], player_id, "new", int(time.time())))
            oid = cur.lastrowid
        
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Bajarildi", callback_data=f"done:{oid}")]])
        await bot.send_message(super_admin, f"🛒 <b>Yangi Buyurtma #{oid}</b>\n\nMahsulot: <b>{p['title']}</b>\nPlayer ID: <code>{player_id}</code>\nMijoz: @{tg.get('username','-')} (<code>{tg['id']}</code>)", parse_mode="HTML", reply_markup=kb)
        return web.json_response({"ok": True, "balance": bal - p["price"]})

    async def topup(r):
        b = await r.json()
        amt = int(b.get("amount", 0))
        holder = str(b.get("holder_name", "")).strip()
        tg = r["tg"]
        with conn() as c:
            card = c.execute("SELECT * FROM cards WHERE active=1 LIMIT 1").fetchone()
            if not card: return web.json_response({"error": "Karta mavjud emas"})
            exact = amt + random.randint(1, 99)
            cur = c.execute("INSERT INTO payments(tg_id,base,exact,card_id,status,created,expires,holder_name,claimed) VALUES(?,?,?,?,?,?,?,?,1)",
                            (tg["id"], amt, exact, card["id"], "pending", int(time.time()), int(time.time())+300, holder))
            pid = cur.lastrowid
            bal = c.execute("SELECT balance FROM users WHERE tg_id=?", (tg["id"],)).fetchone()[0]
        
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"pay_ok:{pid}"), InlineKeyboardButton(text="❌ Rad etish", callback_data=f"pay_no:{pid}")]])
        await bot.send_message(super_admin, f"🔔 <b>To'lov Tekshiruvi #{pid}</b>\n\nMijoz: @{tg.get('username','-')} (<code>{tg['id']}</code>)\nF.I.Sh: <b>{hesc(holder)}</b>\nTo'lov Summasi: <b>{fmt(exact)} UZS</b>\nKarta: <code>{card['number']}</code>", parse_mode="HTML", reply_markup=kb)
        return web.json_response({"card": card["number"], "holder": card["holder"], "exact": exact})

    app = web.Application(middlewares=[auth])
    app.add_routes([
        web.get("/", index),
        web.post("/api/state", state),
        web.post("/api/order", order),
        web.post("/api/topup", topup)
    ])
    return app

router = Router()

def admin_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🛒 Buyurtmalar"), KeyboardButton(text="📦 Mahsulot Qo'shish")],
        [KeyboardButton(text="💳 Karta Sozlash"), KeyboardButton(text="📊 Statistika")]
    ], resize_keyboard=True)

class AddProd(StatesGroup):
    game = State()
    title = State()
    price = State()
    icon = State()

@router.message(CommandStart())
async def start(m: Message):
    upsert_user(m.from_user.id, m.from_user.username, m.from_user.full_name)
    kb = admin_kb() if is_admin(m.from_user.id, SUPER_ADMIN) else InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Xaridga o'tish 🚀", web_app=WebAppInfo(url=WEBAPP_URL))]])
    await m.answer(f"Assalomu alaykum <b>{hesc(m.from_user.full_name)}</b>!\nWazzr Bot-ga xush kelibsiz.", parse_mode="HTML", reply_markup=kb)

@router.message(F.text == "📦 Mahsulot Qo'shish")
async def add_prod_btn(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id, SUPER_ADMIN): return
    with conn() as c:
        cats = c.execute("SELECT key, name FROM categories").fetchall()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=c["name"], callback_data=f"pcat:{c['key']}")] for c in cats])
    await m.answer("Kategoriyani tanlang:", reply_markup=kb)

@router.callback_query(F.data.startswith("pcat:"))
async def pcat_chosen(cb: CallbackQuery, state: FSMContext):
    await state.update_data(game=cb.data.split(":")[1])
    await state.set_state(AddProd.title)
    await cb.message.answer("Mahsulot nomini yuboring (masalan: `100 💎`):", parse_mode="HTML")

@router.message(AddProd.title)
async def prod_title(m: Message, state: FSMContext):
    await state.update_data(title=m.text.strip())
    await state.set_state(AddProd.price)
    await m.answer("Mahsulot narxini so'mda yuboring (masalan: `12000`):")

@router.message(AddProd.price)
async def prod_price(m: Message, state: FSMContext):
    try: price = int(m.text.strip())
    except: return await m.answer("Faqat raqam yuboring:")
    await state.update_data(price=price)
    await state.set_state(AddProd.icon)
    await m.answer("Mahsulot uchun ikonka (emoji) yuboring (masalan: `💎`, `🪙`):")

@router.message(AddProd.icon)
async def prod_icon(m: Message, state: FSMContext):
    d = await state.get_data()
    await state.clear()
    with conn() as c:
        c.execute("INSERT INTO products(game,title,price,icon) VALUES(?,?,?,?)", (d["game"], d["title"], d["price"], m.text.strip()))
    await m.answer("✅ Mahsulot muvaffaqiyatli qo'shildi va Mini App'da darhol paydo bo'ldi!")

@router.callback_query(F.data.startswith("pay_ok:"))
async def pay_ok(cb: CallbackQuery):
    pid = int(cb.data.split(":")[1])
    with conn() as c:
        p = c.execute("SELECT * FROM payments WHERE id=? AND status='pending'", (pid,)).fetchone()
        if p:
            c.execute("UPDATE payments SET status='paid' WHERE id=?", (pid,))
            c.execute("UPDATE users SET balance=balance+? WHERE tg_id=?", (p["exact"], p["tg_id"]))
    if p:
        try: await cb.bot.send_message(p["tg_id"], f"✅ To'lovingiz tasdiqlandi! Balansingizga +{fmt(p['exact'])} UZS qo'shildi.")
        except: pass
    await cb.message.edit_text(cb.message.text + "\n\n✅ TASDIQLANDI")

@router.callback_query(F.data.startswith("pay_no:"))
async def pay_no(cb: CallbackQuery):
    pid = int(cb.data.split(":")[1])
    with conn() as c:
        c.execute("UPDATE payments SET status='cancelled' WHERE id=?", (pid,))
    await cb.message.edit_text(cb.message.text + "\n\n❌ RAD ETILDI")

async def main():
    logging.basicConfig(level=logging.INFO)
    db_init(); shop_init()
    bot = Bot(TOKEN)
    runner = web.AppRunner(make_app(bot, TOKEN, SUPER_ADMIN))
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', PORT).start()
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
