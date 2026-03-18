<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import dayjs from 'dayjs';
	import relativeTime from 'dayjs/plugin/relativeTime';
	dayjs.extend(relativeTime);

	import { getRejectResponses } from '$lib/apis/evaluations';
	import { getUsersForAdmin } from '$lib/apis/chats';
	import Spinner from '$lib/components/common/Spinner.svelte';

	const i18n = getContext('i18n');

	type RejectItem = {
		chat_id: string;
		message_id: string;
		user_content: string;
		assistant_content: string;
		model_id: string;
		chat_title: string;
		user_id: string;
		updated_at: number;
	};

	let items: RejectItem[] = [];
	let loading = false;
	let customKeywords = '';
	let selectedUserId = '';
	let userList: { id: string; name: string }[] = [];
	let modalItem: RejectItem | null = null;

	const token = typeof localStorage !== 'undefined' ? localStorage.token : '';

	$: userNamesMap = userList.length ? Object.fromEntries(userList.map((u) => [u.id, u.name])) : {};

	function preview(text: string, maxLen: number = 120): string {
		if (!text) return '—';
		return text.length <= maxLen ? text : text.slice(0, maxLen) + '…';
	}

	async function load() {
		loading = true;
		try {
			items = await getRejectResponses(token, {
				user_id: selectedUserId || undefined,
				keywords: customKeywords.trim() || undefined
			});
		} catch (e) {
			toast.error($i18n.t('Failed to load reject responses'));
			items = [];
		}
		loading = false;
	}

	async function loadUsers() {
		try {
			userList = await getUsersForAdmin(token);
		} catch (_) {
			userList = [];
		}
	}

	function openModal(item: RejectItem) {
		modalItem = item;
	}
	function closeModal() {
		modalItem = null;
	}

	onMount(() => {
		load();
		loadUsers();
	});
</script>

<div class="flex flex-col gap-6">
	<div class="rounded-xl border border-gray-200 dark:border-gray-700 p-4">
		<h3 class="text-sm font-medium mb-3">{$i18n.t('Reject responses (policy refusals)')}</h3>
		<p class="text-xs text-gray-500 dark:text-gray-400 mb-3">
			{$i18n.t('Assistant messages that match policy-refusal keywords (e.g. 申し訳ありません, I can only answer). No LLM judge; content-based filter.')}
		</p>
		<div class="flex flex-wrap items-center gap-2 mb-3">
			<select
				class="rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-sm px-3 py-1.5"
				bind:value={selectedUserId}
				on:change={() => load()}
			>
				<option value="">{$i18n.t('All users')}</option>
				{#each userList as u (u.id)}
					<option value={u.id}>{u.name || u.id}</option>
				{/each}
			</select>
			<input
				type="text"
				class="rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-sm px-3 py-1.5 min-w-[200px]"
				placeholder="{$i18n.t('Extra keywords (comma-separated)')}"
				bind:value={customKeywords}
			/>
			<button
				class="px-3 py-1.5 rounded-lg bg-black dark:bg-white text-white dark:text-black text-sm font-medium hover:opacity-90 disabled:opacity-50"
				disabled={loading}
				on:click={() => load()}
			>
				{#if loading}
					<Spinner className="size-4 inline mr-1" />
				{/if}
				{$i18n.t('Refresh')}
			</button>
		</div>
		<div class="overflow-x-auto">
			{#if loading && items.length === 0}
				<div class="py-8 flex justify-center">
					<Spinner className="size-8" />
				</div>
			{:else if items.length === 0}
				<p class="text-sm text-gray-500 py-4">{$i18n.t('No reject responses found')}</p>
			{:else}
				<table class="w-full text-sm text-left text-gray-500 dark:text-gray-400 table-auto">
					<thead class="text-xs text-gray-800 uppercase bg-transparent dark:text-gray-200">
						<tr class="border-b border-gray-200 dark:border-gray-700">
							<th class="px-2 py-2">{$i18n.t('User')}</th>
							<th class="px-2 py-2">{$i18n.t('Chat')}</th>
							<th class="px-2 py-2 max-w-[180px]">{$i18n.t('User prompt')}</th>
							<th class="px-2 py-2 max-w-[220px]">{$i18n.t('Assistant response')}</th>
							<th class="px-2 py-2">{$i18n.t('Model')}</th>
							<th class="px-2 py-2">{$i18n.t('Updated')}</th>
							<th class="px-2 py-2 w-20"></th>
						</tr>
					</thead>
					<tbody>
						{#each items as item (item.chat_id + item.message_id)}
							<tr class="border-b border-gray-100 dark:border-gray-800">
								<td class="px-2 py-1.5">{(userNamesMap[item.user_id] ?? item.user_id) || '—'}</td>
								<td class="px-2 py-1.5 truncate max-w-[140px]" title={item.chat_title}>{item.chat_title || item.chat_id}</td>
								<td class="px-2 py-1.5 text-gray-700 dark:text-gray-300 max-w-[180px]">
									<span class="line-clamp-2" title={item.user_content}>{preview(item.user_content, 80)}</span>
								</td>
								<td class="px-2 py-1.5 text-gray-700 dark:text-gray-300 max-w-[220px]">
									<span class="line-clamp-2" title={item.assistant_content}>{preview(item.assistant_content, 100)}</span>
								</td>
								<td class="px-2 py-1.5 truncate max-w-[100px]">{item.model_id || '—'}</td>
								<td class="px-2 py-1.5">{item.updated_at ? dayjs(item.updated_at * 1000).fromNow() : '—'}</td>
								<td class="px-2 py-1.5">
									<button
										type="button"
										class="text-xs text-blue-600 dark:text-blue-400 hover:underline"
										on:click={() => openModal(item)}
									>
										{$i18n.t('Full')}
									</button>
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			{/if}
		</div>
	</div>
</div>

{#if modalItem}
	<div
		class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
		role="dialog"
		aria-modal="true"
		on:click|self={closeModal}
		on:keydown={(e) => e.key === 'Escape' && closeModal()}
	>
		<div
			class="bg-white dark:bg-gray-800 rounded-xl shadow-xl max-w-2xl w-full max-h-[80vh] overflow-hidden flex flex-col"
			on:click|stopPropagation
		>
			<div class="p-4 border-b border-gray-200 dark:border-gray-700 flex justify-between items-center">
				<h4 class="text-sm font-medium">{$i18n.t('Reject response detail')}</h4>
				<button type="button" class="text-gray-500 hover:text-gray-700" on:click={closeModal}>×</button>
			</div>
			<div class="p-4 overflow-y-auto flex-1 space-y-4">
				<div>
					<p class="text-xs text-gray-500 mb-1">{$i18n.t('User prompt')}</p>
					<div class="text-sm rounded-lg bg-gray-100 dark:bg-gray-700 p-3 whitespace-pre-wrap">{modalItem.user_content || '—'}</div>
				</div>
				<div>
					<p class="text-xs text-gray-500 mb-1">{$i18n.t('Assistant response')}</p>
					<div class="text-sm rounded-lg bg-gray-100 dark:bg-gray-700 p-3 whitespace-pre-wrap">{modalItem.assistant_content || '—'}</div>
				</div>
				<p class="text-xs text-gray-500">
					{userNamesMap[modalItem.user_id] ?? modalItem.user_id} · {modalItem.chat_title || modalItem.chat_id} · {modalItem.model_id || '—'}
				</p>
			</div>
		</div>
	</div>
{/if}
