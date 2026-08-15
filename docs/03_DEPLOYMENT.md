# 3. Deploying to the Cloud

Running locally means the bot only works while your laptop is on and
ngrok is running. This gets it running continuously, without
depending on any of your own devices.

## The honest state of "free" hosting in 2026

Platforms change their pricing often — this was last verified in
August 2026. **Railway and Fly.io have both removed their free
tiers** — Railway now gives a one-time $5 trial then requires payment;
Fly.io removed free compute entirely in 2024. If you're reading this
later, verify current pricing before assuming either is free.

**Render's free tier is still genuinely free forever** (no credit
card, no time limit) — but web services sleep after ~15 minutes of
inactivity, waking with a 30-50 second delay on the next request.
Since this bot has a background scheduler that needs to keep running
continuously (for the "schedule order" feature), sleeping is a real
problem, not just a slow first response.

Two real ways to actually solve this for free:

- **Option A — Render + a free keep-alive ping.** Zero cost, zero
  infrastructure to manage, minor reliance on a third-party pinger
  staying up. Good default choice.
- **Option B — Oracle Cloud "Always Free" tier.** A genuine always-on
  VM with no sleep at all, free indefinitely — but you manage the
  server yourself (Linux, systemd, HTTPS), and ARM instance capacity
  can be hard to get in some regions. More effort, more control.

Both are covered below. If you'd rather just pay a few dollars a
month for simplicity, Railway's Hobby plan ($5/mo) or a small VPS
(Hetzner, DigitalOcean, ~$4-6/mo) are perfectly reasonable
alternatives.

---

## Option A: Render (free) + keep-alive ping

### Step 1 — Push the code to a private GitHub repo

Make sure `.gitignore` is in place first (it already is in this repo)
— it keeps `.env`, `swiggy_token.json`, and local state files out of
version control. **Never commit real secrets.**

```bash
git init
git add .
git commit -m "Initial commit"
```

Create a **private** repo on github.com (the `+` icon top-right →
"New repository" → check "Private" → don't initialize with a
README, since you already have one). GitHub will show you commands
like these — run them:

```bash
git remote add origin https://github.com/YOUR_USERNAME/whatsapp-grocery-bot.git
git branch -M main
git push -u origin main
```

If prompted for credentials, GitHub wants a personal access token,
not your password — it'll walk you through generating one on first
push.

### Step 2 — Deploy on Render

1. Go to **render.com** → sign up (GitHub login is easiest, no
   credit card required for the free tier).
2. **"New +"** → **"Web Service"** → connect your GitHub repo.
3. Runtime: **Python 3**. Build command: `pip install -r requirements.txt`.
   Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
   (Render's `Procfile` support works too, since one's already in
   this repo).
4. Instance type: **Free**.
5. Click **"Create Web Service"** — it'll fail on the first deploy,
   which is expected, since environment variables aren't set yet.

### Step 3 — Add environment variables

In the Render dashboard: your service → **"Environment"** tab → add
every value from your local `.env`:

```
VERIFY_TOKEN=your_value
WHATSAPP_TOKEN=your_value
PHONE_NUMBER_ID=1184578861416430
ALLOWED_NUMBERS=91XXXXXXXXX1,91XXXXXXXXX2
GEMINI_API_KEY=your_value
GROQ_API_KEY=your_value
ADDRESS_MAP=91XXXXXXXXX1:Home,91XXXXXXXXX2:Home
MAX_ORDER_AMOUNT=500
```

### The Swiggy token needs one extra step

`swiggy_token.json` is gitignored (correctly — it's a secret) and
won't exist on a fresh server. Instead, open your local
`swiggy_token.json`, copy its entire contents, and add it as one more
environment variable:

```
SWIGGY_TOKEN_JSON={"access_token":"...","expires_at":1234567890}
```

`main.py`'s `load_swiggy_token()` checks this env var first, and
falls back to the local file — so this works identically whether
hosted or local.

### Step 4 — Point the webhook at Render instead of ngrok

1. Once deployed, copy your Render URL (looks like
   `https://your-app.onrender.com`).
2. Meta dashboard → WhatsApp → Configuration → Webhook.
3. Replace the ngrok URL with `https://your-app.onrender.com/webhook`.
4. Click "Verify and Save".
5. Send a test message. Check Render's **"Logs"** tab to watch it
   arrive.

You can turn off ngrok and your local server for good at this point.

### Step 5 — Set up the keep-alive ping (the part that actually matters)

Without this, the app sleeps after 15 minutes idle and your
background scheduler stops running until the next message wakes it.

1. Go to **cron-job.org** (free, no credit card) → sign up.
2. Create a new cron job:
   - URL: `https://your-app.onrender.com/health`
   - Schedule: every **10 minutes**
   - Method: GET
3. Save it. That's it — this endpoint (`/health`, already in `main.py`)
   just returns `{"status": "ok"}`, doing nothing else, purely to
   keep the process alive.

Your app will now genuinely never sleep, for $0/month total.

---

## Option B: Oracle Cloud "Always Free" (a real always-on VM, no sleep)

More setup, but a proper server with no external dependency on a
pinger, and no PaaS-style limits.

1. Sign up at **oracle.com/cloud/free** (requires a credit card for
   identity verification — it will not be charged unless you
   explicitly upgrade).
2. Create a compute instance: choose the **"Always Free eligible"**
   shape — either an Ampere A1 (ARM) instance, or if that region is
   out of capacity (a common, known issue — just try a different
   availability domain or region), fall back to the
   **VM.Standard.E2.1.Micro** shape, which is more reliably
   available.
3. Choose Ubuntu as the OS image. Note the public IP it gives you.
4. SSH in, then:
   ```bash
   sudo apt update && sudo apt install -y python3-pip git
   git clone https://github.com/YOUR_USERNAME/whatsapp-grocery-bot.git
   cd whatsapp-grocery-bot
   pip install -r requirements.txt --break-system-packages
   ```
5. Create your `.env` file directly on the VM (`nano .env`, paste in
   your values) and your `swiggy_token.json` the same way.
6. Set it up as a **systemd service** so it survives reboots and
   crashes:
   ```bash
   sudo nano /etc/systemd/system/grocerybot.service
   ```
   ```ini
   [Unit]
   Description=WhatsApp Grocery Bot
   After=network.target

   [Service]
   WorkingDirectory=/home/ubuntu/whatsapp-grocery-bot
   ExecStart=/usr/bin/python3 -m uvicorn main:app --host 0.0.0.0 --port 8000
   Restart=always
   User=ubuntu

   [Install]
   WantedBy=multi-user.target
   ```
   ```bash
   sudo systemctl enable grocerybot
   sudo systemctl start grocerybot
   ```
7. Open port 8000 in Oracle's security list (or put nginx + a free
   Let's Encrypt certificate in front of it, since Meta requires
   HTTPS for the webhook — this is the extra step Render/Railway
   handle for you automatically).
8. Point the Meta webhook at your VM's HTTPS URL, same verification
   step as always.

---

## The one recurring manual task, on either option

Every **~5 days**, Swiggy's access token expires (no auto-refresh in
their current API):

1. On your own machine, run `python3 swiggy_login.py` again.
2. Copy the new contents of `swiggy_token.json`.
3. Update the `SWIGGY_TOKEN_JSON` variable (Render) or the file on
   your VM (Oracle) with the new value.

This can't be fully automated given Swiggy's current auth design (no
refresh tokens) — everything else runs on its own once deployed.

## A caveat on local state files either way

Two small local files (`welcomed_senders.json`, `scheduled_orders.json`)
live on disk. On Render's free tier, disk is **not persistent across
deploys** — they'll reset if you redeploy. On Oracle's VM, they
persist normally since it's a real, permanent filesystem. For a
family-scale bot this is a minor, occasional inconvenience on
Render, not a functional blocker.
