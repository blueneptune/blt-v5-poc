// The UI's server (server.js): what passes through to Prefect and blt's
// API, and that it never does without HTTPS.
import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import http from 'node:http';
import https from 'node:https';
import net from 'node:net';
import { tmpdir } from 'node:os';
import path from 'node:path';
import tls from 'node:tls';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
// @ts-expect-error - plain JavaScript, run by node directly
import { createServer, downloadPath, forwardHeaders, routesFrom, upstreamFor } from './server.js';

const ENV = { BLT_PASS_PREFECT: 'http://127.0.0.1:4200', BLT_PASS_API: 'http://127.0.0.1:8000/' };

describe('the pass-through paths', () => {
	const routes = routesFrom(ENV);

	it('sends /prefect/api to Prefect and /api to blt, the UI the rest', () => {
		expect(upstreamFor(routes, '/prefect/api/health')).toBe('http://127.0.0.1:4200/api/health');
		expect(upstreamFor(routes, '/prefect/api')).toBe('http://127.0.0.1:4200/api/');
		expect(upstreamFor(routes, '/api/v1/sources?x=1')).toBe('http://127.0.0.1:8000/v1/sources?x=1');
		expect(upstreamFor(routes, '/apis')).toBeNull();
		expect(upstreamFor(routes, '/prefect')).toBeNull();
		expect(upstreamFor(routes, '/jobs')).toBeNull();
		expect(upstreamFor(routes, '/api/../jobs')).toBeNull();
	});

	it('passes nothing when not configured', () => {
		expect(upstreamFor(routesFrom({}), '/api/v1')).toBeNull();
	});

	it('drops hop-by-hop headers and names the upstream host', () => {
		const out = forwardHeaders(
			{ host: 'tools:443', connection: 'keep-alive', 'x-api-key': 'k', authorization: 'Basic x' },
			'http://127.0.0.1:8000/v1',
			'10.0.0.9'
		);
		expect(out).toEqual({
			host: '127.0.0.1:8000',
			'x-api-key': 'k',
			authorization: 'Basic x',
			'x-forwarded-proto': 'https',
			'x-forwarded-for': '10.0.0.9'
		});
	});

	it('serves only plain file names from the downloads folder', () => {
		expect(downloadPath('/d', '/downloads/blt-collector-windows.zip')).toBe(
			'/d/blt-collector-windows.zip'
		);
		expect(downloadPath('/d', '/downloads/../etc/passwd')).toBeNull();
		expect(downloadPath('/d', '/downloads/.hidden')).toBeNull();
		expect(downloadPath('/d', '/downloads/a/b')).toBeNull();
		expect(downloadPath(undefined, '/downloads/x.zip')).toBeNull();
	});
});

describe('the server', () => {
	let dir: string;
	let cert: Buffer;
	let upstream: http.Server;
	const servers: http.Server[] = [];

	beforeAll(async () => {
		dir = mkdtempSync(path.join(tmpdir(), 'blt-ui-'));
		execFileSync('openssl', [
			'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1', '-subj', '/CN=localhost',
			'-addext', 'subjectAltName=DNS:localhost,IP:127.0.0.1',
			'-keyout', path.join(dir, 'key.pem'), '-out', path.join(dir, 'cert.pem')
		], { stdio: 'ignore' });
		cert = readFileSync(path.join(dir, 'cert.pem'));
		writeFileSync(path.join(dir, 'bundle.zip'), 'zip bytes');
		// A stand-in for Prefect and the API: echoes what it was sent, and
		// answers a WebSocket upgrade by echoing bytes back.
		upstream = http.createServer((req, res) => {
			let body = '';
			req.on('data', (c) => (body += c));
			req.on('end', () => {
				res.writeHead(200, { 'content-type': 'application/json' });
				res.end(JSON.stringify({ url: req.url, method: req.method, body, auth: req.headers.authorization, host: req.headers.host }));
			});
		});
		upstream.on('upgrade', (req, socket) => {
			socket.write(`HTTP/1.1 101 Switching Protocols\r\nupgrade: websocket\r\nconnection: Upgrade\r\nx-path: ${req.url}\r\n\r\n`);
			socket.pipe(socket);
		});
		await new Promise<void>((r) => upstream.listen(0, '127.0.0.1', r));
	});

	afterAll(() => {
		upstream.close();
		for (const s of servers) s.close();
	});

	async function start(withTls: boolean): Promise<number> {
		const port = (upstream.address() as net.AddressInfo).port;
		const env = { BLT_PASS_PREFECT: `http://127.0.0.1:${port}`, BLT_PASS_API: `http://127.0.0.1:${port}`, BLT_DOWNLOADS: dir };
		const ui = (_req: http.IncomingMessage, res: http.ServerResponse) => {
			res.writeHead(200);
			res.end('the UI');
		};
		const options = withTls
			? { cert, key: readFileSync(path.join(dir, 'key.pem')) }
			: null;
		const server = createServer(env, ui, options);
		servers.push(server);
		await new Promise<void>((r) => server.listen(0, '127.0.0.1', r));
		return (server.address() as net.AddressInfo).port;
	}

	function get(port: number, url: string, init: { method?: string; body?: string; secure?: boolean } = {}) {
		const secure = init.secure ?? true;
		return new Promise<{ status: number; body: string }>((resolve, reject) => {
			const req = (secure ? https : http).request(
				{ host: '127.0.0.1', port, path: url, method: init.method ?? 'GET', ca: cert, servername: 'localhost', headers: { authorization: 'Basic abc' } },
				(res) => {
					let body = '';
					res.on('data', (c) => (body += c));
					res.on('end', () => resolve({ status: res.statusCode ?? 0, body }));
				}
			);
			req.on('error', reject);
			req.end(init.body);
		});
	}

	it('passes requests through over HTTPS, credentials and body included', async () => {
		const port = await start(true);
		const prefect = JSON.parse((await get(port, '/prefect/api/health')).body);
		expect(prefect).toMatchObject({ url: '/api/health', method: 'GET', auth: 'Basic abc' });
		const api = JSON.parse((await get(port, '/api/v1/jobs', { method: 'POST', body: '{"a":1}' })).body);
		expect(api).toMatchObject({ url: '/v1/jobs', method: 'POST', body: '{"a":1}' });
		expect(await get(port, '/jobs')).toEqual({ status: 200, body: 'the UI' });
		expect(await get(port, '/downloads/bundle.zip')).toEqual({ status: 200, body: 'zip bytes' });
		expect((await get(port, '/downloads/missing.zip')).status).toBe(404);
	});

	it('passes a WebSocket through', async () => {
		const port = await start(true);
		const socket = tls.connect({ host: '127.0.0.1', port, ca: cert, servername: 'localhost' });
		const answer = await new Promise<string>((resolve, reject) => {
			let seen = '';
			socket.on('secureConnect', () => {
				socket.write('GET /prefect/api/events/in HTTP/1.1\r\nhost: x\r\nupgrade: websocket\r\nconnection: Upgrade\r\n\r\n');
			});
			socket.on('data', (c) => {
				seen += c;
				if (seen.includes('\r\n\r\n') && !seen.includes('ping')) socket.write('ping');
				if (seen.includes('ping')) resolve(seen);
			});
			socket.on('error', reject);
		});
		socket.destroy();
		expect(answer).toContain('101 Switching Protocols');
		expect(answer).toContain('x-path: /api/events/in');
		expect(answer.endsWith('ping')).toBe(true);
	});

	it('passes nothing through without HTTPS', async () => {
		const port = await start(false);
		expect(await get(port, '/api/v1/jobs', { secure: false })).toEqual({ status: 200, body: 'the UI' });
	});
});
