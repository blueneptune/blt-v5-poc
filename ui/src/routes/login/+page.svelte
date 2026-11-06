<script lang="ts">
	import { Button } from '$lib/components/ui/button';

	let { data, form } = $props();
</script>

<svelte:head><title>Sign in · blt</title></svelte:head>

<div class="flex min-h-[80svh] items-center justify-center">
	<form method="POST" class="bg-card w-full max-w-sm rounded-xl border p-6 shadow-xs">
		<h1 class="text-xl font-semibold tracking-tight">Sign in to blt</h1>
		{#if data.problem}
			<p class="text-destructive mt-3 text-sm" role="alert">
				Sign-in is not set up: {data.problem}
			</p>
		{/if}
		<label class="mt-5 block text-sm font-medium" for="name">Name</label>
		<input
			id="name"
			name="name"
			autocomplete="username"
			required
			value={form?.name ?? ''}
			class="bg-background mt-1 h-9 w-full rounded-md border px-3 text-sm"
		/>
		<label class="mt-4 block text-sm font-medium" for="password">Password</label>
		<input
			id="password"
			name="password"
			type="password"
			autocomplete="current-password"
			required
			class="bg-background mt-1 h-9 w-full rounded-md border px-3 text-sm"
		/>
		{#if form?.message}
			<p class="text-destructive mt-3 text-sm" role="alert">{form.message}</p>
		{/if}
		<Button type="submit" class="mt-5 w-full">Sign in</Button>
	</form>
</div>
