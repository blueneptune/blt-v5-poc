import { describe, expect, it } from 'vitest';
import { hashPassword, looksLikeHash, verifyPassword } from './password';
import { open, seal } from './session';
import { Throttle } from './throttle';
import { signInConfig } from './auth';

const SECRET = 'x'.repeat(40);

describe('passwords', () => {
	it('match only themselves', async () => {
		const hash = await hashPassword('correct horse battery');
		expect(looksLikeHash(hash)).toBe(true);
		expect(hash).not.toContain('$');
		expect(await verifyPassword('correct horse battery', hash)).toBe(true);
		expect(await verifyPassword('correct horse batterz', hash)).toBe(false);
	});

	it('two hashes of one password differ', async () => {
		expect(await hashPassword('same')).not.toBe(await hashPassword('same'));
	});

	it('something that is not a hash matches nothing', async () => {
		expect(await verifyPassword('', '')).toBe(false);
		expect(await verifyPassword('pw', 'scrypt:1:1:1:x:y')).toBe(false);
		expect(await verifyPassword('pw', 'plain-text-password')).toBe(false);
	});
});

describe('sessions', () => {
	const hash = 'scrypt:1024:8:1:c2FsdHNhbHQ:aGFzaA';
	const now = Date.UTC(2026, 10, 5, 12);

	it('open what they sealed, until they end', () => {
		const cookie = seal(SECRET, hash, 'admin', now);
		expect(open(SECRET, hash, cookie, now)?.user).toBe('admin');
		expect(open(SECRET, hash, cookie, now + 11 * 3600_000)).not.toBeNull();
		expect(open(SECRET, hash, cookie, now + 12 * 3600_000)).toBeNull();
	});

	it('refuse a changed, forged or foreign cookie', () => {
		const cookie = seal(SECRET, hash, 'admin', now);
		const [body, mac] = cookie.split('.');
		const other = Buffer.from(JSON.stringify({ u: 'root', exp: 9e9, v: 'x' })).toString('base64url');
		expect(open(SECRET, hash, `${other}.${mac}`, now)).toBeNull();
		expect(open(SECRET, hash, `${body}.`, now)).toBeNull();
		expect(open(SECRET, hash, 'garbage', now)).toBeNull();
		expect(open('y'.repeat(40), hash, cookie, now)).toBeNull();
		expect(open(SECRET, hash, undefined, now)).toBeNull();
	});

	it('end when the password changes', () => {
		const cookie = seal(SECRET, hash, 'admin', now);
		expect(open(SECRET, `${hash}x`, cookie, now)).toBeNull();
	});
});

describe('the throttle', () => {
	it('stops an address after five wrong passwords, for the window', () => {
		const throttle = new Throttle(5, 60_000);
		for (let i = 0; i < 5; i++) {
			expect(throttle.waitFor('a', i)).toBe(0);
			throttle.failed('a', i);
		}
		expect(throttle.waitFor('a', 10)).toBe(60_000 - 10);
		expect(throttle.waitFor('b', 10)).toBe(0);
		expect(throttle.waitFor('a', 60_001)).toBe(0);
	});

	it('forgets an address that gets it right', () => {
		const throttle = new Throttle(2, 60_000);
		throttle.failed('a', 0);
		throttle.succeeded('a');
		throttle.failed('a', 1);
		expect(throttle.waitFor('a', 2)).toBe(0);
	});
});

describe('the sign-in settings', () => {
	const hash = 'scrypt:1024:8:1:c2FsdHNhbHQ:aGFzaA';

	it('are off only when asked', () => {
		expect(signInConfig({ BLT_UI_SIGN_IN: 'off' }).mode).toBe('off');
		expect(signInConfig({}).mode).toBe('unconfigured');
	});

	it('need a real hash and a long secret', () => {
		expect(signInConfig({ BLT_UI_PASSWORD_HASH: 'hunter2', BLT_UI_SESSION_SECRET: SECRET }).mode).toBe(
			'unconfigured'
		);
		expect(signInConfig({ BLT_UI_PASSWORD_HASH: hash, BLT_UI_SESSION_SECRET: 'short' }).mode).toBe(
			'unconfigured'
		);
		expect(signInConfig({ BLT_UI_PASSWORD_HASH: hash, BLT_UI_SESSION_SECRET: SECRET })).toMatchObject({
			mode: 'on',
			user: 'admin'
		});
	});
});
