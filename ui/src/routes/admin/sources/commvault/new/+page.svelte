<script lang="ts">
	import { enhance } from '$app/forms';
	import { Button } from '$lib/components/ui/button';
	import CollectorSelect from '$lib/components/CollectorSelect.svelte';

	let { data, form } = $props();

	let deployment = $state<'software' | 'saas'>('software');
	let signIn = $state<'password' | 'token'>('password');
	let verify = $state(true);

	const input = 'bg-background mt-1 h-9 w-full rounded-md border px-3 text-sm';
	const radio = 'flex items-start gap-2 rounded-lg border p-3 text-sm has-[:checked]:border-foreground/40 has-[:checked]:bg-muted/40';
</script>

<svelte:head><title>Add a Commvault CommCell · blt</title></svelte:head>

<a href="/admin/sources" class="text-muted-foreground text-sm hover:underline">← Sources</a>
<h1 class="mt-2 text-2xl font-semibold tracking-tight">Add a Commvault CommCell</h1>
<p class="text-muted-foreground mt-1 max-w-2xl text-sm">
	blt reads jobs, storage and inventory from it. Sign in as a Commvault user whose role may only <em>read</em> -
	a service account, not your own. Passwords and tokens are saved in Prefect and never shown again.
</p>

<form
	method="POST"
	action="?/add"
	class="mt-6 grid max-w-2xl gap-5"
	use:enhance={() =>
		async ({ update }) => {
			await update({ reset: false });
		}}
>
	<div>
		<label class="text-sm font-medium" for="name">Name in blt</label>
		<input id="name" name="name" required pattern="[a-z0-9][a-z0-9\-]*" placeholder="cv-prod-01" class={input} />
		<p class="text-muted-foreground mt-1 text-xs">Lower case letters, digits and dashes.</p>
	</div>

	{#if data.collectors.some((c) => !c.own)}
		<div>
			<label class="text-sm font-medium" for="collector">Collected by</label>
			<div class="mt-1"><CollectorSelect id="collector" collectors={data.collectors} /></div>
			<p class="text-muted-foreground mt-1 text-xs">The collector that can reach it. It is checked from there too.</p>
		</div>
	{/if}

	<fieldset class="grid gap-2 sm:grid-cols-2">
		<legend class="mb-1 text-sm font-medium">Where it runs</legend>
		<label class={radio}>
			<input type="radio" name="deployment" value="software" bind:group={deployment} class="mt-0.5" />
			<span><span class="font-medium">A CommServe you run</span><br /><span class="text-muted-foreground text-xs">On-premises or in your own cloud.</span></span>
		</label>
		<label class={radio}>
			<input type="radio" name="deployment" value="saas" bind:group={deployment} class="mt-0.5" />
			<span><span class="font-medium">Commvault Cloud</span><br /><span class="text-muted-foreground text-xs">Metallic: a tenant, or an MSP's customer.</span></span>
		</label>
	</fieldset>

	<div>
		<label class="text-sm font-medium" for="base_url">Address</label>
		<input
			id="base_url"
			name="base_url"
			required={deployment === 'software'}
			placeholder={deployment === 'software' ? 'cs01.example.com' : 'https://api.metallic.io'}
			class={input}
		/>
		<p class="text-muted-foreground mt-1 text-xs">
			{#if deployment === 'software'}
				A host name is enough: blt adds https:// and /commandcenter/api. Give a whole URL for another path
				(…/webconsole/api).
			{:else}
				Leave it empty for https://api.metallic.io.
			{/if}
		</p>
	</div>

	<fieldset class="grid gap-2 sm:grid-cols-2">
		<legend class="mb-1 text-sm font-medium">Signing in</legend>
		<label class={radio}>
			<input type="radio" name="sign_in" value="password" bind:group={signIn} class="mt-0.5" />
			<span><span class="font-medium">User name and password</span><br /><span class="text-muted-foreground text-xs">blt signs in for each run.</span></span>
		</label>
		<label class={radio}>
			<input type="radio" name="sign_in" value="token" bind:group={signIn} class="mt-0.5" />
			<span><span class="font-medium">Access token</span><br /><span class="text-muted-foreground text-xs">Made in Command Center; renewed by blt with its refresh token.</span></span>
		</label>
	</fieldset>

	{#if signIn === 'password'}
		<div class="grid gap-4 sm:grid-cols-2">
			<div>
				<label class="text-sm font-medium" for="username">User name</label>
				<input id="username" name="username" required autocomplete="off" class={input} />
			</div>
			<div>
				<label class="text-sm font-medium" for="password">Password</label>
				<input id="password" name="password" type="password" required autocomplete="off" class={input} />
			</div>
		</div>
	{:else}
		<div class="grid gap-4">
			<div>
				<label class="text-sm font-medium" for="access_token">Access token</label>
				<input id="access_token" name="access_token" type="password" required autocomplete="off" class={input} />
			</div>
			<div>
				<label class="text-sm font-medium" for="refresh_token">Refresh token</label>
				<input id="refresh_token" name="refresh_token" type="password" autocomplete="off" class={input} />
				<p class="mt-1 text-xs text-amber-700 dark:text-amber-300">
					Once blt has this pair, use it nowhere else: a renewal anywhere else ends blt's, and a new token has to
					be made.
				</p>
			</div>
		</div>
	{/if}

	{#if deployment === 'saas'}
		<details class="rounded-lg border p-3 text-sm">
			<summary class="cursor-pointer font-medium">An MSP's customer</summary>
			<p class="text-muted-foreground mt-1 text-xs">
				Only when the login is an MSP's and this source is one of its customers. Leave empty for a tenant's own
				login.
			</p>
			<div class="mt-3 grid gap-4 sm:grid-cols-2">
				<div>
					<label class="text-sm font-medium" for="account_id">Customer account id</label>
					<input id="account_id" name="account_id" class={input} />
				</div>
				<div>
					<label class="text-sm font-medium" for="tenant_guid">Or its tenant GUID</label>
					<input id="tenant_guid" name="tenant_guid" class={input} />
				</div>
			</div>
		</details>
	{/if}

	<div>
		<label class="flex items-center gap-2 text-sm font-medium">
			<input type="checkbox" name="verify_tls" bind:checked={verify} />
			Check its TLS certificate
		</label>
		{#if !verify}
			<p class="mt-1 text-xs text-amber-700 dark:text-amber-300">
				Only for a lab CommServe with a self-signed certificate: anyone between blt and it could read the
				password.
			</p>
		{/if}
	</div>

	{#if form?.message}
		<p class="text-destructive text-sm" role="alert">{form.message}</p>
	{/if}

	<div class="flex items-center gap-3">
		<Button type="submit">Save and check</Button>
		<p class="text-muted-foreground text-xs">Collection stays off until you turn it on.</p>
	</div>
</form>
