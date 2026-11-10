<script lang="ts">
	import { ADD_SOURCE_CHOICES } from '$lib/admin';
</script>

<svelte:head><title>Add a source · blt</title></svelte:head>

<a href="/admin/sources" class="text-muted-foreground text-sm hover:underline">← Sources</a>
<h1 class="mt-2 text-2xl font-semibold tracking-tight">Add a source</h1>
<p class="text-muted-foreground mt-1 text-sm">Which product is it?</p>

<ul class="mt-6 grid max-w-3xl gap-3 sm:grid-cols-3">
	{#each ADD_SOURCE_CHOICES as choice (choice.type)}
		<li>
			<a
				href="/admin/sources/{choice.type}/new"
				class="bg-card hover:border-foreground/30 flex h-full flex-col rounded-xl border p-4 transition-colors"
			>
				<span class="font-medium">{choice.label}</span>
				<span class="text-muted-foreground mt-1 text-sm">{choice.description}</span>
			</a>
		</li>
	{/each}
</ul>
