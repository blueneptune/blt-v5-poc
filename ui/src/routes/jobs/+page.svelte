<script lang="ts">
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import { onDestroy } from 'svelte';
	import { RefreshCw, Search } from '@lucide/svelte';
	import StateBadge from '$lib/components/StateBadge.svelte';
	import StateBar from '$lib/components/StateBar.svelte';
	import { bytes, duration, when } from '$lib/format';
	import {
		KINDS,
		PAGE_SIZE,
		STATES,
		WINDOWS,
		filtersToQuery,
		type JobFilters,
		type Window
	} from '$lib/jobs';

	let { data } = $props();

	const filters = $derived(data.filters);
	const pages = $derived(Math.max(1, Math.ceil(data.total / PAGE_SIZE)));
	const view = $derived(data.grouped ? '&view=protection' : '');

	/** The link for these filters, back to page 1 unless a page is given. */
	function link(change: Partial<JobFilters>, keepView = true): string {
		const query = filtersToQuery({ ...filters, page: 1, ...change });
		const extra = keepView && data.grouped ? 'view=protection' : '';
		if (!extra) return `/jobs${query}`;
		return `/jobs${query ? `${query}&${extra}` : `?${extra}`}`;
	}

	function toggle(list: string[], value: string): string[] {
		return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
	}

	let asset = $state('');
	$effect(() => {
		asset = filters.asset;
	});

	function search(event: SubmitEvent) {
		event.preventDefault();
		goto(link({ asset: asset.trim() }), { keepFocus: true });
	}

	// While anything shown is still running, look again every 30 s.
	let timer: ReturnType<typeof setInterval> | undefined;
	$effect(() => {
		clearInterval(timer);
		if (data.jobs.some((job) => job.is_active)) timer = setInterval(() => invalidateAll(), 30_000);
	});
	onDestroy(() => clearInterval(timer));

	const chip = (on: boolean) =>
		`rounded-full border px-2.5 py-1 text-xs font-medium transition-colors ${
			on ? 'bg-primary text-primary-foreground border-primary' : 'hover:bg-accent text-muted-foreground'
		}`;
</script>

<div class="flex flex-wrap items-baseline justify-between gap-2">
	<h1 class="text-2xl font-semibold tracking-tight">Jobs</h1>
	<div class="flex items-center gap-2 text-sm">
		<a href={link({}, false)} class={chip(!data.grouped)}>Every job</a>
		<a
			href={`${link({}, false)}${filtersToQuery({ ...filters, page: 1 }) ? '&' : '?'}view=protection`}
			class={chip(data.grouped)}
		>
			By protection
		</a>
		<button
			type="button"
			onclick={() => invalidateAll()}
			class="text-muted-foreground hover:text-foreground rounded-md p-1.5"
			aria-label="Refresh"
		>
			<RefreshCw class="size-4" />
		</button>
	</div>
</div>

<!-- The filter bar: kinds first, Backup on by default (decision 3). -->
<div class="bg-card mt-4 space-y-3 rounded-xl border p-3">
	<div class="flex flex-wrap items-center gap-1.5">
		<span class="text-muted-foreground w-14 text-xs">Kind</span>
		<a href={link({ kinds: [] })} class={chip(filters.kinds.length === 0)}>all</a>
		{#each KINDS as kind (kind)}
			<a href={link({ kinds: toggle(filters.kinds, kind) })} class={chip(filters.kinds.includes(kind))}>
				{kind}
			</a>
		{/each}
	</div>
	<div class="flex flex-wrap items-center gap-1.5">
		<span class="text-muted-foreground w-14 text-xs">State</span>
		{#each STATES as state (state)}
			<a
				href={link({ states: toggle(filters.states, state) })}
				class={chip(filters.states.includes(state))}
			>
				{state}
			</a>
		{/each}
	</div>
	<div class="flex flex-wrap items-center gap-1.5">
		<span class="text-muted-foreground w-14 text-xs">Source</span>
		{#each data.sources as source (source.id)}
			<a
				href={link({ sources: toggle(filters.sources, source.name) })}
				class={chip(filters.sources.includes(source.name))}
				title={source.type}
			>
				{source.name}
			</a>
		{/each}
	</div>
	<div class="flex flex-wrap items-center gap-3">
		<span class="text-muted-foreground w-14 text-xs">Started</span>
		<div class="flex flex-wrap gap-1.5">
			{#each Object.keys(WINDOWS) as window (window)}
				<a href={link({ window: window as Window })} class={chip(filters.window === window)}>
					{window === 'all' ? 'any time' : `last ${window}`}
				</a>
			{/each}
			<a
				href={link({ active: filters.active === true ? null : true })}
				class={chip(filters.active === true)}
			>
				running now
			</a>
		</div>
		<form class="ml-auto flex items-center gap-1.5" onsubmit={search}>
			<Search class="text-muted-foreground size-4" />
			<input
				bind:value={asset}
				placeholder="Asset name"
				class="bg-background h-8 w-44 rounded-md border px-2 text-sm"
				aria-label="Search by asset name"
			/>
		</form>
	</div>
	{#if filters.protection}
		<p class="text-sm">
			Protection <strong>{filters.protection}</strong>
			<a href={link({ protection: '' })} class="text-muted-foreground ml-1 underline">clear</a>
		</p>
	{/if}
</div>

{#if data.grouped}
	<!-- The rollup: one line per source and protection. -->
	<div class="bg-card mt-4 overflow-x-auto rounded-xl border">
		<table class="w-full text-sm">
			<thead class="text-muted-foreground border-b text-left text-xs">
				<tr>
					<th class="px-3 py-2 font-medium">Protection</th>
					<th class="px-3 py-2 font-medium">Source</th>
					<th class="w-1/2 px-3 py-2 font-medium">Outcome</th>
					<th class="px-3 py-2 text-right font-medium">Jobs</th>
				</tr>
			</thead>
			<tbody class="divide-y">
				{#each data.groups as row (`${row.source_name}/${row.protection}`)}
					<tr class="hover:bg-muted/50">
						<td class="px-3 py-2">
							{#if row.protection}
								<a
									class="hover:underline"
									href={link({ protection: row.protection, sources: [row.source_name ?? ''] }, false)}
								>
									{row.protection}
								</a>
							{:else}
								<span class="text-muted-foreground">none</span>
							{/if}
						</td>
						<td class="px-3 py-2 whitespace-nowrap">{row.source_name}</td>
						<td class="px-3 py-2">
							<StateBar states={row.states} total={row.total} />
							<p class="text-muted-foreground mt-1 text-xs">
								{Object.entries(row.states).map(([s, n]) => `${n} ${s}`).join(', ')}
							</p>
						</td>
						<td class="px-3 py-2 text-right tabular-nums">{row.total}</td>
					</tr>
				{:else}
					<tr><td colspan="4" class="text-muted-foreground px-3 py-6 text-center">No jobs match.</td></tr>
				{/each}
			</tbody>
		</table>
	</div>
{:else}
	<div class="bg-card mt-4 overflow-x-auto rounded-xl border">
		<table class="w-full text-sm">
			<thead class="text-muted-foreground border-b text-left text-xs">
				<tr>
					<th class="px-3 py-2 font-medium">State</th>
					<th class="px-3 py-2 font-medium">Asset</th>
					<th class="px-3 py-2 font-medium">Protection</th>
					<th class="px-3 py-2 font-medium">Source</th>
					<th class="px-3 py-2 font-medium">Kind</th>
					<th class="px-3 py-2 font-medium">Started</th>
					<th class="px-3 py-2 font-medium">Took</th>
					<th class="px-3 py-2 text-right font-medium">Written</th>
				</tr>
			</thead>
			<tbody class="divide-y">
				{#each data.jobs as job (job.id)}
					<tr
						class="hover:bg-muted/50 cursor-pointer {job.state === 'failed' ? 'bg-red-50/60 dark:bg-red-950/20' : ''}"
						onclick={() => goto(`/jobs/${job.id}`)}
					>
						<td class="px-3 py-2"><StateBadge state={job.state} title={job.product_status} /></td>
						<td class="max-w-64 px-3 py-2">
							<a href="/jobs/{job.id}" class="font-medium hover:underline" onclick={(e) => e.stopPropagation()}>
								{job.asset_name ?? job.product_job_key}
							</a>
							{#if job.message}
								<p class="text-muted-foreground truncate text-xs" title={job.message}>{job.message}</p>
							{/if}
						</td>
						<td class="text-muted-foreground px-3 py-2 whitespace-nowrap">{job.protection ?? ''}</td>
						<td class="px-3 py-2 whitespace-nowrap">
							{job.source_name}
							<span class="text-muted-foreground text-xs">{job.source_type}</span>
						</td>
						<td class="text-muted-foreground px-3 py-2">{job.kind}</td>
						<td class="px-3 py-2 whitespace-nowrap tabular-nums">{when(job.started_at)}</td>
						<td class="px-3 py-2 whitespace-nowrap tabular-nums">{duration(job.started_at, job.ended_at)}</td>
						<td class="px-3 py-2 text-right whitespace-nowrap tabular-nums">{bytes(job.bytes_transferred)}</td>
					</tr>
				{:else}
					<tr><td colspan="8" class="text-muted-foreground px-3 py-6 text-center">No jobs match.</td></tr>
				{/each}
			</tbody>
		</table>
	</div>

	<div class="text-muted-foreground mt-3 flex items-center justify-between text-sm">
		<span>{data.total} jobs</span>
		{#if pages > 1}
			<div class="flex items-center gap-2">
				{#if filters.page > 1}
					<a class="hover:text-foreground underline" href={link({ page: filters.page - 1 })}>Newer</a>
				{/if}
				<span>Page {filters.page} of {pages}</span>
				{#if filters.page < pages}
					<a class="hover:text-foreground underline" href={link({ page: filters.page + 1 })}>Older</a>
				{/if}
			</div>
		{/if}
	</div>
{/if}
