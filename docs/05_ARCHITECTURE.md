# 5. Architecture

Written for whoever maintains this next — including future-you six
months from now.

## The pipeline, end to end

```
WhatsApp voice/text
  → Meta Cloud API webhook (POST /webhook)
  → Box 2: transcription (Groq Whisper, always-English output)
  → Box 3: parsing (Gemini) → structured {items, address_override, ...}
  → Box 4: Instamart search (Swiggy MCP) → priced, matched products
  → Box 5: confirmation loop (YES/NO/modify)
  → Box 6: fulfillment choice (ORDER / CART / SCHEDULE)
  → Box 7: real cart update, checkout, or scheduled firing
```

## State machine

Everything is keyed by sender (phone number), except the cart itself,
which is genuinely shared (see below). A message is routed based on
which dict the sender currently appears in:

| State dict | Meaning | Set by | Cleared by |
|---|---|---|---|
| `PENDING_ORDERS` | Items searched, waiting for YES/NO/modify | `search_and_confirm()` | confirming or cancelling |
| `PENDING_FULFILLMENT_CHOICE` | Confirmed, waiting for ORDER/CART/SCHEDULE | `ask_fulfillment_choice()` | choosing one |
| `PENDING_FINAL_CONFIRM` | Chose ORDER, waiting for the final double-check | `handle_fulfillment_choice()` | confirming or cancelling |

Routing happens once, at the top of `process_message()` — global
commands (help, clear cart, what's in my cart, repeat order, what's
scheduled) are checked **before** any state-based routing, so they
work regardless of what's currently pending.

## Why the cart is a single shared object, not per-sender

The whole family uses **one** Swiggy account — there is genuinely
only one real cart, no matter who's talking to the bot. Pretending
each phone number has its own isolated cart was an earlier bug: two
people ordering close together could silently overwrite each other's
items. `SHARED_CART` (a single global, not a dict keyed by sender)
reflects this reality. A new order from a *different* sender than
whoever's cart is currently sitting there does **not** silently
merge — it warns and starts fresh, since merging two unrelated
people's orders would be worse than replacing.

`CART_LOCK` (an `asyncio.Lock`) serializes every real cart-touching
API call, so two near-simultaneous requests can't interleave their
`clear_cart`/`update_cart` calls against the one real cart.

## Persistence

Three small local JSON files carry state across restarts:
- `welcomed_senders.json` — who's already seen the one-time welcome
- `scheduled_orders.json` — pending scheduled orders
- `swiggy_token.json` — the Swiggy OAuth token (or `SWIGGY_TOKEN_JSON`
  env var when hosted — see [03_DEPLOYMENT.md](03_DEPLOYMENT.md))

Everything else (`PENDING_ORDERS`, `SHARED_CART`, etc.) is in-memory
only and resets on restart — acceptable for personal-scale use, since
a restart mid-confirmation just means re-sending the order once.

## Safety layers, in the order they actually run

1. **Item confirmation** — nothing happens to the real cart until you
   say YES.
2. **Fulfillment choice** — ORDER/CART/SCHEDULE, not automatic.
3. **Real-total spending cap** (`MAX_ORDER_AMOUNT`) — checked against
   the *actual* total including fees (from `get_cart`'s
   `billBreakdown`), not the item-only estimate, right before
   checkout.
4. **Final double-confirmation** for ORDER — the whole order is shown
   again, and requires a second explicit "ORDER" before checkout
   actually fires.
5. **Verification, not blind trust** — every cart-mutating operation
   (`prepare_real_cart()`) calls `get_cart` afterward and logs the
   real result, rather than trusting `update_cart`'s own "success"
   response (which has been observed to be wrong — see
   [06_TROUBLESHOOTING.md](06_TROUBLESHOOTING.md)).

## Key functions, if you're reading the code cold

- `call_swiggy_tool()` — the one place that talks to Swiggy's MCP
  server; handles the two different response shapes Swiggy returns
  and raises clearly on real errors.
- `parse_request()` / `classify_pending_reply()` — two separate
  Gemini prompts (initial parse vs. correction) that must be kept in
  sync if you change the item schema — this has bitten us before.
- `pick_best_variant()` — decides which pack size to actually buy
  when a product has multiple variants (e.g. 500ml vs 1L milk).
- `prepare_real_cart()` — the single choke point for every real cart
  write: clear → add items → try a coupon → verify via `get_cart`.
- `format_bill_breakdown()` — turns Swiggy's real fee breakdown into
  a readable message.
