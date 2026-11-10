<script lang="ts">
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import { onDestroy, onMount } from 'svelte';
	import { Columns3, Pin, PinOff, RefreshCw, Search } from '@lucide/svelte';
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
	import {
		JOB_COLUMNS,
		MAX_WIDTH,
		MIN_WIDTH,
		defaultPrefs,
		parsePrefs,
		reduction,
		resetWidth,
		setWidth,
		shownColumns,
		toggleColumn,
		widthOf,
		type ColumnPrefs,
		type JobColumn
	} from '$lib/jobColumns';

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

	// The heading, filter bar and column headings stay on screen while a long
	// list scrolls, unless the viewer unpins them; the choice is theirs, kept in
	// this browser. Not on a phone, where the bar would fill half the screen.
	const PIN_KEY = 'blt.jobs.pinFilters';
	let pinned = $state(true);
	onMount(() => {
		try {
			pinned = localStorage.getItem(PIN_KEY) !== 'false';
		} catch {
			// No storage (private window): stay pinned.
		}
	});
	function togglePin() {
		pinned = !pinned;
		try {
			localStorage.setItem(PIN_KEY, String(pinned));
		} catch {
			// Not remembered, but still works for this visit.
		}
	}

	// Which columns are shown, and how wide: the viewer's own, kept in this
	// browser (lib/jobColumns.ts). A column is resized by dragging its
	// heading's right edge, or with the arrow keys there; a double-click (or
	// Home) gives it back its own width.
	const COLUMNS_KEY = 'blt.jobs.columns';
	let prefs = $state<ColumnPrefs>(defaultPrefs());
	onMount(() => {
		try {
			prefs = parsePrefs(localStorage.getItem(COLUMNS_KEY));
		} catch {
			// No storage: the usual columns.
		}
	});
	function keep(next: ColumnPrefs) {
		prefs = next;
		try {
			localStorage.setItem(COLUMNS_KEY, JSON.stringify(next));
		} catch {
			// Not remembered, but still works for this visit.
		}
	}
	const columns = $derived(shownColumns(prefs));
	const tableWidth = $derived(columns.reduce((sum, c) => sum + widthOf(prefs, c), 0));

	let dragging: { id: string; x: number; width: number } | null = null;
	function grab(event: PointerEvent, column: JobColumn) {
		event.preventDefault();
		(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
		dragging = { id: column.id, x: event.clientX, width: widthOf(prefs, column) };
	}
	function drag(event: PointerEvent) {
		if (dragging) prefs = setWidth(prefs, dragging.id, dragging.width + event.clientX - dragging.x);
	}
	function drop() {
		if (dragging) keep(prefs);
		dragging = null;
	}
	function nudge(event: KeyboardEvent, column: JobColumn) {
		const step = event.shiftKey ? 64 : 16;
		const width = widthOf(prefs, column);
		if (event.key === 'ArrowLeft') keep(setWidth(prefs, column.id, width - step));
		else if (event.key === 'ArrowRight') keep(setWidth(prefs, column.id, width + step));
		else if (event.key === 'Home') keep(resetWidth(prefs, column.id));
		else return;
		event.preventDefault();
	}

	let picking = $state(false);
	let picker = $state<HTMLElement | null>(null);
	function outside(event: MouseEvent) {
		if (picking && picker && !picker.contains(event.target as Node)) picking = false;
	}

	const chip = (on: boolean) =>
		`rounded-full border px-2.5 py-1 text-xs font-medium transition-colors ${
			on ? 'bg-primary text-primary-foreground border-primary' : 'hover:bg-accent text-muted-foreground'
		}`;
</script>

<svelte:window onclick={outside} onkeydown={(e) => e.key === 'Escape' && (picking = false)} />

<!-- Pinned (wider screens): the page fits the window and only the table
     scrolls, under the bar and with its headings kept at its top. -->
<div class={pinned ? 'md:flex md:h-[calc(100svh-3rem)] md:flex-col' : ''}>
	<div class="shrink-0">
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
				{#if !data.grouped}
					<div class="relative" bind:this={picker}>
						<button
							type="button"
							onclick={() => (picking = !picking)}
							class="text-muted-foreground hover:text-foreground flex items-center gap-1 rounded-md p-1.5 text-xs"
							aria-expanded={picking}
							aria-controls="job-columns"
						>
							<Columns3 class="size-4" /> Columns
						</button>
						{#if picking}
							<div
								id="job-columns"
								class="bg-card text-card-foreground absolute right-0 z-30 mt-1 w-72 rounded-xl border p-2 shadow-lg"
							>
								<fieldset>
									<legend class="text-muted-foreground px-2 pb-1 text-xs">Columns shown</legend>
									<div class="max-h-[60svh] overflow-y-auto">
										{#each JOB_COLUMNS as column (column.id)}
											{@const on = prefs.shown.includes(column.id)}
											<label class="hover:bg-accent flex cursor-pointer items-start gap-2 rounded-md px-2 py-1.5">
												<input
													type="checkbox"
													class="mt-0.5"
													checked={on}
													disabled={on && prefs.shown.length === 1}
													onchange={() => keep(toggleColumn(prefs, column.id))}
												/>
												<span>
													<span class="block text-sm">{column.label}</span>
													<span class="text-muted-foreground block text-xs">{column.description}</span>
												</span>
											</label>
										{/each}
									</div>
								</fieldset>
								<button
									type="button"
									class="text-muted-foreground hover:text-foreground mt-1 w-full rounded-md px-2 py-1.5 text-left text-xs underline"
									onclick={() => keep(defaultPrefs())}
								>
									Back to the usual columns and widths
								</button>
							</div>
						{/if}
					</div>
				{/if}
				<button
					type="button"
					onclick={togglePin}
					class="hidden rounded-md p-1.5 md:block {pinned ? 'text-foreground' : 'text-muted-foreground hover:text-foreground'}"
					aria-pressed={pinned}
					aria-label={pinned ? 'Let the filters scroll away' : 'Keep the filters on screen'}
					title={pinned ? 'Filters pinned: click to let them scroll away' : 'Pin the filters to the top'}
				>
					{#if pinned}<Pin class="size-4" />{:else}<PinOff class="size-4" />{/if}
				</button>
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
	</div>

	{#if data.grouped}
		<!-- The rollup: one line per source and protection. -->
		<div class="bg-card mt-4 min-h-0 overflow-auto rounded-xl border">
			<table class="w-full text-sm">
				<thead
					class="text-muted-foreground bg-card sticky top-0 z-10 text-left text-xs shadow-[inset_0_-1px_0_var(--border)]"
				>
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
		<div class="bg-card mt-4 min-h-0 overflow-auto rounded-xl border">
			<!-- Fixed layout: each column is as wide as its <col> says; the last,
			     empty one takes what a wide window has left over. -->
			<table class="table-fixed text-sm" style="width: max(100%, {tableWidth}px)">
				<colgroup>
					{#each columns as column (column.id)}<col style="width: {widthOf(prefs, column)}px" />{/each}
					<col />
				</colgroup>
				<thead
					class="text-muted-foreground bg-card sticky top-0 z-10 text-left text-xs shadow-[inset_0_-1px_0_var(--border)]"
				>
					<tr>
						{#each columns as column (column.id)}
							<!-- Named outright: its resize handle is not part of its name. -->
							<th
								aria-label={column.label}
								class="relative truncate px-3 py-2 font-medium {column.align === 'right' ? 'text-right' : ''}"
							>
								{column.label}
								<!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
								<span
									role="separator"
									aria-orientation="vertical"
									aria-label="Width of {column.label}"
									aria-valuenow={widthOf(prefs, column)}
									aria-valuemin={MIN_WIDTH}
									aria-valuemax={MAX_WIDTH}
									tabindex="0"
									title="Drag to resize; double-click for its own width"
									class="hover:bg-primary/30 focus-visible:bg-primary/40 absolute top-0 right-0 h-full w-2 cursor-col-resize touch-none outline-none select-none"
									onpointerdown={(e) => grab(e, column)}
									onpointermove={drag}
									onpointerup={drop}
									onpointercancel={drop}
									ondblclick={() => keep(resetWidth(prefs, column.id))}
									onkeydown={(e) => nudge(e, column)}
								></span>
							</th>
						{/each}
						<th aria-hidden="true"></th>
					</tr>
				</thead>
				<tbody class="divide-y">
					{#each data.jobs as job (job.id)}
						<tr
							class="hover:bg-muted/50 cursor-pointer {job.state === 'failed' ? 'bg-red-50/60 dark:bg-red-950/20' : ''}"
							onclick={() => goto(`/jobs/${job.id}`)}
						>
							{#each columns as column (column.id)}
								<td class="truncate px-3 py-2 {column.align === 'right' ? 'text-right tabular-nums' : ''}">
									{@render cell(job, column.id)}
								</td>
							{/each}
							<td></td>
						</tr>
					{:else}
						<tr>
							<td colspan={columns.length + 1} class="text-muted-foreground px-3 py-6 text-center">No jobs match.</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>

		<div class="text-muted-foreground mt-3 flex shrink-0 items-center justify-between text-sm">
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
</div>

{#snippet cell(job: (typeof data.jobs)[number], id: string)}
	{#if id === 'state'}
		<StateBadge state={job.state} title={job.product_status} />
	{:else if id === 'asset'}
		<a
			href="/jobs/{job.id}"
			class="block truncate font-medium hover:underline"
			title={job.asset_name ?? job.product_job_key}
			onclick={(e) => e.stopPropagation()}
		>
			{job.asset_name ?? job.product_job_key}
		</a>
		{#if job.message && !prefs.shown.includes('message')}
			<p class="text-muted-foreground truncate text-xs" title={job.message}>{job.message}</p>
		{/if}
	{:else if id === 'message'}
		<span class="text-muted-foreground" title={job.message ?? ''}>{job.message ?? ''}</span>
	{:else if id === 'asset_type'}
		<span class="text-muted-foreground" title={job.asset_type ?? ''}>{job.asset_type ?? ''}</span>
	{:else if id === 'protection'}
		<span class="text-muted-foreground" title={job.protection ?? ''}>{job.protection ?? ''}</span>
	{:else if id === 'source'}
		{job.source_name} <span class="text-muted-foreground text-xs">{job.source_type}</span>
	{:else if id === 'kind'}
		<span class="text-muted-foreground">{job.kind}</span>
	{:else if id === 'product_status'}
		<span title={job.product_status}>{job.product_status}</span>
	{:else if id === 'job_id'}
		<span class="font-mono text-xs">{job.product_job_key}</span>
	{:else if id === 'started'}
		<span class="tabular-nums">{when(job.started_at)}</span>
	{:else if id === 'ended'}
		<span class="tabular-nums">{job.is_active ? 'running' : when(job.ended_at)}</span>
	{:else if id === 'took'}
		<span class="tabular-nums">{duration(job.started_at, job.ended_at)}</span>
	{:else if id === 'written'}
		{bytes(job.bytes_transferred)}
	{:else if id === 'protected'}
		{bytes(job.bytes_protected)}
	{:else if id === 'reduction'}
		{reduction(job.bytes_protected, job.bytes_transferred)}
	{:else if id === 'attempt'}
		{job.attempt ?? ''}
	{:else if id === 'updated'}
		<span class="tabular-nums">{when(job.updated_at)}</span>
	{:else if id === 'collected'}
		<span class="tabular-nums">{when(job.last_collected_at)}</span>
	{/if}
{/snippet}
