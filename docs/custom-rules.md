# Custom rules

An officer can describe a new check in plain English and a severity. The assistant (Claude Haiku) turns it into one rule of a fixed engine. **No generated code ever runs**: the model fills a closed form, a fixed program runs it. Off by default (`GLDB_CUSTOM_RULES=on`), and only for people who sign in with a personal account. The HTTP side is in [api.md](api.md) section 9c.

## What a rule is

One condition, no "and" / "or", on one kind of document, at `info`, `low` or `medium`. Never `high`, never a hard stop. Four templates:

| Template | Example |
|---|---|
| Field against a value | an invoice total is greater than 100,000; a payment status is one of unpaid, partial |
| Date window | a passport expires more than 0 days before the case date; an invoice is dated after the case date |
| Field missing | a receipt has no receipt number |
| Statement line against line | borrowings (current) + borrowings (non-current) is greater than 2 times total equity |

The assistant may only pick from the fields the system really reads. The list is generated from the document schemas, so it cannot drift. Nationality, sex, date of birth and people's names are not available at all ([name-fairness.md](name-fairness.md)). "Greater than" is not offered on a name.

## What is not possible

The assistant is told this list and must report these instead of guessing: anything the system does not read (notes to the accounts, lender names, loan terms, credit scores, transaction rows), other kinds of document (tax returns, utility bills, social media), outside lookups, more than this year against last year, pictures, logos and layout, fuzzy judgement ("looks suspicious"), other cases, comparing two documents, counting or matching (distinct lenders, similar names), "and" / "or", restricting by currency or converting it (an amount is compared as printed, see below), and anything that changes built-in checks or the score.

## Where it lives

There is no separate page or navigation entry. On the **Check catalogue** page, when `/me` says `custom_rules` is on, a **Create custom check** button sits next to "Change log" and opens a popup (the rule assistant, every step of it, up to saving). Closing the popup throws away an unsaved draft; after a save the saved view stays in the popup, and "Go to my checks" closes it and scrolls to the section. Under the built-in list the **Custom checks** section ("Written by officers, not reviewed by the development team") has three tabs: **My checks** (switch on or off, make public, new version, delete, and the public checks adopted), **Public checks** (adopt, unadopt, administrators can take back) and **Requests to the tech team**. With the feature off, none of this renders. The old `/rules/custom` address redirects to `/rules`.

## What happens to a request

1. **Classified first.** Code checks the words first (empty, instructions to the assistant, protected attributes, "high", "hard stop", switching off built-in checks, changing the score). Then one model call. The final status is decided by code and can only be lowered from what the model said: a "ready" with a word nobody accounted for, a part that cannot be done, a question left open or an invalid rule is not ready. Short words that change what a rule means ("not", "no", "non", "nil", "PO", "GST", "UEN", "ID", and "n't" forms) are accounted for like any other word, so a dropped "not" cannot stay ready. The exception is a rule that is itself the negation: for a missing-field rule ("has no PO reference", "without a GST number") and for a "not equal" or "not in" comparison ("is not paid") the negation words are carried by the rule, so the model need not quote them; a comparison such as "over 50000" does not carry a "not", and "is not over 50000" read as "over 50000" is not ready. A short word that is part of a field the rule reads (PO for the PO reference) is covered too. Letters and digits are split apart on both sides of the word check ("PO123" is "po" and "123"). A request that names a currency for a rule that does not read the currency field is at best partly supported: "amounts are compared as printed, in any currency" becomes a line in "It will NOT check" and saving needs the tick. A currency is a sign ($, a euro, pound or yen sign, RM before a figure), a code next to a figure ("SGD 50000", "50000 SGD", "USD100000") or after "in" / "currency" ("in USD", "currency is SGD"), or a name such as dollars or yen next to a figure or after "in" / "currency" ("over 1000 dollars", "in Singapore dollars"); a bare "SAR" or "CAD", "the dollar amount" and a company called "Euros Trading Pte Ltd" are not. The caveat is added only to a rule that exists: an ambiguous request that names a currency stays ambiguous (and files no ticket), and the caveat comes with the reading the officer chooses or the answer that completes the rule. A caveat made by code never sends a request to the tech team by itself. Answering a question never settles a phrase or word the model dropped: it becomes a line in "It will NOT check", and saving then needs the tick that the rule checks less than asked. Wording about a protected attribute (nationality or origin, age, sex, and the like) is refused by word lists in code, also when the model files it under another reason, and is never sent to the tech team. Words the model refused are not kept or sent on: a refused draft stores the reason only (no request text, and nothing the model said about it).
2. **Six statuses.** `ready`; `needs_clarification` (a question, with options and a suggested answer that is never applied without a click); `ambiguous` (choose between readings); `partially_supported`; `unsupported`; `refused`. A conversation has at most 6 turns and 2 model calls per turn.
3. **Too complex? It is routed.** An unsupported request is stored as a ticket for the tech team at once, and the officer is told "This will be submitted to the tech team" with the ticket id, along with what is possible that is close (a weaker rule, the built-in checks that look at related things). For a partly supported request the screen shows **This rule WILL check** and **It will NOT check**; saving needs a tick that it checks less than asked, and the officer may also send the missing part. A refused request is not ticketed; it is refused with the reason and the attempt is logged (a hash of the text, not the text).
4. **Words back from code.** The readback ("Flags medium when ...") is built from the rule, never from model text.
5. **Duplicates.** A rule that does what a built-in check already does is not saved; the officer is pointed to that check's severity setting. An identical rule among the officer's own, the public ones or the ones they adopted is refused with a pointer to it. Near-duplicates are shown as warnings.
6. **Dry run.** Before saving, the rule is run over stored cases: evaluated, fired, skipped because a field was missing, how many were scans or photos. The rate is out of the cases where the rule could read its figure (evaluated minus skipped), and both numbers are shown. Firing on none, or on more than 30% of the cases it could read, needs an extra confirmation. Saving needs a reason (5 characters at least) and is kept in an append-only change log.

## Private, public, adopted

A rule belongs to the account that wrote it and is **private**: nobody else can read, run or list it. The author can make it **public** (and take it back). Everybody signed in can then see it, labelled custom and unreviewed with the author's name, and each account may **adopt** it, which pins that version and makes it run on analyses that account starts. Nothing public changes anyone's results until they adopt it. A public version never changes, name and description included (they are kept with the version); a new version stays private until published. An administrator (`GLDB_ADMIN_USERS`) can unpublish any public rule; the author cannot publish it again (the rule still runs for the author, who can save the idea as a new rule). Any unpublish ends every adoption: adopters must adopt again. Nor can anyone make public a rule with the same condition as one an administrator took back (retiring it and saving the idea again does not get round that); private use is untouched. Retiring a rule stops it and ends every adoption, each logged; every version is kept, so a past run can still be explained. An account can adopt at most 50 public rules (409 `adoption_limit`).

## How a rule runs

At the start of an analysis the rules of the account that started it are loaded: its own enabled rules and the public ones it adopted. A rule runs only when a document of its type is in the case, once per document. A custom flag is at most medium, is never a hard stop, is not counted in coverage (a rule that did not apply cannot lower it), and counts as one score family. It never closes the fast lane: the displayed score includes the custom points, but the fast-lane test (score of 20 or less) reads the score without them, so custom flags alone leave a case in the fast lane while a built-in flag still takes it out. An amount is compared exactly as printed, in whatever currency the document uses; the readback and the flag's explanation say so ("... in whatever currency the invoice prints"). An analysis loads at most 100 of the account's own rules and 50 adopted ones; a rule beyond that is still listed on the case as a skipped run (`custom_not_loaded`), never silently dropped. Rules never raise: any surprise becomes a skip. If a figure it uses is unverified or the two readings disagree, the flag drops to low and says so. Evidence quotes the document as plain text.

Every loaded rule appears on the case whether it fired or not: in the list of checks that ran, with 0 flags when it found nothing, and in the case timeline, with who wrote it and which version. Flags show as findings labelled as custom. The Check catalogue page has a "Custom checks" section under the built-in list (see Where it lives); its entries carry a "Custom" badge.

## Settings and cost

| Setting | Default | Meaning |
|---|---|---|
| `GLDB_CUSTOM_RULES` | `off` | the feature switch; off: every route says so and nothing runs |
| `GLDB_CUSTOM_RULES_MAX_PER_ACCOUNT` | 50 | active rules per account (at most 100, the number an analysis loads) |
| `GLDB_CUSTOM_RULES_DRY_RUN_CASES` | 200 | newest stored cases a dry run reads |

Only starting a draft and typing more words call the model. They are charged to the signed-in account's daily spend and respect its cap; when it is used up, authoring says so and everything else (listing, adopting, saving, tickets) keeps working, and casework is unaffected.

## Honest limits

- The text an officer types is plain text and is never shown to the model on behalf of another account. Please do not paste case details into a request: drafts and tickets are kept as plain text and are not removed by case retention.
- A case stores the name, author, version and readback of each custom rule that ran on it, so anyone who can open the case can read them (cases are shared). The rule's description, other versions and drafts stay private.
- A re-run by another account loads that account's rules, so results can differ; each run records which rules it used.
- Adoption is pinned: a newer public version reaches adopters only when they adopt again.
- "Aged receivables" and "3 years old" are refused because the word "age" is blocked; say how many days before the case date instead. Nationality words, a country named next to a passport, an identity number or a person, and ages next to a person are refused the same way. A nationality word directly followed by a currency ("Australian dollars", "Japanese yen") is let through, "minor" is refused only as a person ("a minor", "minors", beside a director), and a figure after "over" or "under" is an age only when no amount word ("total", "balance", ...) comes before it without a person word between them ("a total over 500 for a customer" passes, "a balance of customers above 60" is refused). "Set" refuses only when the score is what is set, in either order ("set the score to 0", "set to zero score", "score set to 0"). A few ordinary requests may still be refused or sent back with a question when they did not need to be.
- Tickets stop at `submitted`: there is no notification or workflow yet. The "tech team" is whoever reads the list.
- Bank statement transactions and table columns cannot be addressed in this version.
