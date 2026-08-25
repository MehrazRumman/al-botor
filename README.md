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


# CV Builder — Adding New CV Templates

**Frontend + Backend implementation plan**

| | |
|---|---|
| Frontend | `/home/coder/rn-jobforce/rn-frontend` (Next.js) |
| Backend | `/home/coder/rn-jobforce/rn-backend` (Django) |

**Goal:** go from 2 templates (*Professional*, *Modern*) to 6–8, without paying the per-template cost six times by hand. The template list is supplied separately; Phase 3 is the recipe applied to each one.

---

## 1. How it works today — 3 parallel render paths, all hardcoded to 2 ids

| Path | Implementation | Dispatch |
|---|---|---|
| **React preview** (on screen) | `components/cv-builder/CVTemplate1.tsx` (Professional: dark slate header banner)<br>`components/cv-builder/CVTemplate2.tsx` (Modern: dark sidebar + white main) | `if (selectedTemplate === 'modern')` in `CVPreview.tsx:88` and `PDFOptimizedCV.tsx:103` |
| **Real PDF download** (Builder + Tailor) | `rn-backend/ai_services/simple_cv_pdf.py` (ReportLab) | `if template == "modern"` at line 94 |
| **HTML preview / print** (Tailor) | `rn-backend/ai_services/cv_html_generator.py` | `if template.lower() == "professional"` at line 11 |

Callers:

- `accounts/views.py:4395` — `builder_save_cv` → `POST /accounts/jobseeker/cv/builder-save/`
- `ai_services/views.py:3379` — `generate_cv_pdf` → `POST /ai-services/generate-cv-pdf/`
- `ai_services/views.py:3563` — `generate_cv_html` → `POST /ai-services/generate-cv-html/`
- Frontend caller for the HTML path: `utils/backend-cv-generator.ts`

> **Every new template needs 3 independent implementations** (React/styled-jsx, ReportLab, print-HTML) plus a skeleton thumbnail. That is the cost driver — Phases 0–2 exist to reduce it.

---

## 2. Landmines in the current code (fix regardless)

- **BUG** — `rn-backend/accounts/views.py:4390`
  ```python
  mapped_template = "modern" if template == "modern" else "professional"
  ```
  Any new template id silently downloads as Professional. New templates will look correct on screen and wrong in the downloaded PDF.
- **BUG** — `rn-backend/ai_services/views.py:3361` and `:3557` — hardcoded whitelist `["professional", "modern"]` with silent fallback.
- **BUG** — `hooks/useCVData.ts:177-181` and `:352` — defaults to `"template1"`, an id no template matches. Existing users have stale localStorage values needing migration.
- **SMELL** — `components/cv-builder/TemplateSelection.tsx:95` — ignores the `skeletonComponent` field it already defines and uses a ternary instead.
- **SMELL** — `CVData` is duplicated verbatim in 4 files: `hooks/useCVData.ts`, `CVTemplate1.tsx`, `CVTemplate2.tsx`, `PDFOptimizedCV.tsx`. Any new field must be edited in all four.

---

## Phase 0 — Single source of truth

### Frontend

- **NEW** `lib/cv-templates/registry.ts` — exports `CV_TEMPLATES[]` and the `CVTemplateId` union.
  Entry shape: `{ id, name, description, category, industries[], features[], atsSafe, component, skeleton }`
- **NEW** `lib/cv-templates/types.ts` — move `CVData` here; delete the 4 duplicated copies.

### Backend

- **NEW** `rn-backend/ai_services/cv_templates/registry.py`
  - `TEMPLATE_REGISTRY = { id: {label, pdf_builder, html_builder} }`
  - `VALID_TEMPLATES`
  - `normalize_template(id)` — validate + fall back to `"professional"`
- **NEW** `GET /api/v1/ai-services/cv-templates/` in `ai_services/urls.py` + `views.py`, so the two registries cannot drift.

---

## Phase 1 — Refactor existing code onto the registry

*No new templates yet. Shippable on its own, and it fixes the bugs in section 2.*

### Frontend

| File | Change |
|---|---|
| `components/cv-builder/TemplateSelection.tsx` | Map over `CV_TEMPLATES` instead of the inline array (L21-35); grid `lg:grid-cols-2` → `sm:grid-cols-2 lg:grid-cols-3`; use `template.skeletonComponent` instead of the L95 ternary; replace the hardcoded "Both templates…" tips block (L118-126) with per-template `industries` copy; add category filter chips once >4 templates exist |
| **NEW** `components/cv-builder/TemplatePickerGrid.tsx` | Extract the card grid so Builder **and** Tailor share one picker |
| `components/cv-builder/CVPreview.tsx:83-94` | Registry lookup for `renderTemplate()` and `templateName` |
| `components/cv-builder/PDFOptimizedCV.tsx:53-107` | Same registry lookup |
| `hooks/useCVData.ts:176-182`, `:352` | Default `"professional"`; add `migrateLegacyTemplateId()` — `template1`→`professional`, `template2`→`modern`, unknown→`professional` |
| `components/cv-services/tailor/TailorService.tsx:402-437` | Replace the two hand-written buttons with `<TemplatePickerGrid>`; L449 download label from the registry |
| `hooks/cv-services/use-cv-tailor.ts:222`, `:267` | Drop `=== "modern" ? "modern" : "professional"`, pass the id through |
| `utils/backend-cv-generator.ts:44`, `:120` | Type `'professional' \| 'modern'` → `CVTemplateId` |
| `components/cv-services/feature-info-dialog.tsx:24,40` | Copy: "Choose from two polished, ATS-friendly templates" → N |
| `components/cv-builder/StepIndicator.tsx:129` | Copy |

### Backend

| File | Change |
|---|---|
| `accounts/views.py:4390` | → `normalize_template(...)` — **the download-path bug** |
| `ai_services/views.py:3361`, `:3557` | → `VALID_TEMPLATES` |
| `ai_services/simple_cv_pdf.py:91-96` | → registry dispatch |
| `ai_services/cv_html_generator.py:8-17` | → registry dispatch |

---

## Phase 2 — Shared primitives (before writing any new template)

### Frontend — **NEW** `components/cv-builder/templates/shared/`

- `useDynamicSizing.ts` — lift the autoshrink `useMemo` out of `CVTemplate1.tsx:49-84`
- `A4Page.tsx` — the `210mm`/`297mm` wrapper + `isPreview` clamp + `data-pdf-mode`
- `Section.tsx`, `ExperienceItem.tsx`, `EducationItem.tsx`, `SkillList.tsx`
- `tokens.ts` — palettes/fonts, so a template ≈ tokens + layout instead of 600 lines of copied CSS

### Backend — **NEW** `rn-backend/ai_services/cv_templates/`

- `pdf/_common.py` — move `_personal` / `_experience_items` / `_education_items` / `_skills_list` out of `simple_cv_pdf.py:22-89`; add `base_styles()`, `header_band()`, `section_rule()`, `two_frame_doc()`
- `html/_common.py` — the `@page` block, `print-color-adjust` rules, and the section/item HTML builders currently inlined in `cv_html_generator.py`
- Split per-template builders into `pdf/<id>.py` and `html/<id>.py` — otherwise `simple_cv_pdf.py` (498 lines) and `cv_html_generator.py` (1079 lines) become unmaintainable at 8 templates.

---

## Phase 3 — Per-template implementation (repeat for each supplied template)

Each template = **5 artifacts**: 2 frontend files, 2 backend files, 1 registry pair.

### F1 — `components/cv-builder/templates/<Id>.tsx`

- Props `{ data, isPreview }`, same shape as today's `CVTemplate1Props`
- Wrap in `<A4Page>`; consume `useDynamicSizing(data)` so long CVs shrink instead of spilling onto page 2
- styled-jsx in three tiers, mirroring `CVTemplate1`:
  1. base / screen
  2. `@media print`
  3. `[data-pdf-mode="true"] … !important` — forces `210mm` widths
- Keep the semantic/ATS markup `CVTemplate1` uses — `<header role="banner">`, `<h1 itemProp="name">`, schema.org itemProps, `<section aria-labelledby>`. This is what makes the "ATS-friendly" claim actually true.
- Guard every section with an emptiness check, e.g. `data.workExperience.some(exp => exp.jobTitle || exp.employer)`, so partially-filled CVs never render orphan headings.

### F2 — `components/cv-builder/templates/skeletons/Skeleton<Id>.tsx`

- Pure-Tailwind grey-block thumbnail; no data, no props. Model: `components/cv-builder/SkeletonCVTemplate1.tsx`
- Renders inside the `aspect-[3/4]` box in the picker
- Must visually match its real template, or the picker lies to the user

### F3 — Registry entry in `lib/cv-templates/registry.ts`

`{ id, name, description, category, industries[], features[] (badge chips), atsSafe, component, skeleton }`

This is the **only** frontend edit needed to make the template appear in the builder picker, the tailor picker, the live preview and the PDF-mode preview.

### B1 — `rn-backend/ai_services/cv_templates/pdf/<id>.py` (ReportLab)

This is what users actually download — the expensive file, and the only place where screen and PDF can silently diverge. Pick the archetype:

**(a) Single-column flow** — model: `_generate_professional_pdf` (`simple_cv_pdf.py:101`)
`SimpleDocTemplate` + a `story` list of `Paragraph` / `Table` / `HRFlowable`. Full-bleed header = one-cell `Table` with a `BACKGROUND` `TableStyle`. Multi-page behaviour comes for free.

**(b) Sidebar / two-column** — model: `_generate_modern_pdf` (`simple_cv_pdf.py:270`)
`BaseDocTemplate` + explicit `Frame`s + `FrameBreak`, an `onPage` canvas callback painting the column background, **and a second `PageTemplate` for page 2+ that reverts to one full-width frame**. Without that fallback ReportLab reuses the sidebar frame and overflow content lands inside a 32%-wide column. Every two-column template inherits this requirement.

Build `ParagraphStyle`s from `_common.base_styles()` overridden with the template's tokens — do not redeclare all 10 styles per template.

### B2 — `rn-backend/ai_services/cv_templates/html/<id>.py`

Returns a full standalone HTML document (`@page size: A4`, `print-color-adjust: exact`, inline `<style>`) mirroring the React component. Used by the Tailor preview/print tab via `POST /ai-services/generate-cv-html/`. The CSS is largely the React styled-jsx block with the `[data-pdf-mode]` tier baked in. Model: `generate_professional_template_html` (`cv_html_generator.py:19`).

### B3 — Registry entry in `rn-backend/ai_services/cv_templates/registry.py`

Points at the two builders. Adding it automatically widens `VALID_TEMPLATES`, so both `ai_services` views and the builder-save download accept the new id.

> **Recommended order per template:** React → skeleton → ReportLab → HTML, and diff a rendered PDF against a screenshot before starting the next one.

---

## Phase 4 — CVData extension (only if the supplied templates need new fields)

Current model:

```ts
personalInfo   { fullName, jobTitle, email, phone, address, summary }
workExperience [] { id, jobTitle, employer, location, startDate, endDate, current, description }
education      [] { id, degree, institution, location, startDate, endDate, description }
skills         [] { id, skill, level }
```

The reference sample additionally needs a **photo**, an **"Online Presence" link list**, and **categorised skills with sub-skills** — none exist today. If templates need them:

| File | Change |
|---|---|
| `lib/cv-templates/types.ts` + `hooks/useCVData.ts` | Extend `CVData`, `getInitialData`, and the add/update/remove callbacks |
| `components/cv-builder/PersonalInfoForm.tsx` | Photo upload (base64 into localStorage is simplest; a real upload endpoint if payload size matters) + links repeater |
| `components/cv-builder/SkillsForm.tsx` | Category grouping + sub-skill line |
| `components/cv-builder/StepIndicator.tsx`, `components/cv-services/builder/BuilderService.tsx` | Only if a new wizard step is added |
| `rn-backend/accounts/views.py` (`_builder_cv_data_to_parsed_structure`, ~L4330) | Carry new fields into the stored parsed-CV JSON |
| `rn-backend/ai_services/cv_templates/pdf/_common.py` | `_personal` / `_skills_list`; photo = ReportLab `Image` flowable from base64, with a size/format guard |

All templates must degrade gracefully when these fields are absent — a photo-less user must not get an empty circle.

---

## Phase 5 — QA matrix

Per template, all 4 surfaces:

1. Builder live preview (`CVPreview`)
2. Builder PDF download — `POST /accounts/jobseeker/cv/builder-save/`
3. Tailor HTML preview — `POST /ai-services/generate-cv-html/`
4. Tailor PDF download — `POST /ai-services/generate-cv-pdf/`

Screen and PDF must match — that is the entire point of mirroring ReportLab.

**Content stress cases:** empty summary; 10 work experiences (page-2 / frame-overflow behaviour); 40-character job titles and company names; 30 skills; no education entries; all optional fields empty.

**Other:**

- Set `atsSafe: false` on any sidebar / two-column / graphical template and badge it in the picker — those genuinely parse worse, and the product sells "ATS-friendly"
- Legacy localStorage template id migration
- Picker grid layout on mobile
- Skeleton thumbnail vs real render parity

---

## Suggested sequencing

| PR | Contents |
|---|---|
| 1 | Phase 0 + Phase 1 — registry + refactor; fixes the download-path bug |
| 2 | Phase 2 — shared primitives, frontend + backend |
| 3 | Phase 4 if needed — CVData extension + forms, before any template that depends on photo / links / skill categories |
| 4+ | Phase 3, one PR per template (or batched 2–3), each ending with Phase 5 QA for that template |
