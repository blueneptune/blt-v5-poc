/** Cross-site request forgery: a form posted to blt from another site.
 *
 * SvelteKit's own check accepts a form only from the one address in
 * ORIGIN, and its list of others is fixed when the image is built. A
 * tools server is reached by more than one name - short, full, with :443,
 * by its IP - so this check, made per request, accepts a form whose Origin
 * is the address the browser sent it to (its Host), whichever name that
 * was, or ORIGIN itself. A page on another site cannot make a browser send
 * a matching pair, so it is still refused. */

/** What SvelteKit checks: the content types a cross-site form can send
 * without the browser asking first. */
const FORM_TYPES = ['application/x-www-form-urlencoded', 'multipart/form-data', 'text/plain'];
const UNSAFE = ['POST', 'PUT', 'PATCH', 'DELETE'];

export function isForeignForm(request: Request, origin: string): boolean {
	if (!UNSAFE.includes(request.method)) return false;
	const type = (request.headers.get('content-type') ?? '').split(';', 1)[0].trim().toLowerCase();
	if (!FORM_TYPES.includes(type)) return false;
	const from = request.headers.get('origin');
	if (!from) return true;
	if (from === origin) return false;
	let fromHost: string;
	try {
		fromHost = new URL(from).host;
	} catch {
		return true;
	}
	const host = request.headers.get('host');
	return !host || fromHost.toLowerCase() !== host.toLowerCase();
}
