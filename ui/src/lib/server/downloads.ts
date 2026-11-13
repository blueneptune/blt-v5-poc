/**
 * Files server.js serves at /downloads/ from the folder BLT_DOWNLOADS
 * names: the Windows collector bundle, when deploy/up.sh built it
 * (BLT_UI_TLS=on; docs/windows-collector.md).
 */
import { statSync } from 'node:fs';
import path from 'node:path';
import { env } from '$env/dynamic/private';

export const WINDOWS_COLLECTOR = 'blt-collector-windows.zip';

/** The /downloads/ path of `file`, or null when there is no such file to serve. */
export function downloadHref(file: string, folder: string | undefined = env.BLT_DOWNLOADS): string | null {
	if (!folder) return null;
	try {
		return statSync(path.join(folder, file)).isFile() ? `/downloads/${file}` : null;
	} catch {
		return null;
	}
}
