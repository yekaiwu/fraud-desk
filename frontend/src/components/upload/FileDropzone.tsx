"use client";

import clsx from "clsx";
import { FolderOpen, UploadCloud } from "lucide-react";
import { useId, useRef, useState } from "react";
import { MAX_FILES_PER_CASE, MAX_UPLOAD_MB } from "@/lib/constants";
import { ACCEPT } from "@/lib/uploadCheck";
import { buttonClasses } from "../ui/Button";
import { Guilloche } from "../ui/Guilloche";

/** Hidden files such as .DS_Store come along with a folder; they are never documents. */
const isHidden = (f: File) => f.name.startsWith(".");

/** Stop walking a dropped folder after this many files; the case limit is far below it. */
const WALK_CAP = 400;

function readAll(reader: FileSystemDirectoryReader): Promise<FileSystemEntry[]> {
  return new Promise((resolve, reject) => {
    const out: FileSystemEntry[] = [];
    const next = () =>
      reader.readEntries((batch) => {
        if (batch.length === 0) resolve(out);
        else {
          out.push(...batch);
          next();
        }
      }, reject);
    next();
  });
}

async function walk(entry: FileSystemEntry, out: File[]): Promise<void> {
  if (out.length >= WALK_CAP) return;
  if (entry.isFile) {
    const file = await new Promise<File>((resolve, reject) => (entry as FileSystemFileEntry).file(resolve, reject));
    out.push(file);
  } else if (entry.isDirectory) {
    for (const child of await readAll((entry as FileSystemDirectoryEntry).createReader())) await walk(child, out);
  }
}

/** Files from a drop, including everything inside dropped folders. Falls back to the plain file list. */
export async function filesFromDrop(dt: DataTransfer): Promise<File[]> {
  // The entries must be taken synchronously, before the first await: the browser empties the list after the event.
  const entries: FileSystemEntry[] = [];
  for (const item of Array.from(dt.items ?? [])) {
    const entry = item.kind === "file" && typeof item.webkitGetAsEntry === "function" ? item.webkitGetAsEntry() : null;
    if (entry) entries.push(entry);
  }
  const plain = Array.from(dt.files ?? []);
  if (entries.length === 0 || !entries.some((e) => e.isDirectory)) return plain.filter((f) => !isHidden(f));
  const out: File[] = [];
  try {
    for (const e of entries) await walk(e, out);
  } catch {
    return plain.filter((f) => !isHidden(f));
  }
  return out.filter((f) => !isHidden(f));
}

/**
 * Large drop target for a customer's documents. A real file input sits behind it (focusable, so Tab then
 * Enter or Space opens the picker); a second button picks a whole folder. Dropping files or folders works too.
 */
export function FileDropzone({ onFiles, disabled = false, label = "Choose files" }: { onFiles: (files: File[]) => void; disabled?: boolean; /** Text on the button inside the drop target. */ label?: string }) {
  const id = useId();
  const folderRef = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  return (
    <div>
      <input
        id={id}
        type="file"
        multiple
        accept={ACCEPT}
        disabled={disabled}
        className="peer sr-only"
        onChange={(e) => {
          onFiles(Array.from(e.target.files ?? []).filter((f) => !isHidden(f)));
          e.target.value = "";
        }}
      />
      <label
        htmlFor={id}
        onDragOver={(e) => {
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(false);
          if (disabled) return;
          void filesFromDrop(e.dataTransfer).then((files) => files.length && onFiles(files));
        }}
        className={clsx(
          "group relative flex min-h-56 cursor-pointer flex-col items-center justify-center gap-3 overflow-hidden rounded-lg border-2 border-dashed px-5 py-8 text-center transition-colors",
          "peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-[var(--ring)]",
          over ? "border-brand-2 bg-brand-tint" : "border-rule-strong bg-surface hover:border-brand-2 hover:bg-brand-tint/60",
          disabled && "cursor-not-allowed opacity-60",
        )}
      >
        <Guilloche variant="rosette" opacity={over ? 0.16 : 0.08} className="absolute -bottom-24 -left-20 h-64 w-64 text-brand" />
        <Guilloche variant="rosette" opacity={over ? 0.16 : 0.08} className="absolute -right-20 -top-24 h-64 w-64 text-brand" />
        <span className="relative grid h-14 w-14 place-items-center rounded-full border border-rule bg-paper text-brand-2 shadow-paper">
          <UploadCloud aria-hidden className="h-7 w-7" />
        </span>
        <span className="relative font-display text-[1.375rem] font-semibold leading-tight text-ink">{over ? "Drop to add them" : "Drag the customer's documents here"}</span>
        <span className="relative max-w-sm text-[0.9375rem] text-ink-2">Drop files or a whole folder. PDF, JPEG or PNG, up to {MAX_FILES_PER_CASE} files and {MAX_UPLOAD_MB} MB each.</span>
        <span className={buttonClasses({ variant: "secondary", size: "lg", className: "relative mt-1 group-hover:border-brand-2 group-hover:bg-brand-tint" })}>
          <UploadCloud aria-hidden className="h-4 w-4" />
          {label}
        </span>
      </label>
      <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-1">
        <input
          ref={folderRef}
          type="file"
          multiple
          tabIndex={-1}
          aria-hidden
          className="sr-only"
          {...({ webkitdirectory: "", directory: "" } as Record<string, string>)}
          onChange={(e) => {
            onFiles(Array.from(e.target.files ?? []).filter((f) => !isHidden(f)));
            e.target.value = "";
          }}
        />
        <button type="button" disabled={disabled} onClick={() => folderRef.current?.click()} className={buttonClasses({ variant: "ghost", size: "md", className: "-ml-3" })}>
          <FolderOpen aria-hidden className="h-4 w-4" />
          Choose a folder instead
        </button>
        <span className="text-sm text-muted">Everything inside it is added.</span>
      </div>
    </div>
  );
}
