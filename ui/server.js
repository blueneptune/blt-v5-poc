// blt's UI server: SvelteKit's Node handler (adapter-node), over HTTPS
// when given a certificate, with two paths passed through to the
// services behind it - so a remote collector that can reach this
// machine only on 443 reaches Prefect and blt's API there:
//
//   /prefect/api/...  -> Prefect's API   (BLT_PASS_PREFECT, e.g. http://127.0.0.1:4200)
//   /api/...          -> blt's API       (BLT_PASS_API, e.g. http://127.0.0.1:8000)
//   anything else     -> the UI
//
// The pass-through is on only with HTTPS: Prefect's password and blt's
// API key never cross the network in the clear. Each service checks its
// own credentials, as it does for blt's own worker; this server adds
// none and takes none away. WebSockets (Prefect's events) pass through
// too.
//
// Environment, besides adapter-node's own (HOST, PORT, ORIGIN, ...):
//   BLT_UI_TLS_CERT / BLT_UI_TLS_KEY   PEM files; both set: HTTPS
//   BLT_PASS_PREFECT / BLT_PASS_API    where the two paths go
//   BLT_DOWNLOADS                      a folder served at /downloads/
//                                      (the Windows collector bundle)

import { createReadStream, readFileSync, statSync } from 'node:fs';
import http from 'node:http';
import https from 'node:https';
import net from 'node:net';
import path from 'node:path';

/** The pass-through routes: a path prefix and where it goes. */
export function routesFrom(env) {
	const routes = [];
	if (env.BLT_PASS_PREFECT) routes.push({ prefix: '/prefect/api', target: `${env.BLT_PASS_PREFECT.replace(/\/$/, '')}/api` });
	if (env.BLT_PASS_API) routes.push({ prefix: '/api', target: env.BLT_PASS_API.replace(/\/$/, '') });
	return routes;
}

/** Where a request's path goes: the upstream URL, or null for the UI. */
export function upstreamFor(routes, url) {
	const [pathname, query] = splitQuery(url);
	if (pathname.split('/').includes('..')) return null;
	for (const { prefix, target } of routes) {
		if (pathname === prefix || pathname.startsWith(`${prefix}/`)) {
			return `${target}${pathname.slice(prefix.length) || '/'}${query}`;
		}
	}
	return null;
}

function splitQuery(url) {
	const at = url.indexOf('?');
	return at < 0 ? [url, ''] : [url.slice(0, at), url.slice(at)];
}

// Hop-by-hop headers, which are about one connection and are not passed on.
const HOP = new Set([
	'connection',
	'keep-alive',
	'proxy-authenticate',
	'proxy-authorization',
	'te',
	'trailer',
	'transfer-encoding',
	'upgrade'
]);

/** The headers to send upstream: the client's, less hop-by-hop ones,
 * with the upstream's host and where the request came from. */
export function forwardHeaders(headers, upstream, remoteAddress) {
	const out = {};
	for (const [name, value] of Object.entries(headers)) {
		if (!HOP.has(name.toLowerCase()) && value !== undefined) out[name] = value;
	}
	out.host = new URL(upstream).host;
	out['x-forwarded-proto'] = 'https';
	out['x-forwarded-for'] = remoteAddress ?? '';
	return out;
}

function pass(req, res, upstream) {
	const outgoing = http.request(upstream, {
		method: req.method,
		headers: forwardHeaders(req.headers, upstream, req.socket.remoteAddress)
	});
	outgoing.on('response', (answer) => {
		const headers = {};
		for (const [name, value] of Object.entries(answer.headers)) {
			if (!HOP.has(name) && value !== undefined) headers[name] = value;
		}
		res.writeHead(answer.statusCode ?? 502, headers);
		answer.pipe(res);
	});
	outgoing.on('error', () => {
		if (!res.headersSent) res.writeHead(502, { 'content-type': 'text/plain' });
		res.end('The service behind blt is not answering.\n');
	});
	req.pipe(outgoing);
}

/** A WebSocket (Prefect's events): the upgrade request is sent on as it
 * came, then bytes are copied both ways until either end closes. */
function passUpgrade(req, socket, head, upstream) {
	const target = new URL(upstream);
	const conn = net.connect(Number(target.port) || 80, target.hostname, () => {
		const headers = forwardHeaders(req.headers, upstream, req.socket.remoteAddress);
		headers.connection = 'Upgrade';
		headers.upgrade = req.headers.upgrade ?? 'websocket';
		const lines = [`${req.method} ${target.pathname}${target.search} HTTP/1.1`];
		for (const [name, value] of Object.entries(headers)) {
			for (const one of Array.isArray(value) ? value : [value]) lines.push(`${name}: ${one}`);
		}
		conn.write(`${lines.join('\r\n')}\r\n\r\n`);
		if (head?.length) conn.write(head);
		conn.pipe(socket);
		socket.pipe(conn);
	});
	const close = () => {
		conn.destroy();
		socket.destroy();
	};
	conn.on('error', close);
	socket.on('error', close);
}

/** /downloads/<file>: a file from the downloads folder, nothing else. */
export function downloadPath(folder, url) {
	if (!folder) return null;
	const [pathname] = splitQuery(url);
	const match = /^\/downloads\/([A-Za-z0-9][A-Za-z0-9._-]*)$/.exec(pathname);
	return match ? path.join(folder, match[1]) : null;
}

function download(res, file) {
	let size;
	try {
		const stat = statSync(file);
		if (!stat.isFile()) throw new Error('not a file');
		size = stat.size;
	} catch {
		res.writeHead(404, { 'content-type': 'text/plain' });
		res.end('Not found\n');
		return;
	}
	res.writeHead(200, {
		'content-type': file.endsWith('.zip') ? 'application/zip' : 'application/octet-stream',
		'content-length': size,
		'content-disposition': `attachment; filename="${path.basename(file)}"`
	});
	createReadStream(file).pipe(res);
}

/** The server: HTTPS with `tls` ({cert, key}), plain HTTP without - and
 * then no pass-through. */
export function createServer(env, handler, tls) {
	// Never in the clear: without HTTPS the paths are the UI's (404).
	const routes = tls ? routesFrom(env) : [];
	const downloads = env.BLT_DOWNLOADS;

	const serve = (req, res) => {
		const upstream = upstreamFor(routes, req.url ?? '/');
		if (upstream) return pass(req, res, upstream);
		const file = downloadPath(downloads, req.url ?? '/');
		if (file) return download(res, file);
		return handler(req, res);
	};
	const server = tls ? https.createServer(tls, serve) : http.createServer(serve);
	server.on('upgrade', (req, socket, head) => {
		const upstream = upstreamFor(routes, req.url ?? '/');
		if (upstream) passUpgrade(req, socket, head, upstream);
		else socket.destroy();
	});
	server.routes = routes;
	return server;
}

export async function main(env = process.env) {
	const { handler } = await import('./build/handler.js');
	const tls =
		env.BLT_UI_TLS_CERT && env.BLT_UI_TLS_KEY
			? { cert: readFileSync(env.BLT_UI_TLS_CERT), key: readFileSync(env.BLT_UI_TLS_KEY) }
			: null;
	const server = createServer(env, handler, tls);
	const host = env.HOST ?? '0.0.0.0';
	const port = Number(env.PORT ?? 3000);
	server.listen(port, host, () => {
		const passing = server.routes.map((r) => r.prefix).join(' and ');
		console.log(
			`blt UI on ${tls ? 'https' : 'http'}://${host}:${port}` +
				(passing ? `, passing ${passing} through` : '')
		);
	});
	const stop = () => server.close(() => process.exit(0));
	process.on('SIGTERM', stop);
	process.on('SIGINT', stop);
}

if (import.meta.url === `file://${process.argv[1]}`) {
	await main();
}
