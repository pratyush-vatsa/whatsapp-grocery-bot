# 1. Meta / WhatsApp Business API Setup

Do this first — nothing else in this project works without a WhatsApp
Business number and API credentials from Meta.

## Part A — Create the app and get a number

1. Go to **developers.facebook.com** → "Get Started" → log in with a
   Facebook account.
2. Verify your developer account (email/phone OTP) if prompted.
3. **"My Apps"** → **"Create App"** → choose **"Business"** (or
   "Other" → "Business") → give it a name, e.g. "Family Grocery Bot"
   → **"Create App"**.
4. On the app dashboard, find the **"WhatsApp"** tile under "Add
   products to your app" → click **"Set up"**.
5. Left sidebar → **"WhatsApp"** → **"API Setup"**. This page is your
   control center for everything below.

## Part B — Use your own number instead of the free test number

The free number Meta gives you by default only works for numbers you
manually allow, and looks unfamiliar to family members. Better to use
a real number:

6. On the API Setup page, click **"Add phone number"**.
7. Enter a WhatsApp Business Display Name (e.g. "Family Grocery
   Bot"), category, and business description → Next.
8. Select country code, enter the number. **It must not already have
   an active WhatsApp account on it** — use a spare SIM if needed.
9. Verify via the OTP (SMS or call) → Next.
10. This number now appears in the "From" dropdown on the API Setup
    page. The display name shows publicly once Meta's review
    approves it (usually quick).

## Part C — Get your credentials

11. Click **"Generate access token"** — this is temporary (24h),
    which is fine for local development. (For a permanent token,
    see the note at the bottom of this file.)
12. Copy the **Phone Number ID** shown on this same page — you'll
    need it for `.env`.
13. Under "Send and receive messages" → "To" field → **"Manage
    phone number list"** → add your own number, and every other
    family member's number you want to allow → each gets a WhatsApp
    OTP to verify.

    **Important:** any number not added here simply won't reach your
    bot at all. If you add a family member later, repeat this step
    for their number too.

## What you'll need from this section

By the end of this page you should have:
- A **Phone Number ID**
- A **temporary (or permanent) access token**
- A verified WhatsApp Business number, with every family member's
  personal number added to the allowed "To" list

These go into your `.env` file — see
[02_LOCAL_SETUP.md](02_LOCAL_SETUP.md) next.

## Getting a permanent access token (optional, recommended before real use)

The temporary token from step 11 expires in 24 hours — fine for
testing, annoying for anything longer. To get a permanent one:

1. Meta dashboard → your app → **"App Settings"** → **"Business
   Settings"**.
2. Create a **System User** with admin access to your app.
3. Generate a token for that system user with the `whatsapp_business_messaging`
   and `whatsapp_business_management` permissions, no expiration.
4. Use that token as `WHATSAPP_TOKEN` in your `.env` instead of the
   temporary one.
