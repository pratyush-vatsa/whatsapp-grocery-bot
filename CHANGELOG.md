# Changelog

All notable changes to this project are documented here. Format
loosely follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- Industry-standard repo scaffolding: MIT license, contribution
  guidelines, code of conduct, security policy, issue/PR templates,
  and basic CI.

## [0.3.0]

### Added
- Address-change support in the correction system ("deliver to
  Ayush" as a follow-up now works, not just in the original message)
- Address matching now also checks the recipient name in a saved
  address's line, not just its tag - fixes a real case where renaming
  an address in the Swiggy app didn't update the tag field the API
  returns
- Cancel/edit commands for scheduled orders
- Stock notifications ("notify me when X is back in stock")
- Timezone-aware scheduling (fixed a real bug where scheduled times
  were being interpreted in the server's UTC time instead of IST)
- Operating-hours gate (6 AM-12 AM) for real order/cart actions,
  correctly scoped so searching, confirming, and scheduling still
  work at any hour

### Fixed
- The AI correction classifier no longer misreads a restated item
  detail (e.g. reading back a price) as a confirmation
- Real fee breakdown now shown before the final order confirmation,
  not just after
- A Swiggy auth failure (401) no longer gets mislabeled as a generic
  "couldn't process that voice note" error

### Removed
- Automatic checkout retry-on-failure (added, then explicitly removed
  per product decision - see docs/06_TROUBLESHOOTING.md)

## [0.2.0]

### Added
- Multi-user shared cart handling (same account, multiple family
  members) with staleness detection and clear-on-conflict behavior
- 30-minute abandoned cart auto-clear
- Real-time price breakdown display (item total, fees, GST) before
  committing
- Double-confirmation safety gate before real checkout
- Spending cap checked against the real total, not an estimate
- Always-available commands: help, clear cart, check cart, repeat
  order

### Fixed
- "Cancel order" no longer misfires as a checkout confirmation
- Quantity vs. size parsing ("100g" no longer parsed as 100 units)

## [0.1.0]

### Added
- Initial WhatsApp <-> Groq Whisper <-> Gemini <-> Swiggy Instamart
  MCP integration
- Voice and text ordering in Hindi/Hinglish/English
- Basic item search, confirmation, and real checkout flow
