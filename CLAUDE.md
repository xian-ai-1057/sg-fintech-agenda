# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A static agenda viewer for the **Singapore FinTech Festival 2026**
(18–20 Nov 2026, Singapore Expo). Three parts:

1. **`scrape_sff_agenda.py`** — a Selenium scraper that pulls the live agenda into
   `agenda.csv`.
2. **`agenda.csv`** — the single source of truth for session data.
3. **`index.html`** — a zero-build agenda visualiser (Apple Calendar–style desktop
   view + g0v-style mobile view) that **loads `agenda.csv` at runtime**. This is
   the deployed site (GitHub Pages).

`archive/2025/` holds the frozen 2025 site. It is a *different architecture* —
its data is inlined into its own `index.html` — and should be left alone.

Note: project docs (`README.md`, inline comments) are written primarily in
**Traditional Chinese**; UI labels are bilingual (English-first, with a 中/EN
description toggle). Keep that convention when editing.

## Data flow — read this before changing anything

```
scrape_sff_agenda.py  ──►  agenda.csv  ──►  index.html (fetch at page load)
```

**To change what the site shows, edit `agenda.csv`.** Do not hand-edit session
data into `index.html` — it has none. Dates, day count, stage columns, track
codes and track colours are all *derived from the CSV at runtime*, so a new
year's data needs no viewer changes.

## Commands

The scraper:

```bash
python3 -m pip install -r requirements.txt   # selenium>=4.20; needs Google Chrome installed
python3 scrape_sff_agenda.py                 # full crawl -> agenda.csv
python3 scrape_sff_agenda.py --no-headless   # show the browser window (debug)
python3 scrape_sff_agenda.py --limit-days 1  # only the first festival day
python3 scrape_sff_agenda.py --skip-details  # listing only, no detail pages
python3 scrape_sff_agenda.py -o out.csv --max-sessions 5   # quick smoke test
```

If it finds zero sessions it exits non-zero and **leaves `agenda.csv` untouched**
(rather than clobbering good data with an empty file), printing the CSS selectors
to re-check. Changing year: `FESTIVAL_YEAR` / `FESTIVAL_DAYS` at the top of the
scraper, nothing else.

The viewer has **no build, lint, test, or server step** — but it does need to be
served over HTTP, because `fetch('agenda.csv')` from a `file://` page is blocked
by CORS:

```bash
python3 -m http.server 8000   # then open http://localhost:8000/
```

Opening `index.html` by double-click still works: it detects the failed fetch and
offers a drag-and-drop / file-picker to load `agenda.csv` manually. To preview the
mobile layout on desktop: `index.html?m=1`, or open `Mobile Preview.html`.

## Architecture

### Scraper (`scrape_sff_agenda.py`)

The live agenda is JS-rendered, so it drives real Chrome via Selenium 4
(Selenium Manager auto-downloads chromedriver — only Chrome itself is needed).
Two stages, because the listing cards lack descriptions and venue:

1. **Listing** — `scrape_listing` iterates the festival days. Each day is
   selected via the `?startDate=<epoch-ms>` query param (SGT-midnight timestamps,
   hard-coded in `FESTIVAL_DAYS` alongside the ISO date). Sessions load via
   infinite scroll, so `scroll_to_load_all` scrolls to the bottom until the card
   count stabilises, then `parse_card` reads title/datetime/track/speakers/URL.
   Rows are deduped by detail-page URL.
2. **Detail** — `scrape_detail` opens each session's detail page for
   description, stage, room/location, and event type.

Output is `agenda.csv` (UTF-8 **with BOM**, `utf-8-sig`, so Excel renders
non-ASCII correctly). Columns are defined by `FIELDS`. The CSS selectors
(`CARD`, `.custom-agenda-*`) are coupled to the live site's markup — if scraping
breaks, these are the first thing to re-check.

### Viewer (`index.html`, ~2670 lines)

Self-contained: inline CSS, inline scripts, React + Babel from a CDN, JSX
transpiled in-browser. Organised as four scripts in order:

1. **Inline CSS** (`<style>`, line 10). Includes `@media (max-width: 720px)`
   blocks for mobile, and `.boot*` rules for the pre-data state screens.
   Track colours are **not** here — they are injected at runtime (see below).
2. **CSV loader** (plain `<script>`, ~line 715). An IIFE that fetches
   `agenda.csv`, parses it, and publishes `window.SFF` / `window.SFF_DESC`.
   Key pieces:
   - `VENUE_CFG` (~739) — `"CSV stage/location name": [code, shortLabel, fullVenue]`.
     Insertion order = left-to-right column order in the calendar.
   - `TRACK_CFG` (~767) — `"CSV track name": [code, 中文, colour]`, plus `PALETTE`
     for tracks not listed.
   - `parseCSV` (~793) — minimal RFC 4180 parser: quoted fields, embedded
     newlines (descriptions use them heavily), `""` escapes, BOM.
   - `build` (~910) — turns records into the viewer's data shape.
   - State screens: `showLoading` / `showEmpty` / `showFileFallback` render into
     `#root` before React mounts.
3. **Tweaks UI components** (`<script type="text/babel">`, line 1157). Generic,
   reusable, contains no SFF data. Exports `useTweaks`, `TweaksPanel`, etc.
4. **React app** (`<script type="text/babel">`, line 1701). Entry point `App`
   (~2535).

**Derivation rules in `build`** (these are why a new year needs no viewer edits):

- **Column key** is `stage`, *unless* that stage maps to more than one distinct
  `location` across the CSV — then `location` is used. This is what splits
  `Side Programmes` into its individual lounges, and 2025's `Insights Forum` into
  its individual rooms.
- **Unknown stages/tracks never break the page.** A venue or track missing from
  `VENUE_CFG`/`TRACK_CFG` gets an auto-derived code (initials) and, for tracks,
  the next unused `PALETTE` colour. Add an entry to those tables only to make the
  label prettier.
- **Days** come from the distinct `date` (or `day`) values in CSV order, keyed
  `d1..dN`. `DAYMETA[k]` carries `wd` / `wdz` (CJK weekday) / `date` / `md` /
  `label`; `label` is the day's single stage name, or `"Main Stages"` if it has
  several.
- **Track colours** are injected as a `<style id="sff-track-colors">` of
  `.tk-XX { --c: … }` rules, so the number of tracks is not limited by the
  static CSS.

**`window.SFF` shape**: `{ VENUES, VENUE_FULL, TOPICS, U, S, DAYS, DAYMETA }`.
`S` rows are **positional tuples**, unchanged from the 2025 version:
`[day, venueCode, start, end, access, topic, title, slug, speakers[], stageName]`
— `day` is `"d1".."dN"`; `access` is `O`/`I`/`P` (Open / Invite-Only 🔒 /
Premium ◆); times are `"H:MM"` 24-hour. `window.SFF_DESC` maps
`slug -> [English, 繁體中文]`; `_word_` markers render as `<em>`.

**Async mount handshake**: the loader is async but the React block is
`text/babel` (transpiled on DOMContentLoaded), so neither can assume it runs
first. The loader sets `window.__sffDataReady` and calls `window.__sffMount()`
if present; the React block defines `window.__sffMount` and calls it if
`__sffDataReady` is already set. Whoever is last mounts, exactly once.
Because of this, `D` / `DESC` / `DAYMETA` / `COLS_ALL` are `let`s assigned in
`initData()` — **never read them at babel-block top level.**

Other runtime behaviour in `App`:

- **Desktop vs mobile is one component tree.** `App` checks
  `useMediaQuery("(max-width: 720px)")` (or `?m=1`) and either renders
  `MobileShell` (search + bottom-sheet filters + single-column timeline) or the
  desktop shell with a `Calendar` (`CalendarView`) / `List` (`ListView`) toggle.
- `CalendarView` positions chips by **absolute minute-level offset**
  (`mins()` / `pxPerMin`), not CSS grid rows — this is deliberate so 5/10-minute
  sessions align precisely. `computeLanes` splits overlapping sessions in the
  same column into side-by-side lanes.
- **Persisted UI state** lives in `localStorage`: description language
  (`sff-lang`, `useLang`, defaults to English) and Tweaks settings (`useTweaks`).
- Use the `accOf(code)` / `dayMeta(key)` helpers rather than indexing `ACCESS` /
  `DAYMETA` directly — CSV data can contain values the tables don't cover.

## Deploy

`.github/workflows/pages.yml` publishes the repo root to GitHub Pages on every
push to `main` (live at https://xian-ai-1057.github.io/sg-fintech-agenda/). The
whole directory is uploaded as-is — there is no build step — so `agenda.csv` and
`archive/` are served too, and `index.html` must remain a working standalone file.

## Conventions

- Keep `index.html` **self-contained and build-free** (inline CSS, inline
  scripts, in-browser Babel). Don't introduce a bundler or a package.json.
- **Session data belongs in `agenda.csv`, presentation config in `index.html`.**
  If you find yourself pasting session rows into the HTML, that's the wrong file.
- Don't modify `archive/2025/` — it's a frozen snapshot with a different
  (data-inlined) architecture.
