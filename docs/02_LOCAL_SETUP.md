# 2. Running Locally

This gets the bot running on your own machine — good for development
and testing before you host it properly (see
[03_DEPLOYMENT.md](03_DEPLOYMENT.md)).

## Prerequisites

- Python 3.10+
- A completed [Meta/WhatsApp setup](01_META_WHATSAPP_SETUP.md)
- A Google AI Studio account (for Gemini) — free tier is enough
- A Groq account (for Whisper transcription) — free tier is enough
- A Swiggy account with Instamart access, on the Swiggy Builders Club
  MCP program (see the note at the bottom of this file)
- [ngrok](https://ngrok.com/download) (or any tunnel tool) — lets
  Meta's servers reach your laptop during local testing

## Step-by-step

### 1. Install dependencies

```bash
cd whatsapp-grocery-bot
pip install -r requirements.txt --break-system-packages
```

(Drop `--break-system-packages` if you're using a virtual environment,
which is recommended: `python3 -m venv venv && source venv/bin/activate`
first.)

### 2. Set up your `.env` file

```bash
cp .env.example .env
```

Open `.env` and fill in every value:

```
VERIFY_TOKEN=pick_any_random_string_yourself
WHATSAPP_TOKEN=<from Meta setup, step 11>
PHONE_NUMBER_ID=<from Meta setup, step 12>
ALLOWED_NUMBERS=91XXXXXXXXX1,91XXXXXXXXX2
GEMINI_API_KEY=<from aistudio.google.com>
GROQ_API_KEY=<from console.groq.com>
ADDRESS_MAP=91XXXXXXXXX1:Home,91XXXXXXXXX2:Home
MAX_ORDER_AMOUNT=500
```

Notes:
- `ALLOWED_NUMBERS` and `ADDRESS_MAP` are comma-separated — add one
  entry per family member you want to allow.
- Numbers have no `+` and no spaces (e.g. `91XXXXXXXXX1`, not
  `+91 XXXXXXXXX1`).
- `MAX_ORDER_AMOUNT` blocks real checkout above this rupee amount.
  Keep it low (like the default 500) until you fully trust the bot.

### 3. Log into Swiggy (one-time, then every ~5 days)

```bash
python3 swiggy_login.py
```

This opens a browser for you to log into Swiggy (phone + OTP). It
saves the resulting token to `swiggy_token.json`, valid for about 5
days — Swiggy's API has no automatic refresh yet, so you'll need to
re-run this periodically. `main.py` will tell you clearly (via a
failed message) if the token has expired.

### 4. Start the bot

```bash
python3 main.py
```

You should see:
```
INFO:     Uvicorn running on http://0.0.0.0:8000
```

Keep this terminal open — this is your live server, and its output
is the most useful debugging tool you have (every message, search,
and Swiggy API call gets logged here).

### 5. Start a tunnel (second terminal)

```bash
ngrok http 8000
```

Copy the `https://....ngrok-free.app` URL it prints.

### 6. Connect it to Meta

1. Meta dashboard → WhatsApp → Configuration → Webhook.
2. Callback URL: `https://<your-ngrok-url>/webhook`
3. Verify token: the exact same string as `VERIFY_TOKEN` in your `.env`.
4. Click **"Verify and Save"**.
5. Subscribe to the **"messages"** webhook field (checkbox near the
   callback URL settings).

### 7. Test it

From an allowed WhatsApp number, message the bot number — try
something like "1 kg onions". Watch terminal 1 for `[received]`,
`[parsed]`, and `[send]` log lines, and check your phone for the
reply.

## If step 6 (Verify and Save) fails

Almost always one of:
- The verify token doesn't match `VERIFY_TOKEN` in `.env` exactly.
- Your server (terminal 1) wasn't running when you clicked it.
- ngrok's URL changed since you last copied it (it changes every time
  you restart ngrok on the free tier) — copy it fresh and re-save.

## A note on Swiggy access

This project uses the **Swiggy Builders Club MCP** — a program for
building agents/bots against Swiggy's API. Production access
(placing real orders reliably, at scale) is invite-based; check
Swiggy's Builders Club documentation for current onboarding steps if
you don't already have access. For personal/family use at low
volume, the developer flow used by `swiggy_login.py` is normally
sufficient without needing enterprise onboarding.
