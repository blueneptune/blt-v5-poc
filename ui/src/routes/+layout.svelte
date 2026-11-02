<script lang="ts">
	import '../app.css';
	import { page } from '$app/state';
	import { ModeWatcher, toggleMode } from 'mode-watcher';
	import { Moon, Sun, LockOpen } from '@lucide/svelte';

	let { children, data } = $props();

	function active(href: string): boolean {
		return href === '/' ? page.url.pathname === '/' : page.url.pathname.startsWith(href);
	}
</script>

<svelte:head>
	<link rel="icon" href="/favicon.svg" />
	<title>blt</title>
</svelte:head>

<ModeWatcher />

<div class="flex min-h-svh flex-col md:flex-row">
	<aside class="bg-muted/60 flex shrink-0 flex-col border-b md:w-56 md:border-r md:border-b-0">
		<div class="flex items-center justify-between px-4 py-4">
			<a href="/" class="text-lg font-semibold tracking-tight">blt</a>
			<button
				type="button"
				onclick={toggleMode}
				class="text-muted-foreground hover:text-foreground rounded-md p-1.5"
				aria-label="Switch between light and dark"
			>
				<Sun class="size-4 dark:hidden" />
				<Moon class="hidden size-4 dark:block" />
			</button>
		</div>

		<nav class="flex gap-1 overflow-x-auto px-2 pb-2 md:flex-1 md:flex-col md:pb-0">
			{#each data.nav as item (item.href)}
				<a
					href={item.href}
					aria-current={active(item.href) ? 'page' : undefined}
					class="rounded-md px-3 py-2 text-sm font-medium whitespace-nowrap transition-colors
						{active(item.href)
						? 'bg-accent text-accent-foreground'
						: 'text-muted-foreground hover:bg-accent hover:text-accent-foreground'}"
				>
					{item.label}
				</a>
			{/each}
		</nav>

		<div class="hidden border-t p-3 md:block">
			<p class="truncate text-sm font-medium">{data.user.name}</p>
			{#if !data.user.signedIn}
				<p class="text-muted-foreground flex items-center gap-1 text-xs">
					<LockOpen class="size-3" /> Sign-in not enabled yet
				</p>
			{/if}
		</div>
	</aside>

	<main class="min-w-0 flex-1 p-4 md:p-6">
		{@render children()}
	</main>
</div>
