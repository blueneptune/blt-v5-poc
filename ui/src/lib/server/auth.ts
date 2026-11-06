/**
 * Who is using the UI, and what they may do - the one place sign-in plugs
 * in.
 *
 * For now there is one local account, set in the environment:
 *
 *   BLT_UI_USER            its name (default "admin")
 *   BLT_UI_PASSWORD_HASH   its password's hash: `npm run password`
 *   BLT_UI_SESSION_SECRET  signs the session cookie: 32+ random characters
 *
 * It holds every role. Without the hash and the secret the UI refuses to
 * serve anything but /healthz - it never falls back to open. Only
 * BLT_UI_SIGN_IN=off opens it, for a UI that cannot be reached from
 * anywhere else.
 *
 * When Zitadel arrives (docs/vision.md 4.1), `currentUser` reads its
 * session instead, and nothing else changes: every page already asks
 * `allowed` / `requireRole`, and the navigation is filtered by role.
 */
import { env } from '$env/dynamic/private';
import { error, type Cookies, type RequestEvent } from '@sveltejs/kit';
import { timingSafeEqual } from 'node:crypto';
import { looksLikeHash, verifyPassword } from './password';
import { COOKIE, SESSION_HOURS, open, seal } from './session';
import { Throttle } from './throttle';

/** Roles from docs/common-jobs.md decision 3: engineering sees blt's own
 * collection runs, operations does not; admin manages sources and runs. */
export const ROLES = ['operations', 'engineering', 'admin'] as const;
export type Role = (typeof ROLES)[number];

export interface User {
	id: string;
	name: string;
	roles: Role[];
	/** False only when sign-in is turned off: the UI then says so. */
	signedIn: boolean;
}

export type SignIn =
	| { mode: 'on'; user: string; passwordHash: string; secret: string }
	| { mode: 'off' }
	| { mode: 'unconfigured'; problem: string };

export function signInConfig(source: Record<string, string | undefined> = env): SignIn {
	if (source.BLT_UI_SIGN_IN === 'off') return { mode: 'off' };
	const passwordHash = source.BLT_UI_PASSWORD_HASH ?? '';
	const secret = source.BLT_UI_SESSION_SECRET ?? '';
	if (!looksLikeHash(passwordHash))
		return {
			mode: 'unconfigured',
			problem: 'BLT_UI_PASSWORD_HASH is not set to a hash from `npm run password`.'
		};
	if (secret.length < 32)
		return { mode: 'unconfigured', problem: 'BLT_UI_SESSION_SECRET must be 32 characters or more.' };
	return { mode: 'on', user: source.BLT_UI_USER || 'admin', passwordHash, secret };
}

const OPEN_USER: User = { id: 'lab', name: 'Lab', roles: [...ROLES], signedIn: false };

function accountUser(name: string): User {
	return { id: name, name, roles: [...ROLES], signedIn: true };
}

/** The signed-in user, or null for nobody. */
export function currentUser(event: RequestEvent, config = signInConfig()): User | null {
	if (config.mode === 'off') return OPEN_USER;
	if (config.mode !== 'on') return null;
	const session = open(config.secret, config.passwordHash, event.cookies.get(COOKIE));
	return session && session.user === config.user ? accountUser(session.user) : null;
}

const throttle = new Throttle();

function sameText(a: string, b: string): boolean {
	const left = Buffer.from(a);
	const right = Buffer.from(b);
	// Compared at one length, so the time taken does not give the name away.
	return left.length === right.length && timingSafeEqual(left, right);
}

export type SignInResult = { ok: true } | { ok: false; message: string };

/** Checks the name and password and, if they are right, sets the session
 * cookie. `address` is who is trying, for the throttle. */
export async function signIn(
	cookies: Cookies,
	url: URL,
	address: string,
	name: string,
	password: string,
	config = signInConfig()
): Promise<SignInResult> {
	if (config.mode !== 'on') return { ok: false, message: 'Sign-in is not set up.' };
	const wait = throttle.waitFor(address);
	if (wait > 0)
		return {
			ok: false,
			message: `Too many wrong passwords. Try again in ${Math.ceil(wait / 60000)} minutes.`
		};
	// Always hash, so a wrong name takes as long as a wrong password.
	const passwordOk = await verifyPassword(password, config.passwordHash);
	if (!passwordOk || !sameText(name, config.user)) {
		throttle.failed(address);
		return { ok: false, message: 'That name and password do not match.' };
	}
	throttle.succeeded(address);
	cookies.set(COOKIE, seal(config.secret, config.passwordHash, config.user), {
		path: '/',
		httpOnly: true,
		sameSite: 'lax',
		secure: url.protocol === 'https:',
		maxAge: SESSION_HOURS * 3600
	});
	return { ok: true };
}

export function signOut(cookies: Cookies): void {
	cookies.delete(COOKIE, { path: '/' });
}

export function allowed(user: User | null, role: Role | undefined): boolean {
	return user !== null && (role === undefined || user.roles.includes(role));
}

/** In a load function or action: stop here unless the user has the role. */
export function requireRole(user: User | null, role: Role): asserts user is User {
	if (!allowed(user, role)) error(403, `This needs the ${role} role.`);
}
