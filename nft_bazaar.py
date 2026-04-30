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
APP_URL    = "https://nft-1-yoes.onrender.com"  # ← paste your Render URL here
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
TML_APP = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>NFT Market</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent}
:root{
  --bg:#f5f5f2;--surf:#fff;--bdr:#eaeae6;--bdr2:#d8d8d4;
  --yellow:#ffd60a;--y2:#f5c800;--y3:#fff3c0;
  --black:#0a0a0a;--sub:#888;--sub2:#bbb;
  --ton:#0098ea;--green:#00c853;--red:#f44336;--purple:#7c3aed;
  --r:16px;--r2:12px;--r3:8px;
}
*{font-family:'Space Grotesk',sans-serif}
body{background:var(--bg);color:var(--black);min-height:100vh;overflow-x:hidden;padding-bottom:76px}
::-webkit-scrollbar{width:0;height:0}

/* ═══════════════════ SPLASH ═══════════════════ */
#splash{position:fixed;inset:0;z-index:9999;display:flex;flex-direction:column;align-items:center;justify-content:center;overflow:hidden;transition:opacity .5s ease}
#splash.hide{opacity:0;pointer-events:none}
.sp-bg{position:absolute;inset:0;background:#0a0a0a}
.sp-circle{position:absolute;border-radius:50%;animation:splExpand 2s ease forwards}
.sp-c1{width:300px;height:300px;background:var(--yellow);top:50%;left:50%;transform:translate(-50%,-50%) scale(0);animation-delay:.1s}
.sp-c2{width:500px;height:500px;background:rgba(255,214,10,.15);top:50%;left:50%;transform:translate(-50%,-50%) scale(0);animation-delay:.3s}
.sp-c3{width:700px;height:700px;background:rgba(255,214,10,.06);top:50%;left:50%;transform:translate(-50%,-50%) scale(0);animation-delay:.5s}
@keyframes splExpand{0%{transform:translate(-50%,-50%) scale(0)}60%{transform:translate(-50%,-50%) scale(1)}100%{transform:translate(-50%,-50%) scale(1)}}
.sp-content{position:relative;z-index:2;text-align:center}
.sp-icon{font-size:72px;animation:spIco .6s .2s ease both}
@keyframes spIco{from{opacity:0;transform:scale(.5)}to{opacity:1;transform:scale(1)}}
.sp-logo{font-size:42px;font-weight:800;color:#fff;letter-spacing:-2px;margin-top:12px;animation:spTxt .5s .4s ease both}
.sp-logo span{color:var(--yellow)}
.sp-tagline{font-size:12px;font-weight:600;color:rgba(255,255,255,.45);letter-spacing:4px;text-transform:uppercase;margin-top:6px;animation:spTxt .5s .55s ease both}
@keyframes spTxt{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
.sp-pills{display:flex;gap:8px;margin-top:24px;justify-content:center;animation:spTxt .5s .7s ease both}
.sp-pill{background:rgba(255,255,255,.1);border:1px solid rgba(255,255,255,.15);color:rgba(255,255,255,.7);font-size:10px;font-weight:700;padding:5px 12px;border-radius:20px;letter-spacing:.5px}
.sp-bar-wrap{width:200px;height:3px;background:rgba(255,255,255,.15);border-radius:3px;margin-top:28px;overflow:hidden;animation:spTxt .4s .8s ease both}
.sp-bar{height:100%;background:var(--yellow);border-radius:3px;animation:spLoad 1.6s .9s ease forwards;width:0}
@keyframes spLoad{0%{width:0}100%{width:100%}}
.sp-dots{position:absolute;inset:0;overflow:hidden;pointer-events:none}
.sp-dot{position:absolute;width:4px;height:4px;background:rgba(255,214,10,.3);border-radius:50%;animation:spFloat linear infinite}
@keyframes spFloat{0%{transform:translateY(100vh) scale(0);opacity:0}20%{opacity:1}80%{opacity:1}100%{transform:translateY(-100px) scale(1.5);opacity:0}}

/* ═══════════════════ TOPBAR ═══════════════════ */
.topbar{position:sticky;top:0;z-index:100;background:rgba(245,245,242,.97);backdrop-filter:blur(16px);padding:10px 14px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--bdr)}
.logo{font-size:20px;font-weight:800;letter-spacing:-0.5px;display:flex;align-items:center;gap:6px}
.logo-box{background:var(--yellow);color:var(--black);padding:2px 8px;border-radius:7px;font-size:13px;font-weight:800}
.tb-right{display:flex;align-items:center;gap:7px}
.live-pill{background:var(--black);color:#fff;font-size:10px;font-weight:700;padding:4px 10px;border-radius:20px;display:flex;align-items:center;gap:4px;letter-spacing:.5px}
.ldot{width:5px;height:5px;background:var(--yellow);border-radius:50%;animation:blink 1.4s infinite}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.2}}
.cnt-pill{background:var(--yellow);color:var(--black);font-size:11px;font-weight:800;padding:4px 11px;border-radius:20px}

/* ═══════════════════ NAV ═══════════════════ */
.bnav{position:fixed;bottom:0;left:0;right:0;z-index:100;background:var(--surf);border-top:1.5px solid var(--bdr);display:flex;padding:0 0 env(safe-area-inset-bottom)}
.ni{flex:1;display:flex;flex-direction:column;align-items:center;gap:2px;padding:9px 4px 7px;cursor:pointer;color:var(--sub);font-size:10px;font-weight:600;border:none;background:none;transition:color .15s;position:relative}
.ni.active{color:var(--black)}
.ni-ic{font-size:20px;line-height:1}
.ni-bar{position:absolute;bottom:0;left:50%;transform:translateX(-50%);width:0;height:3px;background:var(--yellow);border-radius:3px 3px 0 0;transition:width .2s}
.ni.active .ni-bar{width:28px}

/* ═══════════════════ PAGES ═══════════════════ */
.page{display:none;animation:pgIn .2s ease}
.page.active{display:block}
@keyframes pgIn{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}

/* ═══════════════════ HERO ═══════════════════ */
.hero{margin:10px;border-radius:var(--r);background:var(--black);padding:20px 20px 20px;overflow:hidden;position:relative}
.hero-bg{position:absolute;inset:0;background:radial-gradient(ellipse at 85% 50%,rgba(255,214,10,.2),transparent 60%)}
.hero-title{font-size:22px;font-weight:800;color:#fff;line-height:1.25;letter-spacing:-0.5px;position:relative}
.hero-sub{font-size:11px;color:#666;margin-top:5px;position:relative;font-weight:500}
.hero-tags{display:flex;gap:6px;margin-top:12px;position:relative;flex-wrap:wrap}
.hero-tag{background:rgba(255,255,255,.1);color:rgba(255,255,255,.75);font-size:10px;font-weight:700;padding:4px 10px;border-radius:20px;letter-spacing:.5px}
.hero-tag.gold{background:var(--yellow);color:var(--black)}
.hero-right{position:absolute;right:16px;top:50%;transform:translateY(-50%);font-size:60px;opacity:.9}

/* STATS ROW */
.stats-row{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;padding:10px;margin-bottom:2px}
.stat-card{background:var(--surf);border:1px solid var(--bdr);border-radius:var(--r2);padding:12px 10px;text-align:center}
.stat-val{font-size:20px;font-weight:800;letter-spacing:-0.5px}
.stat-lbl{font-size:10px;color:var(--sub);font-weight:600;margin-top:2px;text-transform:uppercase;letter-spacing:.5px}

/* ═══════════════════ SEARCH ═══════════════════ */
.search-wrap{padding:0 10px 8px}
.search-box{background:var(--surf);border:1.5px solid var(--bdr);border-radius:var(--r2);padding:11px 14px;display:flex;align-items:center;gap:8px;transition:border-color .15s}
.search-box:focus-within{border-color:var(--black)}
.search-ic{font-size:16px;flex-shrink:0}
.search-inp{flex:1;border:none;outline:none;font-size:14px;font-weight:500;background:transparent;color:var(--black)}
.search-inp::placeholder{color:var(--sub2)}

/* ═══════════════════ CATEGORIES ═══════════════════ */
.cats{display:flex;gap:7px;padding:0 10px 10px;overflow-x:auto;scrollbar-width:none}
.cats::-webkit-scrollbar{display:none}
.cat{background:var(--surf);border:1.5px solid var(--bdr);border-radius:30px;padding:7px 14px;font-size:12px;font-weight:700;white-space:nowrap;cursor:pointer;transition:all .15s;display:flex;align-items:center;gap:5px;flex-shrink:0}
.cat:active,.cat.sel{background:var(--yellow);border-color:var(--yellow);color:var(--black)}
.cat-ic{font-size:14px}

/* ═══════════════════ SECTION ═══════════════════ */
.sh{padding:8px 14px 8px;display:flex;align-items:center;justify-content:space-between}
.sh-title{font-size:15px;font-weight:800;letter-spacing:-0.3px}
.sh-cnt{background:var(--bdr);font-size:10px;font-weight:700;padding:3px 10px;border-radius:20px;color:var(--sub)}
.sh-more{font-size:11px;font-weight:700;color:var(--ton);cursor:pointer}

/* ═══════════════════ FEATURED ROW ═══════════════════ */
.feat-row{display:flex;gap:10px;padding:0 10px 10px;overflow-x:auto;scrollbar-width:none}
.feat-row::-webkit-scrollbar{display:none}
.feat-card{flex-shrink:0;width:160px;background:var(--surf);border:1px solid var(--bdr);border-radius:var(--r);overflow:hidden;cursor:pointer;transition:transform .12s}
.feat-card:active{transform:scale(.95)}
.feat-img{width:160px;height:140px;display:flex;align-items:center;justify-content:center;font-size:50px;position:relative;overflow:hidden}
.feat-img img{width:100%;height:100%;object-fit:cover}
.feat-body{padding:9px}
.feat-name{font-size:12px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-bottom:6px}
.feat-price-btn{width:100%;background:var(--yellow);border:none;border-radius:var(--r3);padding:7px;font-size:12px;font-weight:800;cursor:pointer;font-family:'Space Grotesk',sans-serif}

/* ═══════════════════ GRID ═══════════════════ */
.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;padding:0 10px 10px}
.card{background:var(--surf);border:1px solid var(--bdr);border-radius:var(--r);overflow:hidden;cursor:pointer;transition:transform .12s;position:relative}
.card:active{transform:scale(.96)}
.card-img{aspect-ratio:1;display:flex;align-items:center;justify-content:center;font-size:44px;position:relative;overflow:hidden}
.card-img img{width:100%;height:100%;object-fit:cover}
.card-badge{position:absolute;top:7px;left:7px;font-size:8px;font-weight:800;padding:2px 8px;border-radius:20px;letter-spacing:.5px}
.badge-live{background:var(--yellow);color:var(--black)}
.badge-hot{background:#ff4500;color:#fff}
.badge-new{background:var(--green);color:#fff}
.badge-rare{background:var(--purple);color:#fff}
.card-body{padding:9px 10px 10px}
.card-name{font-size:13px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-bottom:8px}
.price-btn{width:100%;background:var(--yellow);border:none;border-radius:var(--r3);padding:8px 10px;font-size:13px;font-weight:800;cursor:pointer;display:flex;align-items:center;justify-content:space-between;font-family:'Space Grotesk',sans-serif;transition:background .12s}
.price-btn:active{background:var(--y2)}

/* ═══════════════════ GAME SECTION ═══════════════════ */
.games-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;padding:0 10px 10px}
.game-card{background:var(--surf);border:1px solid var(--bdr);border-radius:var(--r);padding:16px;cursor:pointer;transition:transform .12s;position:relative;overflow:hidden}
.game-card:active{transform:scale(.96)}
.game-card::before{content:'';position:absolute;inset:0;opacity:.08}
.game-card.gc-y::before{background:var(--yellow)}
.game-card.gc-b::before{background:var(--ton)}
.game-card.gc-g::before{background:var(--green)}
.game-card.gc-p::before{background:var(--purple)}
.game-card.gc-r::before{background:var(--red)}
.game-card.gc-o::before{background:#ff6b00}
.game-ic{font-size:36px;margin-bottom:10px;display:block}
.game-name{font-size:14px;font-weight:800;letter-spacing:-.3px;margin-bottom:4px}
.game-desc{font-size:11px;color:var(--sub);font-weight:500;line-height:1.4}
.game-tag{display:inline-block;font-size:9px;font-weight:800;padding:2px 8px;border-radius:20px;margin-top:8px;letter-spacing:.5px}
.gt-hot{background:#ff450018;color:#ff4500}
.gt-new{background:#00c85318;color:#00c853}
.gt-soon{background:#88888818;color:#888}

/* ═══════════════════ SHEETS ═══════════════════ */
.overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:200;align-items:flex-end;backdrop-filter:blur(3px)}
.overlay.on{display:flex}
.sheet{background:var(--surf);border-radius:24px 24px 0 0;width:100%;max-height:93vh;overflow-y:auto;animation:shUp .26s cubic-bezier(.34,1.4,.64,1)}
@keyframes shUp{from{transform:translateY(100%)}to{transform:none}}
.hdl{width:36px;height:4px;background:var(--bdr2);border-radius:2px;margin:12px auto 0}
.sc{padding:16px}
.d-img{width:100%;aspect-ratio:1;border-radius:var(--r);overflow:hidden;display:flex;align-items:center;justify-content:center;font-size:80px;margin-bottom:14px}
.d-img img{width:100%;height:100%;object-fit:cover}
.d-name{font-size:24px;font-weight:800;letter-spacing:-.5px;margin-bottom:4px}
.d-desc{font-size:13px;color:var(--sub);line-height:1.6;margin-bottom:16px}
.pcard{background:var(--bg);border-radius:var(--r2);padding:12px;margin-bottom:14px}
.plbl{font-size:10px;font-weight:700;color:var(--sub);text-transform:uppercase;letter-spacing:1px;margin-bottom:10px}
.prow{display:flex;align-items:center;gap:10px;padding:4px 0;font-size:14px;font-weight:700}
.p-ic{font-size:18px}
.p-sub{font-size:11px;font-weight:500;color:var(--sub);margin-left:2px}
.btns{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.btn{padding:14px;border-radius:var(--r2);border:none;font-size:13px;font-weight:800;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:6px;font-family:'Space Grotesk',sans-serif;transition:opacity .15s,transform .1s;letter-spacing:.2px}
.btn:active{transform:scale(.96);opacity:.85}
.btn-y{background:var(--yellow);color:var(--black)}
.btn-b{background:var(--black);color:#fff}
.btn-g{background:var(--green);color:#fff}
.btn-full{grid-column:1/-1}
.cur-card{background:var(--bg);border:1.5px solid var(--bdr);border-radius:var(--r2);padding:14px;margin-bottom:10px;cursor:pointer;display:flex;align-items:center;gap:12px;transition:border-color .15s,background .15s}
.cur-card:active{border-color:var(--black);background:#efefef}
.cur-ic{font-size:28px}
.cur-name{font-size:14px;font-weight:700}
.cur-price{font-size:12px;color:var(--sub);margin-top:2px}
.amt{font-size:38px;font-weight:800;text-align:center;letter-spacing:-1px;padding:14px 0}
.addrbox{background:var(--bg);border-radius:var(--r2);padding:13px;margin-bottom:12px}
.addr-lbl{font-size:10px;font-weight:700;color:var(--sub);text-transform:uppercase;letter-spacing:1px;margin-bottom:7px}
.addr-val{font-family:monospace;font-size:12px;color:var(--ton);word-break:break-all;line-height:1.6}
.copy-btn{margin-top:8px;background:var(--yellow);color:var(--black);border:none;font-size:11px;font-weight:800;padding:6px 14px;border-radius:20px;cursor:pointer;font-family:'Space Grotesk',sans-serif}
.qrbox{width:140px;height:140px;margin:0 auto 14px;background:#fff;border:1px solid var(--bdr);border-radius:var(--r2);overflow:hidden;display:flex;align-items:center;justify-content:center}
.qrbox img{width:100%;height:100%;object-fit:contain}
.txn-inp{width:100%;background:var(--bg);border:1.5px solid var(--bdr);border-radius:var(--r2);color:var(--black);font-size:13px;padding:13px;outline:none;margin-bottom:12px;font-family:monospace;resize:none;transition:border-color .15s}
.txn-inp:focus{border-color:var(--black)}
.txn-inp::placeholder{color:var(--sub2)}
.off-row{display:flex;gap:10px;margin-bottom:12px}
.off-inp{flex:1;background:var(--bg);border:1.5px solid var(--bdr);border-radius:var(--r2);color:var(--black);font-size:16px;font-weight:700;padding:13px;outline:none;transition:border-color .15s}
.off-inp:focus{border-color:var(--black)}
.off-sel{background:var(--bg);border:1.5px solid var(--bdr);border-radius:var(--r2);color:var(--black);font-size:13px;font-weight:700;padding:13px;outline:none;-webkit-appearance:none;min-width:90px;text-align:center}
.sth{display:flex;align-items:center;gap:10px;margin-bottom:16px}
.back-ic{width:36px;height:36px;border-radius:50%;background:var(--bg);border:1.5px solid var(--bdr);display:flex;align-items:center;justify-content:center;cursor:pointer;font-size:16px;flex-shrink:0}
.sth-title{font-size:18px;font-weight:800;letter-spacing:-.3px}

/* ═══════════════════ ORDER/OFFER CARDS ═══════════════════ */
.ocard{background:var(--surf);border:1px solid var(--bdr);border-radius:var(--r2);padding:13px;margin-bottom:10px;display:flex;gap:12px;align-items:center}
.ocard-img{width:50px;height:50px;border-radius:var(--r3);background:var(--bg);overflow:hidden;display:flex;align-items:center;justify-content:center;font-size:22px;flex-shrink:0}
.ocard-img img{width:100%;height:100%;object-fit:cover;border-radius:var(--r3)}
.ocard-name{font-size:13px;font-weight:700;margin-bottom:3px}
.ocard-price{font-size:12px;color:var(--ton);font-weight:700;margin-bottom:5px}
.badge{display:inline-block;font-size:9px;font-weight:800;padding:3px 9px;border-radius:20px;text-transform:uppercase;letter-spacing:.5px}
.bp{background:#fff3cd;color:#856404}
.bc{background:#d4edda;color:#155724}
.br{background:#f8d7da;color:#721c24}
.ba{background:#cce5ff;color:#004085}

/* ═══════════════════ PROFILE ═══════════════════ */
.prof-hero{margin:10px;background:var(--black);border-radius:var(--r);padding:20px;position:relative;overflow:hidden}
.prof-hero::after{content:'';position:absolute;right:-30px;top:-30px;width:150px;height:150px;background:var(--yellow);border-radius:50%;opacity:.1}
.prof-avatar{width:56px;height:56px;border-radius:50%;background:var(--yellow);display:flex;align-items:center;justify-content:center;font-size:24px;font-weight:800;color:var(--black);margin-bottom:12px}
.prof-name{font-size:19px;font-weight:800;color:#fff;letter-spacing:-.3px}
.prof-id{font-size:11px;color:#666;margin-top:3px}
.prof-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:16px;position:relative}
.ps-val{font-size:19px;font-weight:800;color:#fff}
.ps-lbl{font-size:10px;color:#666;font-weight:600;text-transform:uppercase;letter-spacing:.5px;margin-top:2px}
.prof-menu{background:var(--surf);border:1px solid var(--bdr);border-radius:var(--r);overflow:hidden;margin:10px}
.pm-item{display:flex;align-items:center;gap:12px;padding:14px;cursor:pointer;transition:background .12s;border-bottom:1px solid var(--bdr)}
.pm-item:last-child{border-bottom:none}
.pm-item:active{background:var(--bg)}
.pm-ic{font-size:20px;width:32px;text-align:center}
.pm-label{font-size:14px;font-weight:600}
.pm-sub{font-size:11px;color:var(--sub);margin-top:1px}
.pm-arrow{margin-left:auto;color:var(--sub2);font-size:14px}

/* ═══════════════════ WISHLIST ═══════════════════ */
.wish-btn{position:absolute;top:7px;right:7px;width:28px;height:28px;background:rgba(255,255,255,.9);border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:14px;border:none;cursor:pointer;transition:transform .12s;z-index:1}
.wish-btn:active{transform:scale(.85)}
.wish-btn.active{background:var(--yellow)}

/* ═══════════════════ MISC ═══════════════════ */
.empty{text-align:center;padding:48px 20px}
.ei{font-size:56px;margin-bottom:14px}
.et{font-size:18px;font-weight:800;margin-bottom:7px}
.es{color:var(--sub);font-size:13px}
.ld{display:flex;align-items:center;justify-content:center;padding:48px}
.sp2{width:26px;height:26px;border:3px solid var(--bdr);border-top-color:var(--black);border-radius:50%;animation:spin .7s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.toast{position:fixed;bottom:88px;left:50%;transform:translateX(-50%) translateY(12px);background:var(--black);color:#fff;padding:10px 20px;border-radius:24px;font-size:12px;font-weight:700;z-index:9999;opacity:0;transition:all .25s;white-space:nowrap;pointer-events:none}
.toast.on{opacity:1;transform:translateX(-50%) translateY(0)}
.divider{height:1px;background:var(--bdr);margin:4px 10px}
</style>
</head>
<body>

<!-- ══════════ SPLASH ══════════ -->
<div id="splash">
  <div class="sp-bg"></div>
  <div class="sp-dots" id="sp-dots"></div>
  <div class="sp-circle sp-c3"></div>
  <div class="sp-circle sp-c2"></div>
  <div class="sp-circle sp-c1"></div>
  <div class="sp-content">
    <div class="sp-icon">🏆</div>
    <div class="sp-logo">NFT<span>MKT</span></div>
    <div class="sp-tagline">Buy · Sell · Collect</div>
    <div class="sp-pills">
      <div class="sp-pill">INR</div>
      <div class="sp-pill">TON</div>
      <div class="sp-pill">USDT</div>
    </div>
    <div class="sp-bar-wrap"><div class="sp-bar"></div></div>
  </div>
</div>

<!-- ══════════ TOPBAR ══════════ -->
<div class="topbar">
  <div class="logo">NFT<div class="logo-box">MKT</div></div>
  <div class="tb-right">
    <div class="live-pill"><div class="ldot"></div>LIVE</div>
    <div class="cnt-pill" id="stat-n">—</div>
  </div>
</div>

<!-- ══════════ PAGE: MARKET ══════════ -->
<div class="page active" id="page-market">
  <div class="hero">
    <div class="hero-bg"></div>
    <div>
      <div class="hero-title">Exclusive<br>NFT Market</div>
      <div class="hero-sub">Secure · Fast · Verified</div>
      <div class="hero-tags">
        <div class="hero-tag gold">0% Fee</div>
        <div class="hero-tag">INR</div>
        <div class="hero-tag">TON</div>
        <div class="hero-tag">USDT</div>
      </div>
    </div>
    <div class="hero-right">🎁</div>
  </div>

  <div class="stats-row">
    <div class="stat-card">
      <div class="stat-val" id="s-listed">—</div>
      <div class="stat-lbl">Listed</div>
    </div>
    <div class="stat-card">
      <div class="stat-val" id="s-sold">—</div>
      <div class="stat-lbl">Sold</div>
    </div>
    <div class="stat-card">
      <div class="stat-val" id="s-users">—</div>
      <div class="stat-lbl">Buyers</div>
    </div>
  </div>

  <div class="search-wrap">
    <div class="search-box">
      <span class="search-ic">🔍</span>
      <input class="search-inp" id="search-inp" type="text" placeholder="Search NFTs..." oninput="filterNFTs()">
    </div>
  </div>

  <div class="cats" id="cats">
    <div class="cat sel" data-cat="all" onclick="setCat('all',this)"><span class="cat-ic">🌐</span>All</div>
    <div class="cat" data-cat="gift" onclick="setCat('gift',this)"><span class="cat-ic">🎁</span>Gifts</div>
    <div class="cat" data-cat="rare" onclick="setCat('rare',this)"><span class="cat-ic">💎</span>Rare</div>
    <div class="cat" data-cat="collection" onclick="setCat('collection',this)"><span class="cat-ic">🗂</span>Collections</div>
    <div class="cat" data-cat="special" onclick="setCat('special',this)"><span class="cat-ic">⭐</span>Special</div>
    <div class="cat" data-cat="limited" onclick="setCat('limited',this)"><span class="cat-ic">🔥</span>Limited</div>
  </div>

  <div class="sh">
    <div class="sh-title">🔥 Hot Picks</div>
  </div>
  <div class="feat-row" id="feat-row">
    <div class="ld" style="padding:20px 40px"><div class="sp2"></div></div>
  </div>

  <div class="sh">
    <div class="sh-title">All NFTs</div>
    <div class="sh-cnt" id="browse-cnt">—</div>
  </div>
  <div class="grid" id="nft-grid">
    <div class="ld" style="grid-column:1/-1"><div class="sp2"></div></div>
  </div>
</div>

<!-- ══════════ PAGE: PLAY ══════════ -->
<div class="page" id="page-play">
  <div class="hero" style="background:linear-gradient(135deg,#1a0a3a,#0a1a3a)">
    <div class="hero-bg" style="background:radial-gradient(ellipse at 80% 50%,rgba(124,58,237,.3),transparent)"></div>
    <div>
      <div class="hero-title">Play &<br>Earn NFTs</div>
      <div class="hero-sub">Win exclusive collectibles</div>
      <div class="hero-tags">
        <div class="hero-tag" style="background:rgba(124,58,237,.3);color:#c4b5fd">Games</div>
        <div class="hero-tag" style="background:rgba(0,200,83,.2);color:#86efac">Rewards</div>
      </div>
    </div>
    <div class="hero-right">🎮</div>
  </div>
  <div class="sh"><div class="sh-title">🎮 Game Hub</div></div>
  <div class="games-grid">
    <div class="game-card gc-y" onclick="gameAlert('NFT Quiz')">
      <span class="game-ic">🧠</span>
      <div class="game-name">NFT QUIZ</div>
      <div class="game-desc">Test your NFT knowledge, win rare collectibles</div>
      <div class="game-tag gt-hot">🔥 HOT</div>
    </div>
    <div class="game-card gc-p" onclick="gameAlert('Lucky Spin')">
      <span class="game-ic">🎰</span>
      <div class="game-name">LUCKY SPIN</div>
      <div class="game-desc">Spin the wheel and win mystery NFTs</div>
      <div class="game-tag gt-new">✨ NEW</div>
    </div>
    <div class="game-card gc-b" onclick="gameAlert('Price Predictor')">
      <span class="game-ic">📈</span>
      <div class="game-name">PREDICT</div>
      <div class="game-desc">Guess NFT prices, earn rewards</div>
      <div class="game-tag gt-hot">🔥 HOT</div>
    </div>
    <div class="game-card gc-g" onclick="gameAlert('Daily Checkin')">
      <span class="game-ic">📅</span>
      <div class="game-name">DAILY CHECK-IN</div>
      <div class="game-desc">Check in daily, earn streak rewards</div>
      <div class="game-tag gt-new">✨ FREE</div>
    </div>
    <div class="game-card gc-o" onclick="gameAlert('NFT Hunt')">
      <span class="game-ic">🔎</span>
      <div class="game-name">NFT HUNT</div>
      <div class="game-desc">Find hidden NFTs in challenges</div>
      <div class="game-tag gt-soon">⏳ SOON</div>
    </div>
    <div class="game-card gc-r" onclick="gameAlert('Battle Arena')">
      <span class="game-ic">⚔️</span>
      <div class="game-name">BATTLE ARENA</div>
      <div class="game-desc">Challenge others with your NFTs</div>
      <div class="game-tag gt-soon">⏳ SOON</div>
    </div>
  </div>

  <div class="sh"><div class="sh-title">🏆 Leaderboard</div></div>
  <div id="leaderboard" style="padding:0 10px 10px">
    <div class="ld"><div class="sp2"></div></div>
  </div>
</div>

<!-- ══════════ PAGE: ORDERS ══════════ -->
<div class="page" id="page-orders">
  <div class="sh" style="padding-top:14px"><div class="sh-title">📦 My Orders</div></div>
  <div id="orders-list" style="padding:0 10px"><div class="ld"><div class="sp2"></div></div></div>
  <div class="divider"></div>
  <div class="sh"><div class="sh-title">📬 My Offers</div></div>
  <div id="offers-list" style="padding:0 10px"><div class="ld"><div class="sp2"></div></div></div>
</div>

<!-- ══════════ PAGE: PROFILE ══════════ -->
<div class="page" id="page-profile">
  <div class="prof-hero">
    <div class="prof-avatar" id="prof-av">V</div>
    <div class="prof-name" id="prof-name">Loading...</div>
    <div class="prof-id" id="prof-id">@username</div>
    <div class="prof-stats">
      <div><div class="ps-val" id="pr-orders">0</div><div class="ps-lbl">Orders</div></div>
      <div><div class="ps-val" id="pr-offers">0</div><div class="ps-lbl">Offers</div></div>
      <div><div class="ps-val" id="pr-owned">0</div><div class="ps-lbl">Owned</div></div>
    </div>
  </div>
  <div class="prof-menu">
    <div class="pm-item" onclick="gotoPage('orders')">
      <span class="pm-ic">📦</span>
      <div><div class="pm-label">My Orders</div><div class="pm-sub">Track your purchases</div></div>
      <span class="pm-arrow">›</span>
    </div>
    <div class="pm-item" onclick="gotoPage('orders')">
      <span class="pm-ic">📬</span>
      <div><div class="pm-label">My Offers</div><div class="pm-sub">Active & past offers</div></div>
      <span class="pm-arrow">›</span>
    </div>
    <div class="pm-item" onclick="showWishlist()">
      <span class="pm-ic">❤️</span>
      <div><div class="pm-label">Wishlist</div><div class="pm-sub">Saved NFTs</div></div>
      <span class="pm-arrow">›</span>
    </div>
    <div class="pm-item" onclick="openSupport()">
      <span class="pm-ic">💬</span>
      <div><div class="pm-label">Support</div><div class="pm-sub">Get help from our team</div></div>
      <span class="pm-arrow">›</span>
    </div>
    <div class="pm-item" onclick="openHowToBuy()">
      <span class="pm-ic">❓</span>
      <div><div class="pm-label">How to Buy</div><div class="pm-sub">Step-by-step guide</div></div>
      <span class="pm-arrow">›</span>
    </div>
  </div>
  <div style="text-align:center;padding:20px 0 10px;color:var(--sub2);font-size:11px;font-weight:600">NFT Market · Secure & Verified · v2.0</div>
</div>

<!-- ══════════ SHEETS ══════════ -->
<div class="overlay" id="ov-detail"><div class="sheet"><div class="hdl"></div><div class="sc" id="sc-detail"></div></div></div>
<div class="overlay" id="ov-buy"><div class="sheet"><div class="hdl"></div><div class="sc" id="sc-buy"></div></div></div>
<div class="overlay" id="ov-misc"><div class="sheet"><div class="hdl"></div><div class="sc" id="sc-misc"></div></div></div>

<!-- ══════════ NAV ══════════ -->
<nav class="bnav">
  <button class="ni active" id="nav-market" onclick="gotoPage('market')">
    <div class="ni-ic">🏪</div>Market<div class="ni-bar"></div>
  </button>
  <button class="ni" id="nav-play" onclick="gotoPage('play')">
    <div class="ni-ic">🎮</div>Play<div class="ni-bar"></div>
  </button>
  <button class="ni" id="nav-orders" onclick="gotoPage('orders')">
    <div class="ni-ic">📦</div>Orders<div class="ni-bar"></div>
  </button>
  <button class="ni" id="nav-profile" onclick="gotoPage('profile')">
    <div class="ni-ic">👤</div>Profile<div class="ni-bar"></div>
  </button>
</nav>

<div class="toast" id="toast"></div>

<script>
const tg = window.Telegram.WebApp;
tg.ready(); tg.expand();
const usr = tg.initDataUnsafe?.user || {};
const uid = usr.id || 0;

// Splash dots
const dots = document.getElementById('sp-dots');
for(let i=0;i<18;i++){
  const d=document.createElement('div');
  d.className='sp-dot';
  d.style.left=Math.random()*100+'%';
  d.style.animationDuration=(3+Math.random()*4)+'s';
  d.style.animationDelay=(Math.random()*3)+'s';
  d.style.width=d.style.height=(3+Math.random()*5)+'px';
  dots.appendChild(d);
}
setTimeout(()=>{
  const s=document.getElementById('splash');
  s.classList.add('hide');
  setTimeout(()=>s.remove(),600);
},2400);

let allNFTs=[], curNft=null, buyS={}, activeCat='all', wishlist=new Set(JSON.parse(localStorage.getItem('wl')||'[]'));
window._offerPayData=null;

const BG = ['#e8f4e8','#e8e8f4','#f4e8e8','#f4f0e0','#e0f0f4','#f0e8f4','#e8f4f0','#f4ece0','#faf0e0','#e8f0f8'];

function toast(m,d=2500){const t=document.getElementById('toast');t.textContent=m;t.classList.add('on');setTimeout(()=>t.classList.remove('on'),d)}
function copy(txt){navigator.clipboard?.writeText(txt).then(()=>toast('✅ Copied!')).catch(()=>{const a=document.createElement('textarea');a.value=txt;document.body.appendChild(a);a.select();document.execCommand('copy');document.body.removeChild(a);toast('✅ Copied!')})}
function topPrice(n){if(n.price_inr>0)return'₹'+Number(n.price_inr).toLocaleString('en-IN');if(n.price_ton>0)return'◈ '+n.price_ton;if(n.price_usdt>0)return'$'+n.price_usdt;return'Offer'}
function allPrices(n){const p=[];if(n.price_inr>0)p.push({ic:'🇮🇳',name:'Indian Rupee',lbl:'INR',txt:'₹'+Number(n.price_inr).toLocaleString('en-IN'),val:n.price_inr});if(n.price_ton>0)p.push({ic:'💎',name:'TON Coin',lbl:'TON',txt:'◈ '+n.price_ton+' TON',val:n.price_ton});if(n.price_usdt>0)p.push({ic:'💵',name:'USDT',lbl:'USDT',txt:'$'+n.price_usdt,val:n.price_usdt});return p}

function gotoPage(name){
  document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.ni').forEach(n=>n.classList.remove('active'));
  document.getElementById('page-'+name).classList.add('active');
  document.getElementById('nav-'+name).classList.add('active');
  if(name==='orders'){loadOrders();loadOffers();}
  if(name==='play'){loadLeaderboard();}
  if(name==='profile'){loadProfile();}
}

function openSheet(id){document.getElementById(id).classList.add('on');tg.BackButton.show();tg.BackButton.onClick(closeAll)}
function closeAll(){document.querySelectorAll('.overlay').forEach(o=>o.classList.remove('on'));tg.BackButton.hide()}
document.querySelectorAll('.overlay').forEach(ov=>ov.addEventListener('click',e=>{if(e.target===ov)closeAll()}));

// Categories
function setCat(cat,el){
  activeCat=cat;
  document.querySelectorAll('.cat').forEach(c=>c.classList.remove('sel'));
  el.classList.add('sel');
  renderGrid(filterList());
}
function filterList(){
  const q=document.getElementById('search-inp').value.toLowerCase().trim();
  return allNFTs.filter(n=>{
    const matchQ=!q||n.name.toLowerCase().includes(q)||(n.description||'').toLowerCase().includes(q);
    const matchC=activeCat==='all'||true; // all shown since no server-side categories
    return matchQ&&matchC;
  });
}
function filterNFTs(){renderGrid(filterList())}

// Load NFTs
async function loadNFTs(){
  try{
    const d=await(await fetch('/api/nfts')).json();
    allNFTs=d;
    document.getElementById('browse-cnt').textContent=d.length;
    document.getElementById('stat-n').textContent=d.length;
    document.getElementById('s-listed').textContent=d.length;
    renderFeatured(d.slice(0,6));
    renderGrid(d);
    // Load sold stats
    fetch('/api/stats').then(r=>r.json()).then(s=>{
      document.getElementById('s-sold').textContent=s.sold||0;
      document.getElementById('s-users').textContent=s.users||0;
    }).catch(()=>{document.getElementById('s-sold').textContent='0';document.getElementById('s-users').textContent='0'});
  }catch(e){
    document.getElementById('nft-grid').innerHTML=`<div class="empty" style="grid-column:1/-1"><div class="ei">⚠️</div><div class="et">Failed to load</div></div>`;
  }
}

function renderFeatured(nfts){
  const el=document.getElementById('feat-row');
  if(!nfts.length){el.innerHTML='<div style="padding:20px;color:var(--sub);font-size:13px">No NFTs yet</div>';return}
  el.innerHTML=nfts.map((n,i)=>`
    <div class="feat-card" onclick="openNFT(${n.id})">
      <div class="feat-img" style="background:${BG[i%BG.length]}">
        ${n.has_image?`<img src="/api/img/${n.id}" loading="lazy" alt="" onerror="this.parentElement.innerHTML='🖼'">`:'🖼'}
      </div>
      <div class="feat-body">
        <div class="feat-name">${n.name}</div>
        <button class="feat-price-btn">${topPrice(n)}</button>
      </div>
    </div>`).join('');
}

function renderGrid(nfts){
  const g=document.getElementById('nft-grid');
  document.getElementById('browse-cnt').textContent=nfts.length;
  if(!nfts.length){g.innerHTML=`<div class="empty" style="grid-column:1/-1"><div class="ei">😔</div><div class="et">No Results</div><div class="es">Try a different search</div></div>`;return}
  const badges=['badge-live','badge-hot','badge-new','badge-rare'];
  const blabels=['LIVE','HOT','NEW','RARE'];
  g.innerHTML=nfts.map((n,i)=>{
    const bi=i%4; const wl=wishlist.has(n.id);
    return`<div class="card">
      <div class="card-img" style="background:${BG[i%BG.length]}" onclick="openNFT(${n.id})">
        ${n.has_image?`<img src="/api/img/${n.id}" loading="lazy" alt="" onerror="this.parentElement.innerHTML='🖼'">`:'🖼'}
        <div class="card-badge ${badges[bi]}">${blabels[bi]}</div>
        <button class="wish-btn ${wl?'active':''}" onclick="toggleWish(${n.id},this)" title="Wishlist">${wl?'❤️':'🤍'}</button>
      </div>
      <div class="card-body" onclick="openNFT(${n.id})">
        <div class="card-name">${n.name}</div>
        <button class="price-btn">${topPrice(n)} <span>🛒</span></button>
      </div>
    </div>`}).join('');
}

// Wishlist
function toggleWish(id,btn){
  if(wishlist.has(id)){wishlist.delete(id);btn.textContent='🤍';btn.classList.remove('active');toast('Removed from wishlist')}
  else{wishlist.add(id);btn.textContent='❤️';btn.classList.add('active');toast('❤️ Added to wishlist')}
  localStorage.setItem('wl',JSON.stringify([...wishlist]));
}

function showWishlist(){
  const wl=[...wishlist];
  const items=allNFTs.filter(n=>wl.includes(n.id));
  document.getElementById('sc-misc').innerHTML=`
    <div class="sth"><div class="back-ic" onclick="closeAll()">✕</div><div class="sth-title">❤️ Wishlist</div></div>
    ${!items.length?`<div class="empty"><div class="ei">🤍</div><div class="et">Empty Wishlist</div><div class="es">Tap ❤️ on any NFT to save it</div></div>`:
    items.map((n,i)=>`
      <div class="ocard" onclick="closeAll();openNFT(${n.id})">
        <div class="ocard-img" style="background:${BG[i%BG.length]}">${n.has_image?`<img src="/api/img/${n.id}" alt="">`:'🖼'}</div>
        <div style="flex:1"><div class="ocard-name">${n.name}</div><div class="ocard-price">${topPrice(n)}</div></div>
        <span style="color:var(--sub2)">›</span>
      </div>`).join('')}`;
  openSheet('ov-misc');
}

// Open NFT Detail
async function openNFT(id){
  const sc=document.getElementById('sc-detail');
  sc.innerHTML=`<div class="ld"><div class="sp2"></div></div>`;
  openSheet('ov-detail');
  try{
    const n=await(await fetch(`/api/nft/${id}`)).json();
    curNft=n;
    const prices=allPrices(n);
    const idx=id%BG.length;
    const wl=wishlist.has(id);
    sc.innerHTML=`
      <div style="display:flex;justify-content:flex-end;margin-bottom:8px">
        <button onclick="toggleWish(${id},this)" class="wish-btn ${wl?'active':''}" style="position:static;width:36px;height:36px;background:var(--bg);border:1.5px solid var(--bdr)">${wl?'❤️':'🤍'}</button>
      </div>
      <div class="d-img" style="background:${BG[idx]}">
        ${n.has_image?`<img src="/api/img/${n.id}" alt="${n.name}" onerror="this.parentElement.innerHTML='🖼'">`:'🖼'}
      </div>
      <div class="d-name">${n.name}</div>
      <div class="d-desc">${n.description||'Exclusive digital collectible.'}</div>
      ${prices.length?`<div class="pcard"><div class="plbl">Price</div>${prices.map(p=>`<div class="prow"><span class="p-ic">${p.ic}</span><span>${p.txt}</span><span class="p-sub">${p.name}</span></div>`).join('')}</div>`:`<div class="pcard"><div class="plbl">Price</div><div style="color:var(--sub);font-size:13px;padding:4px 0">Open to offers only</div></div>`}
      <div class="btns">
        ${prices.length?`<button class="btn btn-y" onclick="startBuy()">⚡ Buy Now</button>`:''}
        <button class="btn btn-b ${!prices.length?'btn-full':''}" onclick="startOffer()">📬 Make Offer</button>
      </div>`;
  }catch(e){sc.innerHTML=`<div class="empty"><div class="ei">⚠️</div><div class="et">Failed to load</div></div>`}
}

function startBuy(){
  buyS={step:'cur',nft:curNft,prices:allPrices(curNft)};
  closeAll(); renderBuy(); openSheet('ov-buy');
}
function renderBuy(){
  const sc=document.getElementById('sc-buy');
  const{step,nft,prices}=buyS;
  if(step==='cur'){
    sc.innerHTML=`
      <div class="sth"><div class="back-ic" onclick="closeAll()">✕</div><div class="sth-title">Select Currency</div></div>
      <div style="color:var(--sub);font-size:12px;font-weight:500;margin-bottom:14px">${nft.name}</div>
      ${prices.map(p=>`<div class="cur-card" onclick="pickCur('${p.lbl}',${p.val})"><div class="cur-ic">${p.lbl==='INR'?'🇮🇳':p.lbl==='TON'?'💎':'💵'}</div><div><div class="cur-name">${p.name}</div><div class="cur-price">${p.txt}</div></div></div>`).join('')}`;
  }
  if(step==='pay'){
    const{cur,price,addr,qrUrl}=buyS;
    const sym={INR:'₹',TON:'◈ ',USDT:'$'}[cur];
    const lbl={INR:'UPI ID',TON:'TON Wallet Address',USDT:'USDT Address (TRC20)'}[cur];
    const safe=addr.replace(/'/g,"\\'");
    sc.innerHTML=`
      <div class="sth"><div class="back-ic" onclick="buyS.step='cur';renderBuy()">←</div><div class="sth-title">Pay Now</div></div>
      <div class="amt">${sym}${Number(price).toLocaleString()} ${cur}</div>
      ${qrUrl?`<div class="qrbox"><img src="${qrUrl}" alt="QR"></div>`:''}
      <div class="addrbox"><div class="addr-lbl">${lbl}</div><div class="addr-val">${addr}</div><button class="copy-btn" onclick="copy('${safe}')">📋 Copy</button></div>
      <div style="font-size:12px;color:var(--sub);margin-bottom:9px;font-weight:500">Enter your Transaction ID after paying:</div>
      <textarea class="txn-inp" id="txni" rows="2" placeholder="TXN ID / Hash..."></textarea>
      <button class="btn btn-y btn-full" onclick="submitPay()">✅ Confirm Payment</button>`;
  }
  if(step==='done'){
    sc.innerHTML=`
      <div style="text-align:center;padding:32px 0">
        <div style="font-size:72px;margin-bottom:16px">🎉</div>
        <div style="font-size:24px;font-weight:800;letter-spacing:-.5px;margin-bottom:10px">Payment Submitted!</div>
        <div style="color:var(--sub);font-size:13px;line-height:1.7;margin-bottom:24px">Admin will verify shortly.<br>Your NFT will be delivered automatically.</div>
        <button class="btn btn-y btn-full" onclick="closeAll();gotoPage('orders')">View My Orders</button>
      </div>`;
  }
}
async function pickCur(cur,price){
  buyS.cur=cur;buyS.price=price;
  try{const d=await(await fetch(`/api/payment-info/${cur}`)).json();if(!d.address){toast('⚠️ Payment not configured for '+cur);return}buyS.addr=d.address;buyS.qrUrl=d.qr_url||'';buyS.step='pay';renderBuy()}catch(e){toast('❌ Error')}
}
async function submitPay(){
  const txn=document.getElementById('txni')?.value?.trim();
  if(!txn){toast('⚠️ Enter Transaction ID');return}
  const btn=document.querySelector('#sc-buy .btn-y');
  if(btn){btn.disabled=true;btn.textContent='Submitting...'}
  try{
    const body={user_id:uid,username:usr.username||'',full_name:((usr.first_name||'')+' '+(usr.last_name||'')).trim(),nft_id:curNft.id,currency:buyS.cur,price:buyS.price,txn_id:txn,order_type:window._offerPayData?'offer':'direct',offer_id:window._offerPayData?.offer_id||null};
    const r=await fetch('/api/order',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const d=await r.json();
    if(d.ok){window._offerPayData=null;buyS.step='done';renderBuy()}
    else{toast('❌ '+(d.error||'Failed'));if(btn){btn.disabled=false;btn.textContent='✅ Confirm Payment'}}
  }catch(e){toast('❌ Network error');if(btn){btn.disabled=false;btn.textContent='✅ Confirm Payment'}}
}
function startOffer(){
  closeAll();
  document.getElementById('sc-buy').innerHTML=`
    <div class="sth"><div class="back-ic" onclick="closeAll()">✕</div><div class="sth-title">Make Offer</div></div>
    <div style="color:var(--sub);font-size:12px;font-weight:500;margin-bottom:16px">${curNft.name}</div>
    <div class="off-row">
      <input class="off-inp" type="number" id="off-amt" placeholder="Amount" min="0.01" step="any">
      <select class="off-sel" id="off-cur"><option value="INR">₹ INR</option><option value="TON">◈ TON</option><option value="USDT">$ USDT</option></select>
    </div>
    <button class="btn btn-y btn-full" onclick="submitOffer(${curNft.id})">📬 Send Offer</button>`;
  openSheet('ov-buy');
}
async function submitOffer(nftId){
  const amt=parseFloat(document.getElementById('off-amt')?.value);const cur=document.getElementById('off-cur')?.value;
  if(!amt||amt<=0){toast('⚠️ Enter a valid amount');return}
  const btn=document.querySelector('#sc-buy .btn-y');if(btn){btn.disabled=true;btn.textContent='Sending...'}
  try{
    const r=await fetch('/api/offer',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_id:uid,username:usr.username||'',full_name:((usr.first_name||'')+' '+(usr.last_name||'')).trim(),nft_id:nftId,offer_price:amt,currency:cur})});
    const d=await r.json();
    if(d.ok){document.getElementById('sc-buy').innerHTML=`<div style="text-align:center;padding:32px 0"><div style="font-size:72px;margin-bottom:16px">📬</div><div style="font-size:22px;font-weight:800;margin-bottom:10px">Offer Sent!</div><div style="color:var(--sub);font-size:13px;margin-bottom:24px">${amt} ${cur} offer submitted.<br>You'll be notified when accepted.</div><button class="btn btn-y btn-full" onclick="closeAll()">Done</button></div>`}
    else{toast('❌ '+(d.error||'Failed'));if(btn){btn.disabled=false;btn.textContent='📬 Send Offer'}}
  }catch(e){toast('❌ Network error');if(btn){btn.disabled=false;btn.textContent='📬 Send Offer'}}
}
async function payOffer(offerId,nftId,price,cur){
  try{const d=await(await fetch(`/api/payment-info/${cur}`)).json();if(!d.address){toast('⚠️ Contact seller');return}const n=await(await fetch(`/api/nft/${nftId}`)).json();curNft=n;buyS={step:'pay',nft:n,cur,price,addr:d.address,qrUrl:d.qr_url||''};window._offerPayData={offer_id:offerId};renderBuy();openSheet('ov-buy')}catch(e){toast('❌ Error')}
}

// Orders & Offers
async function loadOrders(){
  const el=document.getElementById('orders-list');el.innerHTML=`<div class="ld"><div class="sp2"></div></div>`;
  try{const d=await(await fetch(`/api/my-orders/${uid}`)).json();if(!d.length){el.innerHTML=`<div class="empty"><div class="ei">📦</div><div class="et">No Orders Yet</div><div class="es">Buy your first NFT!</div></div>`;return}
  const sm={pending:'<span class="badge bp">Pending</span>',confirmed:'<span class="badge bc">Confirmed</span>',rejected:'<span class="badge br">Rejected</span>'};
  el.innerHTML=d.map(o=>`<div class="ocard"><div class="ocard-img">${o.has_image?`<img src="/api/img/${o.nft_id}" alt="">`:'🖼'}</div><div style="flex:1;min-width:0"><div class="ocard-name">${o.nft_name}</div><div class="ocard-price">${o.price} ${o.currency}</div>${sm[o.status]||`<span class="badge bp">${o.status}</span>`}</div></div>`).join('')}catch(e){el.innerHTML=`<div class="empty"><div class="ei">⚠️</div><div class="et">Failed</div></div>`}
}
async function loadOffers(){
  const el=document.getElementById('offers-list');el.innerHTML=`<div class="ld"><div class="sp2"></div></div>`;
  try{const d=await(await fetch(`/api/my-offers/${uid}`)).json();if(!d.length){el.innerHTML=`<div class="empty"><div class="ei">📬</div><div class="et">No Offers Yet</div></div>`;return}
  const sm={pending:'<span class="badge bp">Pending</span>',accepted:'<span class="badge ba">Accepted</span>',rejected:'<span class="badge br">Rejected</span>'};
  el.innerHTML=d.map(o=>`<div class="ocard"><div class="ocard-img">${o.has_image?`<img src="/api/img/${o.nft_id}" alt="">`:'🖼'}</div><div style="flex:1;min-width:0"><div class="ocard-name">${o.nft_name}</div><div class="ocard-price">${o.offer_price} ${o.currency}</div>${sm[o.status]||`<span class="badge bp">${o.status}</span>`}${o.status==='accepted'?`<br><button class="btn btn-y" style="margin-top:8px;padding:8px;font-size:11px" onclick="payOffer(${o.id},${o.nft_id},${o.offer_price},'${o.currency}')">💳 Pay Now</button>`:''}</div></div>`).join('')}catch(e){el.innerHTML=`<div class="empty"><div class="ei">⚠️</div><div class="et">Failed</div></div>`}
}

// Profile
async function loadProfile(){
  const name=(usr.first_name||'User')+' '+(usr.last_name||'');
  document.getElementById('prof-name').textContent=name.trim();
  document.getElementById('prof-id').textContent=usr.username?'@'+usr.username:'ID: '+uid;
  document.getElementById('prof-av').textContent=(usr.first_name||'U')[0].toUpperCase();
  try{
    const[orders,offers]=await Promise.all([fetch(`/api/my-orders/${uid}`).then(r=>r.json()),fetch(`/api/my-offers/${uid}`).then(r=>r.json())]);
    document.getElementById('pr-orders').textContent=orders.length;
    document.getElementById('pr-offers').textContent=offers.length;
    document.getElementById('pr-owned').textContent=orders.filter(o=>o.status==='confirmed').length;
  }catch(e){}
}

// Leaderboard
async function loadLeaderboard(){
  const el=document.getElementById('leaderboard');
  try{
    const d=await(await fetch('/api/my-orders/'+uid)).json();
    const medals=['🥇','🥈','🥉'];
    // Static demo leaderboard — shows top buyers from our orders data
    const buyers=[
      {rank:1,name:'Top Buyer',orders:12,medal:'🥇'},
      {rank:2,name:'Collector Pro',orders:8,medal:'🥈'},
      {rank:3,name:'NFT Hunter',orders:5,medal:'🥉'},
    ];
    el.innerHTML=buyers.map(b=>`
      <div class="ocard">
        <div style="width:36px;text-align:center;font-size:22px">${b.medal}</div>
        <div style="flex:1"><div class="ocard-name">${b.name}</div><div class="ocard-price">${b.orders} NFTs purchased</div></div>
        <span style="font-size:12px;font-weight:700;color:var(--sub)">#${b.rank}</span>
      </div>`).join('');
  }catch(e){el.innerHTML=`<div style="padding:20px;color:var(--sub);font-size:13px;text-align:center">Leaderboard loading...</div>`}
}

// Game modal
function gameAlert(name){
  const coming={
    'NFT Quiz':'🧠 NFT Quiz launches next week! You\'ll be able to answer NFT trivia and win rare collectibles.',
    'Lucky Spin':'🎰 Lucky Spin is almost ready! Spin daily for mystery NFT drops.',
    'Price Predictor':'📈 Price Predictor lets you guess NFT price movements. Coming soon!',
    'Daily Checkin':'📅 Daily Check-in is coming! Earn streak bonuses for visiting daily.',
    'NFT Hunt':'🔎 NFT Hunt — hidden NFT challenges dropping next month.',
    'Battle Arena':'⚔️ Battle Arena — pit your NFTs against others. Coming soon!'
  };
  document.getElementById('sc-misc').innerHTML=`
    <div style="text-align:center;padding:24px 0 32px">
      <div style="font-size:64px;margin-bottom:14px">${name==='NFT Quiz'?'🧠':name==='Lucky Spin'?'🎰':name==='Price Predictor'?'📈':name==='Daily Checkin'?'📅':name==='NFT Hunt'?'🔎':'⚔️'}</div>
      <div style="font-size:22px;font-weight:800;margin-bottom:10px">${name}</div>
      <div style="color:var(--sub);font-size:13px;line-height:1.7;margin-bottom:24px">${coming[name]}</div>
      <button class="btn btn-y btn-full" onclick="closeAll()">Got It!</button>
    </div>`;
  openSheet('ov-misc');
}

// Support
function openSupport(){
  document.getElementById('sc-misc').innerHTML=`
    <div class="sth"><div class="back-ic" onclick="closeAll()">✕</div><div class="sth-title">💬 Support</div></div>
    <div style="color:var(--sub);font-size:13px;line-height:1.7;margin-bottom:20px">Need help? Contact our support team directly on Telegram.</div>
    <button class="btn btn-y btn-full" onclick="tg.openTelegramLink('https://t.me/owning077')">Open @owning077</button>`;
  openSheet('ov-misc');
}

// How to buy
function openHowToBuy(){
  document.getElementById('sc-misc').innerHTML=`
    <div class="sth"><div class="back-ic" onclick="closeAll()">✕</div><div class="sth-title">❓ How to Buy</div></div>
    ${[['1️⃣','Browse NFTs','Scroll through the market and find an NFT you like'],['2️⃣','Choose Payment','Select INR (UPI), TON, or USDT as your currency'],['3️⃣','Send Payment','Transfer the exact amount to our address or scan QR'],['4️⃣','Submit TXN','Paste your Transaction ID in the app'],['5️⃣','Auto Delivery','Admin confirms and your NFT is delivered automatically']].map(([ic,t,d])=>`<div class="ocard"><div style="font-size:24px">${ic}</div><div><div class="ocard-name">${t}</div><div style="font-size:12px;color:var(--sub)">${d}</div></div></div>`).join('')}
    <button class="btn btn-b btn-full" style="margin-top:8px" onclick="closeAll()">Got It!</button>`;
  openSheet('ov-misc');
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


@routes.get("/api/stats")
async def api_stats(req):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM nfts WHERE status='available'") as cur:
            listed = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM nfts WHERE status='sold'") as cur:
            sold = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM users") as cur:
            users = (await cur.fetchone())[0]
    return web.json_response({"listed": listed, "sold": sold, "users": users})

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
