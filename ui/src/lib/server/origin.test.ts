import { describe, expect, it } from 'vitest';
import { isForeignForm } from './origin';

const ORIGIN = 'http://tools-us.example.test:443';

function post(headers: Record<string, string>, method = 'POST'): Request {
	return new Request('http://internal/login', {
		method,
		headers: { 'content-type': 'application/x-www-form-urlencoded', ...headers },
		body: method === 'GET' ? undefined : 'a=1'
	});
}

describe('isForeignForm', () => {
	it('accepts the configured origin', () => {
		expect(isForeignForm(post({ origin: ORIGIN, host: 'tools-us.example.test:443' }), ORIGIN)).toBe(false);
	});

	it('accepts whichever name the browser used: short, full, an IP, with or without a port', () => {
		for (const host of ['tools-us', 'tools-us:443', 'TOOLS-US.example.test:443', '10.1.2.3:443']) {
			expect(isForeignForm(post({ origin: `http://${host.toLowerCase()}`, host }), ORIGIN)).toBe(false);
		}
	});

	it('refuses a form from another site', () => {
		expect(isForeignForm(post({ origin: 'http://evil.example', host: 'tools-us:443' }), ORIGIN)).toBe(true);
	});

	it('refuses a form with no origin, or one that does not parse', () => {
		expect(isForeignForm(post({ host: 'tools-us:443' }), ORIGIN)).toBe(true);
		expect(isForeignForm(post({ origin: 'null', host: 'tools-us:443' }), ORIGIN)).toBe(true);
	});

	it('refuses when the port differs', () => {
		expect(isForeignForm(post({ origin: 'http://tools-us:8090', host: 'tools-us:443' }), ORIGIN)).toBe(true);
	});

	it('leaves alone what a cross-site page cannot send without asking: JSON, and reads', () => {
		const json = new Request('http://internal/x', {
			method: 'POST',
			headers: { 'content-type': 'application/json', origin: 'http://evil.example', host: 'tools-us' },
			body: '{}'
		});
		expect(isForeignForm(json, ORIGIN)).toBe(false);
		expect(isForeignForm(new Request('http://internal/x', { headers: { origin: 'http://evil.example' } }), ORIGIN)).toBe(false);
	});
});
