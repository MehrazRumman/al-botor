# Al Botor

## What it does
This is a Slack bot built for workspace automation. It listens to Slack events and:
- Sends a custom sign-out reply.
- Sends an `@area51` meeting alert by mentioning configured members.
- Saves flagged messages (`--save` / `--saved`) into the channel canvas.
- Posts a welcome message when the bot joins/rejoins a channel.

The bot is deployed on Render, and deployments are automated through GitHub Actions.

## Technologies used
- Python 3
- Flask
- Slack Bolt for Python (`slack_bolt`)
- Slack SDK (`slack_sdk`)
- Gunicorn
- GitHub Actions (CI/CD)
- Render (hosting/deployment)


# Career Hub — Build Plan

## Locked
- Type-discriminated single model, one app: career_hub
- Tables prefixed career_hub_
- No stored tier field — "event-like" is derived from event_date IS NOT NULL
- work_mode (remote/hybrid/onsite), not location/location_type
- Nothing imports as resource_type=career_fair — the CSVs have no dates, so every imported row lands as career_portal / university_careers / event_platform / conference
- Real services.py/serializers.py layering from day one — Insights' model shape is the template, its missing service layer is not

## Data reality (verified against the actual CSVs, not the brief)
- 972 total rows across 8 files; 971 valid, 1 malformed (Under Armour Careers, career_fairs8.csv — 1 field instead of 5, importer skips + logs it)
- 930 unique rows after deduping on normalized link (34 duplicate-link groups, 41 rows collapsed)
- Only 4 duplicate names (WHO, APA, ESA, NBA Careers) — link is the real dedupe signal, not name
- No date column anywhere, in any file
- Location column has exactly 3 values (Hybrid/Remote/Onsite) — it's a work-mode field, not geography
- career_fairs.csv (200 rows, "Job Fair Platforms/Aggregators") is not one shape — it's 5 different content types mixed together

## Model — CareerHubResource (table: career_hub_resource)
- slug — auto-generated, collision-safe
- resource_type — career_fair | career_portal | university_careers | event_platform | conference | networking (reserved) | upskilling (reserved)
- category — JSONField(list), slugs only, validated against a fixed 8-industry taxonomy, GIN index
- requires_affiliation — bool, default False — True for university_careers, must render on the card
- work_mode — remote | hybrid | onsite
- name, organizer, focus, link
- city, country — nullable, unpopulated at seed
- event_date, event_end_date — nullable, unpopulated at seed
- link_status — nullable: ok | broken | unchecked — field exists now, checker not built in v1
- last_checked_at — nullable
- merged_from — JSONField(list) — names of rows collapsed into this one during dedupe
- status, is_featured, click_count
- source — csv_import | admin, set once at creation
- admin_modified — bool, default False — flips true the moment an admin edits a csv_import row

CheckConstraint: event_date IS NOT NULL when resource_type='career_fair'. No-op at import time; starts mattering the first time an admin enters a real dated fair.

## Import classification
- Files 3–8 (Healthcare, Climate, Consulting, FMCG, Retail, Logistics) → career_portal, per-file
- File 2 (Elite University Recruiting) → university_careers, per-file, requires_affiliation=True
- File 1: JobFairX, Eventbrite, Handshake, vFairs, RecruitMilitary, Search Associates, EURES → event_platform, ~27 rows
- File 1: MIT, NYU, UMich, Cal Poly, LBS, HKUST → university_careers, ~49 rows, requires_affiliation=True
- File 1: CES, Slush, Web Summit, SXSW, AfroTech, Grace Hopper, NSBE → conference, ~47 rows
- File 1: govt/workforce boards + employer/recruiter/association → career_portal (proposed), ~77 rows, needs sign-off

File 1 (career_fairs.csv, 200 rows) needs a human-authored resource_type column added before import — the importer reads it, never guesses. Files 2–8 need no CSV edits (fixed per-file mapping).

Import mechanics: natural key = normalized link. Malformed rows logged + skipped. Duplicate-link groups collapse with longest-focus-wins, discarded names appended to merged_from. Re-import never overwrites a row with admin_modified=True. Rows missing from a fresh CSV get needs_review=True + auto-unpublished, never deleted.

## Routes / API
- /career-hub — hub, filter chips for resource_type + work_mode
- /career-hub/category/[slug] — industry landing page (SEO surface)
- /career-hub/[slug] — detail page
- /career-hub/fairs — static filtered view (resource_type=career_fair), empty until admin entry
- API: /api/v1/career-hub/ (public), /api/v1/super-admin/career-hub/ (admin)

(Route structure fixed from an earlier draft that would've hit a Next.js build error — [category] and [slug] can't both sit directly under /career-hub/.)

## Notification scope — v1 ships zero Celery tasks, zero beat entries
7-day reminder and click-count rollup both cut as scheduled jobs (click_count becomes a direct F() increment on the tracked-redirect route). Remote-track and local-alert are deferred out of v1 entirely, pending sign-off — see open questions.

## Blockers
1. File 1's 200-row classification hasn't happened yet — real work (~an afternoon), not code. Needs an owner.
2. CSVs aren't on this machine yet — need a path or the files themselves before any import can run against real data.
3. Two open classification calls need sign-off (below) before building against them.
