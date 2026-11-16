import {
  BanIcon,
  CheckCircle2Icon,
  CircleDashedIcon,
  ClockIcon,
  Loader2Icon,
  ScanSearchIcon,
  ShieldCheckIcon,
  ShieldXIcon,
  TimerOffIcon,
  XCircleIcon,
  type LucideIcon,
} from "lucide-react";

import { Chip } from "@/components/ui/chip";
import { cn } from "@/lib/cn";
import {
  backupLabel,
  backupTone,
  confirmationLabel,
  confirmationTone,
  generationLabel,
  generationTone,
  kindLabel,
  queryLabel,
  queryTone,
} from "@/lib/status";

const QUERY_ICON: Record<string, LucideIcon> = {
  succeeded: CheckCircle2Icon,
  refused: ShieldXIcon,
  failed: XCircleIcon,
  timeout: TimerOffIcon,
  scan_limit: ScanSearchIcon,
};

/** A query's state: icon, word and tone together, never color alone. */
export function QueryStatusChip({ status, className }: { status: string; className?: string }) {
  const Icon = QUERY_ICON[status] ?? CircleDashedIcon;
  return (
    <Chip tone={queryTone(status)} className={className}>
      <Icon aria-hidden />
      {queryLabel(status)}
    </Chip>
  );
}

const GENERATION_ICON: Record<string, LucideIcon> = {
  accepted: CheckCircle2Icon,
  refused: ShieldXIcon,
  no_sql: BanIcon,
  failed: XCircleIcon,
  answered: CheckCircle2Icon,
};

export function GenerationStatusChip({ status, className }: { status: string; className?: string }) {
  const Icon = GENERATION_ICON[status] ?? CircleDashedIcon;
  return (
    <Chip tone={generationTone(status)} className={className}>
      <Icon aria-hidden />
      {generationLabel(status)}
    </Chip>
  );
}

const CONFIRMATION_ICON: Record<string, LucideIcon> = {
  pending: ClockIcon,
  executing: Loader2Icon,
  executed: CheckCircle2Icon,
  failed: XCircleIcon,
  cancelled: BanIcon,
  expired: ClockIcon,
};

export function ConfirmationStatusChip({ status, className }: { status: string; className?: string }) {
  const Icon = CONFIRMATION_ICON[status] ?? CircleDashedIcon;
  return (
    <Chip tone={confirmationTone(status)} className={className}>
      <Icon className={cn(status === "executing" && "animate-spin")} aria-hidden />
      {confirmationLabel(status)}
    </Chip>
  );
}

export function BackupStatusChip({ status, className }: { status: string; className?: string }) {
  const Icon = status === "running" ? Loader2Icon : status === "succeeded" ? CheckCircle2Icon : XCircleIcon;
  return (
    <Chip tone={backupTone(status)} className={className}>
      <Icon className={cn(status === "running" && "animate-spin")} aria-hidden />
      {backupLabel(status)}
    </Chip>
  );
}

/** The badge next to every query or AI generation: did the SQL guard accept it as read-only? */
export function GuardBadge({ passed, className }: { passed: boolean; className?: string }) {
  return passed ? (
    <Chip tone="success" className={className} data-qa="guard-passed">
      <ShieldCheckIcon aria-hidden />
      Read-only, guard passed
    </Chip>
  ) : (
    <Chip tone="danger" className={className} data-qa="guard-refused">
      <ShieldXIcon aria-hidden />
      Refused by the guard
    </Chip>
  );
}

export function KindChip({ kind, className }: { kind: string; className?: string }) {
  return (
    <Chip tone="info" className={className}>
      {kindLabel(kind)}
    </Chip>
  );
}
