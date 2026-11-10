<script lang="ts">
	import { enhance } from '$app/forms';
	import { Button } from '$lib/components/ui/button';
	import RunLog from '$lib/components/RunLog.svelte';
	import CollectorSelect from '$lib/components/CollectorSelect.svelte';
	import { answeredAtInLog, certificatesInLog, type FoundCertificate } from '$lib/admin';

	let { data, form } = $props();

	let ca = $state('');
	let found = $state<FoundCertificate[]>([]);
	let chosen = $state<string | null>(null);
	let url = $state('');
	let legacy = $state(false);
	let movedFrom = $state<string | null>(null);

	function fetched(lines: { message: string }[]) {
		found = certificatesInLog(lines);
		const answered = answeredAtInLog(lines);
		if (answered && answered !== url) {
			movedFrom = url;
			url = answered;
		}
	}

	const input = 'bg-background mt-1 h-9 w-full rounded-md border px-3 text-sm';
</script>

<svelte:head><title>Add a NetBackup primary · blt</title></svelte:head>

<a href="/admin/sources" class="text-muted-foreground text-sm hover:underline">← Sources</a>
<h1 class="mt-2 text-2xl font-semibold tracking-tight">Add a NetBackup primary</h1>
<p class="text-muted-foreground mt-1 max-w-2xl text-sm">
	blt reads storage, SLP images and jobs from it with an API key. Make the key in NetBackup's web UI
	for a user whose role may only <em>read</em> - a service account such as blt_r, not your own. It is
	saved in Prefect and never shown again.
</p>

<form
	method="POST"
	action="?/add"
	class="mt-6 grid max-w-2xl gap-5"
	use:enhance={() =>
		async ({ update }) => {
			// Keep what was typed: fetching the CA must not empty the key.
			await update({ reset: false });
		}}
>
	<div>
		<label class="text-sm font-medium" for="name">Name in blt</label>
		<input id="name" name="name" required pattern="[a-z0-9][a-z0-9\-]*" placeholder="nbu-prod-01" class={input} />
		<p class="text-muted-foreground mt-1 text-xs">Lower case letters, digits and dashes.</p>
	</div>

	{#if data.collectors.some((c) => !c.own)}
		<div>
			<label class="text-sm font-medium" for="collector">Collected by</label>
			<div class="mt-1"><CollectorSelect id="collector" collectors={data.collectors} /></div>
			<p class="text-muted-foreground mt-1 text-xs">
				The collector that can reach it. Its CA is fetched, and it is checked, from there too.
			</p>
		</div>
	{/if}

	<div>
		<label class="text-sm font-medium" for="url">Address</label>
		<div class="flex gap-2">
			<input id="url" name="url" required bind:value={url} placeholder="nbu01.example.com" class={input} />
			<Button type="submit" variant="outline" formaction="?/fetchCa" formnovalidate class="mt-1">
				Fetch its CA
			</Button>
		</div>
		<p class="text-muted-foreground mt-1 text-xs">
			A host name is enough: blt adds https:// and :1556/netbackup - or 443, if that is where its API
			answers (some NetBackup 10.5 primaries).
		</p>
		{#if movedFrom !== null}
			<p class="mt-1 text-xs font-medium text-amber-700 dark:text-amber-300" role="status">
				Port 1556 did not answer, 443 did: the address is now {url}.
			</p>
		{/if}
	</div>

	{#if form && 'caRun' in form && form.caRun}
		<div class="grid gap-3">
			<RunLog
				runId={form.caRun}
				height="max-h-48"
				onupdate={(_, lines) => fetched(lines)}
			/>
			{#each found as certificate, i (i)}
				<div class="bg-card rounded-xl border p-3 text-sm">
					<p class="font-medium">{certificate.subject ?? `Certificate ${i + 1}`}</p>
					<p class="text-muted-foreground mt-1 font-mono text-xs break-all">SHA-256 {certificate.sha256}</p>
					<p class="mt-1 text-xs">
						{#if certificate.legacy}
							It verifies this primary's TLS certificate only with <strong>legacy key sizes</strong> accepted:
							its key is below today's minimum (1024-bit, from an older install). Using it turns that on.
						{:else if certificate.verifies === true}
							It verifies this primary's TLS certificate.
						{:else if certificate.verifies === false}
							It does <strong>not</strong> verify this primary at this address.
						{/if}
					</p>
					<p class="text-muted-foreground mt-2 text-xs">
						Compare the fingerprint with the primary's own before using it: on the primary,
						<code>nbcertcmd -displayCACertDetail</code>.
					</p>
					<Button
						type="button"
						size="sm"
						variant={chosen === certificate.pem ? 'secondary' : 'default'}
						class="mt-2"
						onclick={() => ((ca = certificate.pem), (chosen = certificate.pem), (legacy = certificate.legacy))}
					>
						{chosen === certificate.pem ? 'Using this certificate' : 'The fingerprint matches: use it'}
					</Button>
				</div>
			{/each}
		</div>
	{/if}

	<div>
		<label class="text-sm font-medium" for="ca_certificate">CA certificate (PEM)</label>
		<textarea
			id="ca_certificate"
			name="ca_certificate"
			bind:value={ca}
			rows="5"
			placeholder="-----BEGIN CERTIFICATE-----"
			class="bg-background mt-1 w-full rounded-md border px-3 py-2 font-mono text-xs"
		></textarea>
		<p class="text-muted-foreground mt-1 text-xs">
			Paste it, or fetch it above. Leave it empty only if the primary's certificate is publicly signed.
		</p>
	</div>

	<div>
		<label class="flex items-center gap-2 text-sm font-medium">
			<input type="checkbox" name="legacy_keys" bind:checked={legacy} />
			Accept legacy key sizes
		</label>
		<p class="text-muted-foreground mt-1 text-xs">
			Only for a primary whose CA predates 2048-bit keys (1024-bit, often from a 2017-or-older install, kept
			through upgrades). Its chain, expiry and host name are still checked. The lasting fix is a NetBackup CA
			migration.
		</p>
	</div>

	<div>
		<label class="text-sm font-medium" for="api_key">API key</label>
		<input id="api_key" name="api_key" type="password" required autocomplete="off" class={input} />
	</div>

	{#if form?.message}
		<p class="text-destructive text-sm" role="alert">{form.message}</p>
	{/if}

	<div class="flex items-center gap-3">
		<Button type="submit">Save and check</Button>
		<p class="text-muted-foreground text-xs">Collection stays off until you turn it on.</p>
	</div>
</form>
