<script lang="ts">
	// A source's collection timing (blt.timing): its product's schedule,
	// every so many hours, or daily at a time, and a quiet window; with its
	// next starts. Posts to ?/timing on the page it is in.
	import { enhance } from '$app/forms';
	import { Button } from '$lib/components/ui/button';
	import { COMMON_ZONES, modeOf, type SourceTiming } from '$lib/timing';
	import { until, when } from '$lib/format';

	let {
		timing,
		nextStarts,
		collecting,
		product
	}: { timing: SourceTiming; nextStarts: string[]; collecting: boolean; product: string } = $props();

	// Starts from what is saved; the page re-creates this form when it changes.
	// svelte-ignore state_referenced_locally
	let mode = $state(modeOf(timing));
	// svelte-ignore state_referenced_locally
	let quiet = $state(!!timing.quiet_from);
	const input = 'bg-background h-9 rounded-md border px-3 text-sm';
</script>

<section class="mt-10 max-w-2xl" aria-labelledby="timing">
	<h2 id="timing" class="text-lg font-semibold">Collection timing</h2>
	<p class="text-muted-foreground mt-1 text-sm">
		When its jobs are collected. On {product}'s own schedule, each source starts at an offset of its own, so they
		do not all start at once. A quiet window keeps every collection - storage and inventory too - out of its busy
		hours.
	</p>

	<p class="mt-3 text-sm">
		{#if !collecting}
			<span class="text-muted-foreground">Collection is off: nothing is started.</span>
		{:else if nextStarts.length}
			Next: {#each nextStarts as start, i (start)}{#if i}, {/if}<span title={when(start)}>{when(start)}</span>{#if i === 0}
					<span class="text-muted-foreground">({until(start)})</span>{/if}{/each}
		{:else}
			<span class="text-muted-foreground">No start in the coming week (or its schedule is paused).</span>
		{/if}
	</p>

	<form method="POST" action="?/timing" use:enhance={() => async ({ update }) => update({ reset: false })} class="mt-3 grid gap-4">
		<fieldset class="grid gap-2 text-sm">
			<legend class="mb-1 font-medium">Jobs are collected</legend>
			<label class="flex items-center gap-2">
				<input type="radio" name="mode" value="default" bind:group={mode} /> On {product}'s schedule
			</label>
			<label class="flex flex-wrap items-center gap-2">
				<input type="radio" name="mode" value="every" bind:group={mode} /> Every
				<input
					name="every_hours"
					type="number"
					min="0.25"
					max="168"
					step="0.25"
					aria-label="Hours between collections"
					value={timing.every_minutes ? timing.every_minutes / 60 : 4}
					disabled={mode !== 'every'}
					class="{input} w-20"
				/>
				hours, at the same times each day
			</label>
			<label class="flex flex-wrap items-center gap-2">
				<input type="radio" name="mode" value="daily" bind:group={mode} /> Daily at
				<input
					name="daily_at"
					type="time"
					aria-label="Daily at"
					value={timing.daily_at ?? '06:00'}
					disabled={mode !== 'daily'}
					class={input}
				/>
			</label>
		</fieldset>

		<div class="grid gap-2 text-sm">
			<label class="flex items-center gap-2 font-medium">
				<input type="checkbox" name="quiet" bind:checked={quiet} /> A quiet window
			</label>
			{#if quiet}
				<div class="flex flex-wrap items-center gap-2">
					No collection from
					<input name="quiet_from" type="time" aria-label="Quiet from" value={timing.quiet_from ?? '22:00'} class={input} />
					until
					<input name="quiet_until" type="time" aria-label="Quiet until" value={timing.quiet_until ?? '04:00'} class={input} />
				</div>
			{/if}
		</div>

		<div class="text-sm">
			<label class="font-medium" for="timezone">Time zone</label>
			<input id="timezone" name="timezone" list="zones" value={timing.timezone ?? 'UTC'} class="{input} ml-2 w-56" />
			<datalist id="zones">
				{#each COMMON_ZONES as zone (zone)}<option value={zone}></option>{/each}
			</datalist>
			<p class="text-muted-foreground mt-1 text-xs">For the daily time and the quiet window: the source's own, usually.</p>
		</div>

		<div><Button type="submit" variant="outline">Save timing</Button></div>
	</form>
</section>
