/**
 * Password hashes for the UI's sign-in: scrypt, from Node's own crypto,
 * so there is nothing to install. Only the hash is ever stored (in
 * config/blt.env or the Kubernetes secret); `npm run password` makes one.
 *
 * Format: scrypt:<N>:<r>:<p>:<salt>:<hash>, salt and hash base64url. No
 * "$" in it, so a shell that sources blt.env leaves it alone.
 */
import { randomBytes, scrypt as scryptCallback, timingSafeEqual, type ScryptOptions } from 'node:crypto';

const N = 2 ** 15;
const R = 8;
const P = 1;
const KEY_LENGTH = 32;

function scrypt(password: string, salt: Buffer, options: ScryptOptions): Promise<Buffer> {
	return new Promise((resolve, reject) =>
		scryptCallback(password, salt, KEY_LENGTH, options, (error, key) =>
			error ? reject(error) : resolve(key)
		)
	);
}

function options(n: number, r: number, p: number): ScryptOptions {
	// scrypt needs 128 * N * r bytes; Node's default ceiling is 32 MiB.
	return { N: n, r, p, maxmem: 256 * n * r };
}

export async function hashPassword(password: string): Promise<string> {
	const salt = randomBytes(16);
	const key = await scrypt(password, salt, options(N, R, P));
	return ['scrypt', N, R, P, salt.toString('base64url'), key.toString('base64url')].join(':');
}

/** Whether the password matches the hash. A hash that is not one of ours
 * matches nothing. */
export async function verifyPassword(password: string, stored: string): Promise<boolean> {
	const parts = stored.split(':');
	if (parts.length !== 6 || parts[0] !== 'scrypt') return false;
	const [n, r, p] = parts.slice(1, 4).map(Number);
	if (![n, r, p].every((v) => Number.isInteger(v) && v > 0)) return false;
	const salt = Buffer.from(parts[4], 'base64url');
	const expected = Buffer.from(parts[5], 'base64url');
	if (salt.length < 8 || expected.length !== KEY_LENGTH) return false;
	const key = await scrypt(password, salt, options(n, r, p));
	return timingSafeEqual(key, expected);
}

export function looksLikeHash(stored: string): boolean {
	return /^scrypt:\d+:\d+:\d+:[\w-]+:[\w-]+$/.test(stored);
}
