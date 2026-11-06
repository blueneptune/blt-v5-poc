/**
 * Slows down password guessing: after FAILURES wrong passwords from one
 * address within WINDOW, that address may not try again until the oldest
 * of them is WINDOW old. Kept in memory - a restart forgets, which is fine
 * for one UI process.
 */
export const FAILURES = 5;
export const WINDOW_MS = 15 * 60 * 1000;

export class Throttle {
	private failures = new Map<string, number[]>();

	constructor(
		private limit = FAILURES,
		private windowMs = WINDOW_MS
	) {}

	private recent(key: string, now: number): number[] {
		const kept = (this.failures.get(key) ?? []).filter((t) => now - t < this.windowMs);
		if (kept.length) this.failures.set(key, kept);
		else this.failures.delete(key);
		return kept;
	}

	/** Milliseconds until the address may try again; 0 if it may now. */
	waitFor(key: string, now = Date.now()): number {
		const recent = this.recent(key, now);
		if (recent.length < this.limit) return 0;
		return recent[recent.length - this.limit] + this.windowMs - now;
	}

	failed(key: string, now = Date.now()): void {
		this.failures.set(key, [...this.recent(key, now), now]);
	}

	succeeded(key: string): void {
		this.failures.delete(key);
	}
}
