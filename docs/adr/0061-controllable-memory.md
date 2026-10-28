# ADR-0061: Controllable memory (proposals, real deletion, export)

Status: proposed (block 10). **Nothing was run**: no pytest/vitest, no tsc, eslint or vite, no app, no CI.
Python was only `py_compile`d and `ruff check`ed. The TypeScript was **not type-checked** and `dist` was **not
rebuilt**, so **the UI is not live**. Tests were written and not run.

## Context

ADR-0058 section 4 promised a Memory panel with per-note approval and a real Delete "only after this is specified".
Before this ADR: the vault offered Archive only (`AtomicWriter` moves a page to `_archive/`), the learning loop
(`dodaios/memory/learning`) wrote reviewer changes straight into the notebooks, and the first-run guide delivered
"ask before saving" only as "learning is off until you turn it on" (ADR-0057).

## Decision

### 1. Proposals (per note, never bulk)

- With learning on and `[memory.learning] require_approval` not set to `false` (read with default `true`; the key is
  not declared in `core/config.py`, which allows extra keys), `start_learning` gives the loop a `ProposalStore`
  (`dodaios/memory/control/proposals.py`, file `.memory-proposals.json` next to the Jarvis notebooks).
- A reviewer change (add / replace / remove) becomes a record in state `proposed`. It is **not in the notebooks**, so
  it is in no prompt, snapshot, recall block or voice instruction, and nothing sends it anywhere. Only
  `POST /api/memory-control/proposals/{id}/approve` writes it, through `JarvisNotebook.apply` (same guard, budget and
  ledger as before; ledger source `approved by owner`). The owner may send an edited `text` (edit + approve); the
  edit passes the same `guard.refusal`. A failed write leaves the proposal open.
- `reject` marks the record `rejected`, drops its text, evidence and before-text, and keeps only a hash of
  (target, operation, entry id, normalized text). An identical proposal is not stored again. Hashes are per product.
- Record fields: source (`review: <why>` plus the seat that proposed it), evidence quote, product, provenance.
  There is no approve-all endpoint or method; a test asserts it.
- Product binding: every record carries `current_product()` (`work` / `consumer` / `shared`). A store only lists,
  approves or rejects records of its own product; another product's id answers 404.
- Provenance: `local_only` is always `true`. `provenance` is `restricted` when the material has a positive
  local-only signal (`brain/provenance.withheld_from_remote_store`), else `unlabelled`. A background review has no
  per-turn label, and absence of a label is not read as shareable.
- Explicit requests ("remember that X", the remember tool) still write directly: the owner just asked.
- While approval is required the compactor is not wired, because it rewrites approved notes without asking. The
  notebook may therefore fill up (refused at 125 % of budget) until the owner deletes notes.

### 2. Delete is not Archive

Archive moves a page aside and keeps it (and its copy in `_archive` is not in backups or search). Delete is a
separate action, with two modes chosen by the owner (`POST /api/memory-control/pages/delete`):

- **logical**: the page moves to `<vault>/.trash/<id>/` with a manifest; restorable for 30 days (`RETENTION_DAYS`),
  then removed on the next trash read. `.trash` is dot-prefixed: the page walk, FTS indexer, semantic sync and
  backup snapshot all skip it.
- **permanent** (`ask` risk tier, requires `confirm_permanent`): the page is unlinked; no undo. Also available for a
  trash entry.

Only `knowledge` and `journal` pages can be deleted this way. Learned notes (USER.md / MEMORY.md entries) have
`POST /api/memory-control/notes/delete`, which is **permanent only** (no logical mode for them).

Every delete returns a report: per store done / not done with detail, the pages that link to or repeat it, and the
residual list. The UI shows the report and, before confirming, a fixed "What stays" list. The UI never calls
Archive deletion; the delete dialog says so. The residual sentences come from the server in English and are not
translated (limit).

#### Deletion matrix (page in the vault)

| Store | Removed on permanent delete? | How | Residual |
|---|---|---|---|
| Page file in the vault | yes | `unlink` | disk free space may keep the bytes (not wiped) |
| `_archive/<same path>` copy | yes | `unlink` | an archive copy at another path is not found |
| `.trash` copies of the same path | yes | `rmtree` | none known |
| Search index (`wiki_fts`) | yes, if the DB is found | `fts_index.remove_page`, `secure_delete=ON`, FTS `optimize` | if the DB path is wrong the report says "not done" |
| Embedding index (`wiki_semantic_*`) | yes, if the DB is found | `semantic_index.remove_page`, `secure_delete=ON` | same |
| Candidate journal rows (`target_path`) | yes | `DELETE` | an unconsolidated candidate fact that quotes it has no path and stays |
| In-process caches (page parse, vault model, notebook prompt cache) | yes | invalidated | each cache listed as cleared / NOT cleared; other caches not enumerated |
| Recall caches of an external `TurnMemory` provider | **no** | not reachable from here | provider-owned; unverified |
| Pages that link to or repeat it | **no** | listed only (wikilink or a verbatim 40+ char line) | owner decides; paraphrases are not found |
| `log.md` activity line | **no** | not rewritten | title / summary line remains |
| Backup archives (`wiki-backups/`) | **no** | counted read-only (`archives_with_page`) | stay until rotation: newest 10 kept, **by count not age** |
| Exports already made | **no** | n/a | owner's file |
| Copies sent to a remote engine earlier | **no** | n/a | cannot be recalled |
| Running voice session instructions | **no** | n/a | until the session is renewed |
| Conversation / episode store (`RecallStore`) | **no** | out of scope | may hold the same facts |

Logical mode clears the same indexes and caches (so the page is not recallable) but keeps the file in `.trash`.
Learned-note delete: removes the entry, scrubs the learning ledger (`.learning-ledger.jsonl` and `.1`) of every line
with that entry id or text, clears the notebook cache; backups, exports, remote copies, running sessions and free
space stay.

### 3. Backup and export

- `GET /api/memory-control/export`: a zip of this product's live `.md` pages (`memory/...`) plus `manifest.json`
  (product, time, per-file sha256, what is not included, backup facts, `changed_during_export`). Each file is read
  twice-checked by (mtime, size); an unstable file is named, not silently included. It refuses a vault that sits in
  the other product's data directory (sibling `-work` / `-consumer`) and an export over 200 MB.
- Not in the export: `_archive`, attachments, trash, dot files (ledgers, locks), indexes, caches, backups, keys.
- Backup facts, from `BackupManager`: a tar.gz snapshot before each `AtomicWriter.apply`; `_archive`, `attachments`
  and dot folders are excluded; the newest 10 are kept (`DEFAULT_MAX_BACKUPS`). `GET /api/memory-control/status`
  states it; the panel shows the count.

### 4. Isolation

Work and Consumer use separate data directories and vaults (ADR-0054); proposals are additionally product-keyed;
export refuses the other product's tree. This is a code-path check, not a proof (see Limits).

### Routes and auth

`/api/memory-control/{status,proposals,proposals/{id}/approve|reject,pages/delete,trash,trash/restore|delete,
notes/delete,export}` in `dodaios/ui/web/memory_control_routes.py`, registered in `server.py`. Authentication,
Host/Origin checks (CSRF defence for unsafe methods) and credential checks are the global `SurfaceSecurity` layer
that covers every `/api` route; writes carry `x-jarvis-risk-tier` (`monitor`, permanent deletes `ask`). Not
verified by running.

### UI (source only)

`components/memory/MemoryControl.tsx` (panel on Memory home: proposals with Approve / Edit / Reject, trash with
Restore / Delete for good, Export link, backup fact; `DeletePageDialog` from the page actions), `lib/memoryControlApi.ts`,
and a new locale chunk `memory_control` (en, de, es, pt, zh; the other four are **unreviewed drafts**, flagged by
the `_review_status` key). Minimal edits: `MemoryView.tsx`, `PageReader.tsx`, `i18n/index.ts` (chunk registry).
Delete for learned notes is an API only; the panel has no button for it yet.

## Limits (honest)

- **Not run, not type-checked, dist not rebuilt: the UI is not live.** The semantics above are from reading code.
- The wiki curator's automatic page writes from conversations (candidate journal -> consolidator -> `AtomicWriter`)
  are **not** proposal-gated; only the learning loop is. If the wiki curator is enabled it can still write pages.
- The approved note carries no per-entry privacy label inside the notebook; the label is kept on the proposal
  record and shown, but a remote engine's prompt that includes the notebook snapshot is **not** filtered by it.
- The reviewer's input turns went to the lead's provider when the review ran (possibly remote); proposing does not
  change that.
- Backups are not rewritten on delete; "not sent anywhere" covers the proposal store, not earlier traffic.
- Deletion does not wipe free disk space or SSD wear-levelled blocks; `secure_delete` covers SQLite pages only.
- Derived-note detection is link and verbatim-line based; paraphrases and summaries without a link are missed.
- Logical-delete restore does not re-index; search may miss the page until a rebuild (not verified).
- Only a vault in an `...-work` / `...-consumer` directory is checked for cross-product export; a custom vault path
  elsewhere is not attributable.
- Concurrency with `AtomicWriter` / the file watcher is not coordinated: delete moves files directly.

## Controls prepared, NOT RUN

Written tests: `tests/unit/memory/control/test_proposals.py`, `test_loop_proposals.py`, `test_deletion.py`
(approval gate, edit + guard, no re-proposal after reject, product isolation, no bulk approve, logical restore /
window / purge, permanent delete of file + archive copy + FTS + semantic rows, derived report, backup counting,
unsafe paths, symlink, export manifest and other-product refusal).
Not written: route tests (auth/CSRF), notebook-entry ledger scrub, vitest for the panel, locale parity test.
Manual checklist (all open): approve / edit / reject one note; confirm a pending note is absent from the realtime
prompt; logical delete then restore; permanent delete then search for a phrase; export opens and lists only own
product; two products side by side.

## Block 17: GPT round 4 (H2, H3, H4)

**Nothing was run**: no pytest, no CI, no app, no filesystem experiment. Python was only read, edited,
`py_compile`d and `ruff check`ed. The new tests (`tests/unit/memory/control/test_block17_gpt4.py`) are **written, not
run**. Existing tests that assert the old behaviour were **not** updated and will likely fail until the owner runs and
adjusts them (see "Expected test fallout"). No locale keys were added: no new user-facing string (API messages are
English, like the rest of the module). This section supersedes the Limits bullet "The approved note carries no per-entry
privacy label".

### H2 (HIGH) approved note kept its local-only label only on the proposal: **fixed for the Jarvis notebooks, partial overall**

- **Entry format** (`society/notebook.py`): `Entry` gains `local_only` (default True) and `provenance` (default
  `unavailable`), stored in the entry marker only when not default, so an unlabelled entry stays byte-identical and
  every old or hand-written entry reads as local-only/UNAVAILABLE. Only `local_only` literally `false` together with
  provenance `unrestricted` is shareable (`remote_shareable`); any malformed or partial pair collapses to local-only.
  `change()` takes the labels of the text being written, so a replace never inherits an old entry's label.
- **Write**: `JarvisNotebook.apply(local_only=True, provenance="unavailable", apply_id="")` stores the label (user,
  memory and soul targets) and records it in the ledger. `ProposalStore.approve` always passes `local_only=True` and
  `restricted` or `unavailable`, never `unrestricted`: approval to remember is not consent to send remotely. Nothing in
  this block lets the owner mark a note shareable (open item, no UI). `keep_explicit` (remember tool) stores the label
  from the turn channel (`storage_label`, positive UNRESTRICTED only); the post-turn fallback save is local-only.
- **Read doors**: `snapshot_block`, `memory_block`, `dated_notes` default to `remote=True` (fail closed: shareable
  entries only, no header and no "N notes not shown" counts when none qualifies). `remote=False` is for a PROVEN local
  destination and marks the current turn RESTRICTED when the text includes a local-only entry, so the answer cannot be
  spoken by a remote TTS or replayed to a remote engine. `brain/manager.py`, `realtime/session.py` and `live/session.py`
  (not edited) use the default, so they are remote-safe. `Soul.render_for_prompt(remote=True)` withholds unlabelled
  learned SOUL notes. `SocietyMemory.head` and `recall` hide unlabelled entries of the LEAD notebooks
  (`society/jarvis/{USER,MEMORY}.md`). The review and compaction seats are treated as remote (no positive `is_local`
  signal exists) and are shown shareable entries only. `proactivity.upcoming_notes` (the owner's own page) uses
  `remote=False`.
- **Choice**: fail closed everywhere the notebook has no destination knowledge. Cost: with no way to mark an entry
  shareable yet, a remote engine and the manager's system prompt see **no** learned notebook entries, local engines
  included (the manager builds one prompt for all engines); the reviewer cannot see or merge local-only notes.
- **Residual / open**: (1) the curator/wiki write paths that bypass proposals are a **separate declared open item** and
  are unchanged; (2) other agents' notebooks and agent writes through `SocietyMemory.write` get the default local-only
  label but their read paths are not filtered; (3) the notebook files are vault pages: wiki search, FTS/embedding
  indexing and the turn-memory provider can still surface their raw text to a remote engine (the provider's own labels
  decide; the files themselves carry markers but the index does not); (4) the LEAD's own tool results are not
  destination-aware; (5) no UI to share a note remotely; (6) the exported zip includes the markers as written.

### H3 (HIGH) trash manifests trusted as paths: **fixed in code, not exercised**

`memory/control/deletion.py`: `_validate_manifest` (object schema; `id` equals the folder name and the requested id;
finite non-bool timestamps; optional `sha256` shape; the `product` recorded at deletion, new field, must be the current
one, legacy manifests without it are accepted; canonical relative `.md` path via `canonical_rel`: no `..`, empty or `.`
part, absolute, drive, backslash, NUL, dot or reserved first part), `_is_linked` (`lstat`: symlink, junction or any
reparse point), `_confine` (strict resolve of the base, every existing component must be a real entry, strict resolve
of the deepest existing part must stay inside the base). Used by `_trash_entry`, `list_trash` (invalid entries are not
listed), `restore` (source and destination confined, linked trash root/folder/ancestors refused, the trashed file's
SHA-256 must equal the manifest's, destination re-checked after `mkdir`), `permanent_delete` (trash copy, older trash
copies via validated ids, `_archive/<rel>` confined; linked archive refused), `purge_expired` (acts only on validated
ids, an invalid entry is left alone), `_safe_page` and `logical_delete` (manifest written before the page moves; an
undeleted move failure removes the empty trash folder). Invalid entries answer 404 "Unknown trash entry".
- **Residual**: a TOCTOU window remains between the checks and `os.replace`/`rmtree` (another local process swapping a
  directory for a link); no handle-based (`openat`) walk. `shutil.rmtree` is not given a no-follow guarantee beyond its
  own symlink handling. A corrupt entry stays on disk forever (listed nowhere) until removed by hand.
  `canonical_rel` also rejects live pages under dot folders or with a part ending in `.` or space (none are walked as
  pages anyway).

### H4 (HIGH) (a) proposal approval not atomic; (b) note deletion never scrubbed proposals: **(a) fixed in code, partial for process death at the final write; (b) fixed**

- **(a)** `memory/control/proposals.py`: all stores of one file share one process-wide `RLock` from a registry keyed by
  the real path, plus a cross-process `filelock` on `.memory-proposals.json.lock` (10 s, then 503). `approve` writes the
  record as `applying` with a fresh `apply_id` and the exact text **before** calling `apply` (durable, fsynced
  replace), passes `apply_id` (also written to the notebook ledger) and, on success, writes `approved`. `ValueError`
  from `apply` (refused before any write) restores `proposed`. An `OSError` is ambiguous and goes through the probe.
  A record found `applying` is resolved by `JarvisNotebook.probe_applied` (ledger line with the id is definitive;
  otherwise the notebook text is compared): `applied` finishes it as `approved` (note shows the recovery), `not_applied`
  returns it to `proposed` and tells the owner to approve again, anything else becomes `apply_unknown`, shown with
  its recovery text and only dismissable (`reject`). `apply` is **never** called for a record that was already
  `applying`. `approve` for two concurrent requests applies once (the second sees `approved`/`applying`). The route
  lists `proposed`, `applying` and `apply_unknown` and passes the probe. `reject` is refused while `applying`.
- **Residual**: the probe for `remove`, and for a `replace` whose new text equals the old, cannot prove an outcome and
  ends in `apply_unknown`; a crash between the notebook write and the ledger append falls back to the text compare;
  a stale `.lock` file is harmless but stays; the front end shows the new states as raw state names (UI source is not
  rebuilt and not live).
- **(b)** `deletion.delete_notebook_entry` now calls `ProposalStore.scrub_note` for every record of the file (all
  states and products): matching text, applying text, before-text, evidence containing the note, or entry id; the text,
  evidence, before, applying text and hash are blanked (`scrubbed=true`); a matching **open** proposal is dismissed
  (only its hash remains, so it is not proposed again). Each store (notebook, ledger, proposals, caches) is reported on
  its own; a failure reports `done=false` and puts "The note may STILL be present in: ..." first in `residual`. The
  note is never reported erased from a store that failed or was not touched.
- **Residual**: matching is normalised-text equality or substring in evidence, so a paraphrase in another record is
  missed; the rejected-hash map keeps hashes of rejected texts (hashes, not text); a record `applying` at that moment is
  not scrubbed (reported by the count only); notebook folder backups and exports keep their copy.

### Expected test fallout (not run, not edited)

Tests that build a `JarvisNotebook`, write unlabelled entries and then assert them in `snapshot_block`/`memory_block`/
`dated_notes`/`Soul.render_for_prompt`/`SocietyMemory.head`/`recall` or in the reviewer prompt now see nothing unless
they pass `remote=False` or write with `local_only=False, provenance="unrestricted"`. Tests that fake `apply` with a
fixed signature need `**kwargs`. Tests that hand-write a trash manifest need `id` equal to the folder name.

### Controls prepared, NOT RUN

`tests/unit/memory/control/test_block17_gpt4.py` (label parse/render/roundtrip and malformed-label fail-closed,
replace not inheriting, remote selection, approval passing `local_only=True`, notebook and `snapshot_block` remote
default, turn taint on a local view, lead-notebook body filter; manifest path/id/timestamp/product/hash/linked
folder/root/ancestor/archive cases; shared lock, concurrent approvals applying once, reservation on disk before apply,
crash recovery applied / not applied / unknown, refused apply restoring; probe; proposal scrub and per-store failure
reporting). Not written: route tests, a handle-based traversal test, a Windows junction test, vitest for the new states.
