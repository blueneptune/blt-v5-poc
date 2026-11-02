import { json } from '@sveltejs/kit';

/** For Kubernetes probes: the UI's own process is up. Needs no API. */
export const GET = () => json({ status: 'ok' });
