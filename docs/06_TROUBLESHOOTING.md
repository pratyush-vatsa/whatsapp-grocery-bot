# 6. Troubleshooting — Real Issues Found During Development

Everything here actually happened during testing, not hypothetical
edge cases. Documented so you don't have to rediscover them.

## The Swiggy app shows different cart contents than the bot reports

**This is a real, confirmed Swiggy-side issue, not a bug in this
code.** Extensively tested: every `get_cart` call made immediately
after `update_cart`/`clear_cart` has been correct, every single time,
verified dozens of times. But a *separate* read — the app, or even
this bot's own "what's in my cart" — can occasionally return a stale
or inconsistent snapshot, sometimes just seconds later.

**What causes it (best evidence, not fully confirmed by Swiggy):**
Swiggy's own MCP documentation explicitly warns: *"Do not open the
Swiggy app while using these MCP integrations — using both
simultaneously may cause session conflicts."* Testing strongly
suggests this is accurate, and may extend to any closely-spaced
reads/writes against the cart, not just app-vs-bot specifically.

**What to actually do:**
- Add everything via the bot in one continuous run — don't check the
  app or "what's in my cart" in between.
- Check only once, at the very end.
- Once you open the Swiggy app: either pay there, or clear the cart
  *using the app's own option* — don't come back to WhatsApp to clear
  it, since a command right after closing the app can be unreliable
  too.
- See `swiggy_bug_report.txt` for the full report filed with Swiggy,
  including specific `cartId` values and timestamps proving the
  inconsistency at the API level, not just the app.

## Field names Swiggy's tools actually expect (verified against authoritative docs)

Several of these were initially guessed wrong by pattern-matching
across tools, and had to be corrected after real failures:

| Tool | Correct parameter |
|---|---|
| `update_cart` | `selectedAddressId` |
| `checkout` | `addressId` (different from `update_cart`!) |
| `clear_cart` | **no parameters at all** — just `{}` |
| `checkout` payment method | `"Cash"` or `"UPI"` — **never** `"COD"` (not a real value; was being silently ignored) |

**Lesson:** never assume two Swiggy tools share a parameter
convention just because they're related. When in doubt, fetch the
authoritative schema directly:
`https://mcp.swiggy.com/builders/docs/reference/instamart/<tool_name>.md`

## Swiggy also enforces its own checkout cap

Independent of `MAX_ORDER_AMOUNT` in this project, Swiggy's own
`checkout` tool refuses orders above roughly ₹1000. Not something to
rely on as your primary safety net, but worth knowing it exists as a
backup.

## The quantity-vs-size parsing bug (the most dangerous one found)

Early versions used a single "quantity" field for both a pack count
*and* a weight/volume description. Saying "100g coriander" was
parsed as quantity=100 (100 packs), not 1 pack sized ~100g — turning
a ~₹50 order into a ~₹5000 one before it was caught.

**Fixed by:** splitting the schema into `quantity` (always a pack
count, default 1) and `size` (a weight/volume hint used only to pick
the closest matching pack, never a multiplier). Both `parse_request()`
*and* `classify_pending_reply()` needed this fix — they're separate
prompts, and fixing only one while forgetting the other let the bug
reappear during corrections.

## A near-miss checkout triggered by "cancel order"

The fulfillment-choice stage used to check `"order" in words` to
detect the ORDER choice. Saying "cancel order" — trying to cancel —
contains the literal word "order", and got misread as *choosing* the
ORDER path, nearly placing an unwanted real order.

**Fixed by:** requiring short, unambiguous replies for ORDER/CART,
checking for a cancel word *first*, and adding a dedicated cancel
path at every stage (there previously wasn't one once past item
confirmation).

## Fast-path yes/no misfiring on long corrections

A "fast path" meant for short one-word replies (bare "yes"/"no") was
also matching on any message that merely *contained* a negative word
anywhere — so a long correction sentence containing "nahi" partway
through got treated as a full cancellation.

**Fixed by:** only trusting the fast path for genuinely short replies
(≤3 words); anything longer always goes through full AI
classification instead.

## Token expiry

Swiggy's OAuth access token lasts ~5 days with no refresh token in
their current API. `load_swiggy_token()` raises a clear error when
it's expired — the fix is always just re-running `swiggy_login.py`
(locally, then updating `SWIGGY_TOKEN_JSON` if hosted).

## If a WhatsApp webhook message seems to arrive from nowhere

Message deduplication (`PROCESSED_MESSAGE_IDS`) is in-memory only and
resets on every restart. A delayed webhook retry from before a
restart can occasionally get processed as if brand new, replaying an
old message's entire pipeline. Rare, and generally harmless — reply
NO if it produces an unwanted confirmation.
