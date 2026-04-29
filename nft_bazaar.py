"""
╔══════════════════════════════════════════════════════════╗
║       NFT Bazaar — Telegram Mini App + Bot               ║
║       Created by @owning07 | Support: @owning077         ║
╚══════════════════════════════════════════════════════════╝

SETUP (edit CONFIG below, then upload):
  1. Fill in BOT_TOKEN, ADMIN_IDS, APP_URL
  2. Upload to Wispbyte (Python server)
  3. Startup command: python nft_bazaar.py
  4. Install: pip install aiogram==3.13.0 aiosqlite aiohttp
  5. BotFather → /mybots → Bot → Menu Button → set APP_URL
"""

import asyncio
import logging
import aiosqlite
import os
from datetime import datetime
from aiohttp import web, ClientSession
from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    CallbackQuery, Message, WebAppInfo,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ╔══════════════════════════════════════╗
# ║           EDIT THIS SECTION          ║
# ╚══════════════════════════════════════╝
BOT_TOKEN  = "8702845993:AAH_3yTfyRLwwCSX7n8wyjNpE4CocmKIuMM"
ADMIN_IDS  = [7879101503, 8561142779]
APP_URL    = "https://YOUR-APP-NAME.onrender.com"  # ← paste your Render URL here
PORT       = int(os.getenv("PORT", "10000"))  # Render assigns this
SHOP_NAME  = "NFT Bazaar"
CREATED_BY = "@owning07"
SUPPORT    = "@owning077"
DB_PATH    = "nft_bazaar.db"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp  = Dispatcher(storage=MemoryStorage())

# In-memory image URL cache: nft_id -> telegram file URL
_img_cache: dict[int, str] = {}

# ══════════════════════════════════════
#  FSM STATES
# ══════════════════════════════════════
class S(StatesGroup):
    nft_image        = State()
    nft_name         = State()
    nft_desc         = State()
    nft_price_inr    = State()
    nft_price_ton    = State()
    nft_price_usdt   = State()
    nft_delivery     = State()
    set_upi          = State()
    set_ton          = State()
    set_usdt         = State()
    set_qr_inr       = State()
    set_qr_ton       = State()
    set_qr_usdt      = State()
    broadcast        = State()

# ══════════════════════════════════════
#  DATABASE
# ══════════════════════════════════════
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id    INTEGER PRIMARY KEY,
                username   TEXT DEFAULT '',
                full_name  TEXT DEFAULT '',
                joined_at  TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS config (
                key   TEXT PRIMARY KEY,
                value TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS nfts (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT NOT NULL,
                description     TEXT DEFAULT '',
                image_id        TEXT DEFAULT '',
                price_inr       REAL DEFAULT 0,
                price_ton       REAL DEFAULT 0,
                price_usdt      REAL DEFAULT 0,
                delivery_type   TEXT DEFAULT 'manual',
                delivery_data   TEXT DEFAULT '',
                status          TEXT DEFAULT 'available',
                listed_by       INTEGER NOT NULL,
                listed_at       TEXT DEFAULT '',
                sold_to         INTEGER DEFAULT NULL,
                sold_at         TEXT DEFAULT NULL
            );
            CREATE TABLE IF NOT EXISTS offers (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                nft_id       INTEGER NOT NULL,
                buyer_id     INTEGER NOT NULL,
                buyer_user   TEXT DEFAULT '',
                offer_price  REAL NOT NULL,
                currency     TEXT NOT NULL,
                status       TEXT DEFAULT 'pending',
                created_at   TEXT DEFAULT '',
                resolved_at  TEXT DEFAULT NULL
            );
            CREATE TABLE IF NOT EXISTS orders (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                nft_id       INTEGER NOT NULL,
                buyer_id     INTEGER NOT NULL,
                buyer_user   TEXT DEFAULT '',
                price        REAL NOT NULL,
                currency     TEXT NOT NULL,
                txn_id       TEXT DEFAULT '',
                order_type   TEXT DEFAULT 'direct',
                offer_id     INTEGER DEFAULT NULL,
                status       TEXT DEFAULT 'pending',
                created_at   TEXT DEFAULT '',
                confirmed_at TEXT DEFAULT NULL
            );
        """)
        defaults = [
            ("inr_upi",""),("ton_addr",""),("usdt_addr",""),
            ("inr_qr", ""),("ton_qr",  ""),("usdt_qr",  ""),
        ]
        for k, v in defaults:
            await db.execute("INSERT OR IGNORE INTO config (key,value) VALUES (?,?)", (k, v))
        await db.commit()
    log.info("✅ DB ready")

# ══════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════
def is_adm(uid: int) -> bool: return uid in ADMIN_IDS
def now()  -> str: return datetime.now().strftime("%Y-%m-%d %H:%M")
def wm()   -> str: return f"\n\n🏪 {SHOP_NAME} | {CREATED_BY}"
def sym(c) -> str: return {"INR":"₹","TON":"💎","USDT":"💵"}.get(c,"")

async def get_cfg(key: str) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM config WHERE key=?", (key,)) as c:
            r = await c.fetchone()
    return r[0] if r else ""

async def set_cfg(key: str, value: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO config (key,value) VALUES (?,?)", (key, value))
        await db.commit()

async def reg_user(user_id: int, username: str = "", full_name: str = ""):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,)) as c:
            if not await c.fetchone():
                await db.execute(
                    "INSERT INTO users (user_id,username,full_name,joined_at) VALUES (?,?,?,?)",
                    (user_id, username, full_name, now())
                )
                await db.commit()

async def notify_admins(text: str, kb=None):
    for aid in ADMIN_IDS:
        try:
            await bot.send_message(aid, text, reply_markup=kb)
        except Exception: pass

async def get_tg_img_url(nft_id: int) -> str:
    """Get public Telegram CDN URL for an NFT image, with cache."""
    if nft_id in _img_cache:
        return _img_cache[nft_id]
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT image_id FROM nfts WHERE id=?", (nft_id,)) as c:
                row = await c.fetchone()
        if not row or not row[0]:
            return ""
        file_id = row[0]
        async with ClientSession() as sess:
            async with sess.get(
                f"https://api.telegram.org/bot{BOT_TOKEN}/getFile",
                params={"file_id": file_id}
            ) as resp:
                data = await resp.json()
        if data.get("ok"):
            path = data["result"]["file_path"]
            url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{path}"
            _img_cache[nft_id] = url
            return url
    except Exception as e:
        log.warning(f"img url error nft {nft_id}: {e}")
    return ""

def prices_str(p_inr, p_ton, p_usdt) -> str:
    parts = []
    if p_inr  > 0: parts.append(f"₹{p_inr:,.0f}")
    if p_ton  > 0: parts.append(f"💎{p_ton:g} TON")
    if p_usdt > 0: parts.append(f"💵{p_usdt:g} USDT")
    return " | ".join(parts) if parts else "Offer Only"

async def safe(cb: CallbackQuery, text: str, kb=None):
    try:
        await cb.message.edit_text(text, reply_markup=kb)
    except Exception:
        try: await cb.message.answer(text, reply_markup=kb)
        except Exception: pass

def kb_home():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔙 Back", callback_data="M_admin")
    ]])

def kb_cancel():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ Cancel", callback_data="M_admin")
    ]])

# ══════════════════════════════════════
#  MINI APP HTML
# ══════════════════════════════════════
HTML_APP = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>NFT Market</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<link href="https://fonts.googleapis.com/css2?family=Syne:wght@700;800&family=DM+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent}
:root{
  --bg:#f4f4f0;--surf:#fff;--card:#fff;
  --bdr:#e8e8e2;--bdr2:#d4d4cc;
  --accent:#0a0a0a;--gold:#c8a84b;
  --ton:#0098ea;--green:#00b86b;--red:#e53935;
  --txt:#0a0a0a;--sub:#888880;--sub2:#bbbbb4;
  --r:18px;--r2:14px;--r3:10px;
}
body{background:var(--bg);color:var(--txt);font-family:'DM Sans',sans-serif;min-height:100vh;overflow-x:hidden;padding-bottom:80px}
::-webkit-scrollbar{width:0}

/* ── SPLASH ── */
#splash{position:fixed;inset:0;z-index:9999;background:#0a0a0a;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:0;transition:opacity .5s ease}
#splash.hide{opacity:0;pointer-events:none}
.splash-logo{font-family:'Syne',sans-serif;font-size:42px;font-weight:800;color:#fff;letter-spacing:-1px;margin-bottom:8px;animation:splashIn .6s ease forwards}
.splash-sub{font-size:13px;color:#666;letter-spacing:3px;text-transform:uppercase;animation:splashIn .6s .15s ease both}
.splash-bar-wrap{width:180px;height:2px;background:#222;border-radius:2px;margin-top:32px;overflow:hidden;animation:splashIn .4s .3s ease both}
.splash-bar{height:100%;background:linear-gradient(90deg,#c8a84b,#fff);border-radius:2px;animation:loadbar 1.8s ease forwards}
@keyframes splashIn{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:none}}
@keyframes loadbar{from{width:0}to{width:100%}}
.splash-nfts{position:absolute;bottom:48px;display:flex;gap:14px;opacity:.25;animation:splashIn .6s .5s ease both}
.splash-nft{width:54px;height:54px;border-radius:12px;background:#1a1a1a;border:1px solid #333}

/* ── TOPBAR ── */
.topbar{position:sticky;top:0;z-index:100;background:rgba(244,244,240,.92);backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);border-bottom:1px solid var(--bdr);padding:10px 14px;display:flex;align-items:center;justify-content:space-between}
.logo{font-family:'Syne',sans-serif;font-size:19px;font-weight:800;letter-spacing:-0.5px}
.logo span{color:var(--gold)}
.topbar-right{display:flex;align-items:center;gap:8px}
.live-pill{background:#00b86b18;border:1px solid #00b86b44;color:var(--green);font-size:10px;font-weight:600;padding:3px 9px;border-radius:20px;display:flex;align-items:center;gap:4px}
.live-dot{width:5px;height:5px;background:var(--green);border-radius:50%;animation:pulse 1.4s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
.cnt-badge{background:var(--accent);color:#fff;font-size:11px;font-weight:700;padding:3px 10px;border-radius:20px}

/* ── NAV ── */
.bnav{position:fixed;bottom:0;left:0;right:0;z-index:100;background:rgba(244,244,240,.96);backdrop-filter:blur(16px);border-top:1px solid var(--bdr);display:flex;padding:4px 0 env(safe-area-inset-bottom)}
.ni{flex:1;display:flex;flex-direction:column;align-items:center;gap:3px;padding:8px 4px 5px;cursor:pointer;color:var(--sub);font-size:10px;font-weight:500;border:none;background:none;transition:color .2s;letter-spacing:.3px}
.ni.active{color:var(--accent)}
.ni-ic{font-size:20px;line-height:1}

/* ── PAGES ── */
.page{display:none;animation:pgIn .22s ease}
.page.active{display:block}
@keyframes pgIn{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}

/* ── HERO ── */
.hero{margin:12px;background:var(--accent);border-radius:var(--r);padding:20px;display:flex;align-items:center;justify-content:space-between;overflow:hidden;position:relative}
.hero::before{content:'';position:absolute;inset:0;background:radial-gradient(circle at 80% 50%,rgba(200,168,75,.25),transparent 60%)}
.hero-title{font-family:'Syne',sans-serif;font-size:22px;font-weight:800;color:#fff;line-height:1.2;margin-bottom:6px;position:relative}
.hero-sub{font-size:12px;color:#888;position:relative}
.hero-right{position:relative;font-size:48px}

/* ── SECTION ── */
.sec{padding:4px 14px 8px;display:flex;align-items:center;justify-content:space-between}
.sec-title{font-family:'Syne',sans-serif;font-size:16px;font-weight:800;letter-spacing:-0.3px}
.sec-cnt{background:var(--bdr);font-size:10px;font-weight:700;padding:3px 10px;border-radius:20px;color:var(--sub)}

/* ── NFT GRID ── */
.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;padding:0 12px 12px}
.card{background:var(--card);border:1px solid var(--bdr);border-radius:var(--r);overflow:hidden;cursor:pointer;transition:transform .15s,box-shadow .15s;position:relative}
.card:active{transform:scale(.96)}
.card-img-wrap{position:relative;aspect-ratio:1;background:linear-gradient(135deg,#f0f0ea,#e8e8e0);overflow:hidden;display:flex;align-items:center;justify-content:center;font-size:40px}
.card-img-wrap img{width:100%;height:100%;object-fit:cover}
.card-body{padding:10px}
.card-name{font-family:'Syne',sans-serif;font-size:13px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-bottom:2px}
.card-num{font-size:10px;color:var(--sub);margin-bottom:7px}
.price-row{display:flex;align-items:center;gap:6px}
.ton-ico{width:14px;height:14px;background:var(--ton);border-radius:50%;display:inline-flex;align-items:center;justify-content:center;font-size:8px;color:#fff;font-weight:800;flex-shrink:0}
.price-val{font-weight:700;font-size:13px;color:var(--ton)}
.price-inr{font-size:10px;color:var(--sub);margin-left:2px}
.cart-btn{position:absolute;bottom:10px;right:10px;width:28px;height:28px;background:var(--accent);color:#fff;border:none;border-radius:8px;font-size:14px;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:transform .1s}
.cart-btn:active{transform:scale(.88)}
.live-tag{position:absolute;top:8px;left:8px;background:#00b86b;color:#fff;font-size:8px;font-weight:800;padding:2px 7px;border-radius:20px;letter-spacing:.5px}

/* ── SHEET ── */
.overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:200;backdrop-filter:blur(4px);align-items:flex-end}
.overlay.on{display:flex}
.sheet{background:var(--bg);border-radius:24px 24px 0 0;width:100%;max-height:92vh;overflow-y:auto;animation:sheetUp .28s cubic-bezier(.34,1.56,.64,1)}
@keyframes sheetUp{from{transform:translateY(100%)}to{transform:none}}
.handle{width:36px;height:4px;background:var(--bdr2);border-radius:2px;margin:12px auto 0}
.sc{padding:16px}
.dimg-wrap{width:100%;aspect-ratio:1;border-radius:var(--r);background:linear-gradient(135deg,#f0f0ea,#e4e4dc);overflow:hidden;display:flex;align-items:center;justify-content:center;font-size:80px;margin-bottom:16px}
.dimg-wrap img{width:100%;height:100%;object-fit:cover}
.d-name{font-family:'Syne',sans-serif;font-size:24px;font-weight:800;letter-spacing:-0.5px;margin-bottom:4px}
.d-desc{color:var(--sub);font-size:13px;line-height:1.6;margin-bottom:16px}
.price-card{background:var(--surf);border:1px solid var(--bdr);border-radius:var(--r2);padding:14px;margin-bottom:14px}
.price-lbl{font-size:10px;color:var(--sub);text-transform:uppercase;letter-spacing:1px;margin-bottom:10px;font-weight:600}
.prow{display:flex;align-items:center;gap:10px;padding:5px 0}
.prow-ico{font-size:20px;width:32px;text-align:center}
.prow-val{font-weight:700;font-size:15px}
.prow-name{color:var(--sub);font-size:11px;margin-left:2px}
.btns{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:4px}
.btn{padding:14px;border-radius:var(--r2);border:none;font-size:13px;font-weight:700;cursor:pointer;transition:opacity .2s,transform .1s;display:flex;align-items:center;justify-content:center;gap:6px;font-family:'DM Sans',sans-serif}
.btn:active{transform:scale(.96);opacity:.85}
.btn-p{background:var(--accent);color:#fff}
.btn-s{background:var(--surf);border:1.5px solid var(--bdr2);color:var(--txt)}
.btn-g{background:var(--green);color:#fff}
.btn-ton{background:var(--ton);color:#fff}
.btn-full{grid-column:1/-1}

/* ── CURRENCY SELECT ── */
.cur-opt{background:var(--surf);border:1.5px solid var(--bdr);border-radius:var(--r2);padding:14px;margin-bottom:10px;cursor:pointer;display:flex;align-items:center;gap:12px;transition:border-color .2s,background .2s}
.cur-opt:active,.cur-opt.sel{border-color:var(--accent);background:#f8f8f4}
.cur-ic{font-size:24px;width:40px;text-align:center}
.cur-name{font-size:14px;font-weight:600}
.cur-price{font-size:12px;color:var(--sub);margin-top:1px}

/* ── PAY SCREEN ── */
.addr-card{background:var(--surf);border:1px solid var(--bdr);border-radius:var(--r2);padding:14px;margin-bottom:12px}
.addr-lbl{font-size:10px;color:var(--sub);text-transform:uppercase;letter-spacing:1px;margin-bottom:8px;font-weight:600}
.addr-val{font-family:monospace;font-size:12px;color:var(--ton);word-break:break-all;line-height:1.6}
.copy-btn{margin-top:8px;background:#0098ea12;border:1px solid #0098ea33;color:var(--ton);font-size:11px;font-weight:600;padding:6px 14px;border-radius:20px;cursor:pointer;font-family:'DM Sans',sans-serif}
.qr-wrap{width:140px;height:140px;margin:0 auto 14px;background:#fff;border:1px solid var(--bdr);border-radius:var(--r2);overflow:hidden;display:flex;align-items:center;justify-content:center}
.qr-wrap img{width:100%;height:100%;object-fit:contain}
.amt-big{font-family:'Syne',sans-serif;font-size:34px;font-weight:800;text-align:center;padding:14px 0;letter-spacing:-1px}
.txn-input{width:100%;background:var(--surf);border:1.5px solid var(--bdr);border-radius:var(--r2);color:var(--txt);font-size:13px;padding:14px;outline:none;margin-bottom:12px;font-family:monospace;resize:none;transition:border-color .2s}
.txn-input:focus{border-color:var(--accent)}
.txn-input::placeholder{color:var(--sub2)}

/* ── OFFER ── */
.off-row{display:flex;gap:10px;margin-bottom:12px}
.off-inp{flex:1;background:var(--surf);border:1.5px solid var(--bdr);border-radius:var(--r2);color:var(--txt);font-size:16px;font-weight:600;padding:14px;outline:none;font-family:'DM Sans',sans-serif}
.off-inp:focus{border-color:var(--accent)}
.off-sel{background:var(--surf);border:1.5px solid var(--bdr);border-radius:var(--r2);color:var(--txt);font-size:13px;font-weight:600;padding:14px;outline:none;-webkit-appearance:none;min-width:90px;text-align:center;font-family:'DM Sans',sans-serif}

/* ── ORDER/OFFER CARDS ── */
.ocard{background:var(--surf);border:1px solid var(--bdr);border-radius:var(--r2);padding:14px;margin-bottom:10px;display:flex;gap:12px;align-items:center}
.ocard-img{width:50px;height:50px;border-radius:var(--r3);background:linear-gradient(135deg,#f0f0ea,#e4e4dc);overflow:hidden;display:flex;align-items:center;justify-content:center;font-size:22px;flex-shrink:0}
.ocard-img img{width:100%;height:100%;object-fit:cover;border-radius:var(--r3)}
.ocard-name{font-family:'Syne',sans-serif;font-size:13px;font-weight:700;margin-bottom:4px}
.ocard-price{font-size:12px;color:var(--ton);font-weight:600;margin-bottom:5px}
.badge{display:inline-block;font-size:9px;font-weight:700;padding:2px 8px;border-radius:20px;text-transform:uppercase;letter-spacing:.5px}
.bp{background:#ff990018;color:#e08000}
.bc{background:#00b86b18;color:#00b86b}
.br{background:#e5393518;color:#e53935}
.ba{background:#0098ea18;color:#0098ea}

/* ── STEP HEADER ── */
.step-h{display:flex;align-items:center;gap:10px;margin-bottom:16px}
.back-btn{width:36px;height:36px;border-radius:50%;background:var(--surf);border:1.5px solid var(--bdr);display:flex;align-items:center;justify-content:center;font-size:16px;cursor:pointer;flex-shrink:0;color:var(--txt)}
.step-title{font-family:'Syne',sans-serif;font-size:18px;font-weight:800;letter-spacing:-0.3px}

/* ── EMPTY ── */
.empty{text-align:center;padding:48px 24px}
.empty-ic{font-size:52px;margin-bottom:16px}
.empty-t{font-family:'Syne',sans-serif;font-size:18px;font-weight:800;margin-bottom:8px}
.empty-s{color:var(--sub);font-size:13px}

/* ── LOADER ── */
.ld{display:flex;align-items:center;justify-content:center;padding:48px}
.sp{width:28px;height:28px;border:3px solid var(--bdr);border-top-color:var(--accent);border-radius:50%;animation:spin .7s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}

/* ── TOAST ── */
.toast{position:fixed;bottom:96px;left:50%;transform:translateX(-50%) translateY(16px);background:var(--accent);color:#fff;padding:10px 20px;border-radius:24px;font-size:12px;font-weight:600;z-index:9999;opacity:0;transition:all .28s;white-space:nowrap;pointer-events:none}
.toast.on{opacity:1;transform:translateX(-50%) translateY(0)}
</style>
</head>
<body>

<!-- SPLASH -->
<div id="splash">
  <div class="splash-logo">NFT<span style="color:#c8a84b">Market</span></div>
  <div class="splash-sub">Exclusive Digital Assets</div>
  <div class="splash-bar-wrap"><div class="splash-bar"></div></div>
  <div class="splash-nfts">
    <div class="splash-nft"></div>
    <div class="splash-nft"></div>
    <div class="splash-nft"></div>
    <div class="splash-nft"></div>
  </div>
</div>

<!-- TOPBAR -->
<div class="topbar">
  <div class="logo">NFT<span>Market</span></div>
  <div class="topbar-right">
    <div class="live-pill"><div class="live-dot"></div>Live</div>
    <div class="cnt-badge" id="stat-n">—</div>
  </div>
</div>

<!-- BROWSE -->
<div class="page active" id="page-browse">
  <div class="hero">
    <div>
      <div class="hero-title">Trade NFTs<br>on Telegram</div>
      <div class="hero-sub">INR · TON · USDT</div>
    </div>
    <div class="hero-right">🖼</div>
  </div>
  <div class="sec">
    <div class="sec-title">Available Now</div>
    <div class="sec-cnt" id="browse-cnt">—</div>
  </div>
  <div class="grid" id="nft-grid">
    <div class="ld" style="grid-column:1/-1"><div class="sp"></div></div>
  </div>
</div>

<!-- ORDERS -->
<div class="page" id="page-orders">
  <div class="sec" style="padding-top:14px"><div class="sec-title">My Orders</div></div>
  <div id="orders-list" style="padding:0 12px"><div class="ld"><div class="sp"></div></div></div>
</div>

<!-- OFFERS -->
<div class="page" id="page-offers">
  <div class="sec" style="padding-top:14px"><div class="sec-title">My Offers</div></div>
  <div id="offers-list" style="padding:0 12px"><div class="ld"><div class="sp"></div></div></div>
</div>

<!-- DETAIL SHEET -->
<div class="overlay" id="ov-detail">
  <div class="sheet">
    <div class="handle"></div>
    <div class="sc" id="sc-detail"></div>
  </div>
</div>

<!-- ACTION SHEET (buy/offer) -->
<div class="overlay" id="ov-buy">
  <div class="sheet">
    <div class="handle"></div>
    <div class="sc" id="sc-buy"></div>
  </div>
</div>

<!-- NAV -->
<nav class="bnav">
  <button class="ni active" id="nav-browse" onclick="gotoPage('browse')">
    <div class="ni-ic">🏪</div>Market
  </button>
  <button class="ni" id="nav-orders" onclick="gotoPage('orders')">
    <div class="ni-ic">📦</div>Orders
  </button>
  <button class="ni" id="nav-offers" onclick="gotoPage('offers')">
    <div class="ni-ic">📬</div>Offers
  </button>
</nav>

<div class="toast" id="toast"></div>

<script>
const tg = window.Telegram.WebApp;
tg.ready(); tg.expand();
const usr = tg.initDataUnsafe?.user || {};
const uid = usr.id || 0;

// Hide splash after 2s
setTimeout(() => {
  document.getElementById('splash').classList.add('hide');
  setTimeout(() => document.getElementById('splash').remove(), 600);
}, 1900);

let curNft = null, buyS = {};

function toast(m, d=2500) {
  const t = document.getElementById('toast');
  t.textContent = m; t.classList.add('on');
  setTimeout(() => t.classList.remove('on'), d);
}
function copy(txt) {
  navigator.clipboard?.writeText(txt).then(() => toast('✅ Copied!')).catch(() => {
    const a = document.createElement('textarea');
    a.value = txt; document.body.appendChild(a); a.select();
    document.execCommand('copy'); document.body.removeChild(a); toast('✅ Copied!');
  });
}
function fmtPrice(n) {
  const p = [];
  if (n.price_inr>0)  p.push('₹'+Number(n.price_inr).toLocaleString('en-IN'));
  if (n.price_ton>0)  p.push('◈ '+n.price_ton+' TON');
  if (n.price_usdt>0) p.push('$'+n.price_usdt);
  return p.length ? p[0] : 'Offer Only';
}
function fmtAll(n) {
  const p = [];
  if (n.price_inr>0)  p.push({ic:'₹',name:'Indian Rupee',lbl:'INR',txt:'₹'+Number(n.price_inr).toLocaleString('en-IN'),val:n.price_inr});
  if (n.price_ton>0)  p.push({ic:'◈',name:'TON Coin',lbl:'TON',txt:n.price_ton+' TON',val:n.price_ton});
  if (n.price_usdt>0) p.push({ic:'$',name:'USDT (TRC20)',lbl:'USDT',txt:'$'+n.price_usdt,val:n.price_usdt});
  return p;
}

function gotoPage(name) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.ni').forEach(n => n.classList.remove('active'));
  document.getElementById('page-'+name).classList.add('active');
  document.getElementById('nav-'+name).classList.add('active');
  if (name==='orders') loadOrders();
  if (name==='offers') loadOffers();
}

function openSheet(id) {
  document.getElementById(id).classList.add('on');
  tg.BackButton.show();
  tg.BackButton.onClick(closeAll);
}
function closeSheet(id) { document.getElementById(id).classList.remove('on'); }
function closeAll() {
  document.querySelectorAll('.overlay').forEach(o => o.classList.remove('on'));
  tg.BackButton.hide();
}
document.querySelectorAll('.overlay').forEach(ov => {
  ov.addEventListener('click', e => { if (e.target===ov) closeAll(); });
});

async function loadNFTs() {
  try {
    const d = await (await fetch('/api/nfts')).json();
    document.getElementById('browse-cnt').textContent = d.length;
    document.getElementById('stat-n').textContent = d.length;
    const g = document.getElementById('nft-grid');
    if (!d.length) {
      g.innerHTML = `<div class="empty" style="grid-column:1/-1"><div class="empty-ic">🖼</div><div class="empty-t">No NFTs Yet</div><div class="empty-s">Check back soon!</div></div>`;
      return;
    }
    g.innerHTML = d.map(n => `
      <div class="card" onclick="openNFT(${n.id})">
        <div class="card-img-wrap">
          ${n.has_image ? `<img src="/api/img/${n.id}" loading="lazy" alt="" onerror="this.parentElement.innerHTML='🖼'">` : '🖼'}
          <div class="live-tag">LIVE</div>
        </div>
        <div class="card-body">
          <div class="card-name">${n.name}</div>
          <div class="price-row">
            <div class="ton-ico">◈</div>
            <span class="price-val">${fmtPrice(n)}</span>
          </div>
        </div>
        <button class="cart-btn" onclick="event.stopPropagation();openNFT(${n.id})">+</button>
      </div>`).join('');
  } catch(e) {
    document.getElementById('nft-grid').innerHTML = `<div class="empty" style="grid-column:1/-1"><div class="empty-ic">⚠️</div><div class="empty-t">Failed to load</div></div>`;
  }
}

async function openNFT(id) {
  const sc = document.getElementById('sc-detail');
  sc.innerHTML = `<div class="ld"><div class="sp"></div></div>`;
  openSheet('ov-detail');
  try {
    const n = await (await fetch(`/api/nft/${id}`)).json();
    curNft = n;
    const prices = fmtAll(n);
    sc.innerHTML = `
      <div class="dimg-wrap">${n.has_image?`<img src="/api/img/${n.id}" alt="${n.name}" onerror="this.parentElement.innerHTML='🖼'">`:'🖼'}</div>
      <div class="d-name">${n.name}</div>
      <div class="d-desc">${n.description||'Exclusive digital collectible.'}</div>
      ${prices.length?`
      <div class="price-card">
        <div class="price-lbl">Price</div>
        ${prices.map(p=>`
          <div class="prow">
            <span class="prow-ico">${p.ic}</span>
            <span class="prow-val">${p.txt}</span>
            <span class="prow-name">${p.name}</span>
          </div>`).join('')}
      </div>`:`<div class="price-card"><div class="price-lbl">Price</div><div style="color:var(--sub);font-size:13px;padding:4px 0">Open to offers</div></div>`}
      <div class="btns">
        ${prices.length?`<button class="btn btn-p" onclick="startBuy()">⚡ Buy Now</button>`:''}
        <button class="btn btn-s ${!prices.length?'btn-full':''}" onclick="startOffer()">📬 Make Offer</button>
      </div>`;
  } catch(e) {
    sc.innerHTML=`<div class="empty"><div class="empty-ic">⚠️</div><div class="empty-t">Failed to load</div></div>`;
  }
}

function startBuy() {
  const n = curNft;
  const prices = fmtAll(n);
  closeSheet('ov-detail');
  buyS = {step:'cur', nft:n, prices};
  renderBuy();
  openSheet('ov-buy');
}

function renderBuy() {
  const sc = document.getElementById('sc-buy');
  const {step, nft, prices} = buyS;

  if (step==='cur') {
    sc.innerHTML = `
      <div class="step-h">
        <div class="back-btn" onclick="closeSheet('ov-buy')">✕</div>
        <div class="step-title">Choose Currency</div>
      </div>
      <div style="color:var(--sub);font-size:12px;margin-bottom:14px">NFT: <b style="color:var(--txt)">${nft.name}</b></div>
      ${prices.map(p=>`
        <div class="cur-opt" onclick="pickCur('${p.lbl}',${p.val})">
          <div class="cur-ic">${p.ic==='₹'?'🇮🇳':p.ic==='◈'?'💎':'💵'}</div>
          <div><div class="cur-name">${p.name}</div><div class="cur-price">${p.txt}</div></div>
        </div>`).join('')}`;
  }

  if (step==='pay') {
    const {cur,price,addr,qrUrl} = buyS;
    const icons = {INR:'🇮🇳',TON:'💎',USDT:'💵'};
    const names = {INR:'UPI ID',TON:'TON Wallet Address',USDT:'USDT Address (TRC20)'};
    const safeAddr = addr.replace(/'/g,"\\'");
    sc.innerHTML = `
      <div class="step-h">
        <div class="back-btn" onclick="buyS.step='cur';renderBuy()">←</div>
        <div class="step-title">Send Payment</div>
      </div>
      <div class="amt-big">${cur==='INR'?'₹':cur==='TON'?'◈':'$'}${Number(price).toLocaleString()} ${cur}</div>
      ${qrUrl?`<div class="qr-wrap"><img src="${qrUrl}" alt="QR"></div>`:''}
      <div class="addr-card">
        <div class="addr-lbl">${names[cur]}</div>
        <div class="addr-val">${addr}</div>
        <button class="copy-btn" onclick="copy('${safeAddr}')">📋 Copy Address</button>
      </div>
      <div style="font-size:12px;color:var(--sub);margin-bottom:10px">Paste your Transaction ID after paying:</div>
      <textarea class="txn-input" id="txni" rows="2" placeholder="Transaction ID / Hash..."></textarea>
      <button class="btn btn-g btn-full" onclick="submitPay()">✅ Confirm Payment</button>`;
  }

  if (step==='done') {
    sc.innerHTML = `
      <div style="text-align:center;padding:36px 0">
        <div style="font-size:72px;margin-bottom:16px">🎉</div>
        <div class="step-title" style="font-size:22px;margin-bottom:10px">Payment Submitted!</div>
        <div style="color:var(--sub);font-size:13px;line-height:1.7;margin-bottom:24px">Admin will verify your payment.<br>Your NFT will be delivered automatically.</div>
        <button class="btn btn-p btn-full" onclick="closeAll()">Done</button>
      </div>`;
  }
}

async function pickCur(cur, price) {
  buyS.cur = cur; buyS.price = price;
  try {
    const d = await (await fetch(`/api/payment-info/${cur}`)).json();
    if (!d.address) { toast('⚠️ Payment not set up for '+cur); return; }
    buyS.addr = d.address; buyS.qrUrl = d.qr_url||'';
    buyS.step = 'pay'; renderBuy();
  } catch(e) { toast('❌ Error loading payment info'); }
}

window._offerPayData = null;
async function submitPay() {
  const txn = document.getElementById('txni')?.value?.trim();
  if (!txn) { toast('⚠️ Enter Transaction ID'); return; }
  const btn = document.querySelector('#sc-buy .btn-g');
  if (btn) { btn.disabled=true; btn.textContent='Submitting...'; }
  try {
    const body = {user_id:uid, username:usr.username||'', full_name:((usr.first_name||'')+' '+(usr.last_name||'')).trim(),
      nft_id:curNft.id, currency:buyS.cur, price:buyS.price, txn_id:txn,
      order_type: window._offerPayData ? 'offer' : 'direct',
      offer_id: window._offerPayData?.offer_id || null};
    const r = await fetch('/api/order',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const d = await r.json();
    if (d.ok) { window._offerPayData=null; buyS.step='done'; renderBuy(); }
    else { toast('❌ '+(d.error||'Failed')); if(btn){btn.disabled=false;btn.textContent='✅ Confirm Payment';} }
  } catch(e) { toast('❌ Network error'); if(btn){btn.disabled=false;btn.textContent='✅ Confirm Payment';} }
}

function startOffer() {
  const n = curNft;
  closeSheet('ov-detail');
  document.getElementById('sc-buy').innerHTML = `
    <div class="step-h">
      <div class="back-btn" onclick="closeSheet('ov-buy')">✕</div>
      <div class="step-title">Make Offer</div>
    </div>
    <div style="color:var(--sub);font-size:12px;margin-bottom:16px">NFT: <b style="color:var(--txt)">${n.name}</b></div>
    <div class="off-row">
      <input class="off-inp" type="number" id="off-amt" placeholder="Amount" min="0.01" step="any">
      <select class="off-sel" id="off-cur">
        <option value="INR">₹ INR</option>
        <option value="TON">◈ TON</option>
        <option value="USDT">$ USDT</option>
      </select>
    </div>
    <button class="btn btn-p btn-full" onclick="submitOffer(${n.id})">📬 Send Offer</button>`;
  openSheet('ov-buy');
}

async function submitOffer(nftId) {
  const amt = parseFloat(document.getElementById('off-amt')?.value);
  const cur = document.getElementById('off-cur')?.value;
  if (!amt||amt<=0) { toast('⚠️ Enter a valid amount'); return; }
  const btn = document.querySelector('#sc-buy .btn-p');
  if (btn) { btn.disabled=true; btn.textContent='Sending...'; }
  try {
    const r = await fetch('/api/offer',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({user_id:uid,username:usr.username||'',full_name:((usr.first_name||'')+' '+(usr.last_name||'')).trim(),nft_id:nftId,offer_price:amt,currency:cur})});
    const d = await r.json();
    if (d.ok) {
      document.getElementById('sc-buy').innerHTML = `
        <div style="text-align:center;padding:36px 0">
          <div style="font-size:72px;margin-bottom:16px">📬</div>
          <div class="step-title" style="font-size:22px;margin-bottom:10px">Offer Sent!</div>
          <div style="color:var(--sub);font-size:13px;margin-bottom:24px">Your offer of <b style="color:var(--ton)">${amt} ${cur}</b><br>was submitted to the seller.</div>
          <button class="btn btn-p btn-full" onclick="closeAll()">Done</button>
        </div>`;
    } else { toast('❌ '+(d.error||'Failed')); if(btn){btn.disabled=false;btn.textContent='📬 Send Offer';} }
  } catch(e) { toast('❌ Network error'); if(btn){btn.disabled=false;btn.textContent='📬 Send Offer';} }
}

async function payOffer(offerId, nftId, price, cur) {
  try {
    const d = await (await fetch(`/api/payment-info/${cur}`)).json();
    if (!d.address) { toast('⚠️ Contact seller for payment details'); return; }
    const n = await (await fetch(`/api/nft/${nftId}`)).json();
    curNft = n;
    buyS = {step:'pay', nft:n, cur, price, addr:d.address, qrUrl:d.qr_url||''};
    window._offerPayData = {offer_id:offerId};
    renderBuy();
    openSheet('ov-buy');
  } catch(e) { toast('❌ Error'); }
}

async function loadOrders() {
  const el = document.getElementById('orders-list');
  el.innerHTML = `<div class="ld"><div class="sp"></div></div>`;
  try {
    const d = await (await fetch(`/api/my-orders/${uid}`)).json();
    if (!d.length) {
      el.innerHTML=`<div class="empty"><div class="empty-ic">📦</div><div class="empty-t">No Orders Yet</div><div class="empty-s">Buy your first NFT!</div></div>`;
      return;
    }
    const smap = {pending:'<span class="badge bp">Pending</span>',confirmed:'<span class="badge bc">Confirmed</span>',rejected:'<span class="badge br">Rejected</span>'};
    el.innerHTML = d.map(o=>`
      <div class="ocard">
        <div class="ocard-img">${o.has_image?`<img src="/api/img/${o.nft_id}" alt="">`:'🖼'}</div>
        <div style="flex:1;min-width:0">
          <div class="ocard-name">${o.nft_name}</div>
          <div class="ocard-price">${o.price} ${o.currency}</div>
          ${smap[o.status]||`<span class="badge bp">${o.status}</span>`}
        </div>
      </div>`).join('');
  } catch(e) { el.innerHTML=`<div class="empty"><div class="empty-ic">⚠️</div><div class="empty-t">Failed to load</div></div>`; }
}

async function loadOffers() {
  const el = document.getElementById('offers-list');
  el.innerHTML = `<div class="ld"><div class="sp"></div></div>`;
  try {
    const d = await (await fetch(`/api/my-offers/${uid}`)).json();
    if (!d.length) {
      el.innerHTML=`<div class="empty"><div class="empty-ic">📬</div><div class="empty-t">No Offers Yet</div><div class="empty-s">Make an offer on any NFT!</div></div>`;
      return;
    }
    const smap = {
      pending:'<span class="badge bp">Pending</span>',
      accepted:'<span class="badge ba">Accepted — Pay Now</span>',
      rejected:'<span class="badge br">Rejected</span>'
    };
    el.innerHTML = d.map(o=>`
      <div class="ocard">
        <div class="ocard-img">${o.has_image?`<img src="/api/img/${o.nft_id}" alt="">`:'🖼'}</div>
        <div style="flex:1;min-width:0">
          <div class="ocard-name">${o.nft_name}</div>
          <div class="ocard-price">${o.offer_price} ${o.currency}</div>
          ${smap[o.status]||`<span class="badge bp">${o.status}</span>`}
          ${o.status==='accepted'?`<br><button class="btn btn-ton" style="margin-top:8px;padding:8px 14px;font-size:12px" onclick="payOffer(${o.id},${o.nft_id},${o.offer_price},'${o.currency}')">💳 Pay Now</button>`:''}
        </div>
      </div>`).join('');
  } catch(e) { el.innerHTML=`<div class="empty"><div class="empty-ic">⚠️</div><div class="empty-t">Failed to load</div></div>`; }
}

loadNFTs();
</script>
</body>
</html>"""
# ══════════════════════════════════════
#  AIOHTTP API ROUTES
# ══════════════════════════════════════
routes = web.RouteTableDef()

@routes.get("/")
async def serve_app(req):
    return web.Response(text=HTML_APP, content_type="text/html")

@routes.get("/api/nfts")
async def api_nfts(req):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id,name,description,image_id,price_inr,price_ton,price_usdt FROM nfts "
            "WHERE status='available' ORDER BY id DESC"
        ) as c:
            rows = await c.fetchall()
    result = []
    for r in rows:
        result.append({
            "id": r[0], "name": r[1], "description": r[2],
            "has_image": bool(r[3]),
            "price_inr": r[4], "price_ton": r[5], "price_usdt": r[6],
        })
    return web.json_response(result)

@routes.get("/api/nft/{nft_id}")
async def api_nft(req):
    nft_id = int(req.match_info["nft_id"])
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id,name,description,image_id,price_inr,price_ton,price_usdt,status FROM nfts WHERE id=?",
            (nft_id,)
        ) as c:
            r = await c.fetchone()
    if not r:
        raise web.HTTPNotFound()
    return web.json_response({
        "id": r[0], "name": r[1], "description": r[2],
        "has_image": bool(r[3]),
        "price_inr": r[4], "price_ton": r[5], "price_usdt": r[6], "status": r[7],
    })

@routes.get("/api/img/{nft_id}")
async def api_img(req):
    nft_id = int(req.match_info["nft_id"])
    url = await get_tg_img_url(nft_id)
    if not url:
        raise web.HTTPNotFound()
    raise web.HTTPFound(url)

@routes.get("/api/payment-info/{currency}")
async def api_pay_info(req):
    cur = req.match_info["currency"].upper()
    if cur not in ("INR", "TON", "USDT"):
        raise web.HTTPBadRequest()
    addr_key = {"INR": "inr_upi", "TON": "ton_addr", "USDT": "usdt_addr"}[cur]
    qr_key   = {"INR": "inr_qr",  "TON": "ton_qr",   "USDT": "usdt_qr"}[cur]
    addr     = await get_cfg(addr_key)
    qr_id    = await get_cfg(qr_key)
    qr_url   = ""
    if qr_id:
        try:
            async with ClientSession() as sess:
                async with sess.get(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/getFile",
                    params={"file_id": qr_id}
                ) as r:
                    data = await r.json()
            if data.get("ok"):
                qr_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{data['result']['file_path']}"
        except Exception: pass
    return web.json_response({"address": addr, "qr_url": qr_url})

@routes.post("/api/order")
async def api_order(req):
    try:
        body = await req.json()
    except Exception:
        return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)

    user_id    = int(body.get("user_id", 0))
    username   = str(body.get("username", ""))
    full_name  = str(body.get("full_name", "")).strip()
    nft_id     = int(body.get("nft_id", 0))
    currency   = str(body.get("currency", "")).upper()
    price      = float(body.get("price", 0))
    txn_id     = str(body.get("txn_id", "")).strip()
    order_type = str(body.get("order_type", "direct"))
    offer_id   = body.get("offer_id")

    if not user_id or not nft_id or currency not in ("INR","TON","USDT") or price <= 0 or not txn_id:
        return web.json_response({"ok": False, "error": "Missing fields"}, status=400)

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id, name, status FROM nfts WHERE id=?", (nft_id,)) as c:
            nft = await c.fetchone()
    if not nft or nft[2] not in ("available", "pending"):
        return web.json_response({"ok": False, "error": "NFT not available"}, status=400)

    await reg_user(user_id, username, full_name)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO orders (nft_id,buyer_id,buyer_user,price,currency,txn_id,order_type,offer_id,status,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,'pending',?)",
            (nft_id, user_id, username, price, currency, txn_id, order_type, offer_id, now())
        )
        async with db.execute("SELECT last_insert_rowid()") as c:
            order_id = (await c.fetchone())[0]
        await db.execute("UPDATE nfts SET status='pending' WHERE id=?", (nft_id,))
        await db.commit()

    await notify_admins(
        f"💸 <b>New Payment!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📋 Order #<code>{order_id}</code>\n"
        f"🖼 <b>{nft[1]}</b> (#{nft_id})\n"
        f"👤 @{username or 'N/A'} (<code>{user_id}</code>)\n"
        f"💰 {sym(currency)}{price:g} {currency}\n"
        f"🆔 TXN: <code>{txn_id}</code>",
        InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Confirm", callback_data=f"POK_{order_id}"),
            InlineKeyboardButton(text="❌ Reject",  callback_data=f"PRJ_{order_id}"),
        ]])
    )
    return web.json_response({"ok": True, "order_id": order_id})

@routes.post("/api/offer")
async def api_offer(req):
    try:
        body = await req.json()
    except Exception:
        return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)

    user_id     = int(body.get("user_id", 0))
    username    = str(body.get("username", ""))
    full_name   = str(body.get("full_name", "")).strip()
    nft_id      = int(body.get("nft_id", 0))
    offer_price = float(body.get("offer_price", 0))
    currency    = str(body.get("currency", "")).upper()

    if not user_id or not nft_id or currency not in ("INR","TON","USDT") or offer_price <= 0:
        return web.json_response({"ok": False, "error": "Missing fields"}, status=400)

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id, name, status FROM nfts WHERE id=?", (nft_id,)) as c:
            nft = await c.fetchone()
    if not nft or nft[2] != "available":
        return web.json_response({"ok": False, "error": "NFT not available"}, status=400)

    await reg_user(user_id, username, full_name)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO offers (nft_id,buyer_id,buyer_user,offer_price,currency,status,created_at) "
            "VALUES (?,?,?,?,?,'pending',?)",
            (nft_id, user_id, username, offer_price, currency, now())
        )
        async with db.execute("SELECT last_insert_rowid()") as c:
            offer_id = (await c.fetchone())[0]
        await db.commit()

    await notify_admins(
        f"📬 <b>New Offer!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📋 Offer #<code>{offer_id}</code>\n"
        f"🖼 <b>{nft[1]}</b> (#{nft_id})\n"
        f"👤 @{username or 'N/A'} (<code>{user_id}</code>)\n"
        f"💰 {sym(currency)}{offer_price:g} {currency}",
        InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Accept", callback_data=f"OOK_{offer_id}"),
            InlineKeyboardButton(text="❌ Reject", callback_data=f"ORJ_{offer_id}"),
        ]])
    )
    return web.json_response({"ok": True, "offer_id": offer_id})

@routes.get("/api/my-orders/{user_id}")
async def api_my_orders(req):
    uid = int(req.match_info["user_id"])
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT o.id, n.id, n.name, n.image_id, o.price, o.currency, o.status, o.created_at "
            "FROM orders o JOIN nfts n ON o.nft_id=n.id WHERE o.buyer_id=? ORDER BY o.id DESC LIMIT 20",
            (uid,)
        ) as c:
            rows = await c.fetchall()
    result = []
    for r in rows:
        result.append({
            "id": r[0], "nft_id": r[1], "nft_name": r[2],
            "has_image": bool(r[3]),
            "price": r[4], "currency": r[5], "status": r[6], "date": r[7],
        })
    return web.json_response(result)

@routes.get("/api/my-offers/{user_id}")
async def api_my_offers(req):
    uid = int(req.match_info["user_id"])
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT o.id, n.id, n.name, n.image_id, o.offer_price, o.currency, o.status, o.created_at "
            "FROM offers o JOIN nfts n ON o.nft_id=n.id WHERE o.buyer_id=? ORDER BY o.id DESC LIMIT 20",
            (uid,)
        ) as c:
            rows = await c.fetchall()
    result = []
    for r in rows:
        result.append({
            "id": r[0], "nft_id": r[1], "nft_name": r[2],
            "has_image": bool(r[3]),
            "offer_price": r[4], "currency": r[5], "status": r[6], "date": r[7],
        })
    return web.json_response(result)

# ══════════════════════════════════════
#  BOT — DELIVERY SYSTEM
# ══════════════════════════════════════
async def deliver_nft(nft_id: int, buyer_id: int, buyer_username: str, nft_name: str):
    """Auto-deliver NFT to buyer based on delivery_type."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT delivery_type, delivery_data FROM nfts WHERE id=?", (nft_id,)
        ) as c:
            row = await c.fetchone()
    if not row:
        return

    dtype, ddata = row

    if dtype == "text" and ddata:
        # Deliver as text message
        try:
            await bot.send_message(
                buyer_id,
                f"🎁 <b>Your NFT: {nft_name}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"{ddata}" + wm()
            )
        except Exception as e:
            log.error(f"Text delivery failed for {buyer_id}: {e}")
            await notify_admins(f"⚠️ Text delivery failed for buyer {buyer_id} (NFT #{nft_id})\n\nDeliver manually:\n{ddata}")

    elif dtype == "file" and ddata:
        # Deliver as file (file_id stored in ddata)
        try:
            await bot.send_document(
                buyer_id, ddata,
                caption=f"🎁 <b>Your NFT: {nft_name}</b>" + wm()
            )
        except Exception:
            try:
                await bot.send_photo(buyer_id, ddata, caption=f"🎁 <b>Your NFT: {nft_name}</b>" + wm())
            except Exception as e:
                log.error(f"File delivery failed: {e}")
                await notify_admins(f"⚠️ File delivery failed for buyer {buyer_id} (NFT #{nft_id})")

    elif dtype == "username" and ddata:
        # NFT is a Telegram username — notify admin to transfer
        buyer_handle = f"@{buyer_username}" if buyer_username else f"ID: {buyer_id}"
        await notify_admins(
            f"🔄 <b>Transfer NFT Now!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🖼 NFT: <b>{nft_name}</b>\n"
            f"📦 Item to transfer: <code>{ddata}</code>\n"
            f"👤 Transfer to: <b>{buyer_handle}</b>\n\n"
            f"Please complete the transfer on Fragment/Telegram."
        )
        # Also tell buyer to expect it
        try:
            await bot.send_message(
                buyer_id,
                f"✅ <b>NFT Purchase Confirmed!</b>\n\n"
                f"🖼 <b>{nft_name}</b>\n\n"
                f"The seller will transfer your NFT to <b>@{buyer_username or 'your account'}</b> shortly.\n"
                f"📞 Contact {SUPPORT} if not received in 24h." + wm()
            )
        except Exception: pass

    else:
        # Manual delivery
        buyer_handle = f"@{buyer_username}" if buyer_username else f"ID: {buyer_id}"
        await notify_admins(
            f"📦 <b>Deliver NFT Manually!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🖼 NFT: <b>{nft_name}</b> (#{nft_id})\n"
            f"👤 Deliver to: <b>{buyer_handle}</b> (<code>{buyer_id}</code>)"
        )
        try:
            await bot.send_message(
                buyer_id,
                f"✅ <b>Payment Confirmed!</b>\n\n"
                f"🖼 <b>{nft_name}</b> is now yours!\n\n"
                f"The seller will transfer your NFT shortly.\n"
                f"📞 Contact {SUPPORT} if not received in 24h." + wm()
            )
        except Exception: pass

# ══════════════════════════════════════
#  BOT — /start
# ══════════════════════════════════════
@dp.message(CommandStart())
async def cmd_start(msg: Message, state: FSMContext):
    await state.clear()
    await reg_user(msg.from_user.id, msg.from_user.username or "", msg.from_user.full_name or "")
    ia = is_adm(msg.from_user.id)

    kb_rows = [[
        InlineKeyboardButton(
            text="🖼 Open NFT Bazaar",
            web_app=WebAppInfo(url=APP_URL)
        )
    ]]
    if ia:
        kb_rows.append([InlineKeyboardButton(text="⚙️ Admin Panel", callback_data="M_admin")])
    kb_rows.append([InlineKeyboardButton(text="💬 Support", url=f"https://t.me/{SUPPORT.lstrip('@')}")])

    await msg.answer(
        f"👋 <b>Welcome to {SHOP_NAME}!</b>\n\n"
        f"🖼 Buy & trade exclusive Telegram NFTs.\n\n"
        f"💰 <b>Payments:</b> INR / TON / USDT\n"
        f"⚡ <b>Delivery:</b> Automatic on confirmation\n\n"
        f"Tap below to open the marketplace:"
        + ("\n\n⚙️ <b>Admin Mode Active</b>" if ia else "")
        + wm(),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows)
    )

# ══════════════════════════════════════
#  BOT — ADMIN PANEL
# ══════════════════════════════════════
def kb_admin():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Add NFT",          callback_data="A_addnft"),
         InlineKeyboardButton(text="🖼 All NFTs",          callback_data="A_allnfts")],
        [InlineKeyboardButton(text="📬 Pending Offers",   callback_data="A_offers"),
         InlineKeyboardButton(text="💸 Pending Payments", callback_data="A_payments")],
        [InlineKeyboardButton(text="💰 Payment Config",   callback_data="A_setpay"),
         InlineKeyboardButton(text="📊 Stats",            callback_data="A_stats")],
        [InlineKeyboardButton(text="👥 Users",            callback_data="A_users"),
         InlineKeyboardButton(text="📢 Broadcast",        callback_data="A_bcast")],
        [InlineKeyboardButton(text="🔙 Main Menu",        callback_data="M_home")],
    ])

@dp.callback_query(F.data == "M_admin")
async def cb_admin(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    if not is_adm(cb.from_user.id): return await cb.answer("⛔", show_alert=True)
    await cb.answer()
    await safe(cb, f"⚙️ <b>Admin Panel</b>\n━━━━━━━━━━━━━━━━━━━━━\n\nManage your NFT marketplace:" + wm(), kb_admin())

@dp.callback_query(F.data == "M_home")
async def cb_home(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.answer()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🖼 Open NFT Bazaar", web_app=WebAppInfo(url=APP_URL))],
        [InlineKeyboardButton(text="⚙️ Admin Panel", callback_data="M_admin")] if is_adm(cb.from_user.id) else [],
    ])
    await safe(cb, f"🖼 <b>{SHOP_NAME}</b>\n\nTap below to open the marketplace:" + wm(), kb)

# ══════════════════════════════════════
#  BOT — CONFIRM / REJECT PAYMENTS
# ══════════════════════════════════════
@dp.callback_query(F.data.startswith("POK_"))
async def cb_pay_ok(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    if not is_adm(cb.from_user.id): return await cb.answer("⛔", show_alert=True)
    await cb.answer()
    order_id = int(cb.data[4:])

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT o.nft_id, o.buyer_id, o.buyer_user, o.price, o.currency, o.status, n.name "
            "FROM orders o JOIN nfts n ON o.nft_id=n.id WHERE o.id=?",
            (order_id,)
        ) as c:
            order = await c.fetchone()

    if not order or order[5] != "pending":
        return await cb.answer("Already processed!", show_alert=True)

    nft_id, buyer_id, buyer_user, price, currency, _, nft_name = order

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE orders SET status='confirmed', confirmed_at=? WHERE id=?", (now(), order_id))
        await db.execute("UPDATE nfts SET status='sold', sold_to=?, sold_at=? WHERE id=?", (buyer_id, now(), nft_id))
        await db.commit()

    try:
        await cb.message.edit_text(cb.message.text + f"\n\n✅ <b>CONFIRMED</b> — {now()}")
    except Exception: pass

    # Auto delivery
    await deliver_nft(nft_id, buyer_id, buyer_user, nft_name)

@dp.callback_query(F.data.startswith("PRJ_"))
async def cb_pay_rj(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    if not is_adm(cb.from_user.id): return await cb.answer("⛔", show_alert=True)
    await cb.answer()
    order_id = int(cb.data[4:])

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT o.nft_id, o.buyer_id, o.price, o.currency, o.status, n.name "
            "FROM orders o JOIN nfts n ON o.nft_id=n.id WHERE o.id=?",
            (order_id,)
        ) as c:
            order = await c.fetchone()

    if not order or order[4] != "pending":
        return await cb.answer("Already processed!", show_alert=True)

    nft_id, buyer_id, price, currency, _, nft_name = order

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE orders SET status='rejected' WHERE id=?", (order_id,))
        await db.execute("UPDATE nfts SET status='available' WHERE id=? AND status='pending'", (nft_id,))
        await db.commit()

    try:
        await cb.message.edit_text(cb.message.text + f"\n\n❌ <b>REJECTED</b> — {now()}")
    except Exception: pass

    try:
        await bot.send_message(
            buyer_id,
            f"❌ <b>Payment Not Verified</b>\n\n"
            f"🖼 NFT: <b>{nft_name}</b>\n"
            f"💰 {sym(currency)}{price:g} {currency}\n\n"
            f"TXN could not be verified.\nContact {SUPPORT} for help." + wm()
        )
    except Exception: pass

# ══════════════════════════════════════
#  BOT — ACCEPT / REJECT OFFERS
# ══════════════════════════════════════
@dp.callback_query(F.data == "A_offers")
async def cb_adm_offers(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.answer()
    if not is_adm(cb.from_user.id): return await cb.answer("⛔", show_alert=True)

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT o.id, n.name, o.offer_price, o.currency, o.buyer_id, o.buyer_user "
            "FROM offers o JOIN nfts n ON o.nft_id=n.id WHERE o.status='pending' ORDER BY o.id DESC LIMIT 20"
        ) as c:
            rows = await c.fetchall()

    if not rows:
        return await safe(cb, "📬 No pending offers." + wm(), kb_home())

    text = "📬 <b>Pending Offers</b>\n━━━━━━━━━━━━━━━━━━━━━\n\n"
    b = InlineKeyboardBuilder()
    for oid, nname, price, cur, bid, buser in rows:
        text += f"<code>#{oid}</code> <b>{nname[:20]}</b> — {sym(cur)}{price:g} {cur} | @{buser or bid}\n"
        b.button(text=f"✅ #{oid}", callback_data=f"OOK_{oid}")
        b.button(text=f"❌ #{oid}", callback_data=f"ORJ_{oid}")
    b.adjust(2)
    b.row(InlineKeyboardButton(text="🔙 Admin", callback_data="M_admin"))
    await safe(cb, text + wm(), b.as_markup())

@dp.callback_query(F.data.startswith("OOK_"))
async def cb_offer_ok(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    if not is_adm(cb.from_user.id): return await cb.answer("⛔", show_alert=True)
    await cb.answer()
    offer_id = int(cb.data[4:])

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT o.nft_id, o.buyer_id, o.buyer_user, o.offer_price, o.currency, o.status, n.name "
            "FROM offers o JOIN nfts n ON o.nft_id=n.id WHERE o.id=?",
            (offer_id,)
        ) as c:
            offer = await c.fetchone()

    if not offer or offer[5] != "pending":
        return await cb.answer("Already resolved!", show_alert=True)

    nft_id, buyer_id, buyer_user, price, currency, _, nft_name = offer

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE offers SET status='accepted', resolved_at=? WHERE id=?", (now(), offer_id))
        await db.commit()

    try:
        await cb.message.edit_text(cb.message.text + f"\n\n✅ <b>ACCEPTED</b> — {now()}")
    except Exception: pass

    cfg_key = {"INR": "inr_upi", "TON": "ton_addr", "USDT": "usdt_addr"}[currency]
    address = await get_cfg(cfg_key)

    pay_msg = (
        f"🎉 <b>Offer Accepted!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🖼 NFT: <b>{nft_name}</b>\n"
        f"💰 Amount: <b>{sym(currency)}{price:g} {currency}</b>\n\n"
        + (f"📤 Send payment to:\n<code>{address}</code>\n\n" if address else
           f"⚠️ Contact {SUPPORT} for payment details.\n\n")
        + f"Then open NFT Bazaar → My Offers → tap <b>Pay Now</b> to submit your TXN." + wm()
    )

    kb_pay = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🖼 Open & Pay", web_app=WebAppInfo(url=APP_URL))
    ]])

    try:
        await bot.send_message(buyer_id, pay_msg, reply_markup=kb_pay)
    except Exception: pass

@dp.callback_query(F.data.startswith("ORJ_"))
async def cb_offer_rj(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    if not is_adm(cb.from_user.id): return await cb.answer("⛔", show_alert=True)
    await cb.answer()
    offer_id = int(cb.data[4:])

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT o.buyer_id, o.offer_price, o.currency, o.status, n.name "
            "FROM offers o JOIN nfts n ON o.nft_id=n.id WHERE o.id=?",
            (offer_id,)
        ) as c:
            offer = await c.fetchone()

    if not offer or offer[3] != "pending":
        return await cb.answer("Already resolved!", show_alert=True)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE offers SET status='rejected', resolved_at=? WHERE id=?", (now(), offer_id))
        await db.commit()

    try:
        await cb.message.edit_text(cb.message.text + f"\n\n❌ <b>REJECTED</b> — {now()}")
    except Exception: pass

    try:
        await bot.send_message(
            offer[0],
            f"❌ <b>Offer Rejected</b>\n\n"
            f"🖼 {offer[4]}\n💰 {sym(offer[2])}{offer[1]:g} {offer[2]}\n\n"
            f"Try a higher offer or contact {SUPPORT}." + wm()
        )
    except Exception: pass

# ══════════════════════════════════════
#  BOT — ADD NFT FLOW
# ══════════════════════════════════════
@dp.callback_query(F.data == "A_addnft")
async def cb_addnft(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    if not is_adm(cb.from_user.id): return await cb.answer("⛔", show_alert=True)
    await cb.answer()
    await state.set_state(S.nft_image)
    await safe(cb, "🖼 <b>Add NFT — Step 1/7</b>\n━━━━━━━━━━━━━━━━━━━━━\n\nSend the <b>NFT image / photo</b>.", kb_cancel())

@dp.message(S.nft_image)
async def proc_nft_img(msg: Message, state: FSMContext):
    if not is_adm(msg.from_user.id): return
    if not msg.photo: return await msg.answer("❌ Send a photo.", reply_markup=kb_cancel())
    await state.update_data(img_id=msg.photo[-1].file_id)
    await state.set_state(S.nft_name)
    await msg.answer("✅ Image saved!\n\n📝 <b>Step 2/7:</b> Send the <b>NFT name</b>.", reply_markup=kb_cancel())

@dp.message(S.nft_name)
async def proc_nft_name(msg: Message, state: FSMContext):
    if not is_adm(msg.from_user.id): return
    name = (msg.text or "").strip()
    if not name: return await msg.answer("❌ Send the name.")
    await state.update_data(name=name)
    await state.set_state(S.nft_desc)
    await msg.answer(f"✅ Name: <b>{name}</b>\n\n📝 <b>Step 3/7:</b> Send the <b>description</b>.", reply_markup=kb_cancel())

@dp.message(S.nft_desc)
async def proc_nft_desc(msg: Message, state: FSMContext):
    if not is_adm(msg.from_user.id): return
    desc = (msg.text or "").strip()
    if not desc: return await msg.answer("❌ Send a description.")
    await state.update_data(desc=desc)
    await state.set_state(S.nft_price_inr)
    await msg.answer("✅ Done!\n\n₹ <b>Step 4/7: INR price</b>\n\nSend a number e.g. <code>5000</code>\nOr <code>0</code> to skip.", reply_markup=kb_cancel())

@dp.message(S.nft_price_inr)
async def proc_inr(msg: Message, state: FSMContext):
    if not is_adm(msg.from_user.id): return
    try:
        p = float((msg.text or "").strip()); assert p >= 0
    except Exception: return await msg.answer("❌ Enter a number or <code>0</code>.")
    await state.update_data(price_inr=p)
    await state.set_state(S.nft_price_ton)
    await msg.answer(f"✅ INR: {'₹'+f'{p:,.0f}' if p>0 else 'Skipped'}\n\n💎 <b>Step 5/7: TON price</b>\n\nSend e.g. <code>10</code> or <code>0</code>.", reply_markup=kb_cancel())

@dp.message(S.nft_price_ton)
async def proc_ton(msg: Message, state: FSMContext):
    if not is_adm(msg.from_user.id): return
    try:
        p = float((msg.text or "").strip()); assert p >= 0
    except Exception: return await msg.answer("❌ Enter a number or <code>0</code>.")
    await state.update_data(price_ton=p)
    await state.set_state(S.nft_price_usdt)
    await msg.answer(f"✅ TON: {f'{p:g} TON' if p>0 else 'Skipped'}\n\n💵 <b>Step 6/7: USDT price</b>\n\nSend e.g. <code>50</code> or <code>0</code>.", reply_markup=kb_cancel())

@dp.message(S.nft_price_usdt)
async def proc_usdt(msg: Message, state: FSMContext):
    if not is_adm(msg.from_user.id): return
    try:
        p = float((msg.text or "").strip()); assert p >= 0
    except Exception: return await msg.answer("❌ Enter a number or <code>0</code>.")
    await state.update_data(price_usdt=p)
    await state.set_state(S.nft_delivery)
    await msg.answer(
        "✅ USDT done!\n\n"
        "📦 <b>Step 7/7: Delivery Data</b>\n━━━━━━━━━━━━━━━━━━━━━\n\n"
        "How should the NFT be delivered after purchase?\n\n"
        "<b>Options:</b>\n"
        "• Send any text/credentials → I'll deliver it as a message\n"
        "• Send a file/image → I'll forward it to buyer\n"
        "• Type a Telegram username like <code>@username</code> → I'll notify you to transfer\n"
        "• Type <code>manual</code> → You handle delivery yourself\n\n"
        "Send your delivery data or <code>manual</code>:",
        reply_markup=kb_cancel()
    )

@dp.message(S.nft_delivery)
async def proc_delivery(msg: Message, state: FSMContext):
    if not is_adm(msg.from_user.id): return
    data = await state.get_data()
    await state.clear()

    # Determine delivery type
    if msg.document or msg.video:
        dtype = "file"
        ddata = (msg.document or msg.video).file_id
    elif msg.photo:
        dtype = "file"
        ddata = msg.photo[-1].file_id
    else:
        raw = (msg.text or "").strip()
        if raw.lower() == "manual":
            dtype, ddata = "manual", ""
        elif raw.startswith("@"):
            dtype, ddata = "username", raw
        else:
            dtype, ddata = "text", raw

    img_id     = data.get("img_id", "")
    name       = data.get("name", "")
    desc       = data.get("desc", "")
    price_inr  = data.get("price_inr", 0)
    price_ton  = data.get("price_ton",  0)
    price_usdt = data.get("price_usdt", 0)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO nfts (name,description,image_id,price_inr,price_ton,price_usdt,"
            "delivery_type,delivery_data,status,listed_by,listed_at) "
            "VALUES (?,?,?,?,?,?,?,?,'available',?,?)",
            (name, desc, img_id, price_inr, price_ton, price_usdt, dtype, ddata, msg.from_user.id, now())
        )
        async with db.execute("SELECT last_insert_rowid()") as c:
            nft_id = (await c.fetchone())[0]
        await db.commit()

    _img_cache.pop(nft_id, None)  # clear cache for new nft

    price_str = prices_str(price_inr, price_ton, price_usdt)
    delivery_display = {"text":"📝 Text/Credentials","file":"📁 File","username":"👤 Username Transfer","manual":"⚙️ Manual"}.get(dtype, dtype)

    await msg.answer(
        f"✅ <b>NFT Listed!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🆔 ID: <code>{nft_id}</code>\n"
        f"🖼 Name: <b>{name}</b>\n"
        f"💰 Price: {price_str}\n"
        f"📦 Delivery: {delivery_display}\n\n"
        f"🟢 Now live in the marketplace!" + wm(),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Add Another", callback_data="A_addnft")],
            [InlineKeyboardButton(text="🖼 All NFTs",    callback_data="A_allnfts")],
            [InlineKeyboardButton(text="⚙️ Admin",       callback_data="M_admin")],
        ])
    )

# ══════════════════════════════════════
#  BOT — ALL NFTS / REMOVE
# ══════════════════════════════════════
@dp.callback_query(F.data == "A_allnfts")
async def cb_allnfts(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.answer()
    if not is_adm(cb.from_user.id): return await cb.answer("⛔", show_alert=True)

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id,name,price_inr,price_ton,price_usdt,status,delivery_type FROM nfts ORDER BY id DESC LIMIT 30"
        ) as c:
            nfts = await c.fetchall()

    if not nfts:
        return await safe(cb, "🖼 No NFTs listed yet." + wm(),
            InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="➕ Add NFT", callback_data="A_addnft")]]))

    icons = {"available":"🟢","pending":"🟡","sold":"🔴"}
    dtypes = {"text":"📝","file":"📁","username":"👤","manual":"⚙️"}
    text = "🖼 <b>All NFT Listings</b>\n━━━━━━━━━━━━━━━━━━━━━\n\n"
    b = InlineKeyboardBuilder()
    for nid, name, p_inr, p_ton, p_usdt, status, dtype in nfts:
        text += f"{icons.get(status,'⚪')} {dtypes.get(dtype,'')}<code>#{nid}</code> <b>{name[:22]}</b> — {prices_str(p_inr,p_ton,p_usdt)}\n"
        if status != "sold":
            b.button(text=f"🗑 #{nid}", callback_data=f"NRM_{nid}")
    b.adjust(3)
    b.row(InlineKeyboardButton(text="➕ Add NFT", callback_data="A_addnft"))
    b.row(InlineKeyboardButton(text="🔙 Admin",   callback_data="M_admin"))
    await safe(cb, text + wm(), b.as_markup())

@dp.callback_query(F.data.startswith("NRM_"))
async def cb_nrm(cb: CallbackQuery, state: FSMContext):
    if not is_adm(cb.from_user.id): return await cb.answer("⛔", show_alert=True)
    await cb.answer()
    nid = int(cb.data[4:])
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT name FROM nfts WHERE id=?", (nid,)) as c:
            r = await c.fetchone()
    if not r: return await safe(cb, "❌ Not found." + wm(), kb_home())
    await safe(cb,
        f"⚠️ <b>Remove NFT #{nid}?</b>\n\n🖼 <b>{r[0]}</b>\n\nThis is permanent.",
        InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🗑 Remove",  callback_data=f"NRMOK_{nid}"),
            InlineKeyboardButton(text="🔙 Cancel",  callback_data="A_allnfts"),
        ]])
    )

@dp.callback_query(F.data.startswith("NRMOK_"))
async def cb_nrmok(cb: CallbackQuery, state: FSMContext):
    if not is_adm(cb.from_user.id): return await cb.answer("⛔", show_alert=True)
    await cb.answer()
    nid = int(cb.data[6:])
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT name FROM nfts WHERE id=?", (nid,)) as c:
            r = await c.fetchone()
        await db.execute("DELETE FROM nfts WHERE id=?", (nid,))
        await db.commit()
    _img_cache.pop(nid, None)
    await safe(cb, f"✅ <b>{r[0] if r else nid}</b> removed." + wm(), kb_home())

# ══════════════════════════════════════
#  BOT — PENDING PAYMENTS LIST
# ══════════════════════════════════════
@dp.callback_query(F.data == "A_payments")
async def cb_adm_payments(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.answer()
    if not is_adm(cb.from_user.id): return await cb.answer("⛔", show_alert=True)

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT o.id, n.name, o.price, o.currency, o.buyer_id, o.buyer_user, o.txn_id "
            "FROM orders o JOIN nfts n ON o.nft_id=n.id WHERE o.status='pending' ORDER BY o.id DESC LIMIT 20"
        ) as c:
            rows = await c.fetchall()

    if not rows:
        return await safe(cb, "💸 No pending payments." + wm(), kb_home())

    text = "💸 <b>Pending Payments</b>\n━━━━━━━━━━━━━━━━━━━━━\n\n"
    b = InlineKeyboardBuilder()
    for oid, nname, price, cur, bid, buser, txn in rows:
        text += f"<code>#{oid}</code> <b>{nname[:18]}</b> {sym(cur)}{price:g} | @{buser or bid}\nTXN: <code>{txn[:25]}</code>\n\n"
        b.button(text=f"✅ #{oid}", callback_data=f"POK_{oid}")
        b.button(text=f"❌ #{oid}", callback_data=f"PRJ_{oid}")
    b.adjust(2)
    b.row(InlineKeyboardButton(text="🔙 Admin", callback_data="M_admin"))
    await safe(cb, text + wm(), b.as_markup())

# ══════════════════════════════════════
#  BOT — PAYMENT CONFIG
# ══════════════════════════════════════
@dp.callback_query(F.data == "A_setpay")
async def cb_setpay(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.answer()
    if not is_adm(cb.from_user.id): return await cb.answer("⛔", show_alert=True)

    upi   = await get_cfg("inr_upi")   or "❌ Not set"
    ton   = await get_cfg("ton_addr")  or "❌ Not set"
    usdt  = await get_cfg("usdt_addr") or "❌ Not set"
    iq    = "✅" if await get_cfg("inr_qr")  else "❌"
    tq    = "✅" if await get_cfg("ton_qr")  else "❌"
    uq    = "✅" if await get_cfg("usdt_qr") else "❌"

    await safe(cb,
        f"💰 <b>Payment Config</b>\n━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"₹ UPI: <code>{upi}</code>  QR:{iq}\n\n"
        f"💎 TON: <code>{ton[:30]}</code>  QR:{tq}\n\n"
        f"💵 USDT: <code>{usdt[:30]}</code>  QR:{uq}",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="₹ UPI",     callback_data="SP_INR"),
             InlineKeyboardButton(text="💎 TON",    callback_data="SP_TON"),
             InlineKeyboardButton(text="💵 USDT",   callback_data="SP_USDT")],
            [InlineKeyboardButton(text="🖼 INR QR", callback_data="SQ_INR"),
             InlineKeyboardButton(text="🖼 TON QR", callback_data="SQ_TON"),
             InlineKeyboardButton(text="🖼 USDT QR",callback_data="SQ_USDT")],
            [InlineKeyboardButton(text="🔙 Admin",  callback_data="M_admin")],
        ])
    )

@dp.callback_query(F.data.startswith("SP_"))
async def cb_sp(cb: CallbackQuery, state: FSMContext):
    if not is_adm(cb.from_user.id): return await cb.answer("⛔", show_alert=True)
    await cb.answer()
    cur = cb.data[3:]
    st = {"INR": S.set_upi, "TON": S.set_ton, "USDT": S.set_usdt}[cur]
    labels = {"INR": "UPI ID (e.g. name@upi)", "TON": "TON wallet address", "USDT": "USDT address (TRC20)"}
    await state.set_state(st)
    await safe(cb, f"💰 Send your <b>{labels[cur]}</b>:", kb_cancel())

@dp.message(S.set_upi)
async def proc_upi(msg: Message, state: FSMContext):
    if not is_adm(msg.from_user.id): return
    v = (msg.text or "").strip()
    if not v: return
    await set_cfg("inr_upi", v); await state.clear()
    await msg.answer(f"✅ UPI set: <code>{v}</code>", reply_markup=kb_home())

@dp.message(S.set_ton)
async def proc_ton_addr(msg: Message, state: FSMContext):
    if not is_adm(msg.from_user.id): return
    v = (msg.text or "").strip()
    if not v: return
    await set_cfg("ton_addr", v); await state.clear()
    await msg.answer(f"✅ TON set:\n<code>{v}</code>", reply_markup=kb_home())

@dp.message(S.set_usdt)
async def proc_usdt_addr(msg: Message, state: FSMContext):
    if not is_adm(msg.from_user.id): return
    v = (msg.text or "").strip()
    if not v: return
    await set_cfg("usdt_addr", v); await state.clear()
    await msg.answer(f"✅ USDT set:\n<code>{v}</code>", reply_markup=kb_home())

@dp.callback_query(F.data.startswith("SQ_"))
async def cb_sq(cb: CallbackQuery, state: FSMContext):
    if not is_adm(cb.from_user.id): return await cb.answer("⛔", show_alert=True)
    await cb.answer()
    cur = cb.data[3:]
    st = {"INR": S.set_qr_inr, "TON": S.set_qr_ton, "USDT": S.set_qr_usdt}[cur]
    await state.set_state(st)
    await safe(cb, f"🖼 Send the <b>QR image</b> for <b>{cur}</b> payments:", kb_cancel())

@dp.message(S.set_qr_inr)
async def proc_qr_inr(msg: Message, state: FSMContext):
    if not is_adm(msg.from_user.id): return
    if not msg.photo: return await msg.answer("❌ Send an image.")
    await set_cfg("inr_qr", msg.photo[-1].file_id); await state.clear()
    await msg.answer("✅ INR QR saved!", reply_markup=kb_home())

@dp.message(S.set_qr_ton)
async def proc_qr_ton(msg: Message, state: FSMContext):
    if not is_adm(msg.from_user.id): return
    if not msg.photo: return await msg.answer("❌ Send an image.")
    await set_cfg("ton_qr", msg.photo[-1].file_id); await state.clear()
    await msg.answer("✅ TON QR saved!", reply_markup=kb_home())

@dp.message(S.set_qr_usdt)
async def proc_qr_usdt(msg: Message, state: FSMContext):
    if not is_adm(msg.from_user.id): return
    if not msg.photo: return await msg.answer("❌ Send an image.")
    await set_cfg("usdt_qr", msg.photo[-1].file_id); await state.clear()
    await msg.answer("✅ USDT QR saved!", reply_markup=kb_home())

# ══════════════════════════════════════
#  BOT — STATS + USERS + BROADCAST
# ══════════════════════════════════════
@dp.callback_query(F.data == "A_stats")
async def cb_stats(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.answer()
    if not is_adm(cb.from_user.id): return await cb.answer("⛔", show_alert=True)

    async with aiosqlite.connect(DB_PATH) as db:
        for q, key in [
            ("SELECT COUNT(*) FROM users", "users"),
            ("SELECT COUNT(*) FROM nfts", "total"),
            ("SELECT COUNT(*) FROM nfts WHERE status='available'", "avail"),
            ("SELECT COUNT(*) FROM nfts WHERE status='sold'", "sold"),
            ("SELECT COUNT(*) FROM orders WHERE status='confirmed'", "confirmed"),
            ("SELECT COUNT(*) FROM orders WHERE status='pending'", "pord"),
            ("SELECT COUNT(*) FROM offers WHERE status='pending'", "poff"),
        ]:
            async with db.execute(q) as c:
                locals()[key] = (await c.fetchone())[0]

    await safe(cb,
        f"📊 <b>Stats</b>\n━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 Users: <b>{users}</b>\n"
        f"🖼 Total NFTs: <b>{total}</b>  |  🟢 Live: <b>{avail}</b>  |  🔴 Sold: <b>{sold}</b>\n\n"
        f"✅ Confirmed Sales: <b>{confirmed}</b>\n"
        f"💸 Pending Payments: <b>{pord}</b>\n"
        f"📬 Pending Offers: <b>{poff}</b>" + wm(),
        kb_home()
    )

@dp.callback_query(F.data == "A_users")
async def cb_users(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.answer()
    if not is_adm(cb.from_user.id): return await cb.answer("⛔", show_alert=True)

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id, username, full_name, joined_at FROM users ORDER BY rowid DESC LIMIT 30") as c:
            users = await c.fetchall()

    if not users:
        return await safe(cb, "👥 No users yet." + wm(), kb_home())

    text = f"👥 <b>Users ({len(users)} recent)</b>\n━━━━━━━━━━━━━━━━━━━━━\n\n"
    for uid2, uname, fname, joined in users:
        text += f"<code>{uid2}</code>  @{uname or 'N/A'}  {fname[:12]}  {joined[:10]}\n"
    await safe(cb, text + wm(), kb_home())

@dp.callback_query(F.data == "A_bcast")
async def cb_bcast(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    if not is_adm(cb.from_user.id): return await cb.answer("⛔", show_alert=True)
    await cb.answer()
    await state.set_state(S.broadcast)
    await safe(cb, "📢 <b>Broadcast</b>\n\nSend your message:", kb_cancel())

@dp.message(S.broadcast)
async def proc_bcast(msg: Message, state: FSMContext):
    if not is_adm(msg.from_user.id): return
    await state.clear()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM users") as c:
            users = await c.fetchall()
    sent = failed = 0
    for (uid2,) in users:
        try:
            await bot.send_message(uid2, msg.text or msg.caption or "📢 Announcement!")
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)
    await msg.answer(f"📢 <b>Done!</b>\n\n✅ Sent: {sent}\n❌ Failed: {failed}" + wm(), reply_markup=kb_home())

# ══════════════════════════════════════
#  MAIN — run web server + bot together
# ══════════════════════════════════════
async def run_web():
    app = web.Application()
    app.add_routes(routes)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    log.info(f"🌐 Web server running on port {PORT}")

async def main():
    await init_db()
    log.info(f"🚀 {SHOP_NAME} starting...")
    log.info(f"🌐 Mini App URL: {APP_URL}")
    log.info(f"👑 Admins: {ADMIN_IDS}")
    await run_web()
    await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
