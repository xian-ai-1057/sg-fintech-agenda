# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A static, single-page agenda viewer for the **Singapore FinTech Festival 2025**
(10–14 Nov, Singapore). Two parts:

1. **`scrape_sff_agenda.py`** — a Selenium scraper that pulls the live agenda into
   `agenda.csv`.
2. **`index.html`** — a self-contained, zero-build agenda visualiser (Apple
   Calendar–style desktop view + g0v-style mobile view). This is the deployed
   site (GitHub Pages).

Note: project docs (`README.md`, inline comments) are written primarily in
**Traditional Chinese**; UI labels are bilingual (English-first, with a 中/EN
description toggle). Keep that convention when editing.

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

The viewer has **no build, lint, test, or server step**. Open `index.html`
directly in a browser. React + Babel are loaded inline from a CDN and JSX is
transpiled in-browser, so any change is testable by reloading the file. To
preview the mobile layout on desktop:

- `index.html?m=1` forces the mobile layout, or
- open `Mobile Preview.html` (renders `index.html?m=1` inside a phone frame).

## Architecture

### Scraper (`scrape_sff_agenda.py`)

The live agenda is JS-rendered, so it drives real Chrome via Selenium 4
(Selenium Manager auto-downloads chromedriver — only Chrome itself is needed).
Two stages, because the listing cards lack descriptions and venue:

1. **Listing** — `scrape_listing` iterates the 5 festival days. Each day is
   selected via the `?startDate=<epoch-ms>` query param (the SGT-midnight
   timestamps are hard-coded in `DAYS`). Sessions load via infinite scroll, so
   `scroll_to_load_all` scrolls to the bottom until the card count stabilises,
   then `parse_card` reads title/datetime/track/speakers/URL. Rows are deduped
   by detail-page URL.
2. **Detail** — `scrape_detail` opens each session's detail page for
   description, stage, room/location, and event type.

Output is `agenda.csv` (UTF-8 **with BOM**, `utf-8-sig`, so Excel renders
non-ASCII correctly). Columns are defined by `FIELDS`. The CSS selectors
(`CARD`, `.custom-agenda-*`) are coupled to the live site's markup — if scraping
breaks, these are the first thing to re-check.

### Viewer (`index.html`)

A single ~2000-line file. **`agenda.csv` is the upstream source, but the viewer
does NOT read it at runtime** — its data is hand-transcribed/curated into inline
JS objects. Editing the agenda shown on the site means editing these inline
objects, not the CSV. The file is organised as four scripts in order:

1. **Inline CSS** (`<style>`, top of file) — includes a `@media (max-width: 720px)`
   block for mobile.
2. **Data — `window.SFF`** (plain `<script>`, ~line 557). An IIFE exposing
   `{ ROOMS, ROOM_FULL, STAGES, STAGE_FULL, TOPICS, U, S, DAYS }`:
   - `ROOMS` / `STAGES` — `[code, shortLabel]` columns. Days 1–2 (Insights Forum,
     Sands Expo) use `ROOMS`; days 3–5 (main stages, Singapore Expo) use `STAGES`.
   - `TOPICS` — track code → `[中文, English]`; drives the 9 colour-coded tracks.
   - `S` — the session array. **Each row is a positional tuple**:
     `[day, room/stage, start, end, access, topic, title, slug, speakers[], stage?]`.
     `day` is `"d1".."d5"`; `access` is `O`/`I`/`P` (Open / Invite-Only 🔒 /
     Premium ◆); times are `"H:MM"`. The `slug` links a row to its description.
3. **Descriptions — `window.SFF_DESC`** (plain `<script>`, ~line 960). Maps
   `slug -> [English, 繁體中文]`. ZH is machine-translated; `_word_` markers
   denote emphasis (rendered as `<em>`).
4. **React app** (`<script type="text/babel">`, ~line 1509). Entry point is
   `App` (~line 1969), mounted via `ReactDOM.createRoot(...).render(<App />)` at
   the bottom. There is a second `text/babel` block above it (~line 1162) holding
   the `Tweaks*` UI-control components.

Key runtime behaviour in `App`:

- **Desktop vs mobile is one component tree.** `App` checks
  `useMediaQuery("(max-width: 720px)")` (or `?m=1`) and either renders
  `MobileShell` (search + bottom-sheet filters + single-column timeline) or the
  desktop shell with a `Calendar` (`CalendarView`) / `List` (`ListView`) toggle.
- `CalendarView` positions chips by **absolute minute-level offset**
  (`mins()` / `pxPerMin`), not CSS grid rows — this is deliberate so 5/10-minute
  sessions align precisely. `computeLanes` splits overlapping sessions in the
  same room/stage into side-by-side lanes.
- **Persisted UI state** lives in `localStorage`: description language
  (`sff-lang`, key handled by `useLang`, defaults to English) and the Tweaks
  panel settings (`useTweaks`).

## Deploy

`.github/workflows/pages.yml` publishes the repo root to GitHub Pages on every
push to `main` (live at https://xian-ai-1057.github.io/sg-fintech-agenda/). The
whole directory is uploaded as-is — there is no build step — so `index.html`
must remain a working standalone file.

## Conventions

- Keep `index.html` **self-contained and build-free** (inline CSS, inline data,
  in-browser Babel). Don't introduce a bundler, package.json, or external data
  fetch without good reason — "double-click to open" is an intended feature.
- When adding/editing sessions, respect the positional tuple shape of `S` and
  keep `slug` consistent between `S` and `SFF_DESC`.
- Speaker lists in `S` are sometimes truncated with a `"等共 N 位講者"` /
  `"等多位講者…"` sentinel entry — follow that pattern rather than listing 15+ names.
