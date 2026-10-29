/**
 * Login-failure limiter. In memory, which is right for the single `next start` process this app runs
 * as (a restart clears it). Only failures count; a correct password never consumes budget.
 *
 * Two budgets: per client address (5 failures / 10 min) and one global budget across all clients
 * (60 / 10 min). The global one is the backstop if a client can forge the address we key on, so a
 * brute-force run stays at about 60 guesses per 10 minutes whatever it sends.
 */
export const LOGIN_MAX_FAILURES = 5;
export const LOGIN_GLOBAL_MAX_FAILURES = 60;
export const LOGIN_WINDOW_MS = 10 * 60 * 1000;
const MAX_TRACKED = 5000;

export class LoginLimiter {
  private perClient = new Map<string, number[]>();
  private global: number[] = [];

  constructor(
    private now: () => number = Date.now,
    private maxFailures = LOGIN_MAX_FAILURES,
    private globalMax = LOGIN_GLOBAL_MAX_FAILURES,
    private windowMs = LOGIN_WINDOW_MS,
  ) {}

  private recent(list: number[], t: number): number[] {
    return list.filter((x) => t - x < this.windowMs);
  }

  /** Seconds to wait before another attempt is allowed, or 0 when allowed. */
  retryAfterS(client: string): number {
    const t = this.now();
    const mine = this.recent(this.perClient.get(client) ?? [], t);
    this.global = this.recent(this.global, t);
    let wait = 0;
    if (mine.length >= this.maxFailures) wait = Math.max(wait, mine[mine.length - this.maxFailures] + this.windowMs - t);
    if (this.global.length >= this.globalMax) wait = Math.max(wait, this.global[this.global.length - this.globalMax] + this.windowMs - t);
    return wait > 0 ? Math.ceil(wait / 1000) : 0;
  }

  recordFailure(client: string): void {
    const t = this.now();
    if (this.perClient.size >= MAX_TRACKED && !this.perClient.has(client)) {
      for (const [k, v] of this.perClient) if (this.recent(v, t).length === 0) this.perClient.delete(k);
    }
    this.perClient.set(client, [...this.recent(this.perClient.get(client) ?? [], t), t]);
    this.global = [...this.recent(this.global, t), t];
  }
}

const KEY = "__gldbLoginLimiter";
/** One limiter per server process, shared by every bundle that imports this module. */
export function loginLimiter(): LoginLimiter {
  const g = globalThis as unknown as Record<string, LoginLimiter | undefined>;
  return (g[KEY] ??= new LoginLimiter());
}
