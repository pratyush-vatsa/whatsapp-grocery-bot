# 4. Bot Commands — What You Can Say

This is written for the person actually using the bot day to day, not
for developers. Share it (or just tell them to say "help") with
anyone new to the bot.

## Ordering

Just say what you want, by voice note or text, in Hindi, Hinglish, or
English:

> "1 kg onions, 2 packets Amul milk, 1 litre Sprite"

The bot will search for each item, show you what it found with real
prices, and ask you to confirm.

## At confirmation ("Reply YES to confirm...")

- **YES** — items are correct, move to the next step
- **NO** — cancel this order
- Anything else — tell it what to change: "remove the milk", "add
  1 kg tomatoes", etc.

## Choosing how to finalize ("Reply ORDER, CART, or...")

- **ORDER** — place it for real right now (blocked automatically if
  the total is above the safety limit, currently ₹500)
- **CART** — just add it to your Swiggy cart, so you can review and
  pay yourself in the app
- **"schedule 8pm"** (or any time) — place it automatically later,
  only between 6:00 AM and 10:00 PM

If you choose **ORDER**, you'll be asked to confirm **one more time**
with the final total before it actually places the order — a
deliberate extra safety step.

## Commands that work anytime, in any state

| Say this | What happens |
|---|---|
| **"help"** / "info" / "commands" | Shows the quick guide again — works anytime, even if you've cleared your chat history |
| **"what's in my cart"** | Shows what's really in your Swiggy cart right now |
| **"clear cart"** | Empties the real Swiggy cart |
| **"repeat the order"** | Re-runs your last order's items fresh (rechecking prices/stock) and asks you to confirm |
| **"what's scheduled"** | Shows any orders you've scheduled for later |

## The one important habit to know

**Add everything you want first, then open the Swiggy app only once,
at the very end.** Checking the app (or asking the bot "what's in my
cart") repeatedly while still adding items can cause things to not
save correctly — this is a known Swiggy-side quirk, documented in
[06_TROUBLESHOOTING.md](06_TROUBLESHOOTING.md).

**Once you're in the Swiggy app:** either pay right there, or if you
decide not to, clear the cart using the app's own option — don't come
back to WhatsApp to clear it, since having the app open at the same
time can make the bot's commands unreliable until you close it.

## Sharing this with a new family member

1. Have them add the bot's number as a contact.
2. Send it any message — a one-time welcome message with these tips
   will appear automatically on their first message.
3. They can always say **"help"** to see it again.
