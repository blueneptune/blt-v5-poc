import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import { WINDOWS_COLLECTOR, downloadHref } from './downloads';

describe('downloadHref', () => {
	it('links a file that is there to be served', () => {
		const folder = mkdtempSync(path.join(tmpdir(), 'blt-downloads-'));
		writeFileSync(path.join(folder, WINDOWS_COLLECTOR), 'zip');
		expect(downloadHref(WINDOWS_COLLECTOR, folder)).toBe('/downloads/blt-collector-windows.zip');
	});

	it('links nothing without a downloads folder, or without the file in it', () => {
		const folder = mkdtempSync(path.join(tmpdir(), 'blt-downloads-'));
		mkdirSync(path.join(folder, WINDOWS_COLLECTOR));
		expect(downloadHref(WINDOWS_COLLECTOR, undefined)).toBeNull();
		expect(downloadHref('missing.zip', folder)).toBeNull();
		// A folder of that name is not a file.
		expect(downloadHref(WINDOWS_COLLECTOR, folder)).toBeNull();
	});
});
