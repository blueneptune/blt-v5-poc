/**
 * The sign-in session: a cookie the UI's server signs, so nothing is kept
 * on the server and a restart signs nobody out.
 *
 *   <payload, base64url JSON>.<HMAC-SHA256 of it, base64url>
 *
 * The payload carries who, when it ends, and a fingerprint of the
 * password hash: setting a new password ends every session made with the
 * old one. The cookie is HttpOnly, so no script on the page can read it.
 */
import { createHmac, timingSafeEqual } from 'node:crypto';

export const COOKIE = 'blt_session';
export const SESSION_HOURS = 12;

export interface Session {
	user: string;
	/** Seconds since 1970, as JWTs do. */
	expires: number;
}

interface Payload {
	u: string;
	exp: number;
	v: string;
}

function mac(secret: string, text: string): Buffer {
	return createHmac('sha256', secret).update(text).digest();
}

/** Changes whenever the password does. */
export function fingerprint(secret: string, passwordHash: string): string {
	return mac(secret, `password:${passwordHash}`).subarray(0, 12).toString('base64url');
}

export function seal(secret: string, passwordHash: string, user: string, now = Date.now()): string {
	const payload: Payload = {
		u: user,
		exp: Math.floor(now / 1000) + SESSION_HOURS * 3600,
		v: fingerprint(secret, passwordHash)
	};
	const body = Buffer.from(JSON.stringify(payload)).toString('base64url');
	return `${body}.${mac(secret, body).toString('base64url')}`;
}

/** The session the cookie holds, or null if it is missing, forged, from
 * an old password, or over. */
export function open(
	secret: string,
	passwordHash: string,
	cookie: string | undefined,
	now = Date.now()
): Session | null {
	if (!cookie) return null;
	const [body, signature, ...rest] = cookie.split('.');
	if (!body || !signature || rest.length) return null;
	const given = Buffer.from(signature, 'base64url');
	const expected = mac(secret, body);
	if (given.length !== expected.length || !timingSafeEqual(given, expected)) return null;
	let payload: Payload;
	try {
		payload = JSON.parse(Buffer.from(body, 'base64url').toString());
	} catch {
		return null;
	}
	if (typeof payload.u !== 'string' || typeof payload.exp !== 'number') return null;
	if (payload.v !== fingerprint(secret, passwordHash)) return null;
	if (payload.exp * 1000 <= now) return null;
	return { user: payload.u, expires: payload.exp };
}
