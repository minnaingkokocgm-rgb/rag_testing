# Open WebUI — Database Tables Reference

This document lists the main database tables used by Open WebUI (SQLAlchemy/Peewee), their purpose, and key columns. The schema is shared between SQLite and PostgreSQL (with minor dialect differences for JSON).

---

## Table index

| Table | Purpose |
|-------|--------|
| [user](#user) | User accounts and profile |
| [api_key](#api_key) | API keys for programmatic access |
| [auth](#auth) | Email/password credentials (links to user) |
| [chat](#chat) | Chat sessions and conversation metadata |
| [chat_file](#chat_file) | Links between chats, messages, and uploaded files |
| [chat_message](#chat_message) | Individual messages in a chat (normalized) |
| [folder](#folder) | User folders for organizing chats |
| [tag](#tag) | User-defined tags for chats |
| [file](#file) | Uploaded file metadata and storage info |
| [message](#message) | Channel messages (Channels feature) |
| [message_reaction](#message_reaction) | Emoji reactions on channel messages |
| [channel](#channel) | Channels (group chat / space) |
| [channel_member](#channel_member) | Channel membership and settings |
| [channel_file](#channel_file) | Files attached to channels |
| [channel_webhook](#channel_webhook) | Webhooks for posting into channels |
| [group](#group) | User groups (for RBAC) |
| [group_member](#group_member) | Group membership |
| [access_grant](#access_grant) | Read/write grants for resources (knowledge, model, prompt, etc.) |
| [model](#model) | Custom/registered LLM models (display name, params, base model) |
| [knowledge](#knowledge) | RAG knowledge bases |
| [knowledge_file](#knowledge_file) | Files belonging to a knowledge base |
| [prompt](#prompt) | Saved prompts / slash commands |
| [prompt_history](#prompt_history) | Version history for prompts |
| [note](#note) | User notes (artifacts, journals) |
| [function](#function) | Custom Python functions (BYOF) |
| [tool](#tool) | Custom tools / MCP-style tools |
| [skill](#skill) | Skills (reusable prompt/instruction blocks) |
| [memory](#memory) | User memory / long-term context text |
| [feedback](#feedback) | User feedback (e.g. ratings, evaluations) |
| [oauth_session](#oauth_session) | OAuth tokens per user/provider |

---

## Where conversation and model response output are stored

When you have a conversation with a model, both **user messages** and **model (assistant) response output** are stored in two places:

### 1. Primary: `chat` table, `chat` column (JSON)

- The **main** storage is the **`chat`** table, in the **`chat`** column (a single JSON blob per chat).
- Structure:  
  `chat["history"]["messages"]` is an object keyed by **message id**. Each value is one message, e.g.:
  - `role`: `"user"` | `"assistant"` | `"system"`
  - `content`: text (for user) or model reply text (for assistant)
  - For assistant messages: may also have `model`, `output` (e.g. blocks), `usage`, etc.
- The UI loads a chat by reading this JSON (e.g. `GET /chats/{id}` → `Chats.get_chat_by_id` returns the row; the frontend uses `chat.chat.history.messages`).
- When the client has the full updated conversation (including the new assistant reply after streaming), it typically calls **`POST /chats/{id}`** with the merged `chat` payload. The backend then calls **`Chats.update_chat_by_id(id, updated_chat)`**, which **overwrites the `chat.chat` column** with that JSON. So **all model response output for that chat lives inside this JSON** under `history.messages`.

### 2. Secondary: `chat_message` table (normalized rows)

- The **`chat_message`** table stores **one row per message** (same conversation data in normalized form).
- For **assistant** messages, the model’s reply is stored in:
  - **`content`** (JSON): often the main text or blocks.
  - **`output`** (JSON): assistant output (e.g. block list for rich content).
- This table is written in two ways:
  - **Dual-write** when creating/importing a chat: initial messages are written to both `chat.chat` and `chat_message` via `ChatMessages.upsert_message`.
  - **Per-message update**: when the client calls **`POST /chats/{id}/messages/{message_id}`**, the backend uses **`Chats.upsert_message_to_chat_by_id_and_message_id`**, which updates both the `chat.chat` JSON and the corresponding **`chat_message`** row (including **`content`** and **`output`**).
- If the client **only** sends the full chat with **`POST /chats/{id}`** and never calls the per-message endpoint, then **only the `chat.chat` JSON is updated**; the `chat_message` row for that new assistant message may not exist or may be outdated. So for the “normal” flow (stream, then POST full chat), **the single source of truth for the latest model output is `chat.chat` → `history.messages`**.

**Summary**

| Location | What holds the model response |
|----------|------------------------------|
| **`chat.chat`** (JSON) | Full conversation; assistant messages in `chat["history"]["messages"][messageId]` with `role: "assistant"`, `content`, and often `output`. |
| **`chat_message`** (table) | One row per message; assistant reply in **`content`** and **`output`** columns. Filled when chats are created/imported or when the client updates a message by id. |

For analytics or querying by message (e.g. by model, token usage), the **`chat_message`** table is useful (it has `model_id`, `usage`, etc.). For “what the user sees” and the canonical conversation state, **`chat.chat`** is the primary store.

---

## Auth & users

### user

Stores user accounts and profile data.

| Column | Type | Description |
|--------|------|-------------|
| id | String (PK) | User UUID |
| email | String | Email address |
| username | String(50) | Optional username |
| role | String | e.g. `admin`, `user`, `pending` |
| name | String | Display name |
| profile_image_url | Text | Avatar URL or path |
| profile_banner_image_url | Text | Optional banner |
| bio, gender, date_of_birth, timezone | Text/Date | Profile fields |
| presence_state, status_emoji, status_message, status_expires_at | Various | Presence/status |
| info, settings | JSON | Extra info and UI settings |
| oauth | JSON | OAuth provider → `{ sub }` mapping |
| scim | JSON | SCIM provider → `{ external_id }` mapping |
| last_active_at, updated_at, created_at | BigInteger | Timestamps (epoch) |

---

### api_key

API keys for authenticating requests (tied to a user).

| Column | Type | Description |
|--------|------|-------------|
| id | Text (PK) | e.g. `key_{user_id}` |
| user_id | Text | Owner user id |
| key | Text | Hashed or raw key (unique) |
| data | JSON | Optional metadata |
| expires_at, last_used_at | BigInteger | Optional timestamps |
| created_at, updated_at | BigInteger | Timestamps |

---

### auth

Local email/password credentials. One row per user who can sign in with password; id matches `user.id`.

| Column | Type | Description |
|--------|------|-------------|
| id | String (PK) | Same as user.id |
| email | String | Login email |
| password | Text | Hashed password |
| active | Boolean | Whether login is allowed |

---

## Chats & conversations

### chat

One row per chat session. Holds title, owner, and a large JSON blob `chat` (e.g. history.messages). Shared chats use a separate row with `user_id = shared-{original_chat_id}` and the original row stores `share_id`.

| Column | Type | Description |
|--------|------|-------------|
| id | String (PK) | Chat UUID |
| user_id | String | Owner (or `shared-{chat_id}` for shared copy) |
| title | Text | Chat title |
| chat | JSON | Full conversation state (history, messages, etc.) |
| created_at, updated_at | BigInteger | Timestamps |
| share_id | Text | Id of the shared copy (if shared) |
| archived | Boolean | Archived flag |
| pinned | Boolean | Pinned in sidebar |
| meta | JSON | e.g. tags list |
| folder_id | Text | Parent folder id |

Indexes: `folder_id`, `user_id + pinned`, `user_id + archived`, `updated_at + user_id`, etc.

---

### chat_file

Junction table: which files are attached to which chat and optional message.

| Column | Type | Description |
|--------|------|-------------|
| id | Text (PK) | UUID |
| user_id | Text | Owner |
| chat_id | Text (FK → chat) | Chat |
| message_id | Text | Optional message id |
| file_id | Text (FK → file) | File |
| created_at, updated_at | BigInteger | Timestamps |

Unique on `(chat_id, file_id)`.

---

### chat_message

Normalized per-message rows for a chat (used alongside or instead of the `chat.chat` JSON for queries/analytics). One row per message in a conversation.

| Column | Type | Description |
|--------|------|-------------|
| id | Text (PK) | Message id (matches key in chat JSON) |
| chat_id | Text (FK → chat) | Chat |
| user_id | Text | Sender |
| role | Text | `user`, `assistant`, `system` |
| parent_id | Text | Thread parent message id |
| content | JSON | Message content (string or blocks) |
| output | JSON | Assistant output |
| model_id | Text | Model used (assistant messages) |
| files, sources, embeds | JSON | Attachments / RAG sources |
| done | Boolean | Completion status |
| status_history, error | JSON | Streaming/error info |
| usage | JSON | Token usage, etc. |
| created_at, updated_at | BigInteger | Timestamps |

Indexes: `chat_id`, `user_id`, `model_id`, `(chat_id, parent_id)`, etc.

---

### folder

User-created folders to organize chats (tree via `parent_id`).

| Column | Type | Description |
|--------|------|-------------|
| id | Text (PK) | Folder UUID |
| parent_id | Text | Parent folder id |
| user_id | Text | Owner |
| name | Text | Folder name |
| items | JSON | Optional ordering/children info |
| meta, data | JSON | Extra data |
| is_expanded | Boolean | UI state |
| created_at, updated_at | BigInteger | Timestamps |

---

### tag

User-defined tags (e.g. for labeling chats). Id is normalized name (e.g. lowercase, underscores).

| Column | Type | Description |
|--------|------|-------------|
| id | String | Normalized tag id (part of PK) |
| name | String | Display name |
| user_id | String | Owner (part of PK) |
| meta | JSON | Optional |

Primary key: `(id, user_id)`.

---

## Files

### file

Metadata for uploaded files (path, hash, user).

| Column | Type | Description |
|--------|------|-------------|
| id | String (PK) | File UUID |
| user_id | String | Owner |
| hash | Text | Optional content hash |
| filename | Text | Original filename |
| path | Text | Storage path |
| data, meta | JSON | Extra metadata (e.g. content_type, size) |
| created_at, updated_at | BigInteger | Timestamps |

---

## Channels (group chat / spaces)

### channel

A channel (space) for group messaging.

| Column | Type | Description |
|--------|------|-------------|
| id | Text (PK) | Channel UUID |
| user_id | Text | Creator/owner |
| type | Text | Channel type |
| name | Text | Channel name |
| description | Text | Optional |
| is_private | Boolean | Private flag |
| data, meta | JSON | Extra data |
| created_at, updated_at | BigInteger | Timestamps |
| updated_by | Text | Last updater |
| archived_at, archived_by | BigInteger/Text | Archive info |
| deleted_at, deleted_by | BigInteger/Text | Soft delete |

---

### channel_member

Membership of a user in a channel (role, mute, pin, last read).

| Column | Type | Description |
|--------|------|-------------|
| id | Text (PK) | Membership id |
| channel_id | Text | Channel |
| user_id | Text | User |
| role | Text | Member role |
| status | Text | e.g. active |
| is_active | Boolean | In channel or left |
| is_channel_muted, is_channel_pinned | Boolean | User preferences |
| data, meta | JSON | Extra |
| invited_at, invited_by | BigInteger/Text | Invite info |
| joined_at, left_at | BigInteger | Join/leave time |
| last_read_at | BigInteger | Read position |
| created_at, updated_at | BigInteger | Timestamps |

---

### channel_file

Files attached to a channel (and optional message).

| Column | Type | Description |
|--------|------|-------------|
| id | Text (PK) | UUID |
| user_id | Text | Uploader |
| channel_id | Text (FK → channel) | Channel |
| message_id | Text (FK → message) | Optional message |
| file_id | Text (FK → file) | File |
| created_at, updated_at | BigInteger | Timestamps |

Unique on `(channel_id, file_id)`.

---

### channel_webhook

Webhooks that can post into a channel (e.g. external integrations).

| Column | Type | Description |
|--------|------|-------------|
| id | Text (PK) | Webhook UUID |
| channel_id | Text | Channel |
| user_id | Text | Creator |
| name | Text | Webhook name |
| profile_image_url | Text | Optional avatar |
| token | Text | Secret for authentication |
| last_used_at | BigInteger | Last use time |
| created_at, updated_at | BigInteger | Timestamps |

---

### message

A message in a channel (not in a 1:1 chat). Supports threads via `reply_to_id` / `parent_id` and pins.

| Column | Type | Description |
|--------|------|-------------|
| id | Text (PK) | Message UUID |
| user_id | Text | Sender |
| channel_id | Text | Channel |
| reply_to_id | Text | Direct reply target |
| parent_id | Text | Thread root (for thread replies) |
| is_pinned | Boolean | Pinned in channel |
| pinned_at, pinned_by | BigInteger/Text | Pin info |
| content | Text | Body |
| data, meta | JSON | Extra (e.g. webhook id) |
| created_at, updated_at | BigInteger | Timestamps (e.g. time_ns) |

---

### message_reaction

Emoji (or other) reactions on a channel message.

| Column | Type | Description |
|--------|------|-------------|
| id | Text (PK) | Reaction id |
| user_id | Text | User who reacted |
| message_id | Text | Message |
| name | Text | Reaction name (e.g. emoji code) |
| created_at | BigInteger | Timestamp |

---

## Groups & access control

### group

User-defined groups for RBAC (e.g. sharing models, knowledge).

| Column | Type | Description |
|--------|------|-------------|
| id | Text (PK) | Group UUID |
| user_id | Text | Owner/admin |
| name | Text | Group name |
| description | Text | Optional |
| data, meta | JSON | Extra |
| permissions | JSON | Group-level permissions |
| created_at, updated_at | BigInteger | Timestamps |

---

### group_member

Links users to groups.

| Column | Type | Description |
|--------|------|-------------|
| id | Text (PK) | Membership id |
| group_id | Text (FK → group) | Group |
| user_id | Text | User |
| created_at, updated_at | BigInteger | Timestamps |

---

### access_grant

Fine-grained read/write access to a resource (replaces or complements legacy `access_control` JSON on resources). Used for knowledge, model, prompt, tool, note, channel, file.

| Column | Type | Description |
|--------|------|-------------|
| id | Text (PK) | Grant id |
| resource_type | Text | e.g. `knowledge`, `model`, `prompt`, `tool`, `note`, `channel`, `file` |
| resource_id | Text | Id of the resource |
| principal_type | Text | `user` or `group` |
| principal_id | Text | user_id, group_id, or `*` (public) |
| permission | Text | `read` or `write` |
| created_at | BigInteger | Timestamp |

Unique on `(resource_type, resource_id, principal_type, principal_id, permission)`.

---

## Models & knowledge

### model

Custom or registered LLM model definition (display name, params, link to base model). Used for model picker and proxy config.

| Column | Type | Description |
|--------|------|-------------|
| id | Text (PK) | Model id (API id) |
| user_id | Text | Owner (or system) |
| base_model_id | Text | Underlying model id (optional) |
| name | Text | Display name |
| params | JSON | Model params (temp, etc.) |
| meta | JSON | e.g. description, profile_image_url, capabilities |
| is_active | Boolean | Shown in UI / usable |
| created_at, updated_at | BigInteger | Timestamps |

---

### knowledge

RAG knowledge base (collection of documents).

| Column | Type | Description |
|--------|------|-------------|
| id | Text (PK) | Knowledge base UUID |
| user_id | Text | Owner |
| name | Text | Name |
| description | Text | Optional |
| meta | JSON | Extra |
| created_at, updated_at | BigInteger | Timestamps |

Access: via `access_grant` with `resource_type = 'knowledge'`.

---

### knowledge_file

Files (documents) in a knowledge base.

| Column | Type | Description |
|--------|------|-------------|
| id | Text (PK) | UUID |
| knowledge_id | Text (FK → knowledge) | Knowledge base |
| file_id | Text (FK → file) | File |
| user_id | Text | Who added it |
| created_at, updated_at | BigInteger | Timestamps |

Unique on `(knowledge_id, file_id)`.

---

## Prompts & notes

### prompt

Saved prompt / slash command (name, command, content).

| Column | Type | Description |
|--------|------|-------------|
| id | Text (PK) | Prompt UUID |
| command | String | Slash command (unique) |
| user_id | String | Owner |
| name | Text | Display name |
| content | Text | Prompt body |
| data, meta | JSON | Extra |
| tags | JSON | Optional tags |
| is_active | Boolean | Usable |
| version_id | Text | Points to active prompt_history id |
| created_at, updated_at | BigInteger | Timestamps |

---

### prompt_history

Version history for prompts (snapshots for rollback/diff).

| Column | Type | Description |
|--------|------|-------------|
| id | Text (PK) | History entry id |
| prompt_id | Text | Prompt |
| parent_id | Text | Previous version (optional) |
| snapshot | JSON | Full prompt snapshot |
| user_id | Text | Who created this version |
| commit_message | Text | Optional message |
| created_at | BigInteger | Timestamp |

---

### note

User notes (e.g. artifacts, journals). Access via `access_grant` with `resource_type = 'note'`.

| Column | Type | Description |
|--------|------|-------------|
| id | Text (PK) | Note UUID |
| user_id | Text | Owner |
| title | Text | Title |
| data, meta | JSON | Content and extra |
| created_at, updated_at | BigInteger | Timestamps |

---

## Functions, tools, skills

### function

Custom Python function (BYOF) — code and metadata for LLM function calling.

| Column | Type | Description |
|--------|------|-------------|
| id | String (PK) | Function id |
| user_id | String | Owner |
| name | Text | Name |
| type | Text | Function type |
| content | Text | Python code |
| meta | JSON | e.g. description, manifest |
| valves | JSON | Config/valves |
| is_active | Boolean | Enabled |
| is_global | Boolean | Available to all users |
| created_at, updated_at | BigInteger | Timestamps |

---

### tool

Custom tool definition (e.g. MCP-style tools). Access via `access_grant` with `resource_type = 'tool'`.

| Column | Type | Description |
|--------|------|-------------|
| id | String (PK) | Tool id |
| user_id | String | Owner |
| name | Text | Name |
| content | Text | Implementation/config |
| specs | JSON | Tool spec (e.g. parameters) |
| meta | JSON | e.g. description, manifest |
| valves | JSON | Config |
| created_at, updated_at | BigInteger | Timestamps |

---

### skill

Reusable skill block (name, description, content). Access via `access_grant` with `resource_type = 'skill'` (conceptually; resource_type may be extended).

| Column | Type | Description |
|--------|------|-------------|
| id | String (PK) | Skill id |
| user_id | String | Owner |
| name | Text | Name (unique) |
| description | Text | Optional |
| content | Text | Skill content |
| meta | JSON | e.g. tags |
| is_active | Boolean | Usable |
| created_at, updated_at | BigInteger | Timestamps |

---

## Memory & feedback

### memory

Long-term user memory (text stored for context/RAG).

| Column | Type | Description |
|--------|------|-------------|
| id | String (PK) | Memory id |
| user_id | String | Owner |
| content | Text | Memory text |
| created_at, updated_at | BigInteger | Timestamps |

---

### feedback

User feedback on model outputs (e.g. ratings, evaluations). Used for leaderboards and analytics.

| Column | Type | Description |
|--------|------|-------------|
| id | Text (PK) | Feedback id |
| user_id | Text | User |
| version | BigInteger | Schema/version |
| type | Text | Feedback type |
| data | JSON | Payload (e.g. rating, model_id) |
| meta | JSON | Extra |
| snapshot | JSON | Optional context snapshot |
| created_at, updated_at | BigInteger | Timestamps |

---

## OAuth

### oauth_session

Stored OAuth tokens per user and provider (encrypted). Used for SSO and token refresh.

| Column | Type | Description |
|--------|------|-------------|
| id | Text (PK) | Session id |
| user_id | Text | User |
| provider | Text | e.g. google, github |
| token | Text | Encrypted JSON (access_token, refresh_token, etc.) |
| expires_at | BigInteger | Token expiry |
| created_at, updated_at | BigInteger | Timestamps |

Indexes: `user_id`, `expires_at`, `(user_id, provider)`.

---

## Relationships (summary)

- **user** ↔ **auth**: 1:1 by id (password users).
- **user** ↔ **api_key**: 1:N.
- **user** ↔ **chat**: 1:N (user_id); shared chats reference `share_id` and a second row with `user_id = shared-{chat_id}`.
- **chat** ↔ **chat_message**: 1:N (chat_id); **chat** ↔ **chat_file** ↔ **file**: N:M via chat_file.
- **chat** ↔ **folder**: N:1 (folder_id); **chat.meta.tags** references tag ids.
- **channel** ↔ **channel_member** (user_id), **channel** ↔ **message** (channel_id), **channel** ↔ **channel_file** ↔ **file**, **channel** ↔ **channel_webhook**.
- **group** ↔ **group_member** (user_id).
- **access_grant**: links **user** or **group** (principal_id) to a resource (resource_type + resource_id) with read/write.
- **knowledge** ↔ **knowledge_file** ↔ **file**.
- **prompt** ↔ **prompt_history** (version_id, prompt_id).

For exact FK constraints and cascade behavior, see the SQLAlchemy model definitions in `backend/open_webui/models/`.
