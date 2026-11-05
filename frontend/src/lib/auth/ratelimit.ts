/**
 * Login-failure limiter. In memory, which is right for the single `next start` process this app runs
 * as (a restart clears it). Only failures count; a correct password never consumes budget.
 *
 * Two budgets: per client (the caller passes address plus account name, so one mistyped password does
 * not lock out everybody on the same Wi-Fi; 5 failures / 10 min) and one global budget across all
 * clients (60 / 10 min). The global one is the backstop if a client can forge the address we key on.
 * Only failures for an account that exists count toward it (a made-up name from a forged address
 * must not be able to lock everybody out), and while it is used up a correct username and password
 * still signs in (the per-client budget still applies to it). Both can be raised for a workshop with
 * GLDB_LOGIN_MAX_FAILURES and GLDB_LOGIN_GLOBAL_MAX_FAILURES (positive integers).
 *
 * The check and the reservation of an attempt happen in one synchronous step (``reserve``), before the
 * caller awaits the password comparison, so a burst of parallel guesses cannot all pass the check.
 */
function envInt(name: string, fallback: number): number {
  const n = Number(process.env[name]);
  return Number.isInteger(n) && n > 0 ? n : fallback;
}
export const LOGIN_MAX_FAILURES = 5;
export const LOGIN_GLOBAL_MAX_FAILURES = 60;
export const LOGIN_WINDOW_MS = 10 * 60 * 1000;
const MAX_TRACKED = 5000;

export interface LoginSlot {
  /** Seconds to wait; 0 when the attempt is allowed. */
  wait: number;
  /** True when this attempt took a place in the budget (a blocked one did not). */
  reserved: boolean;
  /** Give the place back: the password was right. */
  refund: () => void;
}

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

  private waitFor(list: number[], max: number, t: number): number {
    return list.length >= max ? Math.max(0, list[list.length - max] + this.windowMs - t) : 0;
  }

  /**
   * Check and, in the same synchronous step, reserve a place for one attempt as if it will fail; call
   * ``refund`` if it turns out to be right. ``known`` is whether the account exists: only those attempts
   * count toward the global budget. ``wait > 0`` without ``reserved`` means the client budget is used up.
   * ``wait > 0`` with ``reserved`` means only the global budget is used up: the caller still compares
   * the password and lets a correct one in, and refuses a wrong one with the wait.
   */
  reserve(client: string, known: boolean): LoginSlot {
    const t = this.now();
    const mine = this.recent(this.perClient.get(client) ?? [], t);
    this.global = this.recent(this.global, t);
    const clientWait = this.waitFor(mine, this.maxFailures, t);
    if (clientWait > 0) return { wait: Math.ceil(clientWait / 1000), reserved: false, refund: () => {} };
    const globalWait = this.waitFor(this.global, this.globalMax, t);
    this.recordFailure(client, known && globalWait === 0, t);
    return {
      wait: globalWait > 0 ? Math.ceil(globalWait / 1000) : 0,
      reserved: true,
      refund: () => this.refund(client, t, known && globalWait === 0),
    };
  }

  private refund(client: string, at: number, wasGlobal: boolean): void {
    const mine = this.perClient.get(client);
    const i = mine ? mine.indexOf(at) : -1;
    if (mine && i >= 0) mine.splice(i, 1);
    if (wasGlobal) {
      const j = this.global.indexOf(at);
      if (j >= 0) this.global.splice(j, 1);
    }
  }

  /**
   * ``global`` false records the failure for the client only (an account that does not exist). ``at`` is
   * the time to store; ``reserve`` passes the one it already read, so ``refund`` finds exactly that entry
   * (a second clock read could differ by a millisecond and the refund would miss).
   */
  recordFailure(client: string, global = true, at?: number): void {
    const t = at ?? this.now();
    if (this.perClient.size >= MAX_TRACKED && !this.perClient.has(client)) {
      for (const [k, v] of this.perClient) if (this.recent(v, t).length === 0) this.perClient.delete(k);
    }
    this.perClient.set(client, [...this.recent(this.perClient.get(client) ?? [], t), t]);
    if (global) this.global = [...this.recent(this.global, t), t];
  }
}

const KEY = "__gldbLoginLimiter";
/** One limiter per server process, shared by every bundle that imports this module. */
export function loginLimiter(): LoginLimiter {
  const g = globalThis as unknown as Record<string, LoginLimiter | undefined>;
  return (g[KEY] ??= new LoginLimiter(Date.now, envInt("GLDB_LOGIN_MAX_FAILURES", LOGIN_MAX_FAILURES), envInt("GLDB_LOGIN_GLOBAL_MAX_FAILURES", LOGIN_GLOBAL_MAX_FAILURES)));
}
