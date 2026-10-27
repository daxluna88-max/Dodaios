# ADR-0063 - Italian interface language (unreviewed draft) and a static accessibility pass

**Status:** Proposed; implemented locally in `claude/p15-it-a11y` (code, strings and tests WRITTEN, not run; TypeScript not type-checked; `dist` not rebuilt, so nothing is live) · **Date:** 2026-10-27

Nothing was run for this block: no test runner, no type-check, no lint of the frontend, no build, no app, no screen reader, no keyboard session. Only `python3 -m py_compile`, `ruff check` on three Python files (3 pre-existing line-length findings in `config.py`, not from this change) and JSON-reading scripts were used.

## Part A - Italian ("it")

### What was added
- `UiLanguage` now includes `"it"` (`src/i18n/index.ts`), as do the language picker (`LanguagesGroup.tsx`), the first-run language switch (`SetupWizard.tsx`, label "IT"), the date locale (`runs/format.ts`, `it-IT`), the backend `ui.language` literal (`core/config.py`), the route allow-list (`ui/web/settings_routes.py`), the self-mod description and `docs/dodaios-control-api.md`.
- **Not added:** the reply language (`brain.reply_language`, `SUPPORTED_LOCALES`), wake-word and STT lists, and the backend's canned phrases. Choosing Italian changes what the owner reads; the assistant still replies in the language the owner writes in, and backend canned phrases (voice, errors) stay English because `resolve_output_language` returns English for any language without a catalog. That is a separate, larger decision.
- One new English key, `languages_view.options.it` (label and description), is the only change to `en.json`; no existing English text was edited. Other languages show that one key in English via fallback.

### Which namespaces are Italian, and which fall back
Italian (every string is an UNREVIEWED DRAFT):

| Where | Namespace | Coverage |
|---|---|---|
| `locales/it.json` | `control_strip` | complete (120 of 120 keys) |
| | `nav` | complete |
| | `common` | complete |
| | `settings_hub` | complete (the Settings shell: search, groups, back) |
| | `languages_view` | only `title`, `subtitle`, `ui_section`, `ui_hint`, `options.it`; the rest falls back |
| | `language_select`, `jarvis_dock`, `voice_state`, `home` (greeting/hints/composer/theme; the prompt-mode strings fall back) | complete or near complete |
| | `permissions` | the macOS permission toasts (`restart_failed`, `toast.*`) |
| `locales/memory_control/it.json` | `memory_control` | complete |
| `locales/first_run_hardening/it.json` | `first_run_hardening` | complete |
| `locales/personality_limits/it.json` | `personality_limits` | complete |
| `locales/onboarding/it.json` | `first_run`, `app_tour` | complete |

Falls back to English (no Italian yet): in the main dictionary everything not listed above, most importantly `settings_view` (424 keys: the individual settings pages), `topbar`, `sidebar`, `live`, `agent_chat`, `assistant_view`, `profile_view`, `apikeys_*`, `docs_view`, `skills_view` and about 170 more (`it.json` holds 282 of 9333 English leaf keys); and the lazy chunks `marketplace`, `local_models`, `society`, `computers`, `appshot_editor`, `providers`, `pane_review`, `memory`, `self_build`, `ship`.

### Fallback behaviour (read in code)
`resolve(lang, key)` in `i18n/index.ts` looks in this order: the language's main dictionary, the language's loaded chunks, the English main dictionary, the English chunks, then the key itself. So a missing Italian key shows English text, never a raw key, as long as the English chunk is loaded (views ask for their chunk with `useLocaleChunk`). Until the Italian main dictionary has been fetched, `RESOURCES.it` is the English dictionary (same as de/es/pt/zh). A chunk with no Italian file has no `it` loader (`ChunkLoaders.it` is optional), so `loadLocaleChunk` pushes nothing for Italian and English answers. The settings search (`settingsSearch.ts`) reads the resident Italian dictionary, so it only finds Italian words for the translated keys.

### Draft marking and the review process needed
Markers follow the sibling draft locales: `_review_status` / `_status` / `review_status` inside each namespace (`UNREVIEWED DRAFT: needs a native-speaker check`), and `_review_status` at the top of `it.json`. These are machine-assisted drafts written without a native review; no quality is claimed. Before Italian is shown as supported: (1) a native Italian speaker reviews every string, with tone (informal "tu"), the legal-ish privacy and deletion texts, and the Stop/approve wording first; (2) markers are removed only by that reviewer's sign-off; (3) the picker description (which says "draft") is updated then; (4) the remaining namespaces are translated in the priority order above; (5) a screenshot pass at 200% zoom for text that grows (Italian runs about 15-25% longer than English).

### Tests (written, not run)
`src/i18n/it-locale-parity.test.ts` (no foreign keys, same placeholders, no empty values, draft marker present, priority chunks and `control_strip` complete, English fallback for an untranslated key and an Italian-less chunk); `ui-locale-lazy.test.ts` gains `it`. Python tests that assert the exact language set were updated to include `it`; tests that glob every locale file and need complete dictionaries (`overlay style`, `pet states`, `voice start refusal`, `team threads`) now exclude `it` with a comment, because Italian is partial by design. The macOS permission-naming test still covers `it` (hence the `permissions` strings).

## Part B - Accessibility, static audit

Scope: the control strip, memory control (`MemoryControl.tsx`), the first-run window and summary (`SetupWizard.tsx`, `FirstRunSteps.tsx`), personality limits (`PersonalityLimits.tsx`), and the shared `Button`/`Badge`. The phone panel `/m/` does not exist in this branch and was skipped. This is a read of the source and the design tokens, not a conformance test. **WCAG compliance is not claimed.**

### Contrast, computed from the tokens (sRGB relative luminance; badge = text on its hue at 12% over `--background`)
`ControlStrip` uses `bg-surface`, but `surface` is only a border-radius in `tailwind.config`, so the strip has no background of its own and sits on `--background`.

| Pair | Light | Dark | 4.5:1 |
|---|---|---|---|
| State badge `accent` (working, thinking, listening, speaking) | 4.25 | 5.87 | light FAIL |
| `success` badge | 4.31 | 7.30 | light FAIL |
| `warning` badge (stopping, partly stopped, waiting, unknown) | 4.23 | 7.83 | light FAIL |
| `destructive` badge (error) | 4.77 | 4.15 | dark FAIL |
| Default badge (stopped, idle): muted on secondary | 6.92 | 6.38 | pass |
| Muted text on background | 7.55 | 7.62 | pass |
| STOP: white on `--destructive` | 5.81 | 4.38 | dark FAIL |
| Primary button text | 17.94 | 18.93 | pass |
| Focus ring (accent) against background (needs 3:1) | 5.02 | 6.70 | pass |
| Outline button border `--border-strong` against background (3:1 if the border alone identified the control) | 1.88 | 1.69 | below 3:1, text label present |

Fixes: the strip's state badges now use `text-foreground` (the word is in page ink; the wash and rim keep the hue), which is about 17:1 in both themes; STOP in dark uses `hsl(0 72% 50%)`, 4.89:1 (hover 46%, darker, so higher). The shared tokens were not changed (that is a product-wide decision).

### Findings
| Item | WCAG criterion | State | File |
|---|---|---|---|
| STOP first in focus order, always rendered, native button | 2.4.3, 2.1.1 | OK (read) | `ControlStrip.tsx` |
| Visible focus ring on every `Button` (2px accent, offset) | 2.4.7 | OK (read) | `ui/button.tsx` |
| Status badge text contrast (light accent/success/warning, dark error) | 1.4.3 | Fixed | `ControlStrip.tsx` |
| STOP text contrast in dark theme | 1.4.3 | Fixed | `ControlStrip.tsx` |
| State shown by words, not only colour; job and decision states are text | 1.4.1 | OK (read) | `ControlStrip.tsx` |
| Live regions: polite `role=status` is set to `aria-live=off` while an assertive `role=alert` carries errors and waiting decisions, so one announcement per change | 4.1.3 | OK by design; actual speech output NOT TESTED | `ControlStrip.tsx` |
| `aria-controls` pointed at a panel that is not in the DOM while collapsed | 4.1.2 | Fixed (only set when open) | `ControlStrip.tsx` |
| Approve/Deny row could not wrap at narrow width | 1.4.10 | Fixed (`flex-wrap`) | `ControlStrip.tsx` |
| Details toggle changes its label and also sets `aria-expanded` (can read "Hide details, expanded") | 4.1.2 (best practice) | Open | `ControlStrip.tsx` |
| STOP/Resume explanations are a `title` tooltip plus a screen-reader-only description; sighted keyboard users get no visible hint | 3.3.2 | Open | `ControlStrip.tsx` |
| Memory proposals: identical "Approve / Edit / Reject" buttons had no item context | 2.4.6, 4.1.2 | Fixed (`aria-describedby` the note text; same for trash rows) | `MemoryControl.tsx` |
| Edit hides its own button, so focus was lost when editing started | 2.4.3 | Fixed (`autoFocus` on the textarea) | `MemoryControl.tsx` |
| Delete dialog radio group had no name | 1.3.1, 4.1.2 | Fixed (screen-reader-only legend) | `MemoryControl.tsx` |
| Trash row path was truncated with an ellipsis | 1.4.10 | Fixed (wraps) | `MemoryControl.tsx` |
| Delete dialog: Radix Dialog gives focus trap, Escape and focus return | 2.1.2, 2.4.3 | OK (library behaviour, not run) | `MemoryControl.tsx` |
| After approve/reject/restore the list changes with no announcement and focus drops to the page | 4.1.3, 2.4.3 | Open | `MemoryControl.tsx` |
| First-run window is `aria-modal` but did not take focus | 2.4.3 | Fixed (focuses the dialog on open) | `SetupWizard.tsx` |
| First-run window has no focus trap, no Escape, background still tabbable | 2.1.2 / 2.4.3 | Open | `SetupWizard.tsx` |
| Language switch buttons about 20px high | 2.5.8 (WCAG 2.2) | Fixed (min 24px) | `SetupWizard.tsx` |
| Language switch is a `radiogroup` where every item is a tab stop (no arrow keys) | 2.1.1 (pattern) | Open | `SetupWizard.tsx` |
| Spinners used `animate-spin` regardless of the reduced-motion setting | 2.3.3 (AAA), good practice | Fixed (`motion-safe:`) in the setup files; other spinners in the app not audited | `SetupWizard.tsx`, `FirstRunSteps.tsx`, `ConnectStep.tsx` |
| First-run summary: definition lists, text for on/off states, `role=status` for the unfinished-setup note, `role=alert` for apply errors | 1.3.1, 1.4.1, 4.1.3 | OK (read) | `FirstRunSteps.tsx` |
| Personality limits: static headings and text, values as words and numbers | 1.3.1, 1.4.1 | OK (read) | `PersonalityLimits.tsx` |
| Button height: `sm` is 32px, `default` 36px (24px minimum met; 44px not met) | 2.5.8 AA met; 2.5.5 AAA not | Open (AAA) | `ui/button.tsx` |
| Orb/avatar/waveform check `prefers-reduced-motion` in code (VoiceOrb, DeckOrb, VoiceGlow, ChatStage, VoiceWaveform); Gigi has a CSS rule | 2.3.3 | OK (read); no global fallback rule exists | several |
| Reflow at 200% / 400%: strip and approvals use `flex-wrap`, details grid is one column below `md`, long text wraps; Italian strings are longer and `Button` is `whitespace-nowrap` | 1.4.4, 1.4.10 | Plausible from classes; NOT MEASURED | - |

### Needs a real test (NOT RUN)
Screen reader pass (NVDA/JAWS on Windows, VoiceOver on macOS) of the strip: that a decision is announced once and a state change politely; keyboard-only pass over strip, memory control, delete dialog and the first-run window (tab order, no trap, focus return); 200% and 400% zoom and a 320px width, in English and Italian; forced-colors mode; the real rendered contrast of every theme (the figures above assume the page background is `--background` and ignore the tint of any parent); reduced-motion with the OS setting on; touch on a real device. `ControlStrip.a11y.test.tsx` encodes the static expectations (written, not run).

## Consequences
Italian is selectable and honest about being a draft; it is partial on purpose, with English filling gaps. Until review, treat it as a preview. The open accessibility items above are not hidden by the fixes.
