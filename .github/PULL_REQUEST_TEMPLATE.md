## What does this change?

A short description of what this PR does and why.

## How was this tested?

Be specific — especially if this touches search, cart, checkout, or
scheduling. This codebase has a strong bias toward verifying real API
behavior rather than assuming it (several past bugs traced back to
unverified assumptions about Swiggy's response shapes). If you tested
against a real Swiggy account, say so; if you only checked syntax or
logic, say that too.

- [ ] Ran locally against a real WhatsApp/Swiggy sandbox or account
- [ ] Checked `python3 -c "import ast; ast.parse(open('main.py').read())"` passes
- [ ] Verified this doesn't touch the checkout/spending-cap safety
      logic unintentionally

## Checklist

- [ ] No real phone numbers, addresses, or tokens anywhere in this diff
- [ ] Updated relevant docs in `docs/` if behavior changed
- [ ] Linked any related issue
