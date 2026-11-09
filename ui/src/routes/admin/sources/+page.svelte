<script lang="ts">
	import { enhance } from '$app/forms';
	import { Plus } from '@lucide/svelte';
	import { buttonVariants } from '$lib/components/ui/button';
	import { SOURCE_LABELS, collectorLabel, collectorTone } from '$lib/admin';
	import { ago } from '$lib/format';
	import { stateClass } from '$lib/jobs';
	import CollectorSelect from '$lib/components/CollectorSelect.svelte';

	let { data, form } = $props();
</script>

<svelte:head><title>Sources · Administration · blt</title></svelte:head>

<div class="flex flex-wrap items-end justify-between gap-3">
	<div>
		<h1 class="text-2xl font-semibold tracking-tight">Sources</h1>
		<p class="text-muted-foreground mt-1 text-sm">
			Where blt collects from, and how it signs in. Secrets are never shown: only whether each is set.
		</p>
	</div>
	<a href="/admin/sources/netbackup/new" class={buttonVariants({ size: 'sm' })}>
		<Plus class="size-4" /> Add a NetBackup primary
	</a>
</div>

{#if data.problem}
	<p class="text-destructive mt-6 text-sm" role="alert">Could not read the sources: {data.problem}</p>
{/if}
{#if form?.message}
	<p class="text-destructive mt-4 text-sm" role="alert">{form.name}: {form.message}</p>
{/if}

{#if data.collectors.length}
	<section class="mt-6" aria-labelledby="collectors">
		<h2 id="collectors" class="text-sm font-semibold">Collectors</h2>
		<p class="text-muted-foreground mt-0.5 text-xs">
			The workers that collect. A remote one reaches sources this install cannot, from inside their
			network (docs/remote-collector.md).
		</p>
		<ul class="mt-2 flex flex-wrap gap-2">
			{#each data.collectors as collector (collector.name)}
				<li class="bg-card flex min-w-48 flex-col gap-1 rounded-lg border px-3 py-2 text-sm">
					<div class="flex items-center justify-between gap-3">
						<span class="font-medium">{collectorLabel(collector.name)}</span>
						<span
							class="rounded-full px-2 py-0.5 text-xs font-medium {stateClass(collectorTone(collector.status))}"
						>
							{collector.status}
						</span>
					</div>
					<span class="text-muted-foreground text-xs">
						{collector.sources.length}
						{collector.sources.length === 1 ? 'source' : 'sources'}
						{#if collector.last_polled}· asked for work {ago(collector.last_polled)}{/if}
					</span>
				</li>
			{/each}
		</ul>
	</section>
{/if}

<div class="bg-card mt-6 overflow-x-auto rounded-xl border">
	<table class="w-full text-sm">
		<thead class="text-muted-foreground border-b text-left text-xs">
			<tr>
				<th class="px-4 py-2 font-medium">Name</th>
				<th class="px-4 py-2 font-medium">Product</th>
				<th class="px-4 py-2 font-medium">Address</th>
				<th class="px-4 py-2 font-medium">Secrets set</th>
				<th class="hidden px-4 py-2 font-medium 2xl:table-cell">Changed</th>
				<th class="px-4 py-2 font-medium">Collector</th>
				<th class="px-4 py-2 font-medium">Collecting</th>
			</tr>
		</thead>
		<tbody>
			{#each data.sources as source (source.block_id)}
				<tr class="border-b last:border-0">
					<td class="px-4 py-2 font-medium">
						{#if source.type === 'netbackup'}
							<a href="/admin/sources/netbackup/{source.name}" class="hover:underline">{source.name}</a>
						{:else}
							{source.name}
						{/if}
					</td>
					<td class="px-4 py-2">{SOURCE_LABELS[source.type] ?? source.type}</td>
					<td class="text-muted-foreground max-w-56 truncate px-4 py-2" title={source.address ?? ''}>
						{source.address ?? '—'}
					</td>
					<td class="px-4 py-2">
						{#each source.secrets_set as secret (secret)}
							<span class="bg-muted mr-1 rounded px-1.5 py-0.5 font-mono text-xs">{secret}</span>
						{:else}
							<span class="text-muted-foreground">none</span>
						{/each}
						{#if source.has_ca_certificate}
							<span class="bg-muted mr-1 rounded px-1.5 py-0.5 font-mono text-xs">CA certificate</span>
						{/if}
					</td>
					<td class="text-muted-foreground hidden px-4 py-2 whitespace-nowrap 2xl:table-cell">{ago(source.updated)}</td>
					<td class="px-4 py-2">
						<form method="POST" action="?/collector" use:enhance class="flex items-center gap-1">
							<input type="hidden" name="type" value={source.type} />
							<input type="hidden" name="name" value={source.name} />
							<CollectorSelect
								collectors={data.collectors}
								value={source.collector}
								label="Collector for {source.name}"
								submit
							/>
							<noscript><button type="submit" class="text-xs underline">Set</button></noscript>
						</form>
					</td>
					<td class="px-4 py-2">
						<form method="POST" action="?/collect" use:enhance>
							<input type="hidden" name="type" value={source.type} />
							<input type="hidden" name="name" value={source.name} />
							<input type="hidden" name="collect" value={String(!source.collect)} />
							<button
								type="submit"
								role="switch"
								aria-checked={source.collect}
								aria-label="Collect from {source.name}"
								class="relative inline-flex h-5 w-9 items-center rounded-full transition-colors
									{source.collect ? 'bg-emerald-600' : 'bg-muted-foreground/30'}"
							>
								<span
									class="inline-block size-4 rounded-full bg-white shadow transition-transform
										{source.collect ? 'translate-x-4.5' : 'translate-x-0.5'}"
								></span>
							</button>
						</form>
					</td>
				</tr>
			{:else}
				<tr><td colspan="7" class="text-muted-foreground px-4 py-6 text-center">No sources yet.</td></tr>
			{/each}
		</tbody>
	</table>
</div>

<p class="text-muted-foreground mt-4 text-xs">
	CommCells and Rubrik accounts get their credentials from blt-prefect-import and blt-rubrik for now
	(docs/admin.md); here they can be switched on and off, and given a collector.
</p>
