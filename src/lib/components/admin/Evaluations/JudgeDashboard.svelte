<script lang="ts">
	import { toast } from 'svelte-sonner';
	import dayjs from 'dayjs';
	import relativeTime from 'dayjs/plugin/relativeTime';
	dayjs.extend(relativeTime);

	import { onMount, getContext } from 'svelte';
	const i18n = getContext('i18n');

	import { getChatList, getChatById, getChatsForEvaluation, getUsersForAdmin } from '$lib/apis/chats';
	import { getOpenAIModels } from '$lib/apis/openai';
	import { runJudge, getFeedbackItems } from '$lib/apis/evaluations';

	import Badge from '$lib/components/common/Badge.svelte';
	import Pagination from '$lib/components/common/Pagination.svelte';
	import FeedbackModal from './FeedbackModal.svelte';
	import { WEBUI_API_BASE_URL } from '$lib/constants';
	import Spinner from '$lib/components/common/Spinner.svelte';

	const DEFAULT_JUDGE_PROMPT =
		'You are a judge. Given the user message and the assistant reply, output only a JSON object with exactly these keys: "rating" (integer 1 or -1: 1 = good, -1 = bad) and "reason" (short string). No other text.';

	// Chat selector: scope (my | user | all) and optional user for "By user"
	type ChatScope = 'my' | 'user' | 'all';
	let chatScope: ChatScope = 'my';
	let userList: { id: string; name: string }[] = [];
	let userListLoading = false;
	let selectedUserId = '';
	let chatList: { id: string; title?: string; updated_at?: number; user_id?: string | null }[] = [];
	let chatListLoading = false;
	let selectedChatId = '';
	let chatDetail: { id: string; chat?: { history?: { messages?: Record<string, { role: string; content?: string; model?: string; parentId?: string }> } } } | null = null;
	let chatDetailLoading = false;

	// Assistant messages to evaluate
	let assistantMessages: { id: string; content: string; model: string }[] = [];
	let selectedMessageIds: Set<string> = new Set();

	// Judge config
	let openAIModels: { data?: { id: string; name?: string }[] } = { data: [] };
	let judgeModelId = '';
	let judgeSystemPrompt = DEFAULT_JUDGE_PROMPT;

	// Run
	let running = false;
	let runProgress = '';

	// Log view
	let logItems: { id: string; user?: { name?: string; id?: string }; data?: { rating?: string; reason?: string; model_id?: string }; meta?: { source?: string; chat_id?: string }; updated_at: number }[] = [];
	let logTotal = 0;
	let logPage = 1;
	let logFilters: { source?: string; rating?: string; chat_id?: string } = { source: 'llm_judge' };
	let logOrderBy = 'updated_at';
	let logDirection: 'asc' | 'desc' = 'desc';
	let showFeedbackModal = false;
	let selectedFeedback: typeof logItems[0] | null = null;

	const token = typeof localStorage !== 'undefined' ? localStorage.token : '';

	function getAssistantMessages(chat: typeof chatDetail): { id: string; content: string; model: string }[] {
		if (!chat?.chat?.history?.messages) return [];
		const messages = chat.chat.history.messages;
		return Object.entries(messages)
			.filter(([, m]) => m?.role === 'assistant')
			.map(([id, m]) => ({
				id,
				content: (m?.content ?? '').slice(0, 120) + ((m?.content ?? '').length > 120 ? '…' : ''),
				model: m?.model ?? ''
			}));
	}

	async function loadChatList() {
		if (chatScope === 'user' && !selectedUserId) {
			chatList = [];
			return;
		}
		chatListLoading = true;
		try {
			if (chatScope === 'my') {
				const list = await getChatList(token, null, false, false);
				chatList = Array.isArray(list) ? list.map((c: { id: string; title?: string; updated_at?: number }) => ({ ...c, user_id: null })) : [];
			} else {
				const list = await getChatsForEvaluation(token, chatScope === 'user' ? selectedUserId : undefined);
				chatList = Array.isArray(list) ? list : [];
			}
		} catch (e) {
			const msg = e && typeof e === 'object' && 'message' in e ? (e as { message: string }).message : null;
			toast.error(msg ?? $i18n.t('Failed to load chats'));
			chatList = [];
		}
		chatListLoading = false;
	}

	async function loadUserList() {
		userListLoading = true;
		try {
			userList = await getUsersForAdmin(token);
		} catch (_) {
			userList = [];
		}
		userListLoading = false;
	}

	// User id -> name for display when showing "All" or "By user" chats
	$: userNamesMap = userList.length ? Object.fromEntries(userList.map((u) => [u.id, u.name])) : {};

	async function loadChat() {
		if (!selectedChatId) {
			chatDetail = null;
			assistantMessages = [];
			selectedMessageIds = new Set();
			return;
		}
		chatDetailLoading = true;
		chatDetail = null;
		assistantMessages = [];
		selectedMessageIds = new Set();
		try {
			chatDetail = await getChatById(token, selectedChatId);
			assistantMessages = getAssistantMessages(chatDetail);
			assistantMessages.forEach((m) => selectedMessageIds.add(m.id));
		} catch (e) {
			toast.error($i18n.t('Failed to load chat'));
		}
		chatDetailLoading = false;
	}

	async function loadModels() {
		try {
			const res = await getOpenAIModels(token);
			openAIModels = res && Array.isArray(res) ? { data: res } : res ?? { data: [] };
			const data = openAIModels?.data ?? [];
			if (data.length && !judgeModelId) judgeModelId = data[0]?.id ?? data[0]?.name ?? '';
		} catch (_) {
			openAIModels = { data: [] };
		}
	}

	function toggleMessage(id: string) {
		selectedMessageIds = new Set(selectedMessageIds);
		if (selectedMessageIds.has(id)) selectedMessageIds.delete(id);
		else selectedMessageIds.add(id);
	}

	function selectAllMessages() {
		if (selectedMessageIds.size === assistantMessages.length) {
			selectedMessageIds = new Set();
		} else {
			selectedMessageIds = new Set(assistantMessages.map((m) => m.id));
		}
	}

	async function runJudgeEval() {
		if (!selectedChatId || selectedMessageIds.size === 0) {
			toast.error($i18n.t('Select a chat and at least one message'));
			return;
		}
		running = true;
		runProgress = $i18n.t('Calling judge...');
		try {
			const res = await runJudge(token, {
				chat_id: selectedChatId,
				message_ids: [...selectedMessageIds],
				judge_model_id: judgeModelId || undefined,
				judge_system_prompt: judgeSystemPrompt || undefined
			});
			if (res) {
				runProgress = '';
				toast.success($i18n.t('Evaluated {{n}} message(s), {{f}} failed', { n: res.evaluated, f: res.failed }));
				loadLog();
			} else {
				toast.error($i18n.t('Judge request failed'));
			}
		} catch (e) {
			const msg = e && typeof e === 'object' && 'message' in e ? (e as { message: string }).message : null;
			toast.error(msg ?? e ?? $i18n.t('Judge request failed'));
		}
		running = false;
		runProgress = '';
	}

	async function loadLog() {
		try {
			const res = await getFeedbackItems(
				token,
				logOrderBy,
				logDirection,
				logPage,
				{ ...logFilters, chat_id: logFilters.chat_id || undefined, rating: logFilters.rating || undefined }
			);
			if (res) {
				logItems = res.items ?? [];
				logTotal = res.total ?? 0;
			}
		} catch (_) {
			logItems = [];
			logTotal = 0;
		}
	}

	$: if (selectedChatId && !chatDetail && !chatDetailLoading) {
		loadChat();
	}

	$: if (typeof logPage === 'number') {
		loadLog();
	}

	$: if (chatScope === 'user' && userList.length === 0 && !userListLoading) {
		loadUserList();
	}

	function onScopeOrUserChange() {
		selectedChatId = '';
		chatDetail = null;
		assistantMessages = [];
		selectedMessageIds = new Set();
		loadChatList();
	}

	onMount(() => {
		loadChatList();
		loadModels();
		loadUserList();
	});

	function openLogFeedback(fb: typeof logItems[0]) {
		selectedFeedback = fb;
		showFeedbackModal = true;
	}
	function closeFeedbackModal() {
		showFeedbackModal = false;
		selectedFeedback = null;
	}
</script>

<FeedbackModal bind:show={showFeedbackModal} selectedFeedback={selectedFeedback} onClose={closeFeedbackModal} />

<div class="flex flex-col gap-6">
	<!-- Section 1: Conversation selector -->
	<div class="rounded-xl border border-gray-200 dark:border-gray-700 p-4">
		<h3 class="text-sm font-medium mb-3">{$i18n.t('Conversation')}</h3>
		<!-- Scope: My chats / By user / All -->
		<div class="flex flex-wrap items-center gap-3 mb-3">
			<span class="text-xs text-gray-500 dark:text-gray-400">{$i18n.t('Show chats')}:</span>
			<select
				class="rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-sm px-3 py-1.5"
				bind:value={chatScope}
				on:change={onScopeOrUserChange}
			>
				<option value="my">{$i18n.t('My chats')}</option>
				<option value="user">{$i18n.t('By user')}</option>
				<option value="all">{$i18n.t('All')}</option>
			</select>
			{#if chatScope === 'user'}
				<select
					class="rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-sm px-3 py-1.5 min-w-[140px]"
					disabled={userListLoading}
					bind:value={selectedUserId}
					on:change={onScopeOrUserChange}
				>
					<option value="">{$i18n.t('Select user')}</option>
					{#each userList as u (u.id)}
						<option value={u.id}>{u.name || u.id}</option>
					{/each}
				</select>
			{/if}
		</div>
		<div class="flex flex-col sm:flex-row gap-3">
			<div class="flex-1 min-w-0">
				<select
					class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-sm px-3 py-2"
					disabled={chatListLoading || (chatScope === 'user' && !selectedUserId)}
					bind:value={selectedChatId}
					on:change={() => loadChat()}
				>
					<option value="">{$i18n.t('Select a chat')}</option>
					{#each chatList as chat (chat.id)}
						<option value={chat.id}>
							{chat.title || chat.id}{#if chat.user_id && userNamesMap[chat.user_id]} — {userNamesMap[chat.user_id]}{/if}
						</option>
					{/each}
				</select>
			</div>
		</div>
		{#if chatDetailLoading}
			<div class="mt-2 flex items-center gap-2 text-sm text-gray-500">
				<Spinner className="size-4" />
				{$i18n.t('Loading chat...')}
			</div>
		{:else if selectedChatId && chatDetail}
			<div class="mt-3">
				<div class="flex items-center justify-between mb-2">
					<span class="text-xs text-gray-500">{$i18n.t('Assistant messages to evaluate')}</span>
					<button
						type="button"
						class="text-xs text-blue-600 dark:text-blue-400 hover:underline"
						on:click={selectAllMessages}
					>
						{selectedMessageIds.size === assistantMessages.length ? $i18n.t('Deselect all') : $i18n.t('Select all')}
					</button>
				</div>
				{#if assistantMessages.length === 0}
					<p class="text-sm text-gray-500">{$i18n.t('No assistant messages in this chat')}</p>
				{:else}
					<ul class="max-h-48 overflow-y-auto space-y-1 border border-gray-200 dark:border-gray-700 rounded-lg p-2">
						{#each assistantMessages as msg (msg.id)}
							<li class="flex items-start gap-2 text-sm">
								<input
									type="checkbox"
									checked={selectedMessageIds.has(msg.id)}
									on:change={() => toggleMessage(msg.id)}
									class="mt-1 rounded"
								/>
								<span class="flex-1 min-w-0 truncate text-gray-700 dark:text-gray-300" title={msg.content}>{msg.content}</span>
								{#if msg.model}
									<span class="text-xs text-gray-500 shrink-0">{msg.model}</span>
								{/if}
							</li>
						{/each}
					</ul>
				{/if}
			</div>
		{/if}
	</div>

	<!-- Section 2: Judge config -->
	<div class="rounded-xl border border-gray-200 dark:border-gray-700 p-4">
		<h3 class="text-sm font-medium mb-3">{$i18n.t('Judge config')}</h3>
		<div class="space-y-3">
			<div>
				<label class="block text-xs text-gray-500 mb-1">{$i18n.t('Judge model')}</label>
				<select
					class="w-full max-w-md rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-sm px-3 py-2"
					bind:value={judgeModelId}
				>
					<option value="">{$i18n.t('Default (first available)')}</option>
					{#each (openAIModels?.data ?? []) as model}
						<option value={model.id ?? model.name}>{model.name ?? model.id}</option>
					{/each}
				</select>
			</div>
			<div>
				<label class="block text-xs text-gray-500 mb-1">{$i18n.t('System prompt (optional)')}</label>
				<textarea
					class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-sm px-3 py-2 min-h-[80px]"
					bind:value={judgeSystemPrompt}
					placeholder={DEFAULT_JUDGE_PROMPT}
				></textarea>
			</div>
		</div>
	</div>

	<!-- Section 3: Run -->
	<div class="flex items-center gap-3">
		<button
			class="px-4 py-2 rounded-lg bg-black dark:bg-white text-white dark:text-black text-sm font-medium hover:opacity-90 disabled:opacity-50"
			disabled={running || !selectedChatId || selectedMessageIds.size === 0}
			on:click={runJudgeEval}
		>
			{#if running}
				<Spinner className="size-4 inline mr-2" />
			{/if}
			{$i18n.t('Run LLM judge')}
		</button>
		{#if runProgress}
			<span class="text-sm text-gray-500">{runProgress}</span>
		{/if}
	</div>

	<!-- Section 4: Log view -->
	<div class="rounded-xl border border-gray-200 dark:border-gray-700 p-4">
		<h3 class="text-sm font-medium mb-3">{$i18n.t('Judge results (log)')}</h3>
		<div class="flex flex-wrap gap-2 mb-3">
			<select
				class="rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-xs px-2 py-1"
				bind:value={logFilters.source}
				on:change={() => loadLog()}
			>
				<option value="">{$i18n.t('All sources')}</option>
				<option value="llm_judge">{$i18n.t('LLM judge')}</option>
				<option value="manual">{$i18n.t('Manual')}</option>
			</select>
			<select
				class="rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-xs px-2 py-1"
				bind:value={logFilters.rating}
				on:change={() => loadLog()}
			>
				<option value="">{$i18n.t('All ratings')}</option>
				<option value="1">{$i18n.t('Good (1)')}</option>
				<option value="-1">{$i18n.t('Bad (-1)')}</option>
			</select>
			<button
				class="text-xs px-2 py-1 rounded border border-gray-300 dark:border-gray-600 hover:bg-gray-100 dark:hover:bg-gray-700"
				on:click={() => loadLog()}
			>
				{$i18n.t('Refresh')}
			</button>
		</div>
		<div class="overflow-x-auto">
			{#if logItems === null}
				<Spinner className="size-5 my-4" />
			{:else if (logItems ?? []).length === 0}
				<p class="text-sm text-gray-500 py-4">{$i18n.t('No feedback found')}</p>
			{:else}
				<table class="w-full text-sm text-left text-gray-500 dark:text-gray-400 table-auto">
					<thead class="text-xs text-gray-800 uppercase bg-transparent dark:text-gray-200">
						<tr class="border-b border-gray-200 dark:border-gray-700">
							<th class="px-2 py-2">{$i18n.t('User')}</th>
							<th class="px-2 py-2">{$i18n.t('Model')}</th>
							<th class="px-2 py-2">{$i18n.t('Rating')}</th>
							<th class="px-2 py-2 max-w-[200px]">{$i18n.t('Reason')}</th>
							<th class="px-2 py-2">{$i18n.t('Updated')}</th>
							<th class="px-2 py-2">{$i18n.t('Source')}</th>
						</tr>
					</thead>
					<tbody>
						{#each logItems as feedback (feedback.id)}
							<tr
								class="border-b border-gray-100 dark:border-gray-800 cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800/50"
								on:click={() => openLogFeedback(feedback)}
							>
								<td class="px-2 py-1.5">
									{#if feedback.user?.name}
										<span>{feedback.user.name}</span>
									{:else}
										-
									{/if}
								</td>
								<td class="px-2 py-1.5 truncate max-w-[120px]">{feedback.data?.model_id ?? '-'}</td>
								<td class="px-2 py-1.5">
									{#if feedback.data?.rating === '1'}
										<Badge type="info" content={$i18n.t('Good')} />
									{:else if feedback.data?.rating === '-1'}
										<Badge type="error" content={$i18n.t('Bad')} />
									{:else}
										-
									{/if}
								</td>
								<td class="px-2 py-1.5 truncate max-w-[200px]" title={feedback.data?.reason}>{feedback.data?.reason ?? '-'}</td>
								<td class="px-2 py-1.5">{dayjs((feedback.updated_at ?? 0) * 1000).fromNow()}</td>
								<td class="px-2 py-1.5 text-xs">{feedback.meta?.source ?? '-'}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			{/if}
		</div>
		{#if logTotal > 30}
			<Pagination bind:page={logPage} count={logTotal} perPage={30} />
		{/if}
	</div>
</div>
