/** The frame every step of the setup window shares: title and lede, footer, back. */
import { ArrowLeft } from "lucide-react";
import type { ReactNode } from "react";
import { useT } from "@/i18n";
import { QuietAction } from "../ui";

export function StepHead({ title, lede }: { title: string; lede: string }) {
  return (
    <>
      <h2 className="text-2xl font-semibold tracking-tight text-foreground" data-testid="setup-step-title">
        {title}
      </h2>
      <p className="mt-2.5 text-sm leading-relaxed text-muted-foreground">{lede}</p>
    </>
  );
}

export function StepFooter({ left, children }: { left?: ReactNode; children: ReactNode }) {
  return (
    <div className="mt-6 flex flex-wrap items-center justify-between gap-3">
      <div>{left}</div>
      <div className="flex items-center gap-2">{children}</div>
    </div>
  );
}

export function BackAction({ onClick }: { onClick: () => void }) {
  const t = useT();
  return (
    <QuietAction onClick={onClick} className="inline-flex items-center gap-1 text-sm" testId="setup-back">
      <ArrowLeft aria-hidden className="h-3.5 w-3.5" />
      {t("first_run.back")}
    </QuietAction>
  );
}
