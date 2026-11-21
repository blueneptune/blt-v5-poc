<script lang="ts">
	import { ago, when } from '$lib/format';
	import {
		KINDS,
		KIND_LABEL,
		KIND_ONE,
		VERDICTS,
		VERDICT_HELP,
		VERDICT_LABEL,
		tally,
		verdictClass,
		type Kind,
		type Verdict
	} from '$lib/assets';

	let { data } = $props();

	const TILES: Verdict[] = ['unprotected', 'stale', 'protected', 'excluded'];
	const counts = $derived(tally(data.summary, data.kind));
	const sources = $derived(Object.entries(data.summary.last_inventory));
	const kindCounts = $derived(
		Object.fromEntries(
			KINDS.map((k) => [k, data.summary.counts.filter((c) => c.kind === k).reduce((n, c) => n + c.count, 0)])
		) as Record<Kind, number>
	);

	// The page's address with one thing changed, the others kept.
	// Back to page 1 unless a page is given.
	const at = (change: {
		days?: number;
		kind?: Kind[];
		verdict?: Verdict[];
		source?: string | null;
		page?: number;
	}) => {
		const q = new URLSearchParams({ days: String(change.days ?? data.days) });
		for (const k of change.kind ?? data.kind) q.append('kind', k);
		const verdict = change.verdict ?? data.verdict;
		// An empty verdict list is "every verdict", which has to be said.
		if (verdict.length === 0) q.set('verdict', '');
		for (const v of verdict) q.append('verdict', v);
		const source = change.source === undefined ? data.source : change.source;
		if (source) q.set('source', source);
		if (data.name) q.set('name', data.name);
		if (change.page && change.page > 1) q.set('page', String(change.page));
		return `?${q}`;
	};
	const toggle = <T,>(list: T[], item: T) =>
		list.includes(item) ? list.filter((x) => x !== item) : [...list, item];
	const chip = (on: boolean) =>
		`rounded-full border px-2.5 py-0.5 ${on ? 'bg-accent text-accent-foreground font-medium' : 'text-muted-foreground hover:bg-accent'}`;
</script>

<h1 class="text-2xl font-semibold tracking-tight">Assets</h1>
<p class="text-muted-foreground mt-1 text-sm">
	Everything each backup source knows of, and whether it is backed up. Each source says what exists and what
	protects it; blt judges how recent the backups are.
</p>

<div class="mt-6 grid grid-cols-2 gap-4 lg:grid-cols-4">
	{#each TILES as v (v)}
		<a
			href={at({ verdict: [v] })}
			class="bg-card hover:bg-accent/40 rounded-xl border p-4 shadow-sm"
			aria-current={data.verdict.length === 1 && data.verdict[0] === v ? 'true' : undefined}
			title={VERDICT_HELP[v]}
		>
			<p class="text-muted-foreground text-xs">{VERDICT_LABEL[v]}</p>
			<p class="mt-1 text-2xl font-semibold tabular-nums">{counts[v].toLocaleString()}</p>
		</a>
	{/each}
</div>

<div class="mt-6 flex flex-col gap-2 text-xs">
	<div class="flex flex-wrap items-center gap-1" role="group" aria-label="Verdict">
		<span class="text-muted-foreground mr-1 w-16">Verdict</span>
		<a href={at({ verdict: [] })} class={chip(data.verdict.length === 0)}>All</a>
		{#each VERDICTS as v (v)}
			<a
				href={at({ verdict: toggle(data.verdict, v) })}
				aria-current={data.verdict.includes(v) ? 'true' : undefined}
				class={chip(data.verdict.includes(v))}
				title={VERDICT_HELP[v]}>{VERDICT_LABEL[v]}</a
			>
		{/each}
	</div>
	<div class="flex flex-wrap items-center gap-1" role="group" aria-label="Kind">
		<span class="text-muted-foreground mr-1 w-16">Kind</span>
		<a href={at({ kind: [] })} class={chip(data.kind.length === 0)}>All</a>
		{#each KINDS.filter((k) => kindCounts[k] > 0 || data.kind.includes(k)) as k (k)}
			<a
				href={at({ kind: toggle(data.kind, k) })}
				aria-current={data.kind.includes(k) ? 'true' : undefined}
				class={chip(data.kind.includes(k))}>{KIND_LABEL[k]}</a
			>
		{/each}
	</div>
	{#if sources.length > 1}
		<div class="flex flex-wrap items-center gap-1" role="group" aria-label="Source">
			<span class="text-muted-foreground mr-1 w-16">Source</span>
			<a href={at({ source: null })} class={chip(!data.source)}>All</a>
			{#each sources as [key] (key)}
				{@const name = key.slice(key.indexOf('/') + 1)}
				<a href={at({ source: name })} class={chip(data.source === name)}>{name}</a>
			{/each}
		</div>
	{/if}
	<div class="flex flex-wrap items-center gap-1" role="group" aria-label="Stale after">
		<span class="text-muted-foreground mr-1 w-16">Stale after</span>
		{#each data.windows as days (days)}
			<a href={at({ days })} class={chip(data.days === days)}>{days === 1 ? '24 hours' : `${days} days`}</a>
		{/each}
	</div>
	<form method="get" class="flex flex-wrap items-center gap-2">
		<span class="text-muted-foreground mr-1 w-16">Name</span>
		<input type="hidden" name="days" value={data.days} />
		{#each data.kind as k (k)}<input type="hidden" name="kind" value={k} />{/each}
		{#if data.verdict.length === 0}<input type="hidden" name="verdict" value="" />{/if}
		{#each data.verdict as v (v)}<input type="hidden" name="verdict" value={v} />{/each}
		{#if data.source}<input type="hidden" name="source" value={data.source} />{/if}
		<input
			name="name"
			value={data.name ?? ''}
			placeholder="Part of a name"
			aria-label="Name contains"
			class="bg-background w-56 rounded-md border px-2 py-1 text-sm"
		/>
		<button class="hover:bg-accent rounded-md border px-2 py-1">Search</button>
	</form>
</div>

<div class="bg-card mt-4 overflow-x-auto rounded-xl border shadow-sm">
	<table class="w-full min-w-[820px] text-sm">
		<thead class="bg-muted/60 text-muted-foreground text-left text-xs">
			<tr>
				<th class="px-3 py-2 font-medium">Name</th>
				<th class="px-3 py-2 font-medium">Kind</th>
				<th class="px-3 py-2 font-medium">Source</th>
				<th class="px-3 py-2 font-medium">Plan / SLA</th>
				<th class="px-3 py-2 font-medium">Verdict</th>
				<th class="px-3 py-2 text-right font-medium">Last backup</th>
			</tr>
		</thead>
		<tbody class="striped">
			{#each data.assets as asset (asset.id)}
				<tr class="border-t">
					<td class="px-3 py-2">
						<span class="font-medium">{asset.name}</span>
						{#if asset.location}<div class="text-muted-foreground text-xs">{asset.location}</div>{/if}
					</td>
					<td class="px-3 py-2">
						{KIND_ONE[asset.kind]}
						<div class="text-muted-foreground text-xs">{asset.app_type ?? asset.source_kind ?? ''}</div>
					</td>
					<td class="px-3 py-2">
						{asset.source_name}
						<div class="text-muted-foreground text-xs">{asset.source_type}</div>
					</td>
					<td class="px-3 py-2">{asset.plan ?? '—'}</td>
					<td class="px-3 py-2">
						<span
							class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium {verdictClass(asset.verdict)}"
							title={VERDICT_HELP[asset.verdict]}>{VERDICT_LABEL[asset.verdict]}</span
						>
						{#if asset.deactivated_policies?.length}
							<div
								class="text-muted-foreground mt-0.5 text-xs"
								title="Turning one of these back on would protect it"
							>
								In deactivated {asset.deactivated_policies.length === 1 ? 'policy' : 'policies'}:
								{asset.deactivated_policies.join(', ')}
							</div>
						{/if}
					</td>
					<td class="px-3 py-2 text-right whitespace-nowrap" title={when(asset.last_backup_at)}>
						{asset.last_backup_at ? ago(asset.last_backup_at) : 'never'}
					</td>
				</tr>
			{:else}
				<tr>
					<td colspan="6" class="text-muted-foreground px-3 py-6 text-center">
						{sources.length ? 'Nothing matches.' : 'No inventory collected yet.'}
					</td>
				</tr>
			{/each}
		</tbody>
	</table>
</div>
{#if data.total > 0}
	<div class="text-muted-foreground mt-2 flex flex-wrap items-center gap-3 text-xs">
		<span>{data.total.toLocaleString()} {data.total === 1 ? 'asset' : 'assets'}</span>
		{#if data.pages > 1}
			{#if data.page > 1}
				<a class="hover:text-foreground underline" href={at({ page: data.page - 1 })}>Previous</a>
			{/if}
			<span>Page {data.page} of {data.pages}</span>
			{#if data.page < data.pages}
				<a class="hover:text-foreground underline" href={at({ page: data.page + 1 })}>Next</a>
			{/if}
		{/if}
	</div>
{/if}

{#if sources.length}
	<p class="text-muted-foreground mt-4 text-xs">
		Inventory last listed:
		{#each sources as [key, last], i (key)}{i ? ' · ' : ''}{key} {last ? ago(last) : 'never'}{/each}
	</p>
{/if}
