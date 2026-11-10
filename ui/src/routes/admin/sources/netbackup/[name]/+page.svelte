<script lang="ts">
	import { enhance } from '$app/forms';
	import { Button } from '$lib/components/ui/button';
	import RunLog from '$lib/components/RunLog.svelte';
	import { when } from '$lib/format';
	import CollectorSelect from '$lib/components/CollectorSelect.svelte';

	let { data, form } = $props();
	const source = $derived(data.source);
	const input = 'bg-background mt-1 h-9 w-full rounded-md border px-3 text-sm';
</script>

<svelte:head><title>{source.name} · Sources · blt</title></svelte:head>

<a href="/admin/sources" class="text-muted-foreground text-sm hover:underline">← Sources</a>
<div class="mt-2 flex flex-wrap items-center gap-3">
	<h1 class="text-2xl font-semibold tracking-tight">{source.name}</h1>
	<span class="text-muted-foreground text-sm">NetBackup primary</span>
</div>

<dl class="mt-4 grid max-w-2xl grid-cols-[auto_1fr] gap-x-6 gap-y-1 text-sm">
	<dt class="text-muted-foreground">Address</dt>
	<dd class="break-all">{source.address}</dd>
	<dt class="text-muted-foreground">API key</dt>
	<dd>{source.secrets_set.includes('api_key') ? 'set' : 'not set'}</dd>
	<dt class="text-muted-foreground">CA certificate</dt>
	<dd>{source.has_ca_certificate ? 'kept' : 'none (publicly signed, or not verified)'}</dd>
	<dt class="text-muted-foreground self-center">Legacy key sizes</dt>
	<dd>
		<form method="POST" action="?/legacyKeys" use:enhance class="flex flex-wrap items-center gap-2">
			<input type="hidden" name="legacy_keys" value={String(!source.legacy_keys)} />
			{#if source.legacy_keys}
				<span class="font-medium text-amber-700 dark:text-amber-300">accepted</span>
				<span class="text-muted-foreground text-xs">(its CA's key is below today's minimum)</span>
			{:else}
				<span>not accepted</span>
			{/if}
			<Button type="submit" size="sm" variant="outline">{source.legacy_keys ? 'Stop accepting' : 'Accept'}</Button>
		</form>
	</dd>
	<dt class="text-muted-foreground">Changed</dt>
	<dd>{when(source.updated)}</dd>
	<dt class="text-muted-foreground self-center">Collected by</dt>
	<dd>
		<form method="POST" action="?/collector" use:enhance class="flex items-center gap-2">
			<CollectorSelect collectors={data.collectors} value={source.collector} label="Collected by" />
			<Button type="submit" size="sm" variant="outline">Set</Button>
		</form>
	</dd>
</dl>

{#if form?.message}
	<p class="text-destructive mt-4 text-sm" role="alert">{form.message}</p>
{:else if form && 'saved' in form}
	<p class="mt-4 text-sm" role="status">{form.saved}</p>
{/if}

<section class="mt-8 max-w-3xl">
	<div class="flex flex-wrap items-center gap-3">
		<h2 class="text-lg font-semibold">Check</h2>
		<form method="POST" action="?/check" use:enhance>
			<Button type="submit" size="sm" variant="outline">Check now</Button>
		</form>
		<form method="POST" action="?/collect" use:enhance class="ml-auto flex items-center gap-2">
			<input type="hidden" name="collect" value={String(!source.collect)} />
			<span class="text-sm">Collecting: <strong>{source.collect ? 'on' : 'off'}</strong></span>
			<Button type="submit" size="sm" variant={source.collect ? 'outline' : 'default'}>
				{source.collect ? 'Turn off' : 'Turn on'}
			</Button>
		</form>
	</div>
	<p class="text-muted-foreground mt-1 text-sm">
		Signs in with the saved key, from a worker, and tries each read blt makes. Turn collection on
		once storage passes; the SLP line says whether this NetBackup version has the SLP backlog.
	</p>
	{#if data.run}
		<div class="mt-3"><RunLog runId={data.run} /></div>
	{/if}
</section>

<section class="mt-10 max-w-2xl">
	<h2 class="text-lg font-semibold">Change</h2>
	<p class="text-muted-foreground mt-1 text-sm">Fill in only what changes.</p>
	<form method="POST" action="?/change" use:enhance class="mt-3 grid gap-4">
		<div>
			<label class="text-sm font-medium" for="url">Address</label>
			<input id="url" name="url" placeholder={source.address ?? ''} class={input} />
		</div>
		<div>
			<label class="text-sm font-medium" for="api_key">New API key</label>
			<input id="api_key" name="api_key" type="password" autocomplete="off" class={input} />
		</div>
		<div>
			<label class="text-sm font-medium" for="ca_certificate">New CA certificate (PEM)</label>
			<textarea
				id="ca_certificate"
				name="ca_certificate"
				rows="4"
				class="bg-background mt-1 w-full rounded-md border px-3 py-2 font-mono text-xs"
			></textarea>
			{#if source.has_ca_certificate}
				<label class="mt-1 flex items-center gap-2 text-sm">
					<input type="checkbox" name="remove_ca" /> Remove the kept CA certificate
				</label>
			{/if}
		</div>
		<div><Button type="submit" variant="outline">Save changes</Button></div>
	</form>
</section>

<section class="mt-10 max-w-2xl">
	<h2 class="text-lg font-semibold">Remove</h2>
	<p class="text-muted-foreground mt-1 text-sm">
		Removes its block: nothing collects from it any more. What blt collected stays.
	</p>
	<form method="POST" action="?/remove" use:enhance class="mt-3 flex flex-wrap items-end gap-2">
		<div>
			<label class="text-sm font-medium" for="confirm">Type {source.name} to remove it</label>
			<input id="confirm" name="confirm" autocomplete="off" class={input} />
		</div>
		<Button type="submit" variant="destructive">Remove</Button>
	</form>
</section>
