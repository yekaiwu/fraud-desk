"use client";

import { AlertOctagon, AlertTriangle, FileImage, FileText, Trash2 } from "lucide-react";
import { useCallback, useMemo, useRef, useState } from "react";
import { MAX_UPLOAD_MB } from "@/lib/constants";
import { formatBytes } from "@/lib/format";
import { countProblem, dupKey, duplicateKeys, fileProblem, sniffMagic } from "@/lib/uploadCheck";
import { Button } from "../ui/Button";

export type FileEntry = { id: number; file: File; problem: string | null };

/** Selected files with advisory checks: size, magic bytes (first 8 bytes), count, duplicate warning. */
export function useFileSelection() {
  const [entries, setEntries] = useState<FileEntry[]>([]);
  const next = useRef(1);

  const add = useCallback(async (files: File[]) => {
    const checked = await Promise.all(
      files.map(async (file) => {
        // skip the byte read for files that are already too big or empty
        const magic = file.size === 0 || file.size > MAX_UPLOAD_MB * 1024 * 1024 ? true : await sniffMagic(file);
        return { id: next.current++, file, problem: fileProblem(file, magic) } satisfies FileEntry;
      }),
    );
    setEntries((cur) => [...cur, ...checked]);
  }, []);
  const remove = useCallback((id: number) => setEntries((cur) => cur.filter((e) => e.id !== id)), []);
  const clear = useCallback(() => setEntries([]), []);

  const dups = useMemo(() => duplicateKeys(entries.map((e) => e.file)), [entries]);
  const countError = entries.length === 0 ? null : countProblem(entries.length);
  const badCount = entries.filter((e) => e.problem).length;
  return {
    entries,
    files: entries.map((e) => e.file),
    add,
    remove,
    clear,
    dups,
    countError,
    badCount,
    blocking: entries.length === 0 || !!countError || badCount > 0,
  };
}

export function FileList({
  entries,
  dups,
  onRemove,
  onClear,
  looksLike,
}: {
  entries: FileEntry[];
  dups: Set<string>;
  onRemove: (id: number) => void;
  onClear?: () => void;
  /** Checklist label the file name suggests, by entry id (a guess from the name only). */
  looksLike?: Map<number, string>;
}) {
  if (entries.length === 0) return null;
  const totalBytes = entries.reduce((s, e) => s + e.file.size, 0);
  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-sans text-[0.9375rem] font-semibold">
          Added documents <span className="tabular text-sm font-normal text-muted">({entries.length}, {formatBytes(totalBytes)})</span>
        </h3>
        {onClear ? (
          <Button variant="ghost" size="sm" icon={<Trash2 aria-hidden className="h-4 w-4" />} onClick={onClear}>
            Remove all
          </Button>
        ) : null}
      </div>
      <ul aria-label="Selected files" className="divide-y divide-rule overflow-hidden rounded-lg border border-rule bg-surface">
        {entries.map((e) => {
          const Icon = /\.(jpe?g|png)$/i.test(e.file.name) ? FileImage : FileText;
          const guess = looksLike?.get(e.id);
          const dup = !e.problem && dups.has(dupKey(e.file));
          return (
            <li key={e.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-1.5 pl-3 pr-1">
              <Icon aria-hidden className={e.problem ? "h-5 w-5 shrink-0 text-sev-high" : "h-5 w-5 shrink-0 text-brand-2"} />
              <div className="min-w-0 flex-1 py-1">
                <p className="break-words text-[0.9375rem] font-medium leading-snug text-ink">{e.file.name}</p>
                {guess && !e.problem ? <p className="text-xs text-muted">Looks like: {guess}</p> : null}
              </div>
              <span className="num text-xs text-ink-2">{formatBytes(e.file.size)}</span>
              <button
                type="button"
                onClick={() => onRemove(e.id)}
                className="inline-flex h-11 w-11 items-center justify-center rounded-md text-ink-2 hover:bg-sev-high-tint hover:text-sev-high"
                aria-label={`Remove ${e.file.name}`}
              >
                <Trash2 aria-hidden className="h-4 w-4" />
              </button>
              {e.problem ? (
                <p className="flex w-full items-start gap-1.5 pb-1.5 pr-3 text-sm font-medium text-sev-high">
                  <AlertOctagon aria-hidden className="mt-0.5 h-4 w-4 shrink-0" />
                  <span>Cannot upload: {e.problem}</span>
                </p>
              ) : dup ? (
                <p className="flex w-full items-start gap-1.5 pb-1.5 pr-3 text-sm text-sev-medium">
                  <AlertTriangle aria-hidden className="mt-0.5 h-4 w-4 shrink-0" />
                  <span>Same name and size as another file here. It will still be uploaded; duplicates are a check, not a gate.</span>
                </p>
              ) : null}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
