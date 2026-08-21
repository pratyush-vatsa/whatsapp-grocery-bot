# Contributing

Thanks for taking a look at this project. It started as a personal
tool (a WhatsApp grocery bot for family use), so contribution here
looks a little different from a typical open source project — a few
notes before you dive in.

## Before you start

This project talks to a **real Swiggy account and real WhatsApp
Business number**. If you're experimenting locally, always use your
own credentials (see [docs/02_LOCAL_SETUP.md](docs/02_LOCAL_SETUP.md))
— never the maintainer's. Nothing in this repo should ever be run
against someone else's live account without explicit permission.

## Reporting a bug

Open an issue using the bug report template. Include:
- What you did, what you expected, what actually happened
- Relevant log lines if you have them (redact phone numbers,
  addresses, and tokens first — see the privacy note below)
- Your Python version and where you're running it (local / Render /
  elsewhere)

## Suggesting a feature

Open an issue using the feature request template. A short explanation
of the use case helps more than a fully-specified implementation —
this project has a specific philosophy (voice-first, safety nets
before speed, no surprise charges) and new features get weighed
against that.

## Submitting a change

1. Fork the repo, create a branch off `main`.
2. Keep changes focused — one logical change per pull request.
3. If you touch anything in the order/checkout/cart path, explain in
   the PR description exactly what you tested and how (this codebase
   has a strong bias toward verifying real API behavior over assuming
   it, given several past bugs traced back to unverified assumptions
   about Swiggy's API shape).
4. Match the existing code style — see
   [docs/05_ARCHITECTURE.md](docs/05_ARCHITECTURE.md) for how the
   codebase is organized.
5. Open the PR against `main` using the pull request template.

## A privacy note for anyone sharing logs or examples

Never include real phone numbers, home addresses, or API tokens in an
issue, PR, or commit — even redacted-looking ones. If you're unsure
whether something is sensitive, leave it out and describe it in
general terms instead (see how `swiggy_bug_report.txt` in this repo
handles this, as a real example).

## Code of conduct

This project follows the [Code of Conduct](CODE_OF_CONDUCT.md).
Participation means agreeing to keep interactions respectful and
constructive.
