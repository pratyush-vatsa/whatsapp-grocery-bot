# Security Policy

This project connects to a real WhatsApp Business number, a real
Swiggy account, and third-party AI APIs (Groq, Google Gemini). Given
that, security reports are taken seriously even though this started
as a personal project.

## Reporting a Vulnerability

**Do not open a public issue for security vulnerabilities.**

Instead, email **pratyushvatsa11@gmail.com** directly with:
- A description of the vulnerability and its potential impact
- Steps to reproduce it, if possible
- Any relevant logs — with phone numbers, addresses, and tokens
  redacted first

You should expect an initial response within a few days. This is a
personal project maintained by one person, not a company with a
dedicated security team, so response times will reflect that.

## Scope

Given the nature of this project, the following are of particular
interest:
- Anything that could let a message from a non-allowlisted phone
  number trigger a real order or cart action
- Anything that could expose the Swiggy OAuth token, WhatsApp access
  token, or Gemini/Groq API keys
- Anything that could bypass the spending cap or double-confirmation
  safety checks before a real checkout
- Anything that could leak one family member's order/address data to
  another unintended party

## Known, Accepted Risks

Documented here rather than treated as new reports:
- The bot's `ALLOWED_NUMBERS` allowlist is the sole gate on who can
  place real orders — this is by design for a small, trusted family
  deployment, and isn't intended to scale to open/public use.
- Local state files (`swiggy_token.json`, `.env`) contain real
  secrets and are `.gitignore`'d — never commit them, and treat any
  copy of them as sensitive.
- Swiggy's OAuth token has no refresh mechanism in the current API
  and is manually rotated roughly every 5 days — this is a Swiggy API
  limitation, not something this project can fix client-side.

## Supported Versions

This is a single, continuously-deployed personal project — only the
latest version on `main` is supported. There is no version branching
or backporting of fixes.
