/**
 * Controllable memory (ADR-0061): proposals waiting for approval, the trash, export, and the
 * Delete dialog. Archive is a different action and is never called deletion here.
 * Written, not type-checked and not run.
 */
import { useCallback, useEffect, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { fill, useLocaleChunk, useT } from "@/i18n";
import { Button } from "@/components/ui/button";
import {
  approveProposal,
  deleteMemoryPage,
  deleteTrashForever,
  EXPORT_URL,
  fetchControlStatus,
  fetchProposals,
  fetchTrash,
  rejectProposal,
  restoreTrash,
  type ControlStatus,
  type DeletionReport,
  type MemoryProposal,
  type TrashItem,
} from "@/lib/memoryControlApi";

const day = (seconds: number) => new Date(seconds * 1000).toLocaleDateString();
const message = (err: unknown) => (err instanceof Error ? err.message : String(err));

export function MemoryControlPanel({ onChanged }: { onChanged?: () => void }) {
  const t = useT();
  useLocaleChunk("memory_control");
  const [status, setStatus] = useState<ControlStatus | null>(null);
  const [proposals, setProposals] = useState<MemoryProposal[]>([]);
  const [trash, setTrash] = useState<TrashItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<{ id: string; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [s, p, tr] = await Promise.all([fetchControlStatus(), fetchProposals(), fetchTrash()]);
      setStatus(s);
      setProposals(p.proposals);
      setTrash(tr.items);
      setError(null);
    } catch (err) {
      setError(message(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const run = async (action: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await action();
      setEditing(null);
      await load();
      onChanged?.();
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="mx-auto w-full max-w-3xl px-4 pt-4" data-testid="memory-control">
      <h2 className="text-sm font-semibold text-foreground-strong">{t("memory_control.title")}</h2>
      {status && (
        <p className="mt-1 text-xs text-muted-foreground">
          {status.require_approval && status.learning_enabled !== false
            ? t("memory_control.mode_ask")
            : status.learning_enabled === false
              ? t("memory_control.mode_off")
              : t("memory_control.mode_direct")}
        </p>
      )}
      {error && (
        <p role="alert" className="mt-2 text-sm text-destructive">
          {error}
        </p>
      )}

      <h3 className="mt-4 text-sm font-medium text-foreground">
        {fill(t("memory_control.proposals_title"), { count: proposals.length })}
      </h3>
      <p className="text-xs text-muted-foreground">{t("memory_control.proposals_hint")}</p>
      {proposals.length === 0 && (
        <p className="mt-2 text-sm text-muted-foreground">{t("memory_control.proposals_empty")}</p>
      )}
      <ul className="mt-2 flex flex-col gap-2">
        {proposals.map((p) => (
          <li key={p.id} className="rounded-lg border border-border bg-background p-3" data-testid="memory-proposal">
            {editing?.id === p.id ? (
              <textarea
                value={editing.text}
                onChange={(event) => setEditing({ id: p.id, text: event.target.value })}
                aria-label={t("memory_control.edit_label")}
                data-escape-local=""
                rows={3}
                className="w-full rounded-md border border-border bg-background p-2 text-sm"
              />
            ) : (
              <p className="text-sm text-foreground">
                {p.operation === "remove" ? `${t("memory_control.op_remove")}: ${p.before}` : p.text}
              </p>
            )}
            <p className="mt-1 text-xs text-muted-foreground">
              {t("memory_control.source")}: {p.source || t("memory_control.source_unknown")} ·{" "}
              {t(p.provenance === "restricted" ? "memory_control.prov_restricted" : "memory_control.prov_unlabelled")}
            </p>
            {p.evidence && <p className="mt-1 text-xs italic text-muted-foreground">“{p.evidence}”</p>}
            <div className="mt-2 flex flex-wrap gap-2">
              <Button
                size="sm"
                disabled={busy}
                onClick={() => run(() => approveProposal(p.id, editing?.id === p.id ? editing.text : undefined))}
              >
                {editing?.id === p.id ? t("memory_control.approve_edited") : t("memory_control.approve")}
              </Button>
              {p.operation !== "remove" && editing?.id !== p.id && (
                <Button size="sm" variant="secondary" disabled={busy} onClick={() => setEditing({ id: p.id, text: p.text })}>
                  {t("memory_control.edit")}
                </Button>
              )}
              <Button size="sm" variant="secondary" disabled={busy} onClick={() => run(() => rejectProposal(p.id))}>
                {t("memory_control.reject")}
              </Button>
            </div>
          </li>
        ))}
      </ul>

      {trash.length > 0 && (
        <>
          <h3 className="mt-4 text-sm font-medium text-foreground">{t("memory_control.trash_title")}</h3>
          <p className="text-xs text-muted-foreground">{t("memory_control.trash_hint")}</p>
          <ul className="mt-2 flex flex-col gap-1">
            {trash.map((item) => (
              <li key={item.id} className="flex items-center gap-2 text-sm">
                <span className="min-w-0 flex-1 truncate">
                  {item.path} · {fill(t("memory_control.restore_until"), { date: day(item.restore_until) })}
                </span>
                <Button size="sm" variant="secondary" disabled={busy} onClick={() => run(() => restoreTrash(item.id))}>
                  {t("memory_control.restore")}
                </Button>
                <Button size="sm" variant="secondary" disabled={busy} onClick={() => run(() => deleteTrashForever(item.id))}>
                  {t("memory_control.delete_forever")}
                </Button>
              </li>
            ))}
          </ul>
        </>
      )}

      <div className="mt-4 flex items-center gap-3">
        <a href={EXPORT_URL} download className="text-sm underline" data-testid="memory-export">
          {t("memory_control.export")}
        </a>
        <span className="text-xs text-muted-foreground">{t("memory_control.export_hint")}</span>
      </div>
      {status && (
        <p className="mt-2 text-xs text-muted-foreground">
          {fill(t("memory_control.backups_fact"), { count: status.backups.keeps_newest })}
        </p>
      )}
    </section>
  );
}

/** Delete (not Archive): two modes, and the list of what stays. */
export function DeletePageDialog({
  open,
  path,
  title,
  onClose,
  onDeleted,
}: {
  open: boolean;
  path: string;
  title: string;
  onClose: () => void;
  onDeleted: () => void;
}) {
  const t = useT();
  useLocaleChunk("memory_control");
  const [mode, setMode] = useState<"logical" | "permanent">("logical");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [report, setReport] = useState<DeletionReport | null>(null);

  useEffect(() => {
    if (open) {
      setMode("logical");
      setError(null);
      setReport(null);
    }
  }, [open]);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      setReport(await deleteMemoryPage(path, mode));
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  };

  const finish = () => {
    const done = report !== null;
    onClose();
    if (done) onDeleted();
  };

  return (
    <Dialog.Root open={open} onOpenChange={(next) => !next && !busy && finish()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-[80] bg-scrim/50" />
        <Dialog.Content
          data-testid="memory-delete-dialog"
          className="fixed left-1/2 top-[12%] z-[90] max-h-[80vh] w-[min(520px,calc(100vw-2rem))] -translate-x-1/2 overflow-y-auto rounded-xl border border-border bg-popover p-5 text-popover-foreground shadow-float"
        >
          <Dialog.Title className="text-base font-semibold text-foreground-strong">
            {fill(t("memory_control.delete_title"), { title })}
          </Dialog.Title>
          <Dialog.Description className="mt-1 text-sm text-muted-foreground">
            {t("memory_control.archive_is_not_delete")}
          </Dialog.Description>
          {report === null ? (
            <>
              <fieldset className="mt-4 flex flex-col gap-2">
                <label className="flex items-start gap-2 text-sm">
                  <input type="radio" name="del-mode" checked={mode === "logical"} onChange={() => setMode("logical")} />
                  <span>{t("memory_control.mode_logical")}</span>
                </label>
                <label className="flex items-start gap-2 text-sm">
                  <input type="radio" name="del-mode" checked={mode === "permanent"} onChange={() => setMode("permanent")} />
                  <span>{t("memory_control.mode_permanent")}</span>
                </label>
              </fieldset>
              <p className="mt-3 text-xs font-medium text-foreground">{t("memory_control.stays_title")}</p>
              <ul className="mt-1 list-disc pl-5 text-xs text-muted-foreground">
                <li>{t("memory_control.stays_backups")}</li>
                <li>{t("memory_control.stays_exports")}</li>
                <li>{t("memory_control.stays_remote")}</li>
                <li>{t("memory_control.stays_derived")}</li>
              </ul>
              {error && (
                <p role="alert" className="mt-3 text-sm text-destructive">
                  {error}
                </p>
              )}
              <div className="mt-5 flex justify-end gap-2">
                <Button variant="secondary" disabled={busy} onClick={onClose}>
                  {t("memory_control.cancel")}
                </Button>
                <Button disabled={busy} onClick={() => void submit()} data-testid="memory-delete-confirm">
                  {mode === "logical" ? t("memory_control.do_logical") : t("memory_control.do_permanent")}
                </Button>
              </div>
            </>
          ) : (
            <div className="mt-4 text-sm" data-testid="memory-delete-report">
              <ul className="flex flex-col gap-1">
                {report.stores.map((s) => (
                  <li key={s.store}>
                    {s.done ? "✓" : "✗"} <strong>{s.store}</strong>: {s.detail}
                  </li>
                ))}
              </ul>
              {report.derived.length > 0 && (
                <>
                  <p className="mt-3 font-medium">{t("memory_control.derived_title")}</p>
                  <ul className="list-disc pl-5 text-xs text-muted-foreground">
                    {report.derived.map((d) => (
                      <li key={d.path}>
                        {d.path} ({d.why})
                      </li>
                    ))}
                  </ul>
                </>
              )}
              <p className="mt-3 font-medium">{t("memory_control.stays_title")}</p>
              <ul className="list-disc pl-5 text-xs text-muted-foreground">
                {report.residual.map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
              <div className="mt-5 flex justify-end">
                <Button onClick={finish}>{t("memory_control.close")}</Button>
              </div>
            </div>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
