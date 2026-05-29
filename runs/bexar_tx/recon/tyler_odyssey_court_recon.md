# Tyler Odyssey court recon — court_civil + court_probate (Build-Mode fingerprint)

## 0. Status and scope

- **Date:** 2026-05-28
- **Mode:** Build-Mode portal fingerprinting (Phase 0 already verified both sources HIGH / PRIMARY_LEAD_SOURCE; this recon is the step that must precede a scraper spec, exactly like the clerk flow).
- **County:** bexar_tx
- **Sources:** `court_civil`, `court_probate` — **both served by the same portal and the same Smart Search form**; they differ only by which CourtLocation the search targets and which case types appear in the results.
- **Portal:** Bexar County Justice Portal — Tyler Technologies **Odyssey Portal** at `https://portal-txbexar.tylertech.cloud/Portal/`
- **Method:** read-only Playwright probe (now that Chromium is installed). No search was submitted (anonymous submit is reCAPTCHA-gated — see §3). No anti-bot bypass attempted.

**Evidence artifacts in `runs/bexar_tx/recon/`:**
- `_tyler_probe_findings.json` — landing + Smart Search/Hearings fingerprint, gate/login/anti-bot scans.
- `_tyler_dropdowns.json` — SearchBy / CourtLocation / CaseType / CaseStatus option enumeration.
- `_tyler_casetypes.json` — CaseType per CourtLocation (probe).
- `_tyler_01_landing.html`, `_tyler_portal_home_dashboard_29.html` — captured rendered DOM.

This document is a **recon findings record + scraper-spec seed**, NOT a scraper spec. It exists to surface the access reality and the open questions the operator must decide before a `court_*` scraper spec is written.

---

## 1. Portal identity & layout

- **Title:** "Bexar County Justice Portal". Official origin links verified in the Phase 0 packet (`bexar.org/3856` District Clerk portal page; `bexar.org/3396` Probate Division).
- **Landing tiles (no login required to see them):** **Smart Search** ("Search for court records"), **Search Hearings** ("Search for court hearings"), **Jail Search**, **Make Payments**.
- **Login:** "Register / Sign In" is offered but **optional** for search — consistent with Phase 0. BUT see the reCAPTCHA finding in §3: anonymous *submitting* is gated.
- One consolidated portal covers District Clerk (civil district + felony), County Clerk (probate + county civil + misdemeanor), and Justice of the Peace (eviction/small claims). So civil-foreclosure and probate are **the same portal, filtered by CourtLocation**.

---

## 2. Smart Search fingerprint (`/Portal/Home/Dashboard/29`)

- **Search form:** `id=frmSS`, **POST → `/Portal/SmartSearch/SmartSearch/SmartSearch`**. Reset: GET `/Portal/SmartSearch/SmartSearch/ResetSmartSearch`.
- **Transport:** server-rendered. **No XHR/fetch JSON API was observed** during load — results come back as HTML from the form POST (classic Odyssey Portal, unlike the PublicSearch React SPA + `/results` URL used for the clerk source). So the scraper will POST a form and parse a returned results page, not hit a JSON endpoint.
- **Primary search modes (`SearchBy`):** Smart Search (free text), Attorney Bar Number, Attorney Name, Business Name, Case Cross-Reference Number, Case Number, Citation Number, Judicial Officer, Nickname, Party Name.
  - **There is NO "date filed" / "all cases on date X" search mode.** (Critical — see §4.)
- **Advanced filter fields (refine a primary search):** `FileDateStart` / `FileDateEnd` (date-filed range), `CaseStatus`, `CourtLocation`, `CaseType`, `JudicialOfficer`, `UseSoundex`, party-name fields (`NameLast/First/Middle/Suffix`), `SearchByPartyName/BusinessName/NickName`.
- **CourtLocation options:** All Locations, **County Clerk**, **District Clerk**, Justice of the Peace.
  - civil foreclosure → **District Clerk**; probate/estate → **County Clerk** (Bexar probate courts are County Courts at Law / Probate Courts under the County Clerk).
- **CaseType options:** only **"All Offices Case Search"** is exposed — even after selecting a specific CourtLocation. **This portal config has no granular case-type pre-filter.** Case type is therefore only knowable from the **result rows**, not from a pre-search filter.

---

## 3. ACCESS BLOCKER (decisive) — reCAPTCHA on anonymous search

- The Smart Search page carries **Google reCAPTCHA v2 (checkbox)**: `data-sitekey="6LfqmHkUAAAAAAKhHRHuxUy6LOMRZSG2LvSwWPO9"`, a `g-recaptcha-response` field, and two settings flags:
  - `Settings_CaptchaEnabled` = **true**
  - `Settings_CaptchaDisabledForAuthenticated` = **true**
- **Interpretation:** an **anonymous** user must solve a reCAPTCHA on every Smart Search submit; a **registered (free) authenticated** user has the CAPTCHA **disabled**. Search Hearings (Dashboard/26) carries the same reCAPTCHA.
- **Consequence for automation:** the portal is publicly *usable by a human*, but **not automatable anonymously** — the reCAPTCHA blocks unattended scraping. The clean automation path is a **free registered account → seeded/authenticated session → CAPTCHA disabled**, then a normal form POST.
- **This refines the Phase 0 proof packet**, which recorded `PUBLIC_SEARCH_ONLY` with no blocker. Public search is real, but the reCAPTCHA gate for automation was not captured at Phase 0. Packet updated 2026-05-28 (blocker + `next_access_strategy = request_free_account`).

---

## 4. Daily-refresh feasibility — the core design problem

The clerk source supports a clean date-cursor daily-refresh because PublicSearch lets you query a `recordedDateRange` directly. **Tyler Smart Search does not** — there is no "list everything filed on date X" query; `FileDateStart/End` only *narrow* a name/case/business search. You cannot discover new filings by date alone through Smart Search.

Candidate discovery strategies (each needs an operator decision and, except where noted, a follow-up probe **with an authenticated account**):

- **(A) Search Hearings (date-based).** The Hearings tile *is* date-driven (hearing-date range) and can enumerate scheduled hearings without a name — including foreclosure (Rule 736 / home-equity) and probate hearings. Also reCAPTCHA-gated. Needs its own fingerprint + confirmation that hearing rows carry case number/type/parties. **Most promising for date-fresh discovery.**
- **(B) Blank-criteria + CourtLocation + FileDate range.** Some Odyssey portals allow an empty search criteria with only a location + date range, returning all cases in that window. **UNKNOWN whether Bexar permits empty criteria** — must be tested with an account (can't test anonymously behind the CAPTCHA).
- **(C) Case-number enumeration.** Texas case numbers are structured by year + type (civil and probate use distinct type tokens). Iterating sequential numbers for a filing period is feasible but heavier and needs the exact Bexar numbering format **confirmed from real results** (not assumed here).
- **(D) Name-driven.** Not viable for discovery — you don't know filer names in advance.

**This is the central open question.** No `court_*` scraper spec can be written until the discovery strategy is chosen and validated with an account.

---

## 5. Lead-value caveat — does a court case carry a property address?

- **Civil foreclosure (District Clerk):** Texas expedited foreclosures (Rule 736) and home-equity foreclosure applications **do** concern a specific property; the application/petition typically references it. Property address is *likely* present in the case/detail but must be confirmed from a real detail page.
- **Probate (County Clerk):** a probate case references the **decedent and estate**, generally **not** a property address. Probate leads from this source would need a join (decedent name → parcel/clerk owner-of-record) to become property-matched — same heir-hunting pattern already noted for the parcel owner-name `estate` signal. So probate-court leads are **party-first, address-later**.
- Implication: court sources may produce **name/event** leads that depend on the existing parcel/clerk join for a property address. This affects how the translator and matcher treat them and should be settled in the spec.

---

## 6. Open questions for the operator (gate the scraper spec)

1. **Access path:** register a free portal account (disables reCAPTCHA) and use a seeded/authenticated session? OR authorize a CAPTCHA solver (cost-gated, MEDIUM)? OR manual/PIA? — *Recommended: free account + seeded session.*
2. **Discovery strategy (§4):** Search Hearings (A) vs blank-criteria date-range (B) vs case-number enumeration (C)? (B) and case-type enumeration both require a probe **with the account**.
3. **In-scope case types:** which civil case types count as foreclosure leads (home-equity / Rule 736 expedited foreclosure / tax suit / others)? Which probate types (administration / heirship / muniment of title / guardianship)?
4. **Court locations:** confirm District Clerk = civil foreclosure scope; County Clerk = probate scope.
5. **Property-address availability (§5):** accept party-first probate leads that rely on the parcel/clerk join, or require an address before exporting?

---

## 7. Recommendation

Both `court_civil` and `court_probate` are genuine PRIMARY_LEAD_SOURCEs, but two gates stand between recon and a buildable scraper: **(1) reCAPTCHA** (solved cleanly by a free registered account) and **(2) the no-date-only-search limitation** (needs a chosen, account-validated discovery strategy — Search Hearings looks the most promising). 

**Next concrete step:** operator registers a free Bexar Justice Portal account; then a second recon **with that account** (a) confirms CAPTCHA-free submit, (b) tests blank-criteria + date-range search and the Search Hearings date listing, and (c) enumerates the real civil/probate case-type strings and a sample detail page (for property-address presence). That second recon produces the data needed to write `court_civil` / `court_probate` scraper specs. Until then both are **WAITING_ON_ACCESS** (`request_free_account`).
