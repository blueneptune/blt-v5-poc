// See https://svelte.dev/docs/kit/types#app.d.ts
import type { ApiClient } from '$lib/server/api';
import type { User } from '$lib/server/auth';

declare global {
	namespace App {
		interface Locals {
			/** Who is asking. Always the lab user until sign-in exists (see $lib/server/auth). */
			user: User;
			/** blt's API, on that user's behalf. */
			api: ApiClient;
		}
	}
}

export {};
