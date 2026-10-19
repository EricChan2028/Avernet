# Bring Your Own Engine: Integration Guide

[简体中文](README.zh-CN.md)

This guide is for teams that run their **own agent engine** and want their bots
to live on the Avernet platform. Users create, configure and chat with those
bots in the Avernet workbench like any other bot. Your engine runs the agent.

If you only want your bots to join multi-bot group chat, you can skip most of
this guide and read [Bot Integration Guide](../../../../docs/bot-integration.md)
instead (see [Step 6](#step-6-optional-join-group-collaboration-bcn)).

> **Status.** Steps 2 to 5 describe the contract the platform already uses with
> the first external engine (teclaw). Step 1 (registering an engine that you
> host yourself) is **not finalized yet**. The items marked **To be confirmed**
> must be agreed with the Avernet team before you go live. See
> [Open items](#open-items).

---

## Contents

- [How the integration works](#how-the-integration-works)
- [Checklist](#checklist)
- [Step 1: Register your engine](#step-1-register-your-engine)
- [Step 2: Accept bot configuration (`POST /api/v1/bot/apply`)](#step-2-accept-bot-configuration)
- [Step 3: Understand the configuration artifact](#step-3-understand-the-configuration-artifact)
- [Step 4: Implement the file endpoints](#step-4-implement-the-file-endpoints)
- [Step 5: Implement the runtime endpoints](#step-5-implement-the-runtime-endpoints)
- [Step 6 (optional): Join group collaboration (BCN)](#step-6-optional-join-group-collaboration-bcn)
- [Step 7: Verify your integration](#step-7-verify-your-integration)
- [Conventions](#conventions)
- [Open items](#open-items)
- [Reference](#reference)

---

## How the integration works

```text
 Avernet workbench (users)
          │
          ▼
 Avernet platform ──── 1. POST /api/v1/bot/apply  (whole bot config) ───▶ ┐
   (backend)      ──── 2. /api/v1/file/*          (edit files)       ───▶ │  Your engine
                  ──── 3. /api/sessions, /api/{engine}/ws (chat)     ───▶ │  (one HTTP service,
                                                                          │   one or many bots)
 Content stores (OSS) ◀──── your engine downloads skills / files ──────── ┘
```

You need to understand three ideas:

1. **The platform owns the configuration and your engine applies it.** Users
   manage a bot's skills, MCP servers, identity files, resources and CLI tools
   in Avernet. Whenever any of them changes, the platform builds the bot's
   **complete** configuration (the *artifact*) and sends it to your engine.
2. **The artifact is a snapshot, not a diff.** Each delivery describes the
   whole desired state. Your engine makes the bot match it exactly.
3. **Large content is passed by reference.** The artifact does not contain file
   bytes. It contains `{store, path}` references, and your engine downloads the
   files from the content store.

---

## Checklist

| # | What you do | Required |
|---|---|---|
| 1 | Register your engine (engine type, base URL, authentication) | Yes |
| 2 | Implement `POST /api/v1/bot/apply` and apply artifacts with full-replace semantics | Yes |
| 3 | Download artifact content from the content stores | Yes |
| 4 | Implement the file endpoints (`/api/v1/file/*`) | Yes, for file editing in the workbench |
| 5 | Implement the runtime endpoints (health, sessions, chat) | Yes, for chat in the workbench |
| 6 | Connect to BCN | Only for multi-bot collaboration |
| 7 | Pass the verification checklist | Yes |

---

## Step 1: Register your engine

To route requests to your engine, the platform needs:

| Item | Description | Status |
|---|---|---|
| **Engine type** | A short, lowercase identifier, such as `teclaw`. It appears as `engine_type` in every artifact. | Agree with the Avernet team |
| **Base URL** | The HTTPS address the platform calls. All paths in this guide are relative to it. | **To be confirmed** |
| **Authentication** | How the platform proves its identity to your engine, and how your engine identifies itself to the platform. | **To be confirmed** |
| **Bot addressing** | How one request names one bot when your service hosts many bots. | **To be confirmed** |

**Current mechanism (platform-hosted engines).** Today, external engine
containers are hosted by the platform and reached through the platform's proxy
gateway. Each container receives requests with an `x-proxypass-token` header,
and the bot is identified by the `bot_id` field in the request body (or the
`x-target-bot-id` header for skill packages). For an engine that **you host
yourself**, the base URL registration and the authentication scheme have not
been designed yet. Agree on them with the Avernet team before you start
Step 2, because they decide the headers on every request below.

---

## Step 2: Accept bot configuration

### `POST /api/v1/bot/apply`

The platform calls this endpoint every time a bot's configuration changes: when
the bot is created, when a user edits skills, MCP servers, identity files,
resources or channels, and when a bot is published.

**Request**

```http
POST /api/v1/bot/apply
Content-Type: application/json
```

```json
{
  "bot_id": "b_01KV5KRZG3FGC3H06RE5T9YRT0",
  "operation": "UPDATE",
  "bot_config": { "...": "the artifact, see Step 3" }
}
```

| Field | Description |
|---|---|
| `bot_id` | The bot this configuration is for. |
| `operation` | Currently always `"UPDATE"`. Treat any value you don't recognize as an error. |
| `bot_config` | The complete artifact (see [Step 3](#step-3-understand-the-configuration-artifact)). |

**Response**

- Return HTTP `2xx` **only after the whole artifact has been applied**.
- Return a non-`2xx` status if the apply failed. The platform reports the
  failure to the user and can redeliver later.
- The platform waits **30 seconds**. If applying can take longer (for example,
  downloading large skill packages), talk to the Avernet team about the timeout.

The response body is not read today. We recommend the standard envelope (see
[Conventions](#conventions)): `{"success": true}`.

**First configuration.** For platform-hosted engines, the first artifact is
handed to the container when it is created, before the bot starts. How a
self-hosted engine receives its first artifact is **To be confirmed**. Until
then, expect the first `POST /api/v1/bot/apply` to arrive for a bot you have not
seen before, and create the bot when it does.

### Rules for applying an artifact

These five rules are the core of the contract. Your engine must follow all of
them.

| # | Rule | What it means |
|---|---|---|
| R1 | **Replace the whole state** | After an apply, the bot has exactly what the artifact lists. If the artifact lists skills `{A, B}` and the bot had `{B, C}`, the result is `{A, B}`: `C` is removed. Don't merge. |
| R2 | **Never touch `MEMORY.md` or `IDENTITY.md`** | They are runtime state that your engine generates, not configuration. Never write them because of an artifact, and never delete them, even when `identity_files` doesn't list them. This is the only exception to R1. |
| R3 | **Be idempotent** | Applying the same artifact twice gives the same result as applying it once, with no leftover files or duplicate entries. Redelivery is normal, not an error. |
| R4 | **Apply before ready** | Don't report a bot as ready until its whole artifact has been applied. |
| R5 | **Ignore unknown fields** | The contract grows. If you see a field you don't know, ignore it. Don't reject the artifact. |

### What "replace" covers, per category

R1 replaces the state *inside a category's area*. The area is different for
each category:

| Category | Area that gets replaced |
|---|---|
| `skills` | The bot's active skill set. Skills not listed are deactivated. |
| `identity_files` | The bot's identity files, **except** `MEMORY.md` and `IDENTITY.md`. |
| `resources` | The resource paths **you delivered before**, plus the paths in this artifact. |
| `mcp.servers` | The bot's enabled MCP servers. |
| `cli_tools` | The CLI tools the platform delivered. Commands that ship with your image are not affected. |

> **Be careful with `resources`.** The area has two edges and both matter.
>
> - **Don't make it too narrow.** If `resources` goes from `[data/a.csv]` to
>   `[]`, you must delete `data/a.csv`. Track which paths you delivered as
>   resources, because the new artifact no longer mentions them.
> - **Don't make it too wide.** The workspace belongs to the bot and its user.
>   Files the user or the model created there are not resources. Never delete
>   them, even if they sit next to a resource path.

### Who owns each category: `ownership`

An artifact can carry an `ownership` map that tells you, per category, whether
the platform's list is authoritative:

```json
"ownership": {
  "mcp": "platform",
  "skills": "platform",
  "resources": "engine",
  "identity_files": "engine"
}
```

| Value | What your engine does |
|---|---|
| `"platform"` | The list in this artifact is the complete desired state. Apply R1. An empty list means remove everything in the area. |
| `"engine"` | Your engine owns this category. Ignore the list in the artifact and keep your current state. |
| Missing (the map, or a key in it) | Keep your engine's existing behavior. |

`mcp` is always `"platform"`. A user editing one file in the workbench produces
`"engine"` for the file categories, because the running bot is the source of
truth for its files. Applying a bot configuration manifest produces
`"platform"` for every category.

---

## Step 3: Understand the configuration artifact

The artifact's formal definition is the JSON Schema
[`artifact.schema.json`](../../src/agentclaw/community/kernel/bot_config/artifact.schema.json).
Validate against it in your tests. This section explains the fields.

### Example

```json
{
  "schema_version": 4,
  "version": 7,
  "engine_type": "teclaw",
  "stores": {
    "bot-data":   { "type": "oss", "bucket": "avernet-prod", "base": "teclaw/prod/bolt_data" },
    "skill-repo": { "type": "oss", "bucket": "avernet-skills", "base": "skills" }
  },
  "skills": [
    { "name": "weather", "scope": "shared", "store": "skill-repo", "path": "team/weather" }
  ],
  "identity_files": [
    { "name": "RULES.md", "store": "bot-data", "path": "staff_u1/bot7/identity/RULES.md" }
  ],
  "resources": [
    { "name": "data/faq.csv", "store": "bot-data", "path": "staff_u1/bot7/workspace/data/faq.csv" }
  ],
  "mcp": {
    "servers": [
      { "server_code": "search", "transport": "http",
        "endpoint": "https://mcp.example.com/search", "headers": { "x-api-key": "..." } },
      { "server_code": "local-db", "transport": "stdio",
        "command": "python3", "args": ["-m", "db_mcp"], "env": { "DB_PATH": "/data/db" } }
    ]
  },
  "cli_tools": [
    { "name": "mycli", "store": "bot-data", "path": "staff_u1/bot7/cli/mycli",
      "md5": "9f2c1b7d4e5a60318c2f0ab4d7e9c135", "version": "1.4.2" }
  ],
  "engine_overrides": { "channels": { "...": "..." } },
  "ownership": { "mcp": "platform", "skills": "engine", "resources": "engine", "identity_files": "engine" },
  "engine_ext": {
    "bot_id": "b_01KV5KRZG3FGC3H06RE5T9YRT0",
    "owner_id": "u1",
    "bot_name": "Support Bot",
    "stage": "draft"
  }
}
```

### Top-level fields

| Field | Required | Description |
|---|---|---|
| `schema_version` | Yes | Contract version, currently `4`. Some fields were added without a version bump (`cli_tools`, `ownership`), so **check whether a field is present** instead of reading the version. |
| `version` | Yes | Content version of this bot. An integer for a published bot, `null` for a draft bot that users are still editing. |
| `engine_type` | Yes | Your engine type (from Step 1). |
| `stores` | Yes | Where content lives, keyed by store ID. See [Downloading content](#downloading-content). |
| `skills` | Yes | Skills to activate. |
| `identity_files` | Yes | Workspace files written by the user or platform (for example `RULES.md`, `AGENTS.md`). |
| `resources` | Yes | Files the user uploaded to the bot's workspace. |
| `mcp.servers` | Yes | MCP servers the bot can use. |
| `cli_tools` | No | Command-line tools to put on the agent's `PATH`. |
| `engine_overrides` | Yes | Bot-specific settings (for example channels) to apply on top of your engine's own base configuration. Your base configuration file belongs to you; the platform never sends it. |
| `ownership` | No | Who owns each category. See [Step 2](#who-owns-each-category-ownership). |
| `engine_ext` | Yes | Free-form JSON that belongs to your engine. See below. |

### File references (`skills`, `identity_files`, `resources`)

Each entry is `{name, store, path}`. `store` is a key in `stores`; `path` is
relative to that store. **You decide where the file lands** inside your engine.

- `skills[]` also has `scope`: `"shared"` (from the skill catalog) or `"user"`
  (uploaded by the user for this bot). A skill's `path` is a **directory
  prefix**: download every object under it to get the whole skill package.
- `identity_files[]` and `resources[]` point to single files. `name` is the
  file's path inside the bot's identity area or workspace (for example
  `data/faq.csv`).

### MCP servers

Each server has a `server_code` and exactly one of two forms:

| Form | `transport` | Fields | What your engine does |
|---|---|---|---|
| Remote | `"http"` or `"sse"` | `endpoint` (required), `headers` | Connect to the URL with these headers. |
| Local | `"stdio"` | `command` (required), `args`, `env` | Start `command` as a child process and talk JSON-RPC over stdin/stdout. |

> **Secrets.** Remote entries can carry credentials inline, in `headers` or as
> an `?authorization=` query on the `endpoint`. Treat the artifact as secret:
> don't log it in full and don't store it unencrypted.

### CLI tools

Each entry is **one command and one executable file**:

| Field | Description |
|---|---|
| `name` | The command name. The agent runs it as `mycli`, not as an absolute path. Unique per bot. |
| `store`, `path` | The executable file. The platform has already downloaded, verified and unpacked it. `path` is always one file, never a directory or archive. |
| `md5` | MD5 of the file. If it matches the tool you already have, skip the download. If it differs, replace the file. |
| `version` | For display only. |

Your engine must: download the file, make it executable (object storage does
not keep file permissions), and put it on the agent process's `PATH` under
`name`. Remove tools that are no longer listed. `"cli_tools": []` means remove
every platform-delivered tool. Commands that come with your image are not
affected.

### `engine_ext`

`engine_ext` is yours. The platform stores it and sends it back without reading
it. The platform always adds four keys:

| Key | Value |
|---|---|
| `bot_id` | The platform's bot ID. |
| `owner_id` | The owner's user ID. |
| `bot_name` | The bot's display name. |
| `stage` | `draft` (being edited), `canary` (verification), `release` (published) or `eval`. |

### Downloading content

`stores` gives the location of each store, **never credentials**:

| Field | Description |
|---|---|
| `type` | `oss` or `nas`. |
| `bucket` | OSS bucket. |
| `base` | Key prefix inside the bucket. |
| `endpoint`, `region` | Optional. If missing, use your own configuration. |

The object key for a reference is `base` + `/` + `path`.

How your engine gets **read access** to these stores (credentials, network
access) is **To be confirmed** for self-hosted engines.

---

## Step 4: Implement the file endpoints

Users can browse and edit a bot's files in the workbench. The platform does
this through file endpoints on your engine, one request per file. The running
bot is the source of truth for its files, so these edits don't go through
`/api/v1/bot/apply`.

### Paths

Every path the platform sends starts with one of three namespaces:

| Namespace | Contents |
|---|---|
| `/workspace/...` | The bot's workspace (resources). User-uploaded skills live in `/workspace/skills-local/<skill-name>/`. |
| `/identity/...` | Identity files (`AGENTS.md`, `RULES.md`, `MEMORY.md`, `IDENTITY.md`, ...). |
| `/config/...` | Your engine's own configuration for this bot, if you expose it (for example `/config/<engine>.json`). |

Map these to real locations in your engine. Reject paths that escape the
namespace (for example with `..`).

### Endpoints

All requests are `POST`.

| Endpoint | Request | Success response |
|---|---|---|
| `/api/v1/file/upload` | Multipart: `file` (the content) and `target_path` (form field, authoritative) | `2xx`. Create parent directories. Overwrite if the file exists. |
| `/api/v1/file/read` | JSON `{"file_path": "/workspace/a.txt"}` | `2xx` with the **raw file bytes** as the body. `404` if the file doesn't exist. |
| `/api/v1/file/list` | JSON `{"dir_path": "/workspace", "recursive": false}` | `2xx` with `{"success": true, "data": {"files": [...]}}` (see below). |
| `/api/v1/file/remove` | JSON `{"target_path": "/workspace/old"}` | `2xx`. Removes a file **or a whole directory tree**. |
| `/api/v1/file/skill-package` | Multipart: `file` (a `.zip`), form field `skills_name`, header `x-target-bot-id` | See below. |

**List entries.** Each item in `data.files`:

```json
{ "name": "faq.csv", "path": "/workspace/data/faq.csv",
  "relative_path": "data/faq.csv", "size": 1024, "is_dir": false }
```

**Skill package upload.** Unpack the zip into
`/workspace/skills-local/<skills_name>/`, replacing what was there. Respond:

```json
{ "success": true, "skill_name": "weather", "action": "created",
  "sha256": "<hex sha256 of the uploaded zip>",
  "target_path": "/workspace/skills-local/weather" }
```

`action` is `created`, `replaced` or `unchanged`. The platform checks that
`skill_name` and `sha256` match what it sent. Errors: `400` for an invalid
package, `413` if it's too large.

---

## Step 5: Implement the runtime endpoints

These endpoints power chat and bot management in the workbench. The platform
forwards a user's request to your engine and returns your answer, so a feature
whose endpoint you don't implement is unavailable for your bots.

| Endpoint | Purpose |
|---|---|
| `GET /health` | Liveness. Return `{"status": "ok"}`. |
| `GET /api/engine/status` | Engine status for the bot. |
| `GET /api/engine/capabilities` | `{"success": true, "data": {"supported": [...]}}`: the features your engine supports. |
| `GET /api/models`, `GET /api/models/{model_id}` | Models the bot can use. |
| `GET /api/sessions`, `POST /api/sessions` | List and create chat sessions. |
| `GET`/`DELETE /api/sessions/{session_id}` | Read and delete one session. |
| `POST /api/sessions/{session_id}/update` | Update session metadata. |
| `GET`/`DELETE /api/sessions/{session_id}/messages` | Read and clear a session's messages. |
| `WebSocket /api/{engine_type}/ws` | Real-time chat (send messages, stream replies, abort). |

**Session IDs.** Session IDs can contain colons (for example
`agent:default:default:c_01M2F9:user:272471`). Some proxies reject colons in a
path, so the platform may send the ID **base64url-encoded without padding**.
Accept both forms on every session route.

The platform's reference implementation of this surface is the Avernet engine
adapter (`engine/adapter/`; see its
[Open-Source Guide](../../../../engine/adapter/docs/open-source-guide.md)). The
exact request and response shapes of each runtime endpoint, and the chat
WebSocket frame protocol, are **To be confirmed** with the Avernet team. Until a
written spec exists, use the engine adapter as the reference.

---

## Step 6 (optional): Join group collaboration (BCN)

To let your bots take part in multi-bot group chat, routing and collaboration,
connect each bot to the Bot Coordination Network (BCN) over WebSocket. This is
independent of Steps 2 to 5 and fully documented in the
[Bot Integration Guide](../../../../docs/bot-integration.md), with a runnable
quick start.

---

## Step 7: Verify your integration

Run these checks before going live. The Avernet team runs the same checks
during joint testing.

**Applying artifacts**

- [ ] After an apply, items the artifact no longer lists are gone (R1).
- [ ] `MEMORY.md` and `IDENTITY.md` still exist and are unchanged after every
      apply, including when `identity_files` doesn't list them (R2).
- [ ] When `resources` goes from `[data/a.csv]` to `[]`, `data/a.csv` is deleted.
- [ ] When all resources move to a new folder, nothing is left in the old folder.
- [ ] Files the user or the model created in the workspace are never deleted.
- [ ] Applying the same artifact twice changes nothing the second time (R3).
- [ ] The bot only reports ready after the whole artifact is applied (R4).
- [ ] An artifact with an unknown field is accepted (R5).
- [ ] With `ownership.resources = "engine"`, the `resources` list has no effect.
- [ ] With `ownership.identity_files = "platform"` and an empty list, every
      identity file except `MEMORY.md` and `IDENTITY.md` is removed.
- [ ] An artifact without `ownership` behaves exactly as before.

**CLI tools**

- [ ] The agent can run each tool by its `name`.
- [ ] A tool with an unchanged `md5` isn't downloaded again; a changed `md5`
      replaces the file.
- [ ] A tool removed from the list can no longer be run.
- [ ] `"cli_tools": []` removes every platform-delivered tool and no image
      commands.

**Files and runtime**

- [ ] Upload, read, list and remove work on `/workspace` and `/identity`.
- [ ] `read` returns `404` for a missing file.
- [ ] `remove` deletes a whole directory.
- [ ] Session routes accept both raw and base64url-encoded session IDs.

---

## Conventions

**JSON envelope.** JSON endpoints return:

```json
{ "success": true, "data": { "...": "..." }, "message": "optional text" }
```

Set `"success": false` with a `message` (and optionally an `error` code) when a
request fails. **The platform treats `"success": false` as a failure even when
the HTTP status is `200`.** The exceptions are `/api/v1/file/read`, which
returns raw bytes, and `/api/v1/file/skill-package`, which puts its fields at
the top level.

**Errors.** Use `400` for invalid input, `403` for a forbidden path, `404` for
something that doesn't exist and `5xx` for engine failures.

**Security.** The artifact and some file contents can contain user secrets. Use
HTTPS, don't log full payloads and don't keep secrets unencrypted.

---

## Open items

Agree on these with the Avernet team before going live:

| # | Item |
|---|---|
| O1 | How a self-hosted engine registers its base URL with the platform. |
| O2 | How the platform authenticates to your engine, and how your engine authenticates to the platform. |
| O3 | How one request addresses one bot when your service hosts many bots. |
| O4 | How a self-hosted engine receives a bot's first artifact (and learns that a bot was deleted). |
| O5 | How your engine gets read access to the content stores. |
| O6 | A written spec of the runtime endpoints' response shapes and the chat WebSocket protocol. |

---

## Reference

| Document | Contents |
|---|---|
| [`artifact.schema.json`](../../src/agentclaw/community/kernel/bot_config/artifact.schema.json) | The formal artifact definition. |
| [Engine convergence contract](../bot-config-manifest/engine-convergence-contract.zh-CN.md) (Chinese) | The full reasoning behind the apply rules in Step 2. |
| [CLI tools contract](../bot-config-manifest/teclaw-cli-contract.zh-CN.md) (Chinese) | The full `cli_tools` contract, with use cases. |
| [Bot Integration Guide](../../../../docs/bot-integration.md) | Connecting bots to BCN for group collaboration. |
| [Engine adapter Open-Source Guide](../../../../engine/adapter/docs/open-source-guide.md) | The reference implementation of the runtime surface. |
