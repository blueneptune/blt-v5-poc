<script lang="ts">
	import { enhance } from '$app/forms';
	import { Button } from '$lib/components/ui/button';
	import RunLog from '$lib/components/RunLog.svelte';
	import CollectorSelect from '$lib/components/CollectorSelect.svelte';
	import TimingForm from '$lib/components/TimingForm.svelte';
	import { sourceKindLabel } from '$lib/admin';
	import { when } from '$lib/format';

	let { data, form } = $props();
	const source = $derived(data.source);
	const commvault = $derived(source.type === 'commvault');
	const byToken = $derived(source.secrets_set.includes('access_token'));
	let signIn = $state<'password' | 'token'>('password');

	const input = 'bg-background mt-1 h-9 w-full rounded-md border px-3 text-sm';
	const keep = () => async ({ update }: { update: (o?: { reset?: boolean }) => Promise<void> }) => update({ reset: false });
</script>

<svelte:head><title>{source.name} · Sources · blt</title></svelte:head>

<a href="/admin/sources" class="text-muted-foreground text-sm hover:underline">← Sources</a>
<div class="mt-2 flex flex-wrap items-center gap-3">
	<h1 class="text-2xl font-semibold tracking-tight">{source.name}</h1>
	<span class="text-muted-foreground text-sm">{sourceKindLabel(source)}</span>
</div>

<dl class="mt-4 grid max-w-2xl grid-cols-[auto_1fr] gap-x-6 gap-y-1 text-sm">
	<dt class="text-muted-foreground">Address</dt>
	<dd class="break-all">{source.address ?? (source.credentials_from ? `from ${source.credentials_from}` : '—')}</dd>
	{#if commvault}
		<dt class="text-muted-foreground">Signs in</dt>
		<dd>
			{#if source.credentials_from}
				with {source.credentials_from}'s login (an MSP's)
			{:else if byToken}
				with an access token{source.secrets_set.includes('refresh_token') ? ', renewed by blt' : ' (no refresh token: it will expire)'}
			{:else if source.login}
				as {source.login}{source.secrets_set.includes('password') ? '' : ' - no password set'}
			{:else}
				<span class="text-destructive">no way to sign in is set</span>
			{/if}
		</dd>
	{:else}
		<dt class="text-muted-foreground">Service account</dt>
		<dd>{source.login ?? 'unnamed'} · secret {source.secrets_set.includes('client_secret') ? 'set' : 'not set'}</dd>
	{/if}
	<dt class="text-muted-foreground self-center">TLS certificate</dt>
	<dd>
		<form method="POST" action="?/verifyTls" use:enhance class="flex flex-wrap items-center gap-2">
			<input type="hidden" name="verify_tls" value={String(source.verify_tls === false)} />
			{#if source.verify_tls === false}
				<span class="font-medium text-amber-700 dark:text-amber-300">not checked</span>
			{:else}
				<span>checked</span>
			{/if}
			<Button type="submit" size="sm" variant="outline">{source.verify_tls === false ? 'Check it' : 'Stop checking'}</Button>
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

<section class="bg-card mt-6 max-w-3xl rounded-xl border p-5 shadow-sm">
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
		{#if commvault}
			Signs in from a worker and tries the reads blt makes: jobs and storage. A token that is due is renewed and
			saved back, as a collection would.
		{:else}
			Signs in from a worker and tries the reads blt makes: jobs, inventory and SLA domains.
		{/if}
		Turn collection on once they pass.
	</p>
	{#if data.run}
		<div class="mt-3"><RunLog runId={data.run} /></div>
	{/if}
</section>

{#key source.updated}
	<TimingForm timing={source.timing ?? {}} nextStarts={source.next_starts ?? []} collecting={source.collect} product={commvault ? 'Commvault' : 'Rubrik'} />
{/key}

<section class="bg-card mt-6 max-w-2xl rounded-xl border p-5 shadow-sm">
	<h2 class="text-lg font-semibold">Change</h2>
	{#if commvault}
		<p class="text-muted-foreground mt-1 text-sm">
			Fill in only what changes. A new way to sign in replaces the old one whole.
		</p>
		<form method="POST" action="?/changeCommvault" use:enhance={keep} class="mt-3 grid gap-4">
			<div>
				<label class="text-sm font-medium" for="base_url">Address</label>
				<input id="base_url" name="base_url" placeholder={source.address ?? ''} class={input} />
			</div>
			<fieldset class="flex flex-wrap gap-4 text-sm">
				<legend class="mb-1 text-sm font-medium">New sign-in</legend>
				<label class="flex items-center gap-2"><input type="radio" bind:group={signIn} value="password" /> User name and password</label>
				<label class="flex items-center gap-2"><input type="radio" bind:group={signIn} value="token" /> Access token</label>
			</fieldset>
			{#if signIn === 'password'}
				<div class="grid gap-4 sm:grid-cols-2">
					<div>
						<label class="text-sm font-medium" for="username">User name</label>
						<input id="username" name="username" placeholder={source.login ?? ''} autocomplete="off" class={input} />
					</div>
					<div>
						<label class="text-sm font-medium" for="password">New password</label>
						<input id="password" name="password" type="password" autocomplete="off" class={input} />
					</div>
				</div>
			{:else}
				<div class="grid gap-4 sm:grid-cols-2">
					<div>
						<label class="text-sm font-medium" for="access_token">New access token</label>
						<input id="access_token" name="access_token" type="password" autocomplete="off" class={input} />
					</div>
					<div>
						<label class="text-sm font-medium" for="refresh_token">Its refresh token</label>
						<input id="refresh_token" name="refresh_token" type="password" autocomplete="off" class={input} />
					</div>
				</div>
			{/if}
			<div><Button type="submit" variant="outline">Save changes</Button></div>
		</form>
	{:else}
		<p class="text-muted-foreground mt-1 text-sm">
			A new service account file - after its secret was rotated in RSC, or for another service account.
		</p>
		<form
			method="POST"
			action="?/changeRubrik"
			enctype="multipart/form-data"
			use:enhance
			class="mt-3 flex flex-wrap items-end gap-3"
		>
			<div>
				<label class="text-sm font-medium" for="service_account">Service account file</label>
				<input
					id="service_account"
					name="service_account"
					type="file"
					accept=".json,application/json"
					required
					class="mt-1 block text-sm file:mr-3 file:rounded-md file:border file:bg-transparent file:px-3 file:py-1.5 file:text-sm"
				/>
			</div>
			<Button type="submit" variant="outline">Replace it</Button>
		</form>
	{/if}
</section>

<section class="bg-card mt-6 max-w-2xl rounded-xl border p-5 shadow-sm">
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
