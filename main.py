"""
Box 1: WhatsApp webhook + allow-list gate.

What this file does:
1. Handles Meta's one-time GET verification handshake.
2. Receives POST requests every time someone messages the bot.
3. Checks the sender's phone number against an allow-list BEFORE
   doing anything else with the message.

Nothing here transcribes audio or talks to any grocery service yet —
that's Box 2 onward. This box's only job is: receive safely, prove it works.
"""

import os
import time
import json
import string
import re
from datetime import datetime, timedelta
import asyncio
import requests
from fastapi import FastAPI, Request, Response, BackgroundTasks
from dotenv import load_dotenv
import uvicorn

load_dotenv()  # reads the .env file in this folder into environment variables

app = FastAPI()


@app.get("/health")
async def health_check():
    """
    Plain endpoint for an external uptime pinger (e.g. cron-job.org) to
    hit every ~10 minutes on free hosting tiers that sleep after
    inactivity (like Render's) - keeps the process, and therefore the
    background scheduler loop, continuously running. Separate from
    /webhook's GET, which is reserved for Meta's own verification
    handshake and expects specific query parameters.
    """
    return {"status": "ok"}

# --- Configuration ---------------------------------------------------
# These now come ONLY from environment variables / .env - never hardcode
# real tokens in code, since this file could end up in git/shared by accident.
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "my_dev_verify_token")
ALLOWED_NUMBERS = set(
    n.strip() for n in os.environ.get("ALLOWED_NUMBERS", "").split(",") if n.strip()
)
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")  # from Meta dashboard
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")  # from Meta dashboard
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")  # from aistudio.google.com/apikey - used later in Box 3
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")  # from console.groq.com - hosts Whisper for transcription

# Box 3: maps each allowed sender to their default delivery address.
# Format in .env: "919999999999:Moms Home,918888888888:Dads Home"
# (avoid commas inside address names - this is a simple format for learning purposes)
def _parse_address_map(raw: str) -> dict:
    mapping = {}
    for pair in raw.split(","):
        if ":" in pair:
            phone, address = pair.split(":", 1)
            mapping[phone.strip()] = address.strip()
    return mapping

ADDRESS_MAP = _parse_address_map(os.environ.get("ADDRESS_MAP", ""))

# Tracks who's already gotten the one-time welcome tip about special
# commands (clear cart, what's in my cart, repeat the order) - shown
# once per sender, on their first-ever message, so it's discoverable
# without needing to be told by whoever built the bot.
#
# Persisted to a small local file - in-memory alone meant every server
# restart (which happens often during dev, and will happen on hosting
# platform redeploys too) re-triggered the welcome message for people
# who'd already seen it.
WELCOMED_SENDERS_FILE = "welcomed_senders.json"


def _load_welcomed_senders() -> set:
    try:
        with open(WELCOMED_SENDERS_FILE) as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def _save_welcomed_senders():
    with open(WELCOMED_SENDERS_FILE, "w") as f:
        json.dump(list(WELCOMED_SENDERS), f)


WELCOMED_SENDERS = _load_welcomed_senders()

# WhatsApp delivers "at least once" - if our webhook doesn't reply fast
# enough, it resends the SAME message. This tracks message IDs we've
# already started processing, so a resend gets ignored instead of
# triggering a second full search/reply. In-memory only - resets on
# restart, which is fine for personal-scale use.
PROCESSED_MESSAGE_IDS = set()


def download_whatsapp_media(media_id: str) -> bytes:
    """
    Two-step fetch, because WhatsApp never sends the file itself -
    only an ID (a 'claim ticket' for the real file).
    Step 1: ask Meta for a temporary download URL for this media ID.
    Step 2: actually download the bytes from that URL.
    Both requests need your access token - the download URL alone isn't public.
    """
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}

    lookup_resp = requests.get(f"https://graph.facebook.com/v20.0/{media_id}", headers=headers, timeout=10)
    lookup_resp.raise_for_status()
    media_url = lookup_resp.json()["url"]

    download_resp = requests.get(media_url, headers=headers, timeout=30)
    download_resp.raise_for_status()
    return download_resp.content


def transcribe_audio(audio_bytes: bytes) -> str:
    """
    Sends raw audio to Gemini and asks it to translate + transcribe into
    English, no matter what language was actually spoken - Hindi,
    Hinglish, or English audio all come back as English text.

    Using this instead of Groq's Whisper translation endpoint, which is
    currently returning 403 Forbidden - likely a model-permission
    restriction on the 'whisper-large-v3' model specifically. Same
    header-based auth pattern as parse_request/classify_pending_reply.
    """
    import base64
    audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")

    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-lite-latest:generateContent"
    headers = {"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"}
    payload = {
        "contents": [{
            "parts": [
                {"text": (
                    "Transcribe this audio and translate it into English, no matter "
                    "what language was spoken (Hindi, Hinglish, or English). Output "
                    "ONLY the English text - no commentary, no labels, nothing else. "
                    "Keep brand names and proper nouns exactly as spoken, do not "
                    "'correct' unfamiliar-sounding names."
                )},
                {"inline_data": {"mime_type": "audio/ogg", "data": audio_b64}},
            ]
        }]
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    return resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()


def parse_request(transcript: str) -> dict:
    """
    Sends the transcript (English from voice, or possibly any language
    from a typed text message) to Gemini and gets back structured JSON.

    CRITICAL: "quantity" is ALWAYS a pack/unit COUNT, never a weight
    number. "100 g of coriander" must be quantity=1, size="100 g" - NOT
    quantity=100. Conflating these caused a real, dangerous bug: a 100g
    coriander request became a 100-PACK order (Rs.5000 instead of ~Rs.50).
    "size" is a hint for picking the right pack, never a multiplier.
    """
    prompt = f"""You are parsing a grocery order message for a voice/text assistant.
The message may be in English already, or in Hindi/Hinglish (if so, translate
item names into English in your output - but keep brand names EXACTLY as
given, never translate or alter a brand name).

Extract it into STRICT JSON with EXACTLY this shape. Output ONLY the JSON - no
markdown code fences, no explanation, nothing else:

{{
  "items": [
    {{"name": "<generic item name>", "brand": "<brand name or null>", "quantity": <COUNT of packs/units, default 1>, "size": "<weight/volume hint like '100 g', '1 kg', '2 litre', '500 ml', or null>"}}
  ],
  "address_override": "<address mentioned, or null>",
  "needs_clarification": <true ONLY if there is no identifiable item at all>,
  "clarification_question": "<short question to ask, or null>"
}}

CRITICAL RULE on quantity vs size - a past bug caused a near-Rs.8000 mistaken
order by getting this wrong, so follow it exactly:
- "quantity" is ALWAYS how many packs/units to buy - a plain COUNT, default 1.
  NEVER put a weight or volume number here.
- "size" is a weight/volume/pack-size HINT used only to help pick the closest
  matching product pack - it is NEVER a multiplier.
- Examples:
  - "100 g of coriander" -> quantity=1, size="100 g" (NOT quantity=100!)
  - "1 kg onions" -> quantity=1, size="1 kg"
  - "2 packets of Amul milk" -> quantity=2, size=null (packet is a count, not a weight)
  - "1 litre of Sprite" -> quantity=1, size="1 litre"
  - "3 dozen eggs" -> quantity=3, size="dozen"
  - "2 kg onions" -> quantity=1, size="2 kg" (buy the pack closest to 2kg total,
    don't multiply - if unsure, quantity=1 with size stating the total desired)

Other rules:
- If quantity isn't stated, default to 1. Missing quantity or brand is NOT a
  reason to set needs_clarification to true.
- Keep brand names EXACTLY as given - never "correct" or replace an
  unfamiliar-sounding brand name. Regional and local brands are common and real.
- address_override: ONLY set this when the message gives an EXPLICIT delivery
  instruction ("send it to X", "deliver to Y", "bhej do X pe"). A product/brand
  name (like "Park Avenue" soap) is NOT an address even if it sounds like one.

Message: "{transcript}"
"""
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-lite-latest:generateContent"
    headers = {"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"}
    payload = {"contents": [{"parts": [{"text": prompt}]}]}

    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    raw_text = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()

    # Gemini sometimes wraps JSON in ```json fences despite instructions - strip them if present.
    if raw_text.startswith("```"):
        raw_text = raw_text.strip("`")
        if raw_text.startswith("json"):
            raw_text = raw_text[4:].strip()

    return json.loads(raw_text)


def resolve_address(sender: str, address_override) -> str:
    """Sender's spoken override wins if present, otherwise their saved default."""
    if address_override:
        return address_override
    return ADDRESS_MAP.get(sender, "(no default address set for this number)")


def load_swiggy_token() -> str:
    """
    Loads the access token saved by swiggy_login.py. Checks the
    SWIGGY_TOKEN_JSON env var first (for hosted deployments, where the
    local swiggy_token.json file won't exist - it's gitignored and never
    committed), then falls back to the local file (for local dev).

    Raises a clear error if it's missing or expired - Swiggy's OAuth has
    no automatic refresh yet, so re-running swiggy_login.py periodically
    (locally, then pasting the new token into SWIGGY_TOKEN_JSON if
    hosted) is a real, expected manual step, not a bug in this code.
    """
    import json as _json

    env_token = os.environ.get("SWIGGY_TOKEN_JSON")
    if env_token:
        token_data = _json.loads(env_token)
    else:
        try:
            with open("swiggy_token.json") as f:
                token_data = _json.load(f)
        except FileNotFoundError:
            raise RuntimeError("No Swiggy login found - run 'python swiggy_login.py' first.")

    if time.time() >= token_data["expires_at"] - 60:
        raise RuntimeError("Swiggy login expired (~5 day limit) - run 'python swiggy_login.py' again.")

    return token_data["access_token"]


async def call_swiggy_tool(tool_name: str, arguments: dict) -> dict:
    """
    Generic helper: calls any Swiggy Instamart MCP tool and returns its
    data payload as a plain dict. Every Box 4+ Instamart call goes
    through this one function - if the connection/auth pattern ever
    needs to change, it changes in exactly one place.

    Defensive by design: MCP tool calls can fail at the TOOL level
    (result.isError=True with a plain-text message) rather than raising
    a normal exception - if we don't check for that, code downstream
    trying to treat the response as JSON data crashes confusingly.
    Always returns a dict, never None, so callers never need a None-check.
    """
    import json as _json
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    token = load_swiggy_token()
    headers = {"Authorization": f"Bearer {token}"}

    async with streamablehttp_client("https://mcp.swiggy.com/im", headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments=arguments)
            text_content = result.content[0].text if result.content else ""
            structured = getattr(result, "structuredContent", None)
            print(f"[swiggy_raw] tool={tool_name} args={arguments} isError={getattr(result, 'isError', False)}")
            print(f"[swiggy_structured] {structured}")
            print(f"[swiggy_text] {text_content[:300]}{'...(truncated)' if len(text_content) > 300 else ''}")

            if getattr(result, "isError", False):
                raise RuntimeError(f"Swiggy tool '{tool_name}' returned an error: {text_content}")

            # Backup check: some failures show up as error-shaped TEXT
            # without the isError flag actually being set. Don't trust
            # the flag alone for something this consequential.
            if "is required" in text_content or "Report ID:" in text_content:
                raise RuntimeError(f"Swiggy tool '{tool_name}' likely failed (error-shaped text): {text_content}")

            # Some tools (get_addresses) put real data in structuredContent.
            # Others (search_products) leave structuredContent empty and put
            # valid JSON in the plain text instead. Try both.
            data = structured if isinstance(structured, dict) else None

            if data is None and text_content:
                try:
                    parsed_text = _json.loads(text_content)
                    if isinstance(parsed_text, dict):
                        data = parsed_text
                except _json.JSONDecodeError:
                    pass  # genuinely just conversational text, not JSON - fine

            if data is None:
                raise RuntimeError(f"Swiggy tool '{tool_name}' gave no usable data (see [swiggy_text] log above)")

            # Some tools wrap the real payload in {"success": true, "data": {...}} -
            # unwrap it so every caller gets the same flat shape either way.
            if isinstance(data.get("data"), dict):
                data = data["data"]

            return data


async def resolve_instamart_address_id(address_label: str, fallback_label: str = None) -> str:
    """Matches our resolved address label (e.g. 'Home') against the
    account's real saved Instamart addresses, returning the actual
    addressId that search_products/checkout require.

    If address_label doesn't match a real saved address (e.g. a
    misheard/misparsed override), falls back to the sender's actual
    default (fallback_label) - NOT an arbitrary first address. Silently
    guessing an unrelated real address (like defaulting to Work) is a
    genuine safety issue, not just an inconvenience."""
    result = await call_swiggy_tool("get_addresses", {})
    addresses = result.get("addresses") or []

    for addr in addresses:
        if addr.get("addressTag", "").lower() == address_label.lower():
            return addr["id"]

    if fallback_label and fallback_label.lower() != address_label.lower():
        print(f"[address_fallback] '{address_label}' didn't match a saved address - trying default '{fallback_label}'")
        for addr in addresses:
            if addr.get("addressTag", "").lower() == fallback_label.lower():
                return addr["id"]

    if addresses:
        print(f"[address_fallback] neither '{address_label}' nor default matched - using most recent address as last resort")
        return addresses[0]["id"]
    raise RuntimeError("No saved Instamart addresses found on this account - add one in the Swiggy app first.")


def pick_best_variant(variations: list, size_hint: str = None) -> dict:
    """
    Picks which pack size to actually order. Just taking variations[0]
    is unsafe - Instamart doesn't always list the everyday single unit
    first, and can list a bulk case (e.g. '1 ltr x 12') before it.

    If a size_hint is given (e.g. "100 g", "1 kg" from the person's
    request), try to find a variation whose pack size matches it first.
    size_hint is a MATCHING target, never a multiplier - it only changes
    WHICH single pack gets chosen, not how many.

    Otherwise: prefer single-unit packs (no 'x' in the quantity
    description) over multi-packs, and among those, pick the cheapest -
    a safe default that avoids accidentally selecting an expensive case
    when someone just asked for "milk."
    """
    single_unit = [v for v in variations if " x " not in (v.get("quantityDescription") or "")]
    candidates = single_unit if single_unit else variations

    if size_hint:
        hint_norm = size_hint.lower().replace(" ", "")
        for v in candidates:
            desc_norm = (v.get("quantityDescription") or "").lower().replace(" ", "")
            if hint_norm == desc_norm or hint_norm in desc_norm or desc_norm in hint_norm:
                return v
        # No matching pack size found - fall through to the cheapest
        # single-unit default rather than guessing further.

    return min(candidates, key=lambda v: (v.get("price") or {}).get("offerPrice", float("inf")))


async def search_grocery_item(address_id: str, item: dict) -> dict:
    """
    Searches Instamart for one parsed item, brand-aware, and classifies
    the result as found / out_of_stock / not_found.
    """
    query = f'{item["brand"]} {item["name"]}' if item.get("brand") else item["name"]
    result = await call_swiggy_tool("search_products", {"addressId": address_id, "query": query})
    products = result.get("products") or []

    if not products:
        return {"status": "not_found", "item": item, "query": query}

    top = products[0]
    variations = top.get("variations") or []
    if not variations:
        return {"status": "not_found", "item": item, "query": query}

    variant = pick_best_variant(variations, item.get("size"))
    product_name = f'{top.get("displayName")} ({variant.get("quantityDescription")})'

    if not variant.get("isInStockAndAvailable", True):
        return {"status": "out_of_stock", "item": item, "product_name": product_name, "query": query}

    price = variant.get("price") or {}
    return {
        "status": "found",
        "item": item,
        "product_name": product_name,
        "spinId": variant.get("spinId"),
        "skuId": variant.get("skuId"),
        "price": price.get("offerPrice") or price.get("mrp"),
    }


def send_whatsapp_message(to: str, text: str):
    """
    Sends a plain text WhatsApp message using Meta's Send Message API.
    This is what turns Box 1 from 'silently logs things' into something
    you can actually see reply on your own phone.
    """
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
        print("[send] SKIPPED - WHATSAPP_TOKEN or PHONE_NUMBER_ID not set in .env yet")
        return

    url = f"https://graph.facebook.com/v20.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text},
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=10)
    print(f"[send] to={to} status={resp.status_code} response={resp.text}")


# --- Step 1: Meta's verification handshake ----------------------------
@app.get("/webhook")
def verify_webhook(request: Request):
    """
    Meta calls this ONCE when you register the webhook URL in the
    Meta Developer dashboard. It sends three query parameters:
      - hub.mode        -> should be "subscribe"
      - hub.verify_token -> a secret string YOU chose when registering
      - hub.challenge    -> a random number Meta wants echoed back

    If our verify_token matches what Meta sent, we prove we own this
    URL by returning the challenge value. Otherwise, reject.
    """
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        print("[verify] handshake succeeded")
        return Response(content=challenge, media_type="text/plain")

    print("[verify] handshake FAILED - token mismatch")
    return Response(content="Forbidden", status_code=403)


# --- Step 2: Receiving actual messages ---------------------------------
def is_allowed(sender_number: str) -> bool:
    """The allow-list gate. Everything else depends on this check happening first."""
    return sender_number in ALLOWED_NUMBERS


def extract_sender_and_message(payload: dict):
    """
    Pulls out the sender's phone number and the message object from
    WhatsApp's (fairly deeply nested) webhook payload shape.
    Returns (None, None) if this payload isn't an actual user message
    (Meta also sends other event types, like delivery/read receipts).
    """
    try:
        entry = payload["entry"][0]
        change = entry["changes"][0]["value"]
        messages = change.get("messages")
        if not messages:
            return None, None  # e.g. a status update, not a new message
        message = messages[0]
        sender = message["from"]  # phone number, no "+" prefix
        return sender, message
    except (KeyError, IndexError):
        return None, None


@app.post("/webhook")
async def receive_message(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()
    sender, message = extract_sender_and_message(payload)

    if sender is None:
        # Not a real user message (could be a read receipt, etc.) - ignore quietly.
        return Response(status_code=200)

    if not is_allowed(sender):
        print(f"[security] BLOCKED message from unregistered number: {sender}")
        return Response(status_code=200)  # Always 200 so Meta doesn't retry

    message_id = message.get("id")
    if message_id in PROCESSED_MESSAGE_IDS:
        print(f"[dedup] already processing message_id={message_id} - ignoring resend")
        return Response(status_code=200)
    PROCESSED_MESSAGE_IDS.add(message_id)

    # Hand the slow work (transcription, parsing, Instamart search) to a
    # background task and respond to WhatsApp immediately. WhatsApp's
    # timeout is only 5-10 seconds - our full pipeline can easily take
    # longer than that, and NOT returning fast is exactly what was
    # causing WhatsApp to think delivery failed and resend the message.
    background_tasks.add_task(process_message, sender, message)
    return Response(status_code=200)


# Box 5: tracks, per sender, an order that's been searched and is
# waiting for an explicit yes/no reply. In-memory only - resets on
# restart, which is an acceptable limitation for personal-scale use
# (a restart mid-confirmation just means re-sending the order once).
PENDING_ORDERS = {}

# Box 6: items are confirmed correct, now waiting for ORDER vs CART choice.
PENDING_FULFILLMENT_CHOICE = {}

# Box 6: chosen ORDER, but not yet actually checked out - one more
# explicit confirmation before real money moves, added after a near-miss
# where a garbled reply almost triggered an unwanted real checkout.
PENDING_FINAL_CONFIRM = {}

# Tracks each sender's last successfully confirmed order (CART or ORDER),
# so "repeat the order" can re-search and re-confirm it fresh - never
# re-adds silently, since prices/stock may have changed since last time.
LAST_ORDER_ITEMS = {}

# Box 6: items were added to the real cart (CART path) and are waiting
# for someone to pay in the app. This is a SINGLE shared value, not
# per-sender - the whole family uses ONE Swiggy account, so there is
# genuinely only one real cart no matter who's talking to the bot.
# Pretending each phone number has its own isolated cart was itself a
# bug - two people ordering near the same time could otherwise wipe
# each other's items out.
SHARED_CART = None  # None, or {"added_by":, "added_at":, "search_results":, "address_id":, "address_label":}
ABANDONED_CART_TIMEOUT_SECONDS = 30 * 60  # 30 minutes

# Serializes anything that actually touches the real Swiggy cart
# (clear_cart/update_cart/checkout) - protects against two near-
# simultaneous requests interleaving and corrupting the one shared cart.
CART_LOCK = asyncio.Lock()

AFFIRMATIVE_REPLIES = {"yes", "y", "haan", "ha", "ok", "okay", "confirm", "yep", "sure", "correct"}
NEGATIVE_REPLIES = {"no", "n", "nahi", "cancel", "nope", "stop"}


HELP_TEXT = (
    "👋 Hi! Just tell me what groceries you'd like, by voice or text - in Hindi, Hinglish, or English.\n\n"
    "A few things you can say anytime:\n"
    "• *\"what's in my cart\"* - check what's really there\n"
    "• *\"clear cart\"* - empty it\n"
    "• *\"repeat the order\"* - reorder what you got last time\n"
    "• *\"help\"* - see this message again anytime\n\n"
    "⚠️ *Tip:* Add everything first. Open the Swiggy app only once, at the end. "
    "If you don't pay, clear the cart there in the app."
)

HELP_WORDS = {"help", "info", "commands", "madad"}
HELP_PHRASES = {"how to use", "what can you do", "how does this work"}


async def maybe_handle_help_command(sender: str, reply_text: str) -> bool:
    """
    Shows the same tips as the one-time welcome message, but available
    anytime by asking - since a one-time message is easy to lose if
    someone clears or deletes their chat history. Matches only the
    whole message against short words (not a substring anywhere) to
    avoid false-triggering on something like "help me add milk".
    """
    normalized = reply_text.strip().lower()
    if normalized in HELP_WORDS or any(p in normalized for p in HELP_PHRASES):
        send_whatsapp_message(sender, HELP_TEXT)
        return True
    return False


async def process_message(sender: str, message: dict):
    """
    Does all the actual work for one message. Runs in the background,
    AFTER we've already told WhatsApp "received" - so no matter how
    long this takes, WhatsApp won't resend the message.

    First decides what the message even IS as plain text (transcribing
    if it's audio), then checks whether this sender has a pending
    confirmation waiting - if so, the text is treated as their answer,
    not a new order.
    """
    msg_type = message.get("type")
    print(f"[received] from={sender} type={msg_type} raw={message}")

    def _send_welcome_if_new() -> bool:
        """Sends the one-time welcome text if this is a brand new
        sender. Returns True if it was actually sent, so the caller can
        avoid immediately repeating the same content - e.g. if someone's
        literal first-ever message is itself "help", the welcome text
        (which is the same content) already answered it."""
        if sender in WELCOMED_SENDERS:
            return False
        WELCOMED_SENDERS.add(sender)
        _save_welcomed_senders()
        send_whatsapp_message(sender, HELP_TEXT)
        return True

    if msg_type == "text":
        reply_text = message["text"]["body"]
        is_new_sender = _send_welcome_if_new()
    elif msg_type == "audio":
        try:
            audio_bytes = download_whatsapp_media(message["audio"]["id"])
            reply_text = transcribe_audio(audio_bytes)
            print(f"[transcript] {reply_text}")
        except Exception as e:
            import traceback
            print(f"[error] transcription failed: {e}")
            traceback.print_exc()
            _send_welcome_if_new()
            send_whatsapp_message(sender, "Sorry, I couldn't process that voice note. Try again?")
            return
        is_new_sender = _send_welcome_if_new()
    else:
        _send_welcome_if_new()
        send_whatsapp_message(sender, f"Got your {msg_type} message. (Box 1 test - nothing else happens yet.)")
        return

    if is_new_sender:
        normalized_first = reply_text.strip().lower()
        if normalized_first in HELP_WORDS or any(p in normalized_first for p in HELP_PHRASES):
            return  # the welcome text just sent already answered this - skip the identical repeat

    # Checked FIRST, regardless of state - a real incident showed that
    # only checking this when nothing was pending meant a "clear cart"
    # attempt during an active confirmation got swallowed by the AI
    # classifier instead, which guessed wrong.
    if await maybe_handle_help_command(sender, reply_text):
        return

    if await maybe_handle_clear_cart_command(sender, reply_text):
        return

    if await maybe_handle_repeat_order_command(sender, reply_text):
        return

    if await maybe_handle_check_scheduled_command(sender, reply_text):
        return

    if await maybe_handle_check_cart_command(sender, reply_text):
        return

    if sender in PENDING_FINAL_CONFIRM:
        await handle_final_order_confirm(sender, reply_text)
    elif sender in PENDING_FULFILLMENT_CHOICE:
        await handle_fulfillment_choice(sender, reply_text)
    elif sender in PENDING_ORDERS:
        await handle_confirmation_reply(sender, reply_text)
    else:
        await handle_new_order_request(sender, reply_text)


def build_order_lines(search_results: list) -> tuple:
    """Shared line-building logic, used everywhere an order needs to be
    displayed: the initial confirmation, the final double-check before
    real checkout, and anywhere else the same order needs re-showing."""
    lines = []
    total = 0
    for r in search_results:
        if r["status"] == "found":
            line_total = r["price"] * r["item"]["quantity"]
            total += line_total
            lines.append(f'✅ {r["item"]["quantity"]}x {r["product_name"]} — Rs.{line_total}')
        elif r["status"] == "out_of_stock":
            lines.append(f'❌ _{r["product_name"]} — out of stock_')
        elif r["status"] == "error":
            lines.append(f'⚠️ _Couldn\'t search "{r["query"]}" - see log_')
        else:
            lines.append(f'❓ _"{r["query"]}" — not found_')
    return lines, total


async def search_and_confirm(sender: str, address_id: str, address_label: str, items: list):
    """
    Searches every item fresh, builds one confirmation message, sends it,
    and stores the result as the new pending order. Shared by both a
    brand-new order AND a correction to an existing one - a correction
    just re-runs this with an updated item list, so pricing/stock is
    always freshly checked rather than reused from before.
    """
    search_results = []
    for one_item in items:
        try:
            search_results.append(await search_grocery_item(address_id, one_item))
        except Exception as e:
            import traceback
            print(f"[error] search failed for item={one_item}: {e}")
            traceback.print_exc()
            search_results.append({"status": "error", "item": one_item, "query": one_item.get("name", "?")})
    print(f"[search_results] {search_results}")

    lines, total = build_order_lines(search_results)

    summary = (
        "🛒 *Your Order*\n\n" + "\n".join(lines)
        + f"\n\n💰 *Total: Rs.{total}*\n📍 *Delivering to:* {address_label}"
        + "\n\nReply *YES* to confirm, *NO* to cancel, or tell me what to change or add."
    )
    send_whatsapp_message(sender, summary)

    PENDING_ORDERS[sender] = {
        "search_results": search_results,
        "address_label": address_label,
        "address_id": address_id,
        "total": total,
    }


def classify_pending_reply(current_search_results: list, reply_text: str) -> dict:
    """
    Figures out what a reply to a pending confirmation actually means:
    confirming, cancelling, or changing something. If modifying, Gemini
    returns the COMPLETE updated item list (not just a diff) - the
    caller just re-searches everything fresh from that list, which is
    simpler and safer than trying to patch individual items in place.

    Important: given BOTH the original request AND the actual matched
    product name for each item - not just the original request. A user
    reacts to what they SEE on screen (e.g. "Amul Pasteurised Butter" if
    that's what a bad search match displayed for "Morris bread"), which
    can differ completely from what they originally asked for. Without
    the matched name, corrections referencing what's on screen can't be
    understood correctly - this was the actual root cause of corrections
    seeming to "randomly" add or remove the wrong thing.
    """
    display_items = [
        {
            "originally_requested": r["item"],
            "actually_matched_product": r.get("product_name"),
            "status": r["status"],
        }
        for r in current_search_results
    ]

    prompt = f"""The user was shown this grocery order and asked to confirm it:
{json.dumps(display_items)}

Each entry shows what the user ORIGINALLY asked for, AND what product was
actually matched and shown to them on screen (search can sometimes match the
wrong product - the user reacts to what they SEE, which may differ from what
they originally requested).

They replied: "{reply_text}"

Decide their intent and respond with STRICT JSON only, no markdown fences:
{{
  "intent": "confirm" | "cancel" | "modify",
  "items": [...]
}}

Rules:
- "confirm": approving the order as shown (yes, haan, correct, looks good).
- "cancel": cancelling entirely (a clear, standalone "no", "cancel", "nahi",
  "stop", "don't order the whole thing"). A negative word like "nahi"
  appearing PARTWAY THROUGH a longer sentence that's correcting one specific
  value (e.g. "100 into coriander nahi, 100 g coriander" - meaning "not
  that number, I meant this instead") is a MODIFY, not a cancel - the
  person is correcting a detail, not cancelling the whole order.
- "modify": ANYTHING changed - removing an item, replacing a wrong item,
  changing a quantity/brand, or adding new items they forgot to mention
  the first time (including if this looks like a whole new voice note -
  treat it as additions/changes to the current order, not a separate order).
- When the user refers to an item by its ACTUALLY_MATCHED_PRODUCT (what they
  saw on screen), match that entry correctly even if it differs completely
  from what was originally requested - e.g. if actually_matched_product is
  "Amul Pasteurised Butter" and the user says "remove Amul butter", remove
  THAT entry, even if originally_requested was for something unrelated.
  Trust what the user is reacting to on screen over the original request.
- When intent is "modify", "items" must be the COMPLETE updated list, using
  the ORIGINALLY_REQUESTED format (name, brand, quantity, size) for every
  item that should remain - keep unchanged items, remove what they asked to
  remove, add what they asked to add.
- When intent is "confirm" or "cancel", "items" can be an empty list.

CRITICAL RULE on quantity vs size - getting this wrong once already caused a
near-Rs.8000 mistaken order, so follow it exactly for every item in "items":
- "quantity" is ALWAYS how many packs/units to buy - a plain COUNT, default 1.
  NEVER put a weight or volume number here.
- "size" is a weight/volume/pack-size HINT (e.g. "100 g", "1 kg", "2 litre")
  used only to help pick the right pack - it is NEVER a multiplier.
- Example: "100 g dhaniya" -> quantity=1, size="100 g" (NOT quantity=100!)
  even when this appears inside a correction/addition, not just a fresh order.
"""
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-lite-latest:generateContent"
    headers = {"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"}
    payload = {"contents": [{"parts": [{"text": prompt}]}]}

    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    raw_text = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()

    if raw_text.startswith("```"):
        raw_text = raw_text.strip("`")
        if raw_text.startswith("json"):
            raw_text = raw_text[4:].strip()

    return json.loads(raw_text)


async def handle_confirmation_reply(sender: str, reply_text: str):
    """
    Interprets a reply to an already-sent confirmation. Unambiguous
    SHORT yes/no skips the LLM call entirely (fast path); anything
    else - a correction, an addition, or genuinely unclear text - goes
    through Gemini to classify properly.

    CRITICAL: the fast path only fires for short replies (<=3 words).
    A real bug: a longer correction sentence that happened to contain
    the word "nahi" partway through (correcting one value, not
    cancelling) got misread as a full cancellation, because the old
    fast path fired on ANY message containing a negative word anywhere
    in it. Long, complex replies always go through full classification.
    """
    words = {w.strip(string.punctuation) for w in reply_text.strip().lower().split()}
    is_aff = bool(words & AFFIRMATIVE_REPLIES)
    is_neg = bool(words & NEGATIVE_REPLIES)
    is_short = len(words) <= 3

    if is_short and is_aff and not is_neg:
        ask_fulfillment_choice(sender)
        return
    if is_short and is_neg and not is_aff:
        send_whatsapp_message(sender, "Okay, cancelled - nothing was ordered.")
        del PENDING_ORDERS[sender]
        return

    pending = PENDING_ORDERS[sender]

    try:
        result = classify_pending_reply(pending["search_results"], reply_text)
    except Exception as e:
        import traceback
        print(f"[error] classify_pending_reply failed: {e}")
        traceback.print_exc()
        send_whatsapp_message(sender, "Sorry, I didn't catch that - reply YES to confirm, NO to cancel, or tell me what to change.")
        return

    print(f"[pending_intent] {result}")
    intent = result.get("intent")

    if intent == "confirm":
        ask_fulfillment_choice(sender)
    elif intent == "cancel":
        send_whatsapp_message(sender, "Okay, cancelled - nothing was ordered.")
        del PENDING_ORDERS[sender]
    elif intent == "modify" and result.get("items"):
        await search_and_confirm(sender, pending["address_id"], pending["address_label"], result["items"])
    else:
        send_whatsapp_message(sender, "Sorry, I didn't catch that - reply YES to confirm, NO to cancel, or tell me what to change.")


def ask_fulfillment_choice(sender: str):
    """Items are confirmed correct - now ask HOW to finalize, moving the
    pending order from item-confirmation state into fulfillment-choice state."""
    PENDING_FULFILLMENT_CHOICE[sender] = PENDING_ORDERS.pop(sender)
    send_whatsapp_message(
        sender,
        "Place the order now, or add to cart to pay yourself in the app?\n"
        "Reply *ORDER*, *CART*, or *\"schedule 8pm\"* to place it later."
    )


def format_bill_breakdown(cart_data: dict) -> str:
    """
    Turns a get_cart-shaped billBreakdown into a readable line-by-line
    summary - item total, handling fee, delivery fee, small-cart fee,
    GST, and any coupon discount, ending with the REAL amount to pay.
    Built after a real surprise: the item-only total shown mid-order
    (Rs.256) didn't match what was actually charged (Rs.263) - these
    extra charges are real and this shows them upfront instead of
    letting them show up as a surprise only in the app.
    """
    if not cart_data:
        return ""
    bill = cart_data.get("billBreakdown") or {}
    lines = bill.get("lineItems") or []
    parts = [f"{li['label']}: Rs.{li['value']}" for li in lines]
    to_pay = (bill.get("toPay") or {}).get("value")
    text = "\n".join(parts)
    if to_pay:
        text += f"\n*To Pay: Rs.{to_pay}*"
    return text


async def prepare_real_cart(address_id: str, search_results: list) -> dict:
    """
    Safety step before adding anything real: clears whatever's currently
    in the cart first (protects against a forgotten manual addition from
    the app riding along with the bot's order), then adds only the
    confirmed items using their real spinId/skuId. Locked so two
    near-simultaneous requests (e.g. two family members) can't
    interleave their clear/update calls against the one shared cart.

    Tries to apply the best available coupon - this is gated per-account
    on Swiggy's side (list_coupons/apply_coupon return a clean "not
    enabled yet" response for non-whitelisted accounts, not an error),
    so an empty result just means no coupon this time, never a failure.

    Returns the REAL cart (from get_cart, after any coupon) rather than
    trusting clear_cart/update_cart's own "success" response - we've
    seen a call report success while the real cart didn't actually
    change, so self-reported success alone isn't trustworthy enough for
    something this consequential. This return value is also the source
    of truth for the real total shown to the person, including fees.
    """
    async with CART_LOCK:
        await call_swiggy_tool("clear_cart", {})  # takes no parameters, per authoritative docs
        cart_items = [
            {"spinId": r["spinId"], "skuId": r["skuId"], "quantity": r["item"]["quantity"]}
            for r in search_results if r["status"] == "found"
        ]
        await call_swiggy_tool("update_cart", {"selectedAddressId": address_id, "items": cart_items})

        try:
            coupons_result = await call_swiggy_tool("list_coupons", {"addressId": address_id})
            applicable = [c for c in (coupons_result.get("availableCoupons") or []) if c.get("applicabilityStatus") == "APPLICABLE"]
            if applicable:
                best_code = applicable[0]["couponCode"]
                print(f"[coupon] applying {best_code}")
                await call_swiggy_tool("apply_coupon", {"couponCode": best_code})
        except Exception as e:
            print(f"[coupon] not applied (may not be enabled for this account yet): {e}")

        try:
            actual_cart = await call_swiggy_tool("get_cart", {"addressId": address_id})
            print(f"[cart_verify] actual cart contents after update: {actual_cart}")
        except Exception as e:
            print(f"[cart_verify] could not verify cart contents: {e}")
            actual_cart = {}

        return actual_cart


MAX_ORDER_AMOUNT = int(os.environ.get("MAX_ORDER_AMOUNT", "500"))


# Box 7: scheduled orders - fire automatically at a chosen time, only
# within a safe window (never place a real order unattended overnight).
SCHEDULE_WINDOW_START_HOUR = 6   # 6:00 AM
SCHEDULE_WINDOW_END_HOUR = 22    # 10:00 PM
SCHEDULED_ORDERS_FILE = "scheduled_orders.json"

SCHEDULE_TIME_PATTERN = re.compile(r'(\d{1,2})(?::(\d{2}))?\s*(am|pm)?', re.IGNORECASE)


def _load_scheduled_orders() -> list:
    try:
        with open(SCHEDULED_ORDERS_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save_scheduled_orders():
    with open(SCHEDULED_ORDERS_FILE, "w") as f:
        json.dump(SCHEDULED_ORDERS, f)


SCHEDULED_ORDERS = _load_scheduled_orders()


def parse_schedule_time(text: str):
    """
    Pulls a time-of-day out of something like "schedule 8pm" or
    "schedule at 6:30am" and returns the next real datetime it refers
    to (today if that time hasn't passed yet, otherwise tomorrow).
    Returns None if no time-like pattern is found at all.
    """
    match = SCHEDULE_TIME_PATTERN.search(text)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    ampm = (match.group(3) or "").lower()
    if hour > 23 or minute > 59:
        return None
    if ampm == "pm" and hour != 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    now = datetime.now()
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


async def maybe_handle_check_scheduled_command(sender: str, reply_text: str) -> bool:
    """Lets someone check their own pending scheduled orders."""
    normalized = reply_text.strip().lower()
    if "scheduled" not in normalized:
        return False
    mine = [o for o in SCHEDULED_ORDERS if o["sender"] == sender]
    if not mine:
        send_whatsapp_message(sender, "You don't have any scheduled orders right now.")
        return True
    lines = [
        f"- {datetime.fromisoformat(o['fire_time']).strftime('%I:%M %p on %b %d')}: ~Rs.{o['total']} (at today's prices)"
        for o in mine
    ]
    send_whatsapp_message(sender, "⏰ *Your scheduled orders:*\n" + "\n".join(lines))
    return True


async def scheduled_orders_loop():
    """
    Runs forever in the background: every minute, checks for scheduled
    orders whose time has come, and places them for real - re-checking
    actual prices and the spending cap at that moment, since prices can
    drift between when something was scheduled and when it actually
    fires.
    """
    global SHARED_CART
    while True:
        await asyncio.sleep(60)
        now = datetime.now()
        due = [o for o in SCHEDULED_ORDERS if datetime.fromisoformat(o["fire_time"]) <= now]
        for order in due:
            SCHEDULED_ORDERS.remove(order)
            _save_scheduled_orders()

            if not (SCHEDULE_WINDOW_START_HOUR <= now.hour < SCHEDULE_WINDOW_END_HOUR):
                send_whatsapp_message(
                    order["sender"],
                    "⏰ Your scheduled order's time fell outside the 6 AM-10 PM safe window, "
                    "so it wasn't placed automatically. Please reorder manually."
                )
                continue

            try:
                actual_cart = await prepare_real_cart(order["address_id"], order["search_results"])
                breakdown = format_bill_breakdown(actual_cart)
                try:
                    real_total = int(float((actual_cart.get("billBreakdown") or {}).get("toPay", {}).get("value", order["total"])))
                except (TypeError, ValueError):
                    real_total = order["total"]

                if real_total > MAX_ORDER_AMOUNT:
                    send_whatsapp_message(
                        order["sender"],
                        f"⏰ Your scheduled order is ready but now costs *Rs.{real_total}* - above the "
                        f"Rs.{MAX_ORDER_AMOUNT} safety limit, so it wasn't auto-placed. It's in your cart - "
                        "open the app to review and pay, or ask to raise the limit."
                    )
                    SHARED_CART = {
                        "added_by": order["sender"],
                        "added_at": time.time(),
                        "search_results": order["search_results"],
                        "address_id": order["address_id"],
                        "address_label": order["address_label"],
                    }
                    continue

                await call_swiggy_tool("checkout", {"addressId": order["address_id"], "paymentMethod": "Cash"})
                send_whatsapp_message(
                    order["sender"],
                    "⏰✅ *Your scheduled order was placed!*\n\n" + (breakdown or f"Total: Rs.{real_total}")
                    + f"\n📍 Delivering to: {order['address_label']}"
                )
            except Exception as e:
                print(f"[error] scheduled order failed for {order['sender']}: {e}")
                send_whatsapp_message(order["sender"], f"⏰ Sorry, your scheduled order failed to place: {e}\nPlease reorder manually.")


async def handle_fulfillment_choice(sender: str, reply_text: str):
    """
    Interprets the ORDER vs CART reply. Rewritten after a real incident:
    saying "cancel order" contains the literal word "order", which the
    old naive check ("order" in words) matched as CHOOSING the order
    path - placing a real, unwanted checkout. Now:
    - cancel is checked FIRST and is its own real option (there was
      previously no way to back out entirely at this stage at all).
    - ORDER/CART only match on SHORT, unambiguous replies - never a
      longer sentence that merely contains the word "order" somewhere.
    - anything else gets treated as a possible correction (route back
      to item confirmation) rather than just repeating "didn't catch
      that" forever, which is what actually happened here.
    - a hard spending cap blocks real checkout above MAX_ORDER_AMOUNT -
      raise it in .env once you trust this fully.
    """
    global SHARED_CART
    words = {w.strip(string.punctuation) for w in reply_text.strip().lower().split()}
    pending = PENDING_FULFILLMENT_CHOICE[sender]
    is_short = len(words) <= 3
    is_cancel = bool(words & NEGATIVE_REPLIES)

    if is_short and is_cancel:
        send_whatsapp_message(sender, "Okay, cancelled - nothing was ordered or added to cart.")
        del PENDING_FULFILLMENT_CHOICE[sender]
        return

    if "schedule" in reply_text.lower() and not is_cancel:
        fire_time = parse_schedule_time(reply_text)
        if not fire_time:
            send_whatsapp_message(sender, "What time should I schedule this for? e.g. \"schedule 8pm\" or \"schedule 6:30am\".")
            return
        if not (SCHEDULE_WINDOW_START_HOUR <= fire_time.hour < SCHEDULE_WINDOW_END_HOUR):
            send_whatsapp_message(
                sender,
                f"I can only schedule orders between {SCHEDULE_WINDOW_START_HOUR}:00 AM and "
                f"{SCHEDULE_WINDOW_END_HOUR - 12}:00 PM. Please pick a time in that range."
            )
            return
        SCHEDULED_ORDERS.append({
            "sender": sender,
            "fire_time": fire_time.isoformat(),
            "search_results": pending["search_results"],
            "address_id": pending["address_id"],
            "address_label": pending["address_label"],
            "total": pending["total"],
        })
        _save_scheduled_orders()
        day_word = "today" if fire_time.date() == datetime.now().date() else "tomorrow"
        send_whatsapp_message(
            sender,
            f"⏰ Scheduled! I'll place this order automatically at *{fire_time.strftime('%I:%M %p')}* {day_word}.\n"
            f"Estimated total (at today's prices): Rs.{pending['total']}\n\n"
            "Say \"what's scheduled\" to check, or I'll message you once it's placed."
        )
        del PENDING_FULFILLMENT_CHOICE[sender]
        return

    if is_short and "cart" in words and not is_cancel and "order" not in words:
        try:
            actual_cart = await prepare_real_cart(pending["address_id"], pending["search_results"])
            breakdown = format_bill_breakdown(actual_cart)
            send_whatsapp_message(
                sender,
                "🛒 *Added to your cart*\n\n" + (breakdown or f"*Rs.{pending['total']}* total.") + "\n\n"
                "Want to add or change something? Tell me now, before opening the app.\n\n"
                "⚠️ Open the Swiggy app only once, when you're fully done. If you don't pay, clear the cart there in the app."
            )
            SHARED_CART = {
                "added_by": sender,
                "added_at": time.time(),
                "search_results": pending["search_results"],
                "address_id": pending["address_id"],
                "address_label": pending["address_label"],
            }
            LAST_ORDER_ITEMS[sender] = {
                "items": [r["item"] for r in pending["search_results"] if r["status"] == "found"],
                "address_id": pending["address_id"],
                "address_label": pending["address_label"],
            }
        except Exception as e:
            import traceback
            print(f"[error] adding to cart failed: {e}")
            traceback.print_exc()
            send_whatsapp_message(sender, "Sorry, something went wrong adding these to your cart. Please try again.")
        del PENDING_FULFILLMENT_CHOICE[sender]
        return

    if is_short and "order" in words and not is_cancel and "cart" not in words:
        # Don't check out yet - one more explicit confirmation first,
        # showing the order again in full. Added after a near-miss where
        # a chain of small misunderstandings almost triggered a real,
        # unwanted checkout with no final safety gate in between.
        del PENDING_FULFILLMENT_CHOICE[sender]
        PENDING_FINAL_CONFIRM[sender] = pending
        lines, _ = build_order_lines(pending["search_results"])
        send_whatsapp_message(
            sender,
            "🧾 *Final check before placing this order:*\n\n" + "\n".join(lines)
            + f"\n\n💰 *Total: Rs.{pending['total']}*\n📍 {pending['address_label']}\n\n"
            "Reply *ORDER* again to place it for real, *NO* to cancel, or tell me what to change."
        )
        return

    # Not a clear ORDER/CART/cancel - could be a correction attempt
    # ("remove the coriander"). Route it through the same classifier
    # used for item corrections, using this stage's item list.
    try:
        result = classify_pending_reply(pending["search_results"], reply_text)
    except Exception as e:
        print(f"[error] classify_pending_reply failed at fulfillment stage: {e}")
        send_whatsapp_message(sender, "Sorry, I didn't catch that - reply ORDER to place it now, CART to just add the items, or NO to cancel everything.")
        return

    intent = result.get("intent")
    if intent == "cancel":
        send_whatsapp_message(sender, "Okay, cancelled - nothing was ordered or added to cart.")
        del PENDING_FULFILLMENT_CHOICE[sender]
    elif intent == "modify" and result.get("items"):
        # Went back to correcting items - return to the item-confirmation
        # stage instead of staying stuck on the ORDER/CART question.
        del PENDING_FULFILLMENT_CHOICE[sender]
        await search_and_confirm(sender, pending["address_id"], pending["address_label"], result["items"])
    else:
        send_whatsapp_message(sender, "Sorry, I didn't catch that - reply ORDER to place it now, CART to just add the items, or NO to cancel everything.")


CLEAR_CART_PHRASES = {"clear cart", "clear the cart", "empty cart", "empty the cart", "remove everything", "remove all", "clear my cart"}

REPEAT_ORDER_PHRASES = {"repeat the order", "repeat order", "repeat my order", "repeat my last order", "repeat last order", "order the same again", "same order again", "order again"}

CHECK_CART_PHRASES = {"what's in my cart", "whats in my cart", "check my cart", "check cart", "show my cart", "what's in the cart", "cart status", "what's in cart"}


async def maybe_handle_check_cart_command(sender: str, reply_text: str) -> bool:
    """
    Shows the cart contents. Prefers our own SHARED_CART snapshot over a
    fresh get_cart call - real testing showed get_cart itself can return
    an inconsistent, STALE answer even seconds apart from an identical
    previous call (same real cart, two consecutive checks, two different
    answers). Since every successful add already verifies itself via its
    own get_cart check at write-time, that snapshot is more trustworthy
    than asking again later. Falls back to a fresh get_cart only when we
    have no tracked state at all (e.g. after a restart).
    """
    normalized = reply_text.strip().lower()
    if not any(phrase in normalized for phrase in CHECK_CART_PHRASES):
        return False

    if SHARED_CART:
        lines, total = build_order_lines(SHARED_CART["search_results"])
        send_whatsapp_message(
            sender,
            "🛒 *Your cart (as last confirmed):*\n\n" + "\n".join(lines) + f"\n\n💰 *Rs.{total}* (before delivery/handling fees)"
        )
        return True

    # Nothing tracked in memory - fall back to a live check, with an
    # honest caveat given what we now know about get_cart's reliability.
    default_label = ADDRESS_MAP.get(sender, "Home")
    address_id = await resolve_instamart_address_id(default_label, fallback_label=default_label)

    try:
        cart = await call_swiggy_tool("get_cart", {"addressId": address_id})
    except Exception as e:
        print(f"[error] check cart command failed: {e}")
        send_whatsapp_message(sender, "Sorry, couldn't check the cart right now.")
        return True

    items = cart.get("items") or []
    if not items:
        send_whatsapp_message(sender, "🛒 Your cart is currently empty (as far as I can tell right now).")
        return True

    lines = [f'- {i["quantity"]}x {i["itemName"]} ({i.get("itemVariant", "")}) — Rs.{i["discountedFinalPrice"]}' for i in items]
    total = cart.get("cartTotalAmount", "?")
    send_whatsapp_message(
        sender,
        "🛒 *Your cart right now (live check):*\n\n" + "\n".join(lines) + f"\n\n💰 *Total: Rs.{total}*\n\n"
        "_(This is a fresh check, not our own tracked state - if it looks off, ask again in a moment.)_"
    )
    return True


async def maybe_handle_repeat_order_command(sender: str, reply_text: str) -> bool:
    """
    Checks for a 'repeat the order' request. Re-searches and re-confirms
    the last order fresh (through the normal confirmation flow) rather
    than silently re-adding it - prices, stock, or brand availability
    may have changed since last time, so this goes through the same
    safety gates as any other order, just pre-filled with last time's
    items instead of asking you to say them again.
    """
    normalized = reply_text.strip().lower()
    if not any(phrase in normalized for phrase in REPEAT_ORDER_PHRASES):
        return False

    last = LAST_ORDER_ITEMS.get(sender)
    if not last:
        send_whatsapp_message(sender, "You don't have a previous order to repeat yet - tell me what you'd like to order.")
        return True

    print(f"[repeat_order] {sender} repeating: {last['items']}")
    await search_and_confirm(sender, last["address_id"], last["address_label"], last["items"])
    return True


async def handle_final_order_confirm(sender: str, reply_text: str):
    """
    The actual checkout only happens from here - after the order was
    shown once for item confirmation, once more for the ORDER/CART
    choice, and now a third time as a final explicit check. This extra
    stage was added specifically so a real checkout is never one
    ambiguous reply away from happening.
    """
    global SHARED_CART
    words = {w.strip(string.punctuation) for w in reply_text.strip().lower().split()}
    pending = PENDING_FINAL_CONFIRM[sender]
    is_short = len(words) <= 3
    is_cancel = bool(words & NEGATIVE_REPLIES)
    is_confirm = bool(words & AFFIRMATIVE_REPLIES) or "order" in words

    if is_short and is_cancel:
        send_whatsapp_message(sender, "Okay, cancelled - nothing was ordered.")
        del PENDING_FINAL_CONFIRM[sender]
        return

    if is_short and is_confirm and not is_cancel:
        try:
            actual_cart = await prepare_real_cart(pending["address_id"], pending["search_results"])
        except Exception as e:
            import traceback
            print(f"[error] preparing cart before checkout failed: {e}")
            traceback.print_exc()
            send_whatsapp_message(sender, f"Sorry, something went wrong preparing the order: {e}\nNothing was charged - please try again.")
            del PENDING_FINAL_CONFIRM[sender]
            return

        breakdown = format_bill_breakdown(actual_cart)
        try:
            real_total = int(float((actual_cart.get("billBreakdown") or {}).get("toPay", {}).get("value", pending["total"])))
        except (TypeError, ValueError):
            real_total = pending["total"]

        # Check the cap against the REAL total (with fees), not the
        # item-only estimate - fees alone could otherwise push a
        # seemingly-under-cap order over the actual safety limit.
        if real_total > MAX_ORDER_AMOUNT:
            send_whatsapp_message(
                sender,
                f"⚠️ With fees, this order is really *Rs.{real_total}* - above the current safety "
                f"limit of Rs.{MAX_ORDER_AMOUNT}. Nothing was charged, but it's sitting in your cart.\n\n"
                + (breakdown + "\n\n" if breakdown else "")
                + "Open the Swiggy app to pay it yourself, or ask to raise the limit if this is expected."
            )
            SHARED_CART = {
                "added_by": sender,
                "added_at": time.time(),
                "search_results": pending["search_results"],
                "address_id": pending["address_id"],
                "address_label": pending["address_label"],
            }
            del PENDING_FINAL_CONFIRM[sender]
            return

        try:
            checkout_result = await call_swiggy_tool("checkout", {"addressId": pending["address_id"], "paymentMethod": "Cash"})
            print(f"[checkout_result] {checkout_result}")
            send_whatsapp_message(
                sender,
                "✅ *Order placed!*\n\n" + (breakdown or f"Total: *Rs.{real_total}*")
                + f"\n📍 Delivering to: {pending['address_label']}\n\n"
                "You'll get updates from Swiggy directly."
            )
            SHARED_CART = None
            LAST_ORDER_ITEMS[sender] = {
                "items": [r["item"] for r in pending["search_results"] if r["status"] == "found"],
                "address_id": pending["address_id"],
                "address_label": pending["address_label"],
            }
        except Exception as e:
            import traceback
            print(f"[error] checkout failed: {e}")
            traceback.print_exc()
            send_whatsapp_message(sender, f"Sorry, placing the order failed: {e}\nNothing was charged - please try again.")
        del PENDING_FINAL_CONFIRM[sender]
        return

    # Not a clear confirm/cancel - could be a last-second correction.
    try:
        result = classify_pending_reply(pending["search_results"], reply_text)
    except Exception as e:
        print(f"[error] classify_pending_reply failed at final-confirm stage: {e}")
        send_whatsapp_message(sender, "Sorry, I didn't catch that - reply ORDER to confirm, NO to cancel, or tell me what to change.")
        return

    intent = result.get("intent")
    if intent == "cancel":
        send_whatsapp_message(sender, "Okay, cancelled - nothing was ordered.")
        del PENDING_FINAL_CONFIRM[sender]
    elif intent == "modify" and result.get("items"):
        del PENDING_FINAL_CONFIRM[sender]
        await search_and_confirm(sender, pending["address_id"], pending["address_label"], result["items"])
    else:
        send_whatsapp_message(sender, "Sorry, I didn't catch that - reply ORDER to confirm, NO to cancel, or tell me what to change.")


async def maybe_handle_clear_cart_command(sender: str, reply_text: str) -> bool:
    """Checks if the message is a direct 'clear my cart' request - works
    regardless of pending state, since the cart is real and shared and
    someone should always be able to just empty it. Returns True if handled."""
    global SHARED_CART
    normalized = reply_text.strip().lower()
    if not any(phrase in normalized for phrase in CLEAR_CART_PHRASES):
        return False

    async with CART_LOCK:
        try:
            if SHARED_CART:
                address_id = SHARED_CART["address_id"]
            else:
                # Nothing tracked (e.g. cart has items from outside the
                # bot, or from before a restart) - still need a real
                # address to target, or clear_cart repeats the exact
                # no-address bug we already fixed for the tracked case.
                default_label = ADDRESS_MAP.get(sender, "Home")
                address_id = await resolve_instamart_address_id(default_label, fallback_label=default_label)
            await call_swiggy_tool("clear_cart", {})  # takes no parameters, per authoritative docs
            try:
                verify = await call_swiggy_tool("get_cart", {"addressId": address_id})
                print(f"[cart_verify] after clear command: {verify}")
            except Exception as e:
                print(f"[cart_verify] could not verify after clear: {e}")
            send_whatsapp_message(sender, "🗑️ Cart cleared.")
        except Exception as e:
            print(f"[error] clear cart command failed: {e}")
            send_whatsapp_message(sender, "Sorry, something went wrong clearing the cart.")
    SHARED_CART = None
    # A real "clear cart" means abandon whatever's in progress too, not
    # just the real Swiggy cart.
    PENDING_ORDERS.pop(sender, None)
    PENDING_FULFILLMENT_CHOICE.pop(sender, None)
    PENDING_FINAL_CONFIRM.pop(sender, None)
    return True


async def abandoned_cart_cleanup_loop():
    """Runs forever in the background: every 5 minutes, checks if the
    shared cart was added via the CART path but never paid for within
    the timeout, and clears it so nothing stale lingers indefinitely."""
    global SHARED_CART
    while True:
        await asyncio.sleep(300)
        if SHARED_CART and time.time() - SHARED_CART["added_at"] > ABANDONED_CART_TIMEOUT_SECONDS:
            notify = SHARED_CART["added_by"]
            try:
                async with CART_LOCK:
                    await call_swiggy_tool("clear_cart", {})  # takes no parameters, per authoritative docs
                send_whatsapp_message(notify, "Your cart items were cleared since the order wasn't completed in time.")
            except Exception as e:
                print(f"[error] abandoned cart cleanup failed: {e}")
            SHARED_CART = None


@app.on_event("startup")
async def start_background_tasks():
    asyncio.create_task(abandoned_cart_cleanup_loop())
    asyncio.create_task(scheduled_orders_loop())


def _cart_is_stale() -> bool:
    if not SHARED_CART:
        return True
    return time.time() - SHARED_CART["added_at"] > ABANDONED_CART_TIMEOUT_SECONDS


async def handle_new_order_request(sender: str, transcript_or_text: str):
    """The Box 2-4 flow: parse -> resolve address -> search -> send confirmation.

    If there's already a non-stale shared cart (added via CART, by
    ANYONE in the family), a new message is treated as an ADDITION to
    that same order/address rather than an unrelated new order - this
    also keeps everything on the correct address, and prevents two
    people's orders from silently clobbering each other."""
    global SHARED_CART
    try:
        parsed = parse_request(transcript_or_text)
        print(f"[parsed] {parsed}")

        if parsed.get("needs_clarification"):
            send_whatsapp_message(sender, parsed.get("clarification_question", "Could you tell me what you'd like to order?"))
            return

        if not _cart_is_stale() and SHARED_CART["added_by"] == sender:
            cart_info = SHARED_CART
            SHARED_CART = None
            existing_items = [r["item"] for r in cart_info["search_results"] if r["status"] == "found"]
            merged_items = existing_items + parsed["items"]
            print(f"[cart_merge] {sender} adding to their own recent cart: {parsed['items']}")
            await search_and_confirm(sender, cart_info["address_id"], cart_info["address_label"], merged_items)
            return

        if not _cart_is_stale() and SHARED_CART["added_by"] != sender:
            # A DIFFERENT person's items are already sitting in the cart -
            # don't silently blend a new person's order into them. Let
            # them know, then proceed independently - confirming this new
            # order will replace what's there once it reaches CART/ORDER.
            send_whatsapp_message(sender, "Note: there were items already in the cart from someone else's order - starting fresh with yours.")
            print(f"[cart_override] {sender} starting a new order, replacing cart previously added by {SHARED_CART['added_by']}")
            SHARED_CART = None

        default_label = ADDRESS_MAP.get(sender)
        address_label = resolve_address(sender, parsed.get("address_override"))
        try:
            address_id = await resolve_instamart_address_id(address_label, fallback_label=default_label)
            await search_and_confirm(sender, address_id, address_label, parsed["items"])
        except RuntimeError as e:
            print(f"[error] Swiggy call failed: {e}")
            send_whatsapp_message(sender, f"(Box 4 test) Swiggy connection issue: {e}")
    except Exception as e:
        import traceback
        print(f"[error] processing failed: {e}")
        traceback.print_exc()
        send_whatsapp_message(sender, "Sorry, I couldn't process that voice note. Try again?")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)