<script lang="ts">
	import { enhance } from '$app/forms';
	import { Button } from '$lib/components/ui/button';
	import CollectorSelect from '$lib/components/CollectorSelect.svelte';

	let { data, form } = $props();
	const input = 'bg-background mt-1 h-9 w-full rounded-md border px-3 text-sm';
</script>

<svelte:head><title>Add a Rubrik account · blt</title></svelte:head>

<a href="/admin/sources" class="text-muted-foreground text-sm hover:underline">← Sources</a>
<h1 class="mt-2 text-2xl font-semibold tracking-tight">Add a Rubrik account</h1>
<p class="text-muted-foreground mt-1 max-w-2xl text-sm">
	blt reads jobs, inventory and SLA domains from Rubrik Security Cloud as a service account. Make one in RSC
	(Settings → Users → Service Accounts) with a role that may only <em>read</em>, and download its JSON file.
	Its secret is saved in Prefect and never shown again; blt does not need the file after.
</p>

<form
	method="POST"
	action="?/add"
	enctype="multipart/form-data"
	class="mt-6 grid max-w-2xl gap-5"
	use:enhance={() =>
		async ({ update }) => {
			await update({ reset: false });
		}}
>
	<div>
		<label class="text-sm font-medium" for="name">Name in blt</label>
		<input id="name" name="name" required pattern="[a-z0-9][a-z0-9\-]*" placeholder="rsc-prod" class={input} />
		<p class="text-muted-foreground mt-1 text-xs">Lower case letters, digits and dashes.</p>
	</div>

	{#if data.collectors.some((c) => !c.own)}
		<div>
			<label class="text-sm font-medium" for="collector">Collected by</label>
			<div class="mt-1"><CollectorSelect id="collector" collectors={data.collectors} /></div>
			<p class="text-muted-foreground mt-1 text-xs">The collector that can reach RSC. It is checked from there too.</p>
		</div>
	{/if}

	<div>
		<label class="text-sm font-medium" for="service_account">Service account file</label>
		<input
			id="service_account"
			name="service_account"
			type="file"
			accept=".json,application/json"
			required
			class="mt-1 block w-full text-sm file:mr-3 file:rounded-md file:border file:bg-transparent file:px-3 file:py-1.5 file:text-sm"
		/>
		<p class="text-muted-foreground mt-1 text-xs">
			The JSON file RSC gave when the service account was made: it holds client_id, client_secret and
			access_token_uri.
		</p>
	</div>

	{#if form?.message}
		<p class="text-destructive text-sm" role="alert">{form.message}</p>
	{/if}

	<div class="flex items-center gap-3">
		<Button type="submit">Save and check</Button>
		<p class="text-muted-foreground text-xs">Collection stays off until you turn it on.</p>
	</div>
</form>
