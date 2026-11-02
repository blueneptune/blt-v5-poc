<script lang="ts">
	import { page } from '$app/state';
</script>

<div class="mx-auto max-w-xl py-16">
	<p class="text-muted-foreground text-sm">{page.status}</p>
	<h1 class="mt-1 text-xl font-semibold">{page.error?.message ?? 'Something went wrong'}</h1>
	<a href="/" class="text-muted-foreground mt-4 inline-block text-sm underline">Back to the overview</a>
</div>
