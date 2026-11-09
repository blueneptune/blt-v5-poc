<script lang="ts">
	// Are runs getting through? The collection slots against the runs that
	// could hold them, and runs that look stuck (GET /v1/admin/runs/health),
	// with a confirmed "clear" that cancels those runs and gives the slots
	// back (docs/admin.md, "Stuck runs").
	import { enhance } from '$app/forms';
	import { CircleCheck, TriangleAlert } from '@lucide/svelte';
	import { Button } from '$lib/components/ui/button';
	import RunBadge from '$lib/components/RunBadge.svelte';
	import type { components } from '$lib/api/schema';

	type Health = components['schemas']['RunHealthOut'];
	type Cleared = components['schemas']['ClearStuckOut'];

	let { health, cleared = null, message = null }: { health: Health; cleared?: Cleared | null; message?: string | null } =
		$props();
	let confirming = $state(false);

	const SHOWN = 8;
	const slots = $derived(health.slots);
	const hours = $derived(health.stuck_after_minutes / 60);

	function minutes(value: number): string {
		if (value < 120) return `${value} min`;
		if (value < 48 * 60) return `${Math.round(value / 60)} h`;
		return `${Math.round(value / 1440)} days`;
	}
	function plural(n: number, word: string): string {
		return `${n} ${word}${n === 1 ? '' : 's'}`;
	}
</script>

<section aria-label="Run health" class="mt-6 max-w-4xl">
	{#if health.healthy}
		<p class="text-muted-foreground flex items-center gap-2 text-sm">
			<CircleCheck class="size-4 text-emerald-600 dark:text-emerald-400" />
			Runs are getting through{#if slots}: {slots.active} of {slots.limit} collection slots taken, {health.collecting}
				collecting{/if}.
		</p>
	{:else}
		<div class="rounded-xl border border-amber-300 bg-amber-50 p-4 text-sm dark:border-amber-800 dark:bg-amber-950/40" role="alert">
			<p class="flex items-center gap-2 font-medium text-amber-900 dark:text-amber-200">
				<TriangleAlert class="size-4" /> Runs are stuck
			</p>
			<ul class="mt-2 list-disc space-y-1 pl-5">
				{#if slots && health.leaked_slots}
					<li>
						{slots.active} of {slots.limit} collection slots are taken, but only {health.collecting}
						{health.collecting === 1 ? 'run is' : 'runs are'} collecting: {plural(health.leaked_slots, 'slot')}
						{health.leaked_slots === 1 ? 'is' : 'are'} held by runs that are gone, and new runs wait for
						{health.leaked_slots === 1 ? 'it' : 'them'}.
					</li>
				{/if}
				{#if health.stuck.length}
					<li>
						{plural(health.stuck.length, 'run')}
						{health.stuck.length === 1 ? 'has' : 'have'} been running for over {hours} h, or starting or
						cancelling for over 10 minutes.
					</li>
				{/if}
			</ul>

			{#if health.stuck.length}
				<table class="mt-3 w-full text-xs">
					<tbody>
						{#each health.stuck.slice(0, SHOWN) as run (run.id)}
							<tr class="border-t border-amber-200 dark:border-amber-900">
								<td class="py-1 pr-3">
									<a href="/admin/runs/{run.id}" class="font-medium hover:underline">{run.name}</a>
									<span class="text-muted-foreground">{run.flow_name ?? ''}</span>
								</td>
								<td class="py-1 pr-3"><RunBadge type={run.state_type} name={run.state_name} /></td>
								<td class="text-muted-foreground py-1 text-right whitespace-nowrap">{minutes(run.stuck_minutes)}</td>
							</tr>
						{/each}
					</tbody>
				</table>
				{#if health.stuck.length > SHOWN}
					<p class="text-muted-foreground mt-1 text-xs">and {health.stuck.length - SHOWN} more.</p>
				{/if}
			{/if}

			{#if confirming}
				<form
					method="POST"
					action="?/clearStuck"
					class="mt-3 rounded-lg border border-amber-300 bg-white/60 p-3 dark:border-amber-800 dark:bg-black/20"
					use:enhance={() =>
						async ({ update }) => {
							confirming = false;
							await update();
						}}
				>
					<input type="hidden" name="stuck_after_minutes" value={health.stuck_after_minutes} />
					{#each health.stuck as run (run.id)}<input type="hidden" name="run" value={run.id} />{/each}
					<p>
						{#if health.stuck.length}This cancels the {plural(health.stuck.length, 'run')} above - no worker
							is running {health.stuck.length === 1 ? 'it' : 'them'} - and then{:else}This{/if}
						brings the slot count down to the runs still collecting. A run that is working keeps its slot,
						and the next scheduled runs catch up.
					</p>
					<div class="mt-2 flex gap-2">
						<Button type="submit" size="sm">Yes, clear them</Button>
						<Button type="button" size="sm" variant="outline" onclick={() => (confirming = false)}>Keep them</Button>
					</div>
				</form>
			{:else}
				<Button type="button" size="sm" class="mt-3" onclick={() => (confirming = true)}>Clear stuck runs…</Button>
			{/if}
		</div>
	{/if}

	{#if cleared}
		<p class="mt-2 text-sm" role="status">
			Cancelled {plural(cleared.cancelled.length, 'run')}{#if cleared.skipped}
				({cleared.skipped} had moved on and were left alone){/if}{#if cleared.slots_before !== null && cleared.slots_before !== cleared.slots_after}; slots taken {cleared.slots_before}
				→ {cleared.slots_after}{/if}.
		</p>
	{:else if message}
		<p class="text-destructive mt-2 text-sm" role="alert">Could not clear them: {message}</p>
	{/if}
</section>
