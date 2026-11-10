<script lang="ts">
	import '../app.css';
	import { page } from '$app/state';
	import { ModeWatcher, toggleMode } from 'mode-watcher';
	import { ChevronRight, Moon, Sun, LockOpen, LogOut } from '@lucide/svelte';
	import { isActive } from '$lib/nav';

	let { children, data } = $props();

	const active = (href: string) => isActive(page.url.pathname, href);
	const link = (on: boolean) =>
		`rounded-md px-3 py-2 text-sm font-medium whitespace-nowrap transition-colors ${
			on
				? 'bg-accent text-accent-foreground'
				: 'text-muted-foreground hover:bg-accent hover:text-accent-foreground'
		}`;

	// A group is open while one of its pages is; folding it is the
	// user's until they go somewhere else in it or out of it.
	let folded = $state<Record<string, boolean>>({});
	$effect(() => {
		page.url.pathname;
		folded = {};
	});
</script>

<svelte:head>
	<link rel="icon" href="/favicon.svg" />
	<title>blt</title>
</svelte:head>

<ModeWatcher />

{#if !data.user}
	<main class="min-w-0 p-4 md:p-6">{@render children()}</main>
{:else}
<div class="flex min-h-svh flex-col md:flex-row">
	<aside class="bg-card flex shrink-0 flex-col border-b md:w-56 md:border-r md:border-b-0">
		<div class="flex items-center gap-1 px-4 py-4">
			<a href="/" class="mr-auto text-lg font-semibold tracking-tight">blt</a>
			<button
				type="button"
				onclick={toggleMode}
				class="text-muted-foreground hover:text-foreground rounded-md p-1.5"
				aria-label="Switch between light and dark"
			>
				<Sun class="size-4 dark:hidden" />
				<Moon class="hidden size-4 dark:block" />
			</button>
			{#if data.user.signedIn}
				<form method="POST" action="/logout" class="md:hidden">
					<button
						type="submit"
						class="text-muted-foreground hover:text-foreground rounded-md p-1.5"
						aria-label="Sign out"
					>
						<LogOut class="size-4" />
					</button>
				</form>
			{/if}
		</div>

		<nav class="flex gap-1 overflow-x-auto px-2 pb-2 md:flex-1 md:flex-col md:pb-0">
			{#each data.nav as item (item.href)}
				{#if item.children}
					{@const open = folded[item.href] === undefined ? active(item.href) : !folded[item.href]}
					<!-- Phone: one link; the group's pages are tabs on its pages. -->
					<a
						href={item.href}
						aria-current={active(item.href) ? 'page' : undefined}
						class="md:hidden {link(active(item.href))}">{item.short ?? item.label}</a
					>
					<div class="hidden md:block">
						<button
							type="button"
							aria-expanded={open}
							aria-controls="nav-{item.href.slice(1)}"
							onclick={() => (folded = { ...folded, [item.href]: open })}
							class="flex w-full items-center gap-1 {link(false)} {active(item.href)
								? 'text-foreground'
								: ''}"
						>
							{item.label}
							<ChevronRight
								class="ml-auto size-4 transition-transform {open ? 'rotate-90' : ''}"
								aria-hidden="true"
							/>
						</button>
						{#if open}
							<div id="nav-{item.href.slice(1)}" class="mt-1 ml-3 flex flex-col gap-1 border-l pl-2">
								{#each item.children as child (child.href)}
									<a
										href={child.href}
										aria-current={active(child.href) ? 'page' : undefined}
										class={link(active(child.href))}>{child.label}</a
									>
								{/each}
							</div>
						{/if}
					</div>
				{:else}
					<a
						href={item.href}
						aria-current={active(item.href) ? 'page' : undefined}
						class={link(active(item.href))}>{item.label}</a
					>
				{/if}
			{/each}
		</nav>

		<div class="hidden items-center justify-between gap-2 border-t p-3 md:flex">
			<div class="min-w-0">
				<p class="truncate text-sm font-medium">{data.user.name}</p>
				{#if !data.user.signedIn}
					<p class="text-muted-foreground flex items-center gap-1 text-xs">
						<LockOpen class="size-3" /> Sign-in is off
					</p>
				{/if}
			</div>
			{#if data.user.signedIn}
				<form method="POST" action="/logout">
					<button
						type="submit"
						class="text-muted-foreground hover:text-foreground rounded-md p-1.5"
						aria-label="Sign out"
						title="Sign out"
					>
						<LogOut class="size-4" />
					</button>
				</form>
			{/if}
		</div>
	</aside>

	<main class="min-w-0 flex-1 p-4 md:p-6">
		{@render children()}
	</main>
</div>
{/if}
