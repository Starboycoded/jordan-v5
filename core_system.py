"""
╔══════════════════════════════════════════════════════════════════╗
║         JORDAN BOT v5.0 — SUPABASE EDITION                      ║
║                                                                  ║
║  CHANGES FROM v4:                                                ║
║  [1]  Google Sheets → Supabase (faster, scalable, multi-tenant) ║
║  [2]  Meta Cloud API replaces Green API                         ║
║  [3]  Multi-tenant ready (client_id on all tables)              ║
║  [4]  All session/profile/inventory ops use Supabase REST       ║
║                                                                  ║
║  RENDER ENV VARS:                                                ║
║    SUPABASE_URL        from supabase.com project settings       ║
║    SUPABASE_KEY        service_role key from supabase.com       ║
║    WHATSAPP_TOKEN      Meta permanent system user token         ║
║    PHONE_NUMBER_ID     Meta phone number ID                     ║
║    GROQ_API_KEY        from console.groq.com (free)             ║
║    GEMINI_API_KEY      from aistudio.google.com (cheap)         ║
║    ANTHROPIC_API_KEY   from console.anthropic.com (premium)     ║
║    AI_ENGINE           groq | gemini | claude (default: groq)   ║
║    ADMIN_SECRET        your private dashboard password          ║
║    BOT_PHONE           your WhatsApp number e.g. 2347025...     ║
║    CATALOG_URL         https://your-app.onrender.com/shop/...   ║
║    CLIENT_ID           UUID of this business in clients table   ║
╚══════════════════════════════════════════════════════════════════╝
"""

import os
import time
import uuid
import json
import traceback
import threading
import random
import requests
from urllib.parse import quote
from flask import Flask, request, jsonify
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# ══════════════════════════════════════════════════════
# 1.  CONFIGURATION
# ══════════════════════════════════════════════════════
SUPABASE_URL      = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY      = os.environ.get("SUPABASE_KEY", "")
GROQ_API_KEY      = os.environ.get("GROQ_API_KEY", "")
GEMINI_API_KEY    = os.environ.get("GEMINI_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ADMIN_SECRET      = os.environ.get("ADMIN_SECRET", "techsquad2025")
BOT_PHONE         = os.environ.get("BOT_PHONE", "2347025041149")
CATALOG_URL       = os.environ.get("CATALOG_URL", "https://bot-test-wddr.onrender.com/shop/tech_squad")
WHATSAPP_TOKEN    = os.environ.get("WHATSAPP_TOKEN", "")
PHONE_NUMBER_ID   = os.environ.get("PHONE_NUMBER_ID", "")
CLIENT_ID         = os.environ.get("CLIENT_ID", "")   # UUID from clients table

AI_ENGINE  = os.environ.get("AI_ENGINE", "groq").lower()
GROQ_MODEL = "llama-3.3-70b-versatile"
META_API_URL = f"https://graph.facebook.com/v17.0/{PHONE_NUMBER_ID}/messages"

# Supabase client
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ══════════════════════════════════════════════════════
# 2.  PHONE HELPERS
# ══════════════════════════════════════════════════════
def clean_phone(phone: str) -> str:
    return str(phone).replace("@c.us", "").replace("@g.us", "").replace("@lid", "").strip()


# ══════════════════════════════════════════════════════
# 3.  WHATSAPP SENDER (Meta Cloud API)
# ══════════════════════════════════════════════════════
def send_whatsapp(to: str, message: str):
    phone = clean_phone(to)
    try:
        resp = requests.post(
            META_API_URL,
            headers={
                "Authorization": f"Bearer {WHATSAPP_TOKEN}",
                "Content-Type": "application/json"
            },
            json={
                "messaging_product": "whatsapp",
                "to": phone,
                "type": "text",
                "text": {"body": message}
            },
            timeout=30
        )
        data = resp.json()
        if resp.status_code != 200:
            print(f"[Meta API] Error: {data}")
        return data
    except Exception as e:
        print(f"[Meta API] {e}")
        return None


# ══════════════════════════════════════════════════════
# 4.  IN-MEMORY STORE + CACHES
# ══════════════════════════════════════════════════════
sessions        = {}
inventory_cache = {"data": None, "last_updated": 0}
profile_cache   = {}
token_log       = {}

CACHE_TTL         = 120
PROFILE_CACHE_TTL = 300
HISTORY_LIMIT     = 6
BROADCAST_DELAY   = 3.0
BROADCAST_HOURLY  = 100

CHECKOUT_TRIGGERS = [
    "checkout", "done", "that's all", "thats all", "place order",
    "i'm done", "im done", "finish", "complete", "confirm",
    "proceed", "ready", "order now", "let's go", "lets go"
]
TRACK_TRIGGERS = [
    "track", "track order", "where is my order",
    "order status", "my order", "check order"
]


def get_session(uid: str) -> dict:
    if uid not in sessions:
        sessions[uid] = {
            "history":       [],
            "cart":          {},
            "stage":         "browsing",
            "name":          "",
            "address":       "",
            "saved_address": "",
            "upsell_done":   False,
            "processing":    False,
            "profile":       None,
        }
    return sessions[uid]


# ══════════════════════════════════════════════════════
# 5.  SUPABASE DATABASE FUNCTIONS
# ══════════════════════════════════════════════════════
def get_inventory():
    """Fetch products from Supabase with 2-min cache."""
    now = time.time()
    if inventory_cache["data"] is None or now - inventory_cache["last_updated"] > CACHE_TTL:
        try:
            query = supabase.table("products").select("*").eq("active", True)
            if CLIENT_ID:
                query = query.eq("client_id", CLIENT_ID)
            result = query.execute()
            # Normalise to same format as old Sheets data
            normalised = []
            for p in result.data:
                normalised.append({
                    "Product":       p.get("name", ""),
                    "Price":         p.get("price", 0),
                    "Description":   p.get("description", ""),
                    "Stock":         p.get("stock", 0),
                    "Tags":          p.get("tags", ""),
                    "Raw_Image_URL": p.get("image_url", ""),
                })
            inventory_cache["data"]         = normalised
            inventory_cache["last_updated"] = now
        except Exception as e:
            print(f"[Supabase] Inventory fetch failed: {e}")
            return inventory_cache["data"] or []
    return inventory_cache["data"]


def get_profile(phone: str):
    """Fetch customer profile from Supabase with 5-min cache."""
    now    = time.time()
    cached = profile_cache.get(phone)
    if cached and now - cached["fetched_at"] < PROFILE_CACHE_TTL:
        return cached["data"]
    try:
        query = supabase.table("customers").select("*").eq("phone", phone)
        if CLIENT_ID:
            query = query.eq("client_id", CLIENT_ID)
        result = query.execute()
        profile = result.data[0] if result.data else None
        # Normalise keys to match old code
        if profile:
            profile = {
                "Name":    profile.get("name", ""),
                "Address": profile.get("address", ""),
                "Phone":   profile.get("phone", ""),
            }
        profile_cache[phone] = {"data": profile, "fetched_at": now}
        return profile
    except Exception as e:
        print(f"[Supabase] Profile fetch failed: {e}")
        return cached["data"] if cached else None


def save_profile(phone: str, name: str, address: str):
    """Upsert customer profile to Supabase."""
    try:
        data = {
            "phone":   phone,
            "name":    name,
            "address": address,
            "last_order": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        if CLIENT_ID:
            data["client_id"] = CLIENT_ID
        supabase.table("customers").upsert(
            data, on_conflict="phone,client_id"
        ).execute()
        profile_cache.pop(phone, None)
    except Exception as e:
        print(f"[Supabase] Save profile failed: {e}")


def log_order(order_id, phone, name, items_text, address):
    """Insert order into Supabase orders table."""
    try:
        data = {
            "customer_phone": phone,
            "items":          f"{order_id} | {items_text}",
            "amount":         0,
            "status":         "Pending",
            "created_at":     time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        if CLIENT_ID:
            data["client_id"] = CLIENT_ID
        result = supabase.table("orders").insert(data).execute()
        print(f"[Order saved] {order_id} → {result.data}")
    except Exception as e:
        print(f"[Supabase] Log order failed: {e}")


def get_order_history(phone: str):
    """Fetch all orders for a customer."""
    try:
        query = supabase.table("orders").select("*").eq("customer_phone", phone)
        if CLIENT_ID:
            query = query.eq("client_id", CLIENT_ID)
        result = query.order("created_at", desc=False).execute()
        # Normalise to old format
        orders = []
        for o in result.data:
            orders.append({
                "OrderID": o.get("id", ""),
                "Items":   o.get("items", ""),
                "Status":  o.get("status", ""),
                "Date":    o.get("created_at", ""),
            })
        return orders
    except Exception as e:
        print(f"[Supabase] Order history failed: {e}")
        return []


def save_session_state(phone: str, session: dict):
    """Upsert session state to Supabase so Render restarts don't lose mid-checkout."""
    try:
        cart_json = json.dumps(session.get("cart", {}))
        data = {
            "phone":      phone,
            "stage":      session.get("stage", "browsing"),
            "name":       session.get("name", ""),
            "address":    session.get("address", ""),
            "cart":       cart_json,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        if CLIENT_ID:
            data["client_id"] = CLIENT_ID
        supabase.table("sessions").upsert(
            data, on_conflict="phone,client_id"
        ).execute()
    except Exception as e:
        print(f"[Sessions] Save failed: {e}")


def load_session_state(phone: str) -> dict | None:
    """Load saved session from Supabase after a restart."""
    try:
        query = supabase.table("sessions").select("*").eq("phone", phone)
        if CLIENT_ID:
            query = query.eq("client_id", CLIENT_ID)
        result = query.execute()
        if not result.data:
            return None
        row = result.data[0]
        # Only restore if updated in last 30 mins
        last = row.get("updated_at", "")
        if last:
            try:
                saved_ts = time.mktime(time.strptime(last[:19], "%Y-%m-%dT%H:%M:%S"))
                if time.time() - saved_ts > 1800:
                    return None
            except Exception:
                pass
        cart = {}
        try:
            cart = json.loads(row.get("cart", "{}"))
        except Exception:
            pass
        return {
            "stage":   row.get("stage", "browsing"),
            "name":    row.get("name", ""),
            "address": row.get("address", ""),
            "cart":    cart,
        }
    except Exception as e:
        print(f"[Sessions] Load failed: {e}")
        return None


def clear_session_state(phone: str):
    """Reset session state after order complete."""
    try:
        data = {
            "phone":      phone,
            "stage":      "browsing",
            "name":       "",
            "address":    "",
            "cart":       "{}",
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        if CLIENT_ID:
            data["client_id"] = CLIENT_ID
        supabase.table("sessions").upsert(
            data, on_conflict="phone,client_id"
        ).execute()
    except Exception as e:
        print(f"[Sessions] Clear failed: {e}")


def get_all_customers():
    """Fetch all customers for broadcast."""
    try:
        query = supabase.table("customers").select("phone")
        if CLIENT_ID:
            query = query.eq("client_id", CLIENT_ID)
        result = query.execute()
        return result.data or []
    except Exception as e:
        print(f"[Supabase] Get customers failed: {e}")
        return []


def get_all_orders():
    """Fetch all orders for admin dashboard."""
    try:
        query = supabase.table("orders").select("*")
        if CLIENT_ID:
            query = query.eq("client_id", CLIENT_ID)
        result = query.order("created_at", desc=True).limit(100).execute()
        return result.data or []
    except Exception as e:
        print(f"[Supabase] Get orders failed: {e}")
        return []


# ══════════════════════════════════════════════════════
# 6.  CART HELPERS
# ══════════════════════════════════════════════════════
def price_map(inventory: list) -> dict:
    return {p.get("Product"): int(p.get("Price", 0)) for p in inventory}


def cart_display(cart: dict, inventory: list) -> str:
    if not cart:
        return "  (empty)"
    pm    = price_map(inventory)
    lines = []
    total = 0
    for item, qty in cart.items():
        sub    = pm.get(item, 0) * qty
        total += sub
        lines.append(f"  {qty}x {item} — NGN {sub:,}")
    lines.append(f"  {'─' * 28}")
    lines.append(f"  *Total: NGN {total:,}*")
    return "\n".join(lines)


def cart_log_text(cart: dict, inventory: list) -> str:
    pm    = price_map(inventory)
    parts = []
    total = 0
    for item, qty in cart.items():
        sub    = pm.get(item, 0) * qty
        total += sub
        parts.append(f"{qty}x {item}")
    return ", ".join(parts) + f" | Total: NGN {total:,}"


def find_upsell(cart: dict, inventory: list):
    cart_tags = set()
    for item in cart:
        for p in inventory:
            if p.get("Product") == item:
                for tag in str(p.get("Tags", "")).lower().split(","):
                    cart_tags.add(tag.strip())
    for p in inventory:
        name = p.get("Product", "")
        try:
            stock = int(p.get("Stock", 0))
        except Exception:
            stock = 0
        if name in cart or stock == 0:
            continue
        tags = [t.strip() for t in str(p.get("Tags", "")).lower().split(",")]
        if any(t in cart_tags for t in tags if t):
            return name
    return None


# ══════════════════════════════════════════════════════
# 7.  TOKEN LOGGER
# ══════════════════════════════════════════════════════
def log_tokens(count: int):
    today = time.strftime("%Y-%m-%d")
    token_log[today] = token_log.get(today, 0) + count
    if token_log[today] % 10000 < count:
        print(f"[Tokens] {today}: {token_log[today]:,} tokens used today")


# ══════════════════════════════════════════════════════
# 8.  AI ENGINE
# ══════════════════════════════════════════════════════
def ask_ai(system_prompt: str, history: list) -> str:
    engine = AI_ENGINE

    if engine == "groq":
        if not GROQ_API_KEY:
            return "GROQ_API_KEY not set."
        try:
            resp = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {GROQ_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": GROQ_MODEL,
                    "messages": [{"role": "system", "content": system_prompt}] + history,
                    "max_tokens": 350,
                    "temperature": 0.6,
                },
                timeout=30,
            )
            data   = resp.json()
            reply  = data["choices"][0]["message"]["content"]
            tokens = data.get("usage", {}).get("total_tokens", 0)
            log_tokens(tokens)
            return reply
        except Exception as e:
            print(f"[Groq] {e}")
            return "One moment, please try again."

    if engine == "gemini":
        if not GEMINI_API_KEY:
            return "GEMINI_API_KEY not set."
        try:
            contents = []
            for msg in history:
                role = "user" if msg["role"] == "user" else "model"
                contents.append({"role": role, "parts": [{"text": msg["content"]}]})
            resp = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}",
                json={
                    "system_instruction": {"parts": [{"text": system_prompt}]},
                    "contents": contents,
                    "generationConfig": {"maxOutputTokens": 350, "temperature": 0.6},
                },
                timeout=30,
            )
            data  = resp.json()
            reply = data["candidates"][0]["content"]["parts"][0]["text"]
            log_tokens(len(system_prompt.split()) + len(reply.split()))
            return reply
        except Exception as e:
            print(f"[Gemini] {e}")
            return "One moment, please try again."

    if engine == "claude":
        if not ANTHROPIC_API_KEY:
            return "ANTHROPIC_API_KEY not set."
        try:
            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 350,
                    "system": system_prompt,
                    "messages": history,
                },
                timeout=30,
            )
            data   = resp.json()
            reply  = data["content"][0]["text"]
            tokens = data.get("usage", {}).get("input_tokens", 0) + \
                     data.get("usage", {}).get("output_tokens", 0)
            log_tokens(tokens)
            return reply
        except Exception as e:
            print(f"[Claude] {e}")
            return "One moment, please try again."

    return "AI_ENGINE not configured correctly."


# ══════════════════════════════════════════════════════
# 9.  SYSTEM PROMPT
# ══════════════════════════════════════════════════════
def build_prompt(inventory, profile, cart) -> str:
    available = []
    for p in inventory:
        try:
            stock = int(p.get("Stock", 0))
        except Exception:
            stock = 0
        if stock > 0:
            available.append(
                f"{p.get('Product')}|NGN {int(p.get('Price',0)):,}"
                f"|{p.get('Description','')}|{p.get('Tags','')}"
            )
    inv_text = "\n".join(available) or "No items in stock."

    profile_text = (
        f"Returning: {profile.get('Name')}, saved address: {profile.get('Address')}"
        if profile else "New customer."
    )

    cart_text = cart_display(cart, inventory) if cart else "Empty"

    return f"""You are Jordan, WhatsApp sales assistant for The Tech Squad (Nigeria).
Be warm, human, brief. Never robotic.

INVENTORY: {inv_text}
CUSTOMER: {profile_text}
CART: {cart_text}
CATALOG: {CATALOG_URL}

RULES:
- On first message: greet + share catalog link
- Add items customer mentions to cart, show updated cart after
- Cart format: list items + total, end with "Reply checkout when ready!"
- Upsell once only: suggest complementary item after first add
- On checkout/done/ready: say "Perfect! Getting your details..."
- On track order: system handles it, respond naturally
- Never change prices, never invent products
- Keep replies under 4 sentences unless showing cart
- Emojis ok, never say "As an AI"
"""


# ══════════════════════════════════════════════════════
# 10. CHECKOUT STATE MACHINE
# ══════════════════════════════════════════════════════
def handle_checkout_state(uid: str, text: str, session: dict):
    stage = session.get("stage", "browsing")

    if stage == "awaiting_name":
        session["name"]  = text.strip().title()
        session["stage"] = "awaiting_address"
        save_session_state(uid, session)
        return (
            f"Nice to meet you, {session['name']}! 😊\n"
            f"What's your delivery address? (street, area, city)"
        )

    if stage == "awaiting_address":
        session["address"] = text.strip()
        save_session_state(uid, session)
        return _generate_receipt(uid, session)

    if stage == "awaiting_address_confirm":
        yes = {"yes","yeah","yep","y","correct","ok","okay","sure","yh","confirm","use it"}
        text_lower_stripped = text.strip().lower()
        if any(text_lower_stripped.startswith(w) for w in yes):
            session["address"] = session["saved_address"]
        else:
            session["address"] = text.strip()
        return _generate_receipt(uid, session)

    return None


def _generate_receipt(uid: str, session: dict) -> str:
    inventory = get_inventory()
    order_id  = f"TS-{uuid.uuid4().hex[:6].upper()}"
    name      = session["name"]
    address   = session["address"]
    cart      = session.get("cart", {})
    pm        = price_map(inventory)

    lines = []
    total = 0
    for item, qty in cart.items():
        sub    = pm.get(item, 0) * qty
        total += sub
        lines.append(f"  {qty}x {item} — NGN {sub:,}")

    receipt = (
        f"ORDER CONFIRMED! 🎉\n\n"
        f"Order ID: *{order_id}*\n"
        f"{'─' * 30}\n"
        f"{chr(10).join(lines)}\n"
        f"{'─' * 30}\n"
        f"*Total: NGN {total:,}*\n\n"
        f"👤 {name}\n"
        f"📍 {address}\n"
        f"💳 Cash on Delivery\n"
        f"🚚 ETA: 2–3 business days\n\n"
        f"Thank you! 🙏 We'll call to confirm delivery.\n"
        f"Save your Order ID: *{order_id}*"
    )

    log_order(order_id, uid, name, cart_log_text(cart, inventory), address)
    save_profile(uid, name, address)
    clear_session_state(uid)
    print(f"[ORDER] {order_id} → {uid}")

    session.update({
        "cart": {}, "stage": "browsing", "history": [],
        "name": "", "address": "", "upsell_done": False,
    })
    return receipt


# ══════════════════════════════════════════════════════
# 11. MAIN CONVERSATION PROCESSOR
# ══════════════════════════════════════════════════════
def process_conversation(uid: str, text: str):
    session = get_session(uid)

    waited = 0
    while session.get("processing") and waited < 5:
        time.sleep(1)
        waited += 1
    session["processing"] = True

    try:
        inventory  = get_inventory()
        text_lower = text.lower().strip()

        # STEP 1: Restore session if Render restarted
        if session.get("stage") == "browsing" and not session.get("cart"):
            saved = load_session_state(uid)
            if saved and saved.get("stage") != "browsing":
                session.update(saved)
                print(f"[Session] Restored {uid}: {saved['stage']}")

        # STEP 2: Checkout state machine
        state_reply = handle_checkout_state(uid, text, session)
        if state_reply:
            time.sleep(random.uniform(1.5, 3.5))
            send_whatsapp(uid, state_reply)
            return

        # STEP 3: Parse cart items
        import re as _re
        added_items = []

        sf_matches = _re.findall(r"(\d+)x\s+(.+?)\s+-\s+NGN", text)
        if sf_matches:
            for qty_str, item_name in sf_matches:
                item_name = item_name.strip()
                qty = int(qty_str)
                for p in inventory:
                    pname = p.get("Product", "")
                    try:
                        stock = int(p.get("Stock", 0))
                    except Exception:
                        stock = 0
                    if stock > 0 and (
                        pname.lower() == item_name.lower() or
                        pname.lower() in item_name.lower() or
                        item_name.lower() in pname.lower()
                    ):
                        session["cart"][pname] = session["cart"].get(pname, 0) + qty
                        added_items.append((pname, qty))
                        break
        else:
            for p in inventory:
                pname = p.get("Product", "")
                try:
                    stock = int(p.get("Stock", 0))
                except Exception:
                    stock = 0
                if stock > 0 and pname.lower() in text_lower:
                    qty = 1
                    words = text_lower.replace("x", " ").split()
                    for word in words:
                        if word.isdigit():
                            qty = int(word)
                            break
                    session["cart"][pname] = session["cart"].get(pname, 0) + qty
                    added_items.append((pname, qty))

        if added_items:
            save_session_state(uid, session)

        # STEP 4: Checkout trigger
        is_order_msg = (
            "i would like to order" in text_lower or
            "please confirm my order" in text_lower
        )
        if any(t in text_lower for t in CHECKOUT_TRIGGERS) or is_order_msg:
            cart = session.get("cart", {})
            if not cart:
                msg = "Your cart is empty! Browse here: " + CATALOG_URL
                time.sleep(random.uniform(1.5, 3.5))
                send_whatsapp(uid, msg)
                return
            profile = get_profile(uid)
            if profile:
                session["stage"]         = "awaiting_address_confirm"
                session["name"]          = profile.get("Name", "")
                session["saved_address"] = profile.get("Address", "")
                saved_addr = profile.get("Address", "")
                reply = "Here's your cart:\n" + cart_display(cart, inventory)
                reply += "\n\nDeliver to saved address?\n" + saved_addr
                reply += "\n\nReply YES to confirm or send a new address."
            else:
                session["stage"] = "awaiting_name"
                reply = "Here's your cart:\n" + cart_display(cart, inventory)
                reply += "\n\nWhat's your full name for delivery?"
            save_session_state(uid, session)
            time.sleep(random.uniform(1.5, 3.5))
            send_whatsapp(uid, reply)
            return

        # STEP 5: Order tracking
        if any(t in text_lower for t in TRACK_TRIGGERS):
            order_hist = get_order_history(uid)
            if order_hist:
                last = order_hist[-1]
                reply = (
                    "Latest order\n\n"
                    "ID: " + str(last.get("OrderID", "")) + "\n"
                    "Items: " + str(last.get("Items", "")) + "\n"
                    "Status: " + str(last.get("Status", "")) + "\n"
                    "Date: " + str(last.get("Date", ""))
                )
            else:
                reply = "No orders found for your number yet."
            time.sleep(random.uniform(1.5, 3.5))
            send_whatsapp(uid, reply)
            return

        # STEP 6: AI reply
        if session.get("profile") is None:
            session["profile"] = get_profile(uid)
        profile = session["profile"]

        system_prompt = build_prompt(inventory, profile, session["cart"])
        session["history"].append({"role": "user", "content": text})
        session["history"] = session["history"][-HISTORY_LIMIT:]

        reply = ask_ai(system_prompt, session["history"])
        session["history"].append({"role": "assistant", "content": reply})

        # STEP 7: Upsell once per order
        if added_items and not session.get("upsell_done"):
            suggestion = find_upsell(session["cart"], inventory)
            if suggestion:
                pm_ = price_map(inventory)
                uprice = pm_.get(suggestion, 0)
                reply += "\n\nCustomers who get " + added_items[0][0]
                reply += " usually grab " + suggestion + " too (NGN " + f"{uprice:,}" + "). Add it?"
                session["upsell_done"] = True

        time.sleep(random.uniform(1.5, 3.5))
        send_whatsapp(uid, reply)

    except Exception as e:
        print(f"[Error] {uid}: {e}")
        traceback.print_exc()
        try:
            time.sleep(random.uniform(1.5, 3.5))
            send_whatsapp(uid, "Something went wrong. Please try again.")
        except Exception:
            pass
    finally:
        session["processing"] = False


# ══════════════════════════════════════════════════════
# 12. WEBHOOK
# ══════════════════════════════════════════════════════
@app.route("/webhook", methods=["GET"])
def webhook_verify():
    mode      = request.args.get("hub.mode")
    token     = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == ADMIN_SECRET:
        print("[Webhook] Verified by Meta")
        return challenge, 200
    return "Forbidden", 403


@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    if not data:
        return "OK", 200
    try:
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                value    = change.get("value", {})
                messages = value.get("messages", [])
                for msg in messages:
                    uid      = clean_phone(msg.get("from", ""))
                    msg_type = msg.get("type", "")

                    if not uid:
                        continue

                    if msg_type in ("image","video","audio","document","sticker"):
                        media_reply = (
                            "Hey! 👋 I cannot view images or screenshots, but no worries! "
                            "Browse our full catalog here and tap any item to order: "
                            + CATALOG_URL
                            + " Or just type what you are looking for and I will help! 😊"
                        )
                        time.sleep(random.uniform(1.5, 3.5))
                        send_whatsapp(uid, media_reply)
                        continue

                    if msg_type == "text":
                        text = msg.get("text", {}).get("body", "").strip()
                        if not text:
                            continue
                        print(f"[Webhook] {uid}: {text[:50]}")
                        t = threading.Thread(
                            target=process_conversation, args=(uid, text)
                        )
                        t.daemon = True
                        t.start()

    except Exception as e:
        print(f"[Webhook] {e}")
        traceback.print_exc()

    return "OK", 200


# ══════════════════════════════════════════════════════
# 13. STOREFRONT
# ══════════════════════════════════════════════════════
@app.route("/shop/<vendor_name>")
def shop(vendor_name):
    try:
        products     = get_inventory()
        vendor_title = vendor_name.replace("_", " ").title()

        import json as _json

        product_list = []
        cards        = ""

        for i, p in enumerate(products):
            name  = p.get("Product", "")
            price = p.get("Price", 0)
            desc  = p.get("Description", "")
            img   = p.get("Raw_Image_URL", "")
            try:
                stock = int(p.get("Stock", 0))
            except Exception:
                stock = 0

            img_tag = (
                f'<img src="{img}" alt="{name}" loading="lazy">'
                if img else
                f'<div class="no-img">{name[:2].upper()}</div>'
            )

            if stock > 0:
                product_list.append({"id": i, "name": name, "price": int(price)})
                btn = f'<button class="btn-add" onclick="addItem({i})">Add to Cart</button>'
            else:
                btn = '<span class="btn-soldout">Sold Out</span>'

            cards += (
                f'<div class="card" id="c{i}">'
                f'{img_tag}'
                f'<div class="info">'
                f'<div class="pname">{name}</div>'
                f'<div class="pdesc">{desc}</div>'
                f'<div class="pfoot">'
                f'<span class="pprice">NGN {int(price):,}</span>'
                f'{btn}'
                f'</div></div></div>'
            )

        pdata = _json.dumps(product_list)

        CSS = """
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#0a0a0a;--card:#141414;--border:#252525;--green:#25D366;--gg:rgba(37,211,102,.15);--text:#f0f0f0;--muted:#777;--red:#ef4444}
body{font-family:'Sora',sans-serif;background:var(--bg);color:var(--text);min-height:100vh;padding-bottom:80px}
.hdr{background:var(--card);border-bottom:1px solid var(--border);padding:14px 20px;text-align:center;position:sticky;top:0;z-index:100;backdrop-filter:blur(10px)}
.hdr h1{font-size:18px;font-weight:700}
.hdr p{color:var(--muted);font-size:10px;margin-top:2px;letter-spacing:2px;text-transform:uppercase}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px;max-width:1100px;margin:18px auto;padding:0 14px}
.card{background:var(--card);border:2px solid var(--border);border-radius:14px;overflow:hidden;display:flex;flex-direction:column;transition:border-color .2s,transform .2s}
.card:hover{transform:translateY(-2px)}
.card.inc{border-color:var(--green)}
.card img,.no-img{width:100%;height:200px;object-fit:cover;display:block;background:#1c1c1c}
.no-img{display:flex;align-items:center;justify-content:center;font-size:44px;font-weight:700;color:#2a2a2a}
.info{padding:14px;flex:1;display:flex;flex-direction:column}
.pname{font-size:14px;font-weight:700;margin-bottom:5px;line-height:1.3}
.pdesc{font-size:12px;color:var(--muted);line-height:1.6;flex:1;margin-bottom:12px}
.pfoot{display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap}
.pprice{font-size:16px;font-weight:700;color:var(--green)}
.btn-add{background:var(--green);color:#000;border:none;font-family:'Sora',sans-serif;font-weight:700;font-size:12px;padding:9px 14px;border-radius:8px;cursor:pointer;transition:all .15s;white-space:nowrap}
.btn-add:hover{opacity:.85}
.btn-add.flash{background:var(--gg);color:var(--green);border:1px solid var(--green)}
.btn-soldout{background:#1a1a1a;color:var(--muted);border:1px solid var(--border);font-size:12px;font-weight:600;padding:9px 14px;border-radius:8px;cursor:not-allowed}
.ftr{text-align:center;padding:20px;color:var(--muted);font-size:11px;border-top:1px solid var(--border);margin-top:10px}
.ftr a{color:var(--green);text-decoration:none}
#toast{position:fixed;top:74px;left:50%;transform:translateX(-50%) translateY(-8px);background:#0d2211;border:1px solid var(--green);color:var(--green);font-size:13px;font-weight:600;padding:9px 18px;border-radius:30px;opacity:0;pointer-events:none;z-index:9999;transition:all .25s;white-space:nowrap}
#toast.on{opacity:1;transform:translateX(-50%) translateY(0)}
#cartBtn{position:fixed;bottom:0;left:0;right:0;background:var(--green);color:#000;border:none;font-family:'Sora',sans-serif;font-weight:700;font-size:15px;padding:16px 20px;cursor:pointer;z-index:500;display:none;align-items:center;justify-content:center;gap:10px;transition:opacity .15s}
#cartBtn:hover{opacity:.9}
#cartBtn.on{display:flex}
.cbadge{background:#000;color:var(--green);font-size:12px;font-weight:700;padding:2px 10px;border-radius:20px}
#ov{position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:600;opacity:0;pointer-events:none;transition:opacity .25s}
#ov.on{opacity:1;pointer-events:all}
#panel{position:fixed;bottom:0;left:0;right:0;background:#0d1a10;border-top:2px solid var(--green);border-radius:20px 20px 0 0;z-index:700;max-height:85vh;display:flex;flex-direction:column;transform:translateY(100%);transition:transform .3s cubic-bezier(.4,0,.2,1)}
#panel.on{transform:translateY(0)}
@media(min-width:768px){
  #cartBtn{bottom:24px;left:auto;right:24px;border-radius:50px;padding:14px 24px;box-shadow:0 4px 20px rgba(37,211,102,.4)}
  #panel{right:0;left:auto;top:0;bottom:0;width:400px;max-height:100vh;border-radius:0;border-top:none;border-left:2px solid var(--green);transform:translateX(100%)}
  #panel.on{transform:translateX(0)}
}
.ph{display:flex;align-items:center;justify-content:space-between;padding:16px 20px;border-bottom:1px solid #1a3020;flex-shrink:0}
.pt{font-size:16px;font-weight:700;color:var(--green)}
.px{background:none;border:none;color:var(--muted);font-size:24px;cursor:pointer;padding:0 4px;line-height:1}
.px:hover{color:var(--text)}
.pb{flex:1;overflow-y:auto;padding:12px 20px}
.pe{color:var(--muted);font-size:13px;text-align:center;padding:30px 0}
.row{display:grid;grid-template-columns:1fr auto auto auto;align-items:center;gap:10px;padding:12px 0;border-bottom:1px solid #1a3020}
.rn{font-size:13px;font-weight:600}
.rp{font-size:12px;color:var(--green);font-weight:700;text-align:right;min-width:80px}
.qw{display:flex;align-items:center;gap:5px}
.qb{background:#1a3a24;border:1px solid #2d5a3a;color:var(--green);width:28px;height:28px;border-radius:6px;font-size:16px;font-weight:700;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:background .15s;flex-shrink:0}
.qb:hover{background:#225530}
.qn{font-size:14px;font-weight:700;min-width:20px;text-align:center}
.rd{background:none;border:none;color:#444;cursor:pointer;font-size:16px;padding:4px;line-height:1;transition:color .15s}
.rd:hover{color:var(--red)}
.pf{padding:16px 20px;border-top:1px solid #1a3020;flex-shrink:0}
.tr{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px}
.tl{font-size:14px;color:var(--muted)}
.tv{font-size:22px;font-weight:700;color:var(--green)}
.bw{width:100%;background:var(--green);color:#000;border:none;font-family:'Sora',sans-serif;font-weight:700;font-size:15px;padding:15px;border-radius:12px;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:8px;margin-bottom:10px;transition:opacity .15s}
.bw:hover{opacity:.85}
.bc{width:100%;background:none;border:1px solid #2a2a2a;color:var(--muted);font-family:'Sora',sans-serif;font-size:12px;padding:10px;border-radius:8px;cursor:pointer;transition:all .15s}
.bc:hover{border-color:var(--red);color:var(--red)}
"""

        JS = """
var P=""" + pdata + """;
var PHONE=\"""" + BOT_PHONE + """\";
var cart={};
var tt;

function addItem(id){
  cart[id]=(cart[id]||0)+1;
  draw();
  show_toast(P[id].name+' added! Tap cart to view 🛒');
  var c=document.getElementById('c'+id);
  if(c)c.classList.add('inc');
  var b=c?c.querySelector('.btn-add'):null;
  if(b){b.textContent='Added ✓';b.classList.add('flash');setTimeout(function(){b.textContent='Add to Cart';b.classList.remove('flash');},1400);}
}
function inc(id){cart[id]=(cart[id]||0)+1;draw();}
function dec(id){
  cart[id]=(cart[id]||1)-1;
  if(cart[id]<=0){delete cart[id];var c=document.getElementById('c'+id);if(c)c.classList.remove('inc');}
  draw();
  if(!Object.keys(cart).length)close_panel();
}
function del(id){
  delete cart[id];
  var c=document.getElementById('c'+id);if(c)c.classList.remove('inc');
  draw();
  if(!Object.keys(cart).length)close_panel();
}
function clear_all(){
  cart={};
  document.querySelectorAll('.card').forEach(function(c){c.classList.remove('inc');});
  draw();close_panel();
}
function draw(){
  var ids=Object.keys(cart);
  var total=0,count=0,html='';
  ids.forEach(function(id){
    var item=P[id],qty=cart[id],sub=item.price*qty;
    total+=sub;count+=qty;
    html+='<div class="row">'
      +'<span class="rn">'+item.name+'</span>'
      +'<div class="qw">'
      +'<button class="qb" onclick="dec('+id+')">&#8722;</button>'
      +'<span class="qn">'+qty+'</span>'
      +'<button class="qb" onclick="inc('+id+')">&#43;</button>'
      +'</div>'
      +'<span class="rp">NGN '+sub.toLocaleString()+'</span>'
      +'<button class="rd" onclick="del('+id+')" title="Remove">&#x2715;</button>'
      +'</div>';
  });
  document.getElementById('pb').innerHTML=html||'<p class="pe">Your cart is empty</p>';
  document.getElementById('tv').textContent='NGN '+total.toLocaleString();
  document.getElementById('cnt').textContent=count+(count===1?' item':' items');
  var btn=document.getElementById('cartBtn');
  if(count>0)btn.classList.add('on');else btn.classList.remove('on');
}
function open_panel(){document.getElementById('panel').classList.add('on');document.getElementById('ov').classList.add('on');}
function close_panel(){document.getElementById('panel').classList.remove('on');document.getElementById('ov').classList.remove('on');}
function show_toast(m){
  var el=document.getElementById('toast');
  el.textContent=m;el.classList.add('on');
  clearTimeout(tt);tt=setTimeout(function(){el.classList.remove('on');},2200);
}
function send_order(){
  var ids=Object.keys(cart);
  if(!ids.length){alert('Your cart is empty!');return;}
  var total=0,msg='Hi Jordan! I would like to order:\\n\\n';
  ids.forEach(function(id){
    var item=P[id],qty=cart[id],sub=item.price*qty;
    total+=sub;
    msg+=qty+'x '+item.name+' - NGN '+sub.toLocaleString()+'\\n';
  });
  msg+='\\nTotal: NGN '+total.toLocaleString();
  msg+='\\n\\nPlease confirm my order!';
  window.open('https://wa.me/'+PHONE+'?text='+encodeURIComponent(msg),'_blank');
}
"""

        return (
            "<!DOCTYPE html><html lang='en'><head>"
            "<meta charset='UTF-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1.0,maximum-scale=1.0'>"
            f"<title>{vendor_title}</title>"
            "<link href='https://fonts.googleapis.com/css2?family=Sora:wght@400;500;600;700&display=swap' rel='stylesheet'>"
            f"<style>{CSS}</style>"
            "</head><body>"
            f"<header class='hdr'><h1>{vendor_title}</h1><p>Powered by CodedLabs</p></header>"
            "<div id='toast'></div>"
            f"<div class='grid'>{cards}</div>"
            f"<footer class='ftr'>Powered by CodedLabs &middot; <a href='https://wa.me/{BOT_PHONE}'>Chat with Jordan</a></footer>"
            "<button id='cartBtn' onclick='open_panel()'>"
            "&#x1F6D2; View Cart <span class='cbadge' id='cnt'>0 items</span>"
            "</button>"
            "<div id='ov' onclick='close_panel()'></div>"
            "<div id='panel'>"
            "<div class='ph'><span class='pt'>&#x1F6D2; Your Cart</span><button class='px' onclick='close_panel()'>&#x2715;</button></div>"
            "<div class='pb' id='pb'><p class='pe'>Your cart is empty</p></div>"
            "<div class='pf'>"
            "<div class='tr'><span class='tl'>Order Total</span><span class='tv' id='tv'>NGN 0</span></div>"
            "<button class='bw' onclick='send_order()'>"
            "<svg width='18' height='18' viewBox='0 0 24 24' fill='currentColor'>"
            "<path d='M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347z'/>"
            "<path d='M12 0C5.373 0 0 5.373 0 12c0 2.127.558 4.126 1.533 5.857L.057 23.743a.75.75 0 00.914.914l5.886-1.476A11.945 11.945 0 0012 24c6.627 0 12-5.373 12-12S18.627 0 12 0zm0 22c-1.907 0-3.698-.528-5.228-1.443l-.374-.222-3.893.976.992-3.786-.245-.389A9.96 9.96 0 012 12C2 6.477 6.477 2 12 2s10 4.477 10 10-4.477 10-10 10z'/>"
            "</svg>Send Order to WhatsApp</button>"
            "<button class='bc' onclick='clear_all()'>Clear entire cart</button>"
            "</div></div>"
            f"<script>{JS}</script>"
            "</body></html>"
        )

    except Exception as e:
        print(f"[Shop] {e}")
        return "Storefront is updating.", 500


# ══════════════════════════════════════════════════════
# 14. BROADCAST
# ══════════════════════════════════════════════════════
@app.route("/broadcast", methods=["POST"])
def broadcast():
    body = request.json or {}
    if body.get("secret") != ADMIN_SECRET:
        return jsonify({"error": "Unauthorized"}), 403
    msg = body.get("message", "").strip()
    if not msg:
        return jsonify({"error": "message required"}), 400

    customers    = get_all_customers()
    sent, failed = 0, 0
    hourly_count = 0

    for c in customers:
        if hourly_count >= BROADCAST_HOURLY:
            print(f"[Broadcast] Hourly limit reached. {sent} sent, stopping.")
            break
        phone = str(c.get("phone", "")).strip()
        if not phone:
            continue
        try:
            send_whatsapp(phone, msg)
            sent         += 1
            hourly_count += 1
            time.sleep(BROADCAST_DELAY)
        except Exception as e:
            print(f"[Broadcast] {phone}: {e}")
            failed += 1

    return jsonify({"sent": sent, "failed": failed, "total": len(customers)})


# ══════════════════════════════════════════════════════
# 15. ADMIN DASHBOARD
# ══════════════════════════════════════════════════════
@app.route("/admin")
def admin():
    if request.args.get("secret") != ADMIN_SECRET:
        return "<h2 style='font-family:sans-serif;color:red;padding:40px'>Unauthorized</h2>", 403

    customers  = get_all_customers()
    sales      = get_all_orders()

    total_orders    = len(sales)
    pending         = sum(1 for s in sales if str(s.get("status","")).lower() == "pending")
    delivered       = sum(1 for s in sales if str(s.get("status","")).lower() == "delivered")
    total_customers = len(customers)
    today           = time.strftime("%Y-%m-%d")
    tokens_today    = token_log.get(today, 0)

    sales_rows = ""
    for s in sales[:100]:
        status = str(s.get("status","Pending"))
        color  = "#22c55e" if status.lower()=="delivered" else "#f59e0b" if status.lower()=="pending" else "#3b82f6"
        sales_rows += f"""<tr>
          <td class="mono">{s.get('id','—')[:12]}...</td>
          <td>{s.get('customer_phone','—')}</td>
          <td class="sm muted">{s.get('items','—')}</td>
          <td class="green">NGN {int(s.get('amount',0)):,}</td>
          <td><span class="badge" style="background:{color}22;color:{color}">{status}</span></td>
          <td class="sm muted">{str(s.get('created_at','—'))[:16]}</td>
          <td>
            <button class="act-btn" onclick="markStatus('{s.get('id','')}','Delivered')">✓ Delivered</button>
          </td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CodedLabs Commerce OS</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;700&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
:root{{--bg:#07070e;--s:#10101a;--b:#1c1c2a;--g:#25D366;--text:#dde;--m:#666;--red:#ef4444}}
body{{font-family:'DM Sans',sans-serif;background:var(--bg);color:var(--text);min-height:100vh}}
header{{background:var(--s);border-bottom:1px solid var(--b);padding:14px 28px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:100}}
header h1{{font-size:15px;font-weight:700;letter-spacing:-.3px}}
.tag{{font-size:10px;background:rgba(37,211,102,.15);color:var(--g);padding:3px 10px;border-radius:20px;font-weight:600}}
/* TABS */
.tabs{{display:flex;gap:4px;padding:16px 24px 0;border-bottom:1px solid var(--b);background:var(--s);position:sticky;top:49px;z-index:99}}
.tab{{padding:9px 18px;font-size:13px;font-weight:600;color:var(--m);border:none;background:none;cursor:pointer;border-bottom:2px solid transparent;transition:all .2s}}
.tab.on{{color:var(--g);border-bottom-color:var(--g)}}
.tab:hover{{color:var(--text)}}
/* PANELS */
.panel{{display:none;max-width:1200px;margin:0 auto;padding:22px 24px 60px}}
.panel.on{{display:block}}
/* STATS */
.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:12px;margin-bottom:24px}}
.stat{{background:var(--s);border:1px solid var(--b);border-radius:14px;padding:18px}}
.stat-n{{font-size:24px;font-weight:700;margin-bottom:2px}}
.stat-l{{font-size:10px;color:var(--m);text-transform:uppercase;letter-spacing:.8px}}
/* CARDS */
.card{{background:var(--s);border:1px solid var(--b);border-radius:14px;overflow:hidden;margin-bottom:22px}}
.card-head{{padding:12px 18px;border-bottom:1px solid var(--b);font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--m);display:flex;align-items:center;justify-content:space-between}}
/* TABLE */
.tbl-wrap{{overflow-x:auto}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
th{{padding:10px 14px;text-align:left;font-size:10px;text-transform:uppercase;letter-spacing:.7px;color:var(--m);font-weight:600;border-bottom:1px solid var(--b)}}
td{{padding:10px 14px;border-top:1px solid var(--b);vertical-align:middle}}
tr:hover td{{background:rgba(255,255,255,.015)}}
.mono{{font-family:monospace;font-size:11px}} .muted{{color:var(--m)}} .sm{{font-size:11px}}
.green{{color:var(--g);font-weight:600}}
.badge{{padding:3px 10px;border-radius:20px;font-size:10px;font-weight:700}}
.act-btn{{background:rgba(37,211,102,.1);color:var(--g);border:1px solid rgba(37,211,102,.3);padding:4px 10px;border-radius:6px;font-size:11px;font-weight:600;cursor:pointer;transition:all .15s}}
.act-btn:hover{{background:rgba(37,211,102,.2)}}
.del-btn{{background:rgba(239,68,68,.1);color:var(--red);border:1px solid rgba(239,68,68,.3);padding:4px 10px;border-radius:6px;font-size:11px;font-weight:600;cursor:pointer;transition:all .15s}}
.del-btn:hover{{background:rgba(239,68,68,.2)}}
/* FORM */
.form-grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px;padding:20px}}
@media(max-width:600px){{.form-grid{{grid-template-columns:1fr}}}}
.form-group{{display:flex;flex-direction:column;gap:6px}}
.form-group.full{{grid-column:1/-1}}
label{{font-size:11px;font-weight:600;color:var(--m);text-transform:uppercase;letter-spacing:.7px}}
input,textarea,select{{background:#0b0b15;border:1px solid var(--b);border-radius:8px;color:var(--text);padding:10px 12px;font-family:inherit;font-size:13px;outline:none;transition:border-color .2s;width:100%}}
input:focus,textarea:focus{{border-color:var(--g)}}
textarea{{resize:vertical;min-height:70px}}
.btn{{background:var(--g);color:#000;border:none;padding:10px 22px;border-radius:8px;font-weight:700;font-size:13px;cursor:pointer;transition:opacity .15s}}
.btn:hover{{opacity:.85}}
.btn-outline{{background:none;border:1px solid var(--b);color:var(--m);padding:10px 22px;border-radius:8px;font-weight:600;font-size:13px;cursor:pointer;transition:all .15s}}
.btn-outline:hover{{border-color:var(--g);color:var(--g)}}
.form-footer{{padding:0 20px 20px;display:flex;gap:10px}}
/* PRODUCT CARDS */
.prod-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px;padding:18px}}
.prod-card{{background:#0d0d18;border:1px solid var(--b);border-radius:12px;overflow:hidden}}
.prod-img{{width:100%;height:140px;object-fit:cover;background:#1a1a2a;display:block}}
.prod-img-placeholder{{width:100%;height:140px;background:#1a1a2a;display:flex;align-items:center;justify-content:center;font-size:32px;font-weight:700;color:#2a2a3a}}
.prod-info{{padding:12px}}
.prod-name{{font-size:13px;font-weight:700;margin-bottom:4px}}
.prod-price{{font-size:15px;font-weight:700;color:var(--g);margin-bottom:4px}}
.prod-stock{{font-size:11px;color:var(--m);margin-bottom:10px}}
.prod-actions{{display:flex;gap:8px}}
/* STATUS */
.status-ok{{color:#22c55e}} .status-low{{color:#f59e0b}} .status-out{{color:var(--red)}}
/* BROADCAST */
.bcast{{padding:18px}}
.bcast p{{font-size:13px;color:var(--m);margin-bottom:14px}}
/* MODAL */
.modal-bg{{position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:200;display:none;align-items:center;justify-content:center}}
.modal-bg.on{{display:flex}}
.modal{{background:var(--s);border:1px solid var(--b);border-radius:16px;width:100%;max-width:520px;max-height:90vh;overflow-y:auto;margin:20px}}
.modal-head{{padding:16px 20px;border-bottom:1px solid var(--b);display:flex;align-items:center;justify-content:space-between}}
.modal-head h3{{font-size:15px;font-weight:700}}
.modal-close{{background:none;border:none;color:var(--m);font-size:22px;cursor:pointer;line-height:1}}
#toast2{{position:fixed;top:20px;right:20px;background:#0d2211;border:1px solid var(--g);color:var(--g);font-size:13px;font-weight:600;padding:10px 18px;border-radius:10px;opacity:0;pointer-events:none;z-index:9999;transition:all .25s}}
#toast2.on{{opacity:1}}
</style></head><body>

<header>
  <h1>⚡ CodedLabs Commerce OS</h1>
  <span class="tag">CodedLabs AI</span>
</header>

<div class="tabs">
  <button class="tab on" onclick="switchTab('orders',this)">📦 Orders</button>
  <button class="tab" onclick="switchTab('products',this)">🗃️ Products</button>
  <button class="tab" onclick="switchTab('broadcast',this)">📣 Broadcast</button>
</div>

<div id="toast2"></div>

<!-- ── ORDERS TAB ── -->
<div class="panel on" id="tab-orders">
  <div class="stats">
    <div class="stat"><div class="stat-n" style="color:var(--g)">{total_orders}</div><div class="stat-l">Total Orders</div></div>
    <div class="stat"><div class="stat-n" style="color:#f59e0b">{pending}</div><div class="stat-l">Pending</div></div>
    <div class="stat"><div class="stat-n" style="color:#22c55e">{delivered}</div><div class="stat-l">Delivered</div></div>
    <div class="stat"><div class="stat-n" style="color:#3b82f6">{total_customers}</div><div class="stat-l">Customers</div></div>
    <div class="stat"><div class="stat-n" style="color:#a78bfa">{tokens_today:,}</div><div class="stat-l">Tokens Today</div></div>
  </div>
  <div class="card">
    <div class="card-head">Orders (last 100)</div>
    <div class="tbl-wrap"><table>
      <thead><tr><th>Order ID</th><th>Phone</th><th>Items</th><th>Amount</th><th>Status</th><th>Date</th><th>Action</th></tr></thead>
      <tbody id="orders-body">{sales_rows}</tbody>
    </table></div>
  </div>
</div>

<!-- ── PRODUCTS TAB ── -->
<div class="panel" id="tab-products">
  <div class="card">
    <div class="card-head">
      <span>Product Inventory</span>
      <button class="btn" onclick="openAddModal()">+ Add Product</button>
    </div>
    <div class="prod-grid" id="prod-grid">
      <p style="color:var(--m);font-size:13px;padding:10px">Loading products...</p>
    </div>
  </div>
</div>

<!-- ── BROADCAST TAB ── -->
<div class="panel" id="tab-broadcast">
  <div class="card">
    <div class="card-head">📣 Broadcast Message</div>
    <div class="bcast">
      <p>Send a message to all {total_customers} customers. Max 100 per hour to avoid WhatsApp bans.</p>
      <textarea id="msg" placeholder="Flash sale today! 🔥 Shop now: {CATALOG_URL}"></textarea>
      <br><br>
      <button class="btn" onclick="sendBroadcast()">Send to All Customers</button>
      <div id="result" style="margin-top:12px;font-size:13px;color:var(--g)"></div>
    </div>
  </div>
</div>

<!-- ── ADD PRODUCT MODAL ── -->
<div class="modal-bg" id="add-modal">
  <div class="modal">
    <div class="modal-head">
      <h3 id="modal-title">Add Product</h3>
      <button class="modal-close" onclick="closeModal()">✕</button>
    </div>
    <div class="form-grid">
      <input type="hidden" id="edit-id">
      <div class="form-group full">
        <label>Product Name</label>
        <input type="text" id="f-name" placeholder="e.g. iPhone 13 Pro">
      </div>
      <div class="form-group full">
        <label>Description</label>
        <textarea id="f-desc" placeholder="Brief product description..."></textarea>
      </div>
      <div class="form-group">
        <label>Price (NGN)</label>
        <input type="number" id="f-price" placeholder="450000">
      </div>
      <div class="form-group">
        <label>Stock</label>
        <input type="number" id="f-stock" placeholder="10">
      </div>
      <div class="form-group full">
        <label>Image URL (direct link)</label>
        <input type="text" id="f-img" placeholder="https://...">
      </div>
      <div class="form-group full">
        <label>Tags (comma separated)</label>
        <input type="text" id="f-tags" placeholder="electronics, phones, accessories">
      </div>
    </div>
    <div class="form-footer">
      <button class="btn" onclick="saveProduct()">Save Product</button>
      <button class="btn-outline" onclick="closeModal()">Cancel</button>
    </div>
  </div>
</div>

<script>
var SECRET = '{ADMIN_SECRET}';

// ── TABS ──
function switchTab(name, el){{
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('on'));
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('on'));
  document.getElementById('tab-'+name).classList.add('on');
  el.classList.add('on');
  if(name==='products') loadProducts();
}}

// ── TOAST ──
function toast(msg,err){{
  var el=document.getElementById('toast2');
  el.textContent=msg;
  el.style.borderColor=err?'#ef4444':'#25D366';
  el.style.color=err?'#ef4444':'#25D366';
  el.classList.add('on');
  setTimeout(()=>el.classList.remove('on'),2500);
}}

// ── PRODUCTS ──
var allProducts=[];

async function loadProducts(){{
  try{{
    const res=await fetch('/admin/products?secret='+SECRET);
    allProducts=await res.json();
    renderProducts(allProducts);
  }}catch(e){{toast('Failed to load products',true);}}
}}

function renderProducts(products){{
  var grid=document.getElementById('prod-grid');
  if(!products.length){{grid.innerHTML='<p style="color:var(--m);font-size:13px;padding:10px">No products yet. Click Add Product to get started.</p>';return;}}
  grid.innerHTML=products.map(p=>{{
    var stockColor=p.stock==0?'status-out':p.stock<=3?'status-low':'status-ok';
    var stockLabel=p.stock==0?'Out of Stock':p.stock<=3?'Low Stock ('+p.stock+')':p.stock+' in stock';
    var img=p.image_url?'<img class="prod-img" src="'+p.image_url+'" onerror="this.style.display=\'none\'">':'<div class="prod-img-placeholder">'+p.name.substring(0,2).toUpperCase()+'</div>';
    return '<div class="prod-card">'+img+'<div class="prod-info"><div class="prod-name">'+p.name+'</div><div class="prod-price">NGN '+parseInt(p.price).toLocaleString()+'</div><div class="prod-stock '+stockColor+'">'+stockLabel+'</div><div class="prod-actions"><button class="act-btn" onclick=\'editProduct('+JSON.stringify(p)+')\'>Edit</button><button class="del-btn" onclick="deleteProduct(\''+p.id+'\')">Delete</button></div></div></div>';
  }}).join('');
}}

function openAddModal(){{
  document.getElementById('modal-title').textContent='Add Product';
  document.getElementById('edit-id').value='';
  document.getElementById('f-name').value='';
  document.getElementById('f-desc').value='';
  document.getElementById('f-price').value='';
  document.getElementById('f-stock').value='';
  document.getElementById('f-img').value='';
  document.getElementById('f-tags').value='';
  document.getElementById('add-modal').classList.add('on');
}}

function editProduct(p){{
  document.getElementById('modal-title').textContent='Edit Product';
  document.getElementById('edit-id').value=p.id;
  document.getElementById('f-name').value=p.name||'';
  document.getElementById('f-desc').value=p.description||'';
  document.getElementById('f-price').value=p.price||'';
  document.getElementById('f-stock').value=p.stock||'';
  document.getElementById('f-img').value=p.image_url||'';
  document.getElementById('f-tags').value=p.tags||'';
  document.getElementById('add-modal').classList.add('on');
}}

function closeModal(){{document.getElementById('add-modal').classList.remove('on');}}

async function saveProduct(){{
  var id=document.getElementById('edit-id').value;
  var data={{
    name:document.getElementById('f-name').value.trim(),
    description:document.getElementById('f-desc').value.trim(),
    price:parseInt(document.getElementById('f-price').value)||0,
    stock:parseInt(document.getElementById('f-stock').value)||0,
    image_url:document.getElementById('f-img').value.trim(),
    tags:document.getElementById('f-tags').value.trim(),
  }};
  if(!data.name){{toast('Product name is required',true);return;}}
  try{{
    var url=id?'/admin/products/'+id+'?secret='+SECRET:'/admin/products?secret='+SECRET;
    var method=id?'PUT':'POST';
    const res=await fetch(url,{{method,headers:{{'Content-Type':'application/json'}},body:JSON.stringify(data)}});
    const result=await res.json();
    if(result.error){{toast(result.error,true);return;}}
    toast(id?'Product updated!':'Product added!');
    closeModal();
    loadProducts();
  }}catch(e){{toast('Save failed',true);}}
}}

async function deleteProduct(id){{
  if(!confirm('Delete this product? This cannot be undone.'))return;
  try{{
    await fetch('/admin/products/'+id+'?secret='+SECRET,{{method:'DELETE'}});
    toast('Product deleted');
    loadProducts();
  }}catch(e){{toast('Delete failed',true);}}
}}

// ── ORDERS ──
async function markStatus(id,status){{
  try{{
    await fetch('/admin/orders/'+id+'?secret='+SECRET,{{
      method:'PUT',
      headers:{{'Content-Type':'application/json'}},
      body:JSON.stringify({{status}})
    }});
    toast('Order marked as '+status);
    setTimeout(()=>location.reload(),1000);
  }}catch(e){{toast('Update failed',true);}}
}}

// ── BROADCAST ──
async function sendBroadcast(){{
  var msg=document.getElementById('msg').value.trim();
  var r=document.getElementById('result');
  if(!msg){{r.textContent='Write a message first.';return;}}
  r.textContent='Sending...';
  try{{
    const res=await fetch('/broadcast',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{secret:SECRET,message:msg}})}});
    const d=await res.json();
    r.textContent='Sent to '+d.sent+' customers. ('+d.failed+' failed)';
  }}catch(e){{r.textContent='Broadcast failed.';}}
}}
</script>
</body></html>"""


# ══════════════════════════════════════════════════════
# 16. PRODUCT MANAGEMENT API
# ══════════════════════════════════════════════════════
@app.route("/admin/orders/<order_id>", methods=["PUT"])
def api_update_order(order_id):
    if request.args.get("secret") != ADMIN_SECRET:
        return jsonify({"error": "Unauthorized"}), 403
    body = request.json or {}
    try:
        supabase.table("orders").update({"status": body.get("status","Pending")}).eq("id", order_id).execute()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/admin/products", methods=["GET"])
def api_get_products():
    if request.args.get("secret") != ADMIN_SECRET:
        return jsonify({"error": "Unauthorized"}), 403
    try:
        query = supabase.table("products").select("*")
        if CLIENT_ID:
            query = query.eq("client_id", CLIENT_ID)
        result = query.order("created_at", desc=False).execute()
        return jsonify(result.data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/admin/products", methods=["POST"])
def api_add_product():
    if request.args.get("secret") != ADMIN_SECRET:
        return jsonify({"error": "Unauthorized"}), 403
    body = request.json or {}
    try:
        data = {
            "name":        body.get("name", "").strip(),
            "description": body.get("description", "").strip(),
            "price":       int(body.get("price", 0)),
            "stock":       int(body.get("stock", 0)),
            "image_url":   body.get("image_url", "").strip(),
            "tags":        body.get("tags", "").strip(),
            "active":      True,
            "created_at":  time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        if CLIENT_ID:
            data["client_id"] = CLIENT_ID
        result = supabase.table("products").insert(data).execute()
        # Bust inventory cache
        inventory_cache["data"]         = None
        inventory_cache["last_updated"] = 0
        return jsonify(result.data[0])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/admin/products/<product_id>", methods=["PUT"])
def api_update_product(product_id):
    if request.args.get("secret") != ADMIN_SECRET:
        return jsonify({"error": "Unauthorized"}), 403
    body = request.json or {}
    try:
        data = {}
        if "name"        in body: data["name"]        = body["name"].strip()
        if "description" in body: data["description"] = body["description"].strip()
        if "price"       in body: data["price"]        = int(body["price"])
        if "stock"       in body: data["stock"]        = int(body["stock"])
        if "image_url"   in body: data["image_url"]   = body["image_url"].strip()
        if "tags"        in body: data["tags"]         = body["tags"].strip()
        if "active"      in body: data["active"]       = bool(body["active"])
        supabase.table("products").update(data).eq("id", product_id).execute()
        inventory_cache["data"]         = None
        inventory_cache["last_updated"] = 0
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/admin/products/<product_id>", methods=["DELETE"])
def api_delete_product(product_id):
    if request.args.get("secret") != ADMIN_SECRET:
        return jsonify({"error": "Unauthorized"}), 403
    try:
        supabase.table("products").delete().eq("id", product_id).execute()
        inventory_cache["data"]         = None
        inventory_cache["last_updated"] = 0
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ══════════════════════════════════════════════════════
# 17. UTILITY ENDPOINTS
# ══════════════════════════════════════════════════════
@app.route("/refresh")
def refresh():
    if request.args.get("secret") != ADMIN_SECRET:
        return "Unauthorized", 403
    inventory_cache["data"]         = None
    inventory_cache["last_updated"] = 0
    profile_cache.clear()
    return "Cache cleared.", 200


@app.route("/ping")
def ping():
    return "pong", 200


@app.route("/")
def health():
    today  = time.strftime("%Y-%m-%d")
    tokens = token_log.get(today, 0)
    return (
        f"Jordan v5 Online | AI: {AI_ENGINE} | "
        f"Tokens today: {tokens:,} | "
        f"Active sessions: {len(sessions)}"
    ), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
