/**
 * Makes the settings for the UI's sign-in, to paste into config/blt.env
 * (or the Kubernetes secret):
 *
 *   npm run password              asks for the password twice
 *   npm run password -- --secret  also makes a new BLT_UI_SESSION_SECRET
 *
 * The password is read without being shown and is never written down;
 * only its hash is printed. A new secret signs everyone out.
 */
import { randomBytes } from 'node:crypto';
import { stdin, stdout, stderr } from 'node:process';
import { hashPassword } from '../src/lib/server/password.ts';

const MINIMUM = 12;

function ask(prompt: string): Promise<string> {
	return new Promise((resolve, reject) => {
		stderr.write(prompt);
		if (!stdin.isTTY) {
			// Piped in: the first line.
			let text = '';
			stdin.setEncoding('utf8');
			stdin.on('data', (chunk) => (text += chunk));
			stdin.on('end', () => resolve(text.split(/\r?\n/)[0]));
			stdin.on('error', reject);
			return;
		}
		let text = '';
		stdin.setRawMode(true);
		stdin.setEncoding('utf8');
		stdin.resume();
		const onData = (key: string) => {
			for (const char of key) {
				if (char === '\r' || char === '\n') {
					stdin.setRawMode(false);
					stdin.pause();
					stdin.off('data', onData);
					stderr.write('\n');
					resolve(text);
					return;
				}
				if (char === '\u0003') {
					stdin.setRawMode(false);
					stderr.write('\n');
					process.exit(130);
				}
				if (char === '\u007f' || char === '\b') text = text.slice(0, -1);
				else text += char;
			}
		};
		stdin.on('data', onData);
	});
}

const password = await ask('Password for the blt UI: ');
if (password.length < MINIMUM) {
	stderr.write(`It needs to be ${MINIMUM} characters or more.\n`);
	process.exit(1);
}
if (stdin.isTTY && (await ask('Again: ')) !== password) {
	stderr.write('The two do not match.\n');
	process.exit(1);
}
stdout.write(`BLT_UI_PASSWORD_HASH=${await hashPassword(password)}\n`);
if (process.argv.includes('--secret'))
	stdout.write(`BLT_UI_SESSION_SECRET=${randomBytes(32).toString('base64url')}\n`);
