<script lang="ts">
	// A product's record laid out: plain fields as a grid, objects as
	// sections of their own (folded below the first level), lists of
	// objects as tables. lib/record.ts decides what is shown how.
	import Self from './ProductRecord.svelte';
	import { cell, columns, label, layout, type Obj } from '$lib/record';

	let {
		record,
		skip = [],
		depth = 0
	}: { record: Obj; skip?: readonly string[]; depth?: number } = $props();

	const MAX_DEPTH = 5;
	const parts = $derived(layout(record, skip));
</script>

{#if parts.fields.length}
	<dl class="grid grid-cols-[minmax(8rem,auto)_1fr] gap-x-6 gap-y-1.5 text-sm sm:grid-cols-[minmax(8rem,auto)_1fr_minmax(8rem,auto)_1fr]">
		{#each parts.fields as [key, value] (key)}
			<dt class="text-muted-foreground" title={key}>{label(key)}</dt>
			<dd class="min-w-0 break-words">{value}</dd>
		{/each}
	</dl>
{/if}

{#each parts.tables as [key, rows] (key)}
	{@const keys = columns(rows)}
	<div class="mt-4">
		<h4 class="text-sm font-medium" title={key}>
			{label(key)} <span class="text-muted-foreground font-normal">({rows.length})</span>
		</h4>
		<div class="mt-1.5 max-h-80 overflow-auto rounded-lg border">
			<table class="w-full text-xs">
				<thead class="bg-muted text-muted-foreground sticky top-0 text-left">
					<tr>
						{#each keys as column (column)}
							<th class="px-2 py-1.5 font-medium whitespace-nowrap" title={column}>{label(column)}</th>
						{/each}
					</tr>
				</thead>
				<tbody class="striped divide-y">
					{#each rows as row, i (i)}
						<tr>
							{#each keys as column (column)}
								<td class="max-w-80 px-2 py-1.5 align-top break-words">{cell(column, row[column])}</td>
							{/each}
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</div>
{/each}

{#each parts.groups as [key, value] (key)}
	{#if depth >= MAX_DEPTH}
		<div class="mt-3">
			<h4 class="text-sm font-medium" title={key}>{label(key)}</h4>
			<pre class="bg-muted mt-1 overflow-auto rounded-lg p-2 text-xs">{JSON.stringify(value, null, 2)}</pre>
		</div>
	{:else if depth === 0}
		<div class="mt-4">
			<h4 class="text-sm font-medium" title={key}>{label(key)}</h4>
			<div class="mt-2 border-l-2 pl-3">
				<Self record={value} depth={depth + 1} />
			</div>
		</div>
	{:else}
		<details class="mt-3">
			<summary class="cursor-pointer text-sm font-medium" title={key}>{label(key)}</summary>
			<div class="mt-2 border-l-2 pl-3">
				<Self record={value} depth={depth + 1} />
			</div>
		</details>
	{/if}
{/each}

{#if depth === 0 && parts.hidden}
	<p class="text-muted-foreground mt-3 text-xs">
		{parts.hidden} empty {parts.hidden === 1 ? 'field' : 'fields'} left out; the raw record below has every one.
	</p>
{/if}
