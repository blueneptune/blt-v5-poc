<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import { onDestroy } from 'svelte';
	import { ArrowLeft } from '@lucide/svelte';
	import StateBadge from '$lib/components/StateBadge.svelte';
	import { ago, bytes, duration, when } from '$lib/format';
	import { recordLabel } from '$lib/jobs';

	let { data } = $props();
	const job = $derived(data.job);

	// A running job's page follows it.
	let timer: ReturnType<typeof setInterval> | undefined;
	$effect(() => {
		clearInterval(timer);
		if (job.is_active) timer = setInterval(() => invalidateAll(), 15_000);
	});
	onDestroy(() => clearInterval(timer));

	const facts = $derived([
		['Kind', job.kind],
		['Product status', job.product_status],
		['Source', `${job.source_name} (${job.source_type})`],
		['Asset', [job.asset_name, job.asset_type].filter(Boolean).join(' · ')],
		['Protection', job.protection],
		['Started', when(job.started_at)],
		['Ended', job.is_active ? 'still running' : when(job.ended_at)],
		['Took', duration(job.started_at, job.ended_at)],
		['Written', bytes(job.bytes_transferred)],
		['Covered', bytes(job.bytes_protected)],
		['Attempt', job.attempt?.toString()],
		['Product job id', job.product_job_key]
	] as const);
</script>

<svelte:head><title>{job.asset_name ?? job.product_job_key} · blt</title></svelte:head>

<a href="/jobs" onclick={(e) => { if (history.length > 1) { e.preventDefault(); history.back(); } }}
	class="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-sm">
	<ArrowLeft class="size-4" /> Jobs
</a>

<div class="mt-3 flex flex-wrap items-center gap-3">
	<h1 class="text-2xl font-semibold tracking-tight">{job.asset_name ?? job.product_job_key}</h1>
	<StateBadge state={job.state} />
</div>
{#if job.message}
	<p class="mt-2 rounded-lg border border-red-200 bg-red-50 p-3 text-sm dark:border-red-900 dark:bg-red-950/30">
		{job.message}
	</p>
{/if}

<div class="mt-6 grid gap-6 lg:grid-cols-3">
	<section class="bg-card rounded-xl border p-4 lg:col-span-2">
		<h2 class="font-medium">Job</h2>
		<dl class="mt-3 grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm sm:grid-cols-[auto_1fr_auto_1fr]">
			{#each facts as [label, value] (label)}
				<dt class="text-muted-foreground">{label}</dt>
				<dd class="min-w-0 break-words">{value || '—'}</dd>
			{/each}
		</dl>
	</section>

	<section class="bg-card rounded-xl border p-4">
		<h2 class="font-medium">Collection</h2>
		<dl class="mt-3 space-y-2 text-sm">
			<div><dt class="text-muted-foreground">First seen</dt><dd>{when(job.first_seen_at)}</dd></div>
			<div>
				<dt class="text-muted-foreground">Last collected</dt>
				<dd>{when(job.last_collected_at)} <span class="text-muted-foreground">({ago(job.last_collected_at)})</span></dd>
			</div>
			<div>
				<dt class="text-muted-foreground">Inventory</dt>
				<dd>{job.source_object_id ? 'linked to its inventory row' : 'not linked yet'}</dd>
			</div>
			<div><dt class="text-muted-foreground">blt job id</dt><dd class="font-mono text-xs break-all">{job.id}</dd></div>
		</dl>
	</section>
</div>

<section class="bg-card mt-6 rounded-xl border p-4">
	<details>
		<summary class="cursor-pointer font-medium">{recordLabel(job.source_type, job.source_name)}</summary>
		<p class="text-muted-foreground mt-1 text-xs">As blt stores it; also at <code>{job.product_path}</code> in the API.</p>
		<pre class="bg-muted mt-3 max-h-[32rem] overflow-auto rounded-lg p-3 text-xs">{JSON.stringify(job.product, null, 2)}</pre>
	</details>
</section>
