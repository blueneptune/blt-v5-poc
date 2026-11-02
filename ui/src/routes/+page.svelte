<script lang="ts">
	import StateBadge from '$lib/components/StateBadge.svelte';
	import StateBar from '$lib/components/StateBar.svelte';
	import { ago } from '$lib/format';

	let { data } = $props();

	const last24h = $derived(data.bySource.reduce((n, row) => n + row.total, 0));
	const maxDay = $derived(Math.max(1, ...data.byDay.map((row) => row.total)));
	const maxSource = $derived(Math.max(1, ...data.bySource.map((row) => row.total)));
</script>

<h1 class="text-2xl font-semibold tracking-tight">Overview</h1>

<div class="mt-6 grid gap-4 sm:grid-cols-3">
	<a href="/jobs?kind=all" class="bg-card rounded-xl border p-4 hover:shadow-sm">
		<p class="text-muted-foreground text-sm">Jobs, last 24 h</p>
		<p class="mt-1 text-3xl font-semibold tabular-nums">{last24h}</p>
	</a>
	<a
		href="/jobs?kind=all&state=failed&state=partial&state=missing"
		class="bg-card rounded-xl border p-4 hover:shadow-sm"
	>
		<p class="text-muted-foreground text-sm">Failed or partial, last 24 h</p>
		<p class="mt-1 text-3xl font-semibold tabular-nums {data.failureCount ? 'text-red-600 dark:text-red-400' : ''}">
			{data.failureCount}
		</p>
	</a>
	<a href="/jobs?kind=all&active=true&window=all" class="bg-card rounded-xl border p-4 hover:shadow-sm">
		<p class="text-muted-foreground text-sm">Running now</p>
		<p class="mt-1 text-3xl font-semibold tabular-nums">{data.activeCount}</p>
	</a>
</div>

<div class="mt-6 grid gap-6 lg:grid-cols-2">
	<section class="bg-card rounded-xl border p-4">
		<h2 class="font-medium">Last 24 hours, by source</h2>
		{#if data.bySource.length === 0}
			<p class="text-muted-foreground mt-3 text-sm">No jobs started in the last day.</p>
		{:else}
			<table class="mt-3 w-full text-sm">
				<tbody>
					{#each data.bySource as row (`${row.source_type}/${row.source_name}`)}
						<tr>
							<td class="py-1.5 pr-3 whitespace-nowrap">
								<a class="hover:underline" href="/jobs?kind=all&source={row.source_name}">
									{row.source_name}
								</a>
								<span class="text-muted-foreground text-xs">{row.source_type}</span>
							</td>
							<td class="w-full py-1.5"><StateBar states={row.states} total={row.total} scale={maxSource} /></td>
							<td class="py-1.5 pl-3 text-right tabular-nums">{row.total}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		{/if}
	</section>

	<section class="bg-card rounded-xl border p-4">
		<h2 class="font-medium">Last 14 days</h2>
		<table class="mt-3 w-full text-sm">
			<tbody>
				{#each data.byDay as row (row.day)}
					<tr>
						<td class="text-muted-foreground py-1 pr-3 whitespace-nowrap tabular-nums">{row.day ?? 'not started'}</td>
						<td class="w-full py-1"><StateBar states={row.states} total={row.total} scale={maxDay} /></td>
						<td class="py-1 pl-3 text-right tabular-nums">{row.total}</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</section>
</div>

<section class="bg-card mt-6 rounded-xl border p-4">
	<h2 class="font-medium">Recent failures</h2>
	{#if data.failures.length === 0}
		<p class="text-muted-foreground mt-3 text-sm">Nothing failed in the last day.</p>
	{:else}
		<ul class="mt-3 divide-y text-sm">
			{#each data.failures as job (job.id)}
				<li class="flex flex-wrap items-center gap-x-3 gap-y-1 py-2">
					<StateBadge state={job.state} title={job.product_status} />
					<a href="/jobs/{job.id}" class="font-medium hover:underline">{job.asset_name ?? job.product_job_key}</a>
					<span class="text-muted-foreground">{job.source_name} · {job.kind}</span>
					<span class="text-muted-foreground min-w-0 flex-1 truncate">{job.message ?? ''}</span>
					<span class="text-muted-foreground text-xs">{ago(job.started_at)}</span>
				</li>
			{/each}
		</ul>
	{/if}
</section>
