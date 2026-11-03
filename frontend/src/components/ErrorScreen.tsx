
/**
 * Full-page "this did not work" screen shared by not-found, error and global-error: a sheet of paper
 * with a plain headline, one calm sentence and the way back.
 * Server-safe (no hooks), so global-error can use it too.
 */
export function ErrorScreen({ eyebrow, title, children, actions, reference }: { eyebrow: string; title: string; children: React.ReactNode; actions: React.ReactNode; reference?: string | null }) {
  return (
    <section aria-labelledby="error-title" className="relative mx-auto mt-2 max-w-3xl overflow-hidden rounded-xl border border-rule bg-surface shadow-paper sm:mt-6">
      <div className="relative px-5 py-10 sm:px-12 sm:py-16">
        <p className="label-caps">{eyebrow}</p>
        <h1 id="error-title" className="mt-3 max-w-[16ch] text-[2.25rem] leading-[1.08] sm:text-[3.25rem]">
          {title}
        </h1>
        <div className="rule-statement mt-6 max-w-md" aria-hidden />
        <div className="mt-5 max-w-prose space-y-2 text-[1.0625rem] text-ink-2">{children}</div>
        {reference ? <p className="num mt-4 text-xs text-muted">Reference: {reference}</p> : null}
        <div className="mt-8 flex flex-wrap gap-3">{actions}</div>
      </div>
    </section>
  );
}
