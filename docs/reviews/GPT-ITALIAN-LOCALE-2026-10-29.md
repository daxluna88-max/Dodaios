# Italian locale implementation for Claude review

Base: `686b0abb9cdaa2a680b9a761ee65db2f1242b43c` from `claude/mobile-nav-rail`.
Local branch: `gpt/it-locale`. No remote branch, PR, merge, application build,
Actions run, provider invocation or VPS change is included in this delivery.

## Implemented

- All 13 English frontend dictionary files have Italian counterparts: the main
  dictionary, all ten lazy sections and both ship dictionaries. There are
  12,923 Italian string values. Keys, types, placeholder multiplicity and URLs
  match the English source. Technical identifiers and brands are preserved.
- Italian interface and reply choices are registered independently; recognition
  already accepts Italian. Settings and onboarding expose the new choice.
  Selecting interface Italian does not change reply or recognition preferences.
- The locale is lazy loaded, applies `html lang=it`, uses Italian numeric/date
  formatting and sends the Italian locale with chat commands.
- UI configuration validation, settings/control APIs and persisted language
  settings accept Italian. The central backend phrase catalog is translated
  completely (312 leaf keys, 364 strings counting variants).
- Known local command failures are translated without rewriting arbitrary
  provider diagnostics, command IDs, permission IDs or model prompts.
- Italian STT tag aliases survive catalog registration. The output guard checks
  the existing conservative Italian detector before its four-language scorer,
  preventing valid Italian from being rejected as Spanish. Tool confirmation
  questions use the Italian catalog.

## Validation performed locally

- 80 frontend tests pass across Italian integration/parity, language settings,
  lazy loading and affected existing language parity checks. They use real
  React/Zustand and jsdom, with the event store mocked in the Italian integration
  tests. HTTP fetches are mocked; this is not Safari or a live server test.
- 19 Python unittest checks pass: catalog/contract boundaries, actual command
  execution and SQLite receipts, tag/output-language regressions, tool question
  formatting, and an actual temporary-file TOML write/load round trip.
  Heavy method/route annotation checks are explicitly isolated using AST; they
  do not prove live HTTP behavior. The command tests isolate an optional secret
  guard import and do not exercise paid providers.
- A focused TypeScript type check passes for i18n/settings and their new tests
  with a typed event-store stub. This is not the full application type check.
- URL comparison reports no changes; `git diff --check` is clean.
- `tests/integration/test_ui_language_route.py` is updated for Italian options
  and preservation of independent settings, but was not executed here.

Frontend command (normal development dependencies required):

```sh
cd dodaios/ui/web/frontend
npm test -- src/i18n/it-language.test.ts src/i18n/it-locale-parity.test.ts src/views/settings/LanguagesGroup.test.tsx src/i18n/ui-locale-lazy.test.ts src/i18n/pt-locale-parity.test.ts src/i18n/zh-locale-parity.test.ts src/i18n/onboarding-i18n-parity.test.ts
```

Backend command (run from repository root):

```sh
python -m unittest tests.test_italian_language_support tests.test_italian_chat_controls tests.test_italian_config_roundtrip
```

## Acceptance still open

Full Italian PRODUCT acceptance is not closed merely by dictionary parity.
Some runtime text is generated outside these catalogs: raw provider diagnostics,
command availability/restart notices, instant acknowledgments, speech pipeline
fallbacks, number spelling and spawn announcements still need a separately
bounded language pass. Several voice wrappers retain fixed four-language lists;
adding Italian blindly can disable acknowledgments or index missing tables.

The UI still defaults to the saved language (English for a new default config).
The intended installed profile must explicitly select Italian. Nothing here
silently migrates unrelated user language settings.

Real iPhone testing remains: settings/onboarding choices, narrow navigation sheet,
opening existing typed and agent chats, long model names, keyboard open, reload,
and tunnel reconnection. This patch does not implement reload session selection
or prove that every historical conversation is discoverable.

Existing source copy translated faithfully may describe Codex as a main Brain;
that claim needs product clarification against the installed provider policy.
This localization does not enable Codex as the main Brain.

## Integration and rollback boundary

Claude reviews this source patch first. A remote push needs Dax's specific
approval under the existing publication restriction. Building/copying a bundle,
restarting the backend and changing the VPS configuration require the applicable
operational authorization; none is implied by this local delivery.

Do not rewrite Claude's App.tsx, Sidebar.tsx, SettingsHubView.tsx,
AgentComposer.tsx or index.css. Their mobile fixes are preserved.

For later deployment, retain the prior source revision, served bundle and
language configuration before applying the reviewed patch. A rollback must
restore the previous configuration language values as well as the previous
source/bundle: the old backend does not accept `ui.language=it`. Do not reset a
profile or reinstall/login to undo this change.
