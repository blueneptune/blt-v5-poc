<script lang="ts">
	import { enhance } from '$app/forms';
	import { Play } from '@lucide/svelte';
	import { Button } from '$lib/components/ui/button';
	import RunBadge from '$lib/components/RunBadge.svelte';
	import RunHealth from '$lib/components/RunHealth.svelte';
	import { cronText, parameterFields } from '$lib/admin';
	import { ago, when } from '$lib/format';

	let { data, form } = $props();
	let open = $state<string | null>(null);

	const scheduled = $derived(data.deployments.filter((d) => d.schedules.length));
	const byHand = $derived(data.deployments.filter((d) => !d.schedules.length));
	const clearing = $derived(form && 'cleared' in form ? form : null);

	function show(value: unknown): string {
		if (value === null || value === undefined) return '';
		return Array.isArray(value) ? value.join(', ') : typeof value === 'object' ? JSON.stringify(value) : String(value);
	}
	const input = 'bg-background mt-1 h-8 w-full rounded-md border px-2 text-sm';
</script>

<svelte:head><title>Schedules · Administration · blt</title></svelte:head>

<h1 class="text-2xl font-semibold tracking-tight">Schedules</h1>
<p class="text-muted-foreground mt-1 max-w-3xl text-sm">
	What runs, and when: schedules are in UTC, times in your own zone. A pause stays when the
	worker restarts; the schedules themselves are set in prefect.yaml.
</p>

{#if data.problem}
	<p class="text-destructive mt-6 text-sm" role="alert">Could not read the schedules: {data.problem}</p>
{/if}

{#if data.health}
	<RunHealth
		health={data.health}
		cleared={clearing?.cleared ?? null}
		message={clearing && 'message' in clearing ? (clearing.message as string) : null}
	/>
{/if}

{#snippet table(rows: typeof data.deployments, heading: string)}
	<h2 class="mt-8 text-lg font-semibold">{heading}</h2>
	<div class="bg-card relative mt-3 overflow-x-auto rounded-xl border shadow-sm">
		<table class="w-full text-sm">
			<thead class="text-muted-foreground border-b text-left text-xs">
				<tr>
					<th class="px-4 py-2 font-medium">Flow</th>
					<th class="px-4 py-2 font-medium">When</th>
					<th class="px-4 py-2 font-medium">Last run</th>
					<th class="px-4 py-2 font-medium">Next</th>
					<th class="px-4 py-2 font-medium"><span class="sr-only">Actions</span></th>
				</tr>
			</thead>
			<tbody class="striped">
				{#each rows as d (d.id)}
					<tr class="border-b last:border-0 align-top">
						<td class="px-4 py-2">
							<a href="/admin/runs?deployment={d.id}" class="font-medium whitespace-nowrap hover:underline">{d.flow_name}</a>
							<span class="text-muted-foreground whitespace-nowrap">/ {d.name}</span>
							{#if d.description}<p class="text-muted-foreground mt-0.5 max-w-md text-xs">{d.description}</p>{/if}
						</td>
						<td class="px-4 py-2 whitespace-nowrap">
							{#each d.schedules as s, i (i)}
								<p class={s.active ? '' : 'text-muted-foreground line-through'}>
									{s.cron ? cronText(s.cron, s.timezone) : `every ${s.interval_seconds}s`}
								</p>
							{:else}
								<span class="text-muted-foreground">by hand</span>
							{/each}
							{#if d.paused}<p class="text-xs font-medium text-amber-700 dark:text-amber-300">paused</p>{/if}
						</td>
						<td class="px-4 py-2 whitespace-nowrap">
							{#if d.last_run}
								<a href="/admin/runs/{d.last_run.id}" class="inline-flex items-center gap-2 hover:underline">
									<RunBadge type={d.last_run.state_type} name={d.last_run.state_name} />
									<span class="text-muted-foreground text-xs">{ago(d.last_run.start_time ?? d.last_run.expected_start_time)}</span>
								</a>
							{:else}
								<span class="text-muted-foreground">never</span>
							{/if}
						</td>
						<td class="text-muted-foreground px-4 py-2 whitespace-nowrap">
							{d.paused ? '' : when(d.next_run?.expected_start_time)}
						</td>
						<td class="px-4 py-2">
							<div class="flex justify-end gap-2">
								{#if d.schedules.length}
									<form method="POST" action="?/pause" use:enhance>
										<input type="hidden" name="id" value={d.id} />
										<input type="hidden" name="paused" value={String(!d.paused)} />
										<Button type="submit" size="sm" variant="outline">{d.paused ? 'Resume' : 'Pause'}</Button>
									</form>
								{/if}
								<Button
									type="button"
									size="sm"
									variant="outline"
									aria-expanded={open === d.id}
									onclick={() => (open = open === d.id ? null : d.id)}
								>
									<Play class="size-3.5" /> Run now
								</Button>
							</div>
						</td>
					</tr>
					{#if open === d.id || form?.id === d.id}
						{@const fields = parameterFields(d.parameter_schema, d.parameters)}
						<tr class="bg-muted/40 border-b">
							<td colspan="5" class="px-4 py-3">
								<form method="POST" action="?/run" use:enhance class="grid max-w-3xl gap-3 sm:grid-cols-2">
									<input type="hidden" name="id" value={d.id} />
									{#each fields as field (field.name)}
										<div>
											{#if field.type === 'boolean'}
												<input type="hidden" name="present:{field.name}" value="1" />
												<label class="mt-5 flex items-center gap-2 text-sm">
													<input type="checkbox" name="param:{field.name}" checked={field.default === true} />
													{field.title}
												</label>
											{:else}
												<label class="text-sm font-medium" for="{d.id}-{field.name}">
													{field.title}{field.required ? '' : ' (optional)'}
												</label>
												<input
													id="{d.id}-{field.name}"
													name="param:{field.name}"
													placeholder={show(field.default)}
													required={field.required}
													class={input}
												/>
											{/if}
											{#if field.description}<p class="text-muted-foreground mt-0.5 text-xs">{field.description}</p>{/if}
											{#if field.type === 'array'}<p class="text-muted-foreground mt-0.5 text-xs">Comma separated.</p>{/if}
										</div>
									{/each}
									<div class="flex items-end gap-3 sm:col-span-2">
										<Button type="submit" size="sm">Start the run</Button>
										{#if !fields.length}<span class="text-muted-foreground text-xs">It takes no parameters.</span>{/if}
										{#if form?.id === d.id && form?.message}
											<span class="text-destructive text-sm" role="alert">{form.message}</span>
										{/if}
									</div>
								</form>
							</td>
						</tr>
					{/if}
				{/each}
			</tbody>
		</table>
	</div>
{/snippet}

{#if scheduled.length}{@render table(scheduled, 'On a schedule')}{/if}
{#if byHand.length}{@render table(byHand, 'By hand, or started by another flow')}{/if}
