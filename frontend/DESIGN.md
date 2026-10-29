# Design guide: "Ledger & Seal"

Read this first. Behaviour is specified in `docs/frontend.md`; this file is how it looks and which building blocks to use.

**Who it is for:** a Green Link Digital Bank compliance or credit officer working through a queue of company cases. Every screen should answer: what do I work on next, why is it risky, what should I do, how do I record the decision. Calm, precise, quick to scan, plain English. Light theme only.

**The idea:** a bank statement with a seal. Warm security paper, ink-green type, hairline rules (a thin line over a thicker one under headings), guilloche line work as texture, brass used only for "needs your attention". Red, amber and green mean risk and nothing else.

Dev reference page with every primitive: `/kit` (404s in production builds; source `src/app/kit/`).

## Tokens (`src/app/globals.css`)

Use the Tailwind utilities (`bg-paper`, `text-ink-2`, `border-rule` ...). Never hard-code hex, never use Tailwind's default palette (`red-50`, `gray-500`).

| Token | Hex | Use | Contrast (checked) |
|---|---|---|---|
| `paper` (= `background`) | #F6F3EC | page | ink 15.1 |
| `paper-2` | #EEEAE0 | wells, hover, table header | ink 14.0, muted 4.5 |
| `surface` (= `card`) | #FFFDF8 | raised cards, inputs | ink 16.5 |
| `ink` (= `foreground`) | #14201B | body text | |
| `ink-2` | #3A4A43 | secondary text | 9.2 on surface, 7.8 on paper-2 |
| `muted` | #5E6D66 | captions, labels | 4.9 paper, 5.4 surface, 4.5 paper-2 |
| `rule` (= `border`) | #D9D3C4 | hairlines, card borders | decorative |
| `rule-strong` | #857D68 | input and control borders | 4.0 surface, 3.7 paper (needs 3) |
| `brand` (= `accent`) | #0F3D2E | header band, primary button, headings accent | 11.0 on paper; on-brand text 11.0 |
| `brand-2` | #1B5E46 | links, active, focus ring | 6.9 paper, 7.6 surface |
| `brand-tint` | #E3EFE8 | selected / hover fill | brand 10.3 |
| `brass` | #B8893B | FILLS ONLY: attention edge, dots. Never text | ink on brass 5.3 |
| `brass-deep` | #7A5A1C | brass as text | 6.3 surface, 5.2 on brass-tint |
| `brass-tint` | #F3E8CF | attention background | |
| `brass-light` | #E3C27F | brass on the dark band, focus ring there | 7.1 on brand |
| `sev-high` / `-tint` | #A8231B / #F8E4E1 | high concern | 7.1 surface, 5.9 on tint |
| `sev-medium` / `-tint` | #9A5B00 / #FBEBD0 | medium concern | 5.3 surface, 4.6 on tint |
| `sev-low` / `-tint` | #2A7050 / #E1F0E8 | low concern, success | 5.9 surface, 5.1 on tint |
| `sev-info` / `-tint` | #3D5A80 / #E4EBF3 | for information | 7.0 surface, 5.9 on tint |
| `on-brand` / `on-brand-muted` | #F6F3EC / #B7CDC1 | text on the green band | 11.0 / 7.3 on brand |

Medium is #9A5B00, darker than the suggested #B26A00, to clear 4.5:1 on its own tint. All pairs: `node scripts/contrast.mjs` (exits 1 on failure). Re-run after touching any colour.

Brass rules: at most one brass element per screen region; it marks what needs attention (active tab edge, `Panel tone="attention"`, mock banner), not decoration.

## Type

Self-hosted from npm (`@fontsource*`, imported in `layout.tsx`; no CDN, CSP-safe, `font-display: swap`).

| Role | Family (Tailwind) | Use |
|---|---|---|
| Display | Fraunces Variable, optical size + weight axes (`font-display`) | h1-h3 (applied automatically), risk numerals, Stat values |
| UI / body | Hanken Grotesk Variable (`font-sans`, default) | everything else, 16 px base |
| Data | IBM Plex Mono 400/500 (`font-mono`, or `.num`) | case ids, UENs, amounts, rule ids; tabular and slashed zero |

Scale: page title `text-[1.75rem] sm:text-[2.25rem]` (SectionHeading size page), section `text-xl`, panel title 1.0625rem, body 1rem (15px `text-[0.9375rem]` allowed for dense rows), caption `text-sm`/`text-xs`, label-caps `.label-caps` (11.5px, 0.09em). Inputs must be 16px. Amounts and ids: `className="num"`. Numbers in columns: `tabular`.

## Space, shape, depth

- Container: `max-w-[1320px]`, gutters 16 / 24 / 32 px (`CONTAINER` exported from `AppShell.tsx`). Vertical rhythm: 24 px between sections (`space-y-6`), 16 px inside panels (`p-4 sm:p-5`).
- Radius: `rounded-md` 6 px (controls), `rounded-lg` 10 px (panels), `rounded-xl` 14 px (dialogs), badges 5 px, pills only for meters.
- Depth: layered paper, not boxes. Page `paper`, cards `surface` + `border-rule` + `shadow-paper`, floating things `shadow-lift`, dialogs `.sheet`. No other shadows, no heavy blur.
- Rules: `.rule-statement` (hairline over a 2px line) under headings; `.rule-statement-soft` inside panels.
- Touch: 44 px minimum targets (`min-h-11`); `Button size="sm"` is 36 px with a mouse and 44 px on touch.
- Focus: 2px ring + 2px offset, brand green on paper, light brass on the dark band (`.band-dark`). Do not remove outlines.

## Motion

One orchestrated entrance only: wrap a page's main sections in `<Stagger>`; direct children fade and rise 10 px, 50 ms apart, first 10 only. Nothing else animates except the RiskGauge arc fill, spinners and the skeleton shimmer. Everything is disabled under `prefers-reduced-motion` (global block in `globals.css`). Do not add hover transforms, parallax or scroll effects.

## Primitives (`import { ... } from "@/components/ui"`)

```tsx
<Button variant="primary|secondary|ghost|danger" size="sm|md|lg" loading icon={<Plus aria-hidden className="h-4 w-4" />}>Record decision</Button>
<Button href="/upload" variant="secondary">New case</Button>        // link that looks like a button
<Badge variant="high|medium|low|info|lane|fast|status|attention|neutral">text</Badge>   // icon + text, never colour alone
<RiskBadge band="high" partial />  <ConcernBadge severity="medium" />  <LaneBadge lane="edd" />  <StatusBadge status="open" />
<Panel title="Findings" eyebrow="3 to check" actions={<Button size="sm" variant="ghost">Expand all</Button>} tone="default|attention|sunken" padding="md|sm|none" footer={...}>...</Panel>
<SectionHeading level={1} size="page" eyebrow="Worklist" description="Highest risk first." actions={...}>Cases to review</SectionHeading>
<RiskGauge score={90} band="high" size="lg" partial />             // role="img" "Risk score 90 of 100, high"; case page hero
<MeterBar label="Checks completed" value={41} max={54} valueText={coverageText(41, 54)} marker={0.7} tone="brand|medium|..." />
<Stat label="Cases waiting" value={12} hint="3 are high risk" tone="high" />
<EmptyState icon={<Inbox aria-hidden className="h-6 w-6" />} title="No cases match" text="Clear a filter." action={<Button variant="secondary">Clear filters</Button>} />
<Skeleton className="h-6 w-40" />  <SkeletonText lines={3} />     // aria-hidden: add a role="status" message beside them
<Kbd>j</Kbd>
<Tabs group="case" label="Case sections" value={tab} onValueChange={setTab} tabs={[{ id: "findings", label: "Findings", count: 4 }]} variant="underline|pill" />
<TabPanel group="case" id="findings" value={tab}>...</TabPanel>      // arrow keys, Home/End, one tab stop
<Notice variant="success|warning|error|info" title="Decision recorded" action={...} onDismiss={...}>Saved.</Notice>   // error = role alert
<Field label="Reason" hint="At least 10 characters." error={err} value={v} onChange={...} />   // + inputClasses for select/textarea
<Logo tone="light|dark" showProduct />  <LogoMark />
<Guilloche variant="band|rosette" opacity={0.12} className="absolute inset-0 text-on-brand" />   // texture only, aria-hidden
<Stagger className="space-y-6">...children...</Stagger>
```

Existing `components/Chips.tsx`, `Tabs.tsx` (TabList), `StateViews.tsx`, `Modal.tsx` keep working and pick up the new tokens; migrate to the primitives above when you touch a page. `buttonClasses()` gives button styling to any element. Dialogs keep using `dialog.sheet`.

## Words (`src/lib/labels.ts`, tested)

Use these, not the API enum values or developer terms.

| Concept | Say |
|---|---|
| lane `loan` / `edd` / `aml` / `fast` | `laneLabel`: Loan review / Enhanced due diligence / AML investigation / Fast lane; one-line meaning via `laneDescription` |
| severity | `severityLabel`: High concern / Medium concern / Low concern / For information |
| band | `bandLabel`: High risk (`bandWord`: High); partial: "Low risk (partial score)"; `scoreSummary` for aria |
| status | `caseStatusLabel`: Being analysed / Ready for review / Waiting for documents / Closed / Could not be analysed |
| hard stop | `HARD_STOP_LABEL`: Blocks the fast lane |
| coverage 41/54 checks | `coverageText(41, 54)`: 41 of 54 checks completed |
| Received but not checked | `UNCHECKED_PAGES_LABEL`: Pages we could not review (`unreviewedPagesText(n)`) |
| Dismiss / Confirm (a flag) | `DISMISS_LABEL`: Mark as not a concern / `CONFIRM_LABEL`: Mark as a real concern |
| rule id | small secondary text only: `ruleIdText(id)`: Check: bs_running_balance (use `num text-xs text-muted`) |
| nav | Worklist (`/`), New case (`/upload`), Control performance (`/effectiveness`), Check catalogue (`/rules`) |

Never call a document authentic, genuine or verified (`wording.test.ts` greps `components/` and `app/`). Do not add an "Internal use only" line (removed by the owner). Keep the disclaimer in the footer only.

## Do / Don't

- Do put the score and the reason before the detail; lead with what the officer should do next.
- Do pair every colour with text and an icon shape. Do keep one `h1` per page (`SectionHeading level={1}`).
- Do use `Panel` for groups, `rule-statement` for section starts, `num` for ids and amounts.
- Do check at 360, 390, 768 and 1440 px; no horizontal page scroll; tables scroll inside a labelled `overflow-x-auto` region.
- Don't use pure white or black, grey boxes, purple or blue gradients, Tailwind default colours, emoji, or heavy shadows.
- Don't use brass as text colour (use `brass-deep`), or red/amber/green for anything but risk and success/failure.
- Don't animate lists on every update (polling must not flicker), and don't add motion beyond Stagger.
- Don't edit `src/lib/auth/**`, `src/proxy.ts`, `src/app/api/**` or `src/lib/types.ts`.

## Run a private dev server and take screenshots

Several agents may share this folder, so each uses its own dist dir and port (never 3000 or 8000):

```bash
cd frontend
NEXT_DIST_DIR=.next-<yourname> NEXT_PUBLIC_USE_MOCK=1 npm run dev -- -H 127.0.0.1 -p 31xx   # background; mock data, no backend
NEXT_DIST_DIR=.next-build npm run build                                                     # builds: never the dist dir of a running server
```

`.next-*/` is git-ignored and excluded from tsc, eslint and vitest. `next dev` rewrites `next-env.d.ts` and `tsconfig.json` includes for your dist dir; if `tsc` complains about a missing `.next-*` type file after stopping your server, run `git checkout frontend/tsconfig.json` for any `.next-*` lines and recreate `next-env.d.ts` (it is git-ignored) by starting any dev server once.

Screenshots (Playwright MCP): save under `.playwright-mcp/<name>.png`, then open with Read. Review at 1440x900 and 390x844. Stop your server and delete your `.next-*` folder when done.

Checks before you finish: `npm run lint`, `npx tsc --noEmit`, `npm test`, `node scripts/contrast.mjs` (if you added colours).
