# Runtime V5 Resource Provider Contract

## Principle

Runtime V5 is question-centered, not tool-centered.

The planner decides the strategy and sources. The capability router executes the selected sources through resource providers.

Legacy runtime fallback is not allowed when V5 is enabled.

## Provider interface

Each provider exposes one business source:

- `people`
- `approval`
- `calendar`
- `task`
- `mail`
- `docs`
- `wiki`
- `base`
- `company_profile`
- `knowledge`
- `workevent`
- `memory`
- `web`

Provider input is `ProviderRequest`:

```json
{
  "source": "people",
  "operation": "get_org_snapshot",
  "intent": {},
  "planner": {},
  "context": {},
  "execution_identity": "bot|user",
  "params": {}
}
```

Provider output is `ProviderResult`:

```json
{
  "source": "people",
  "status": "success|partial|denied|error|skipped",
  "result_type": "organization_snapshot",
  "count": 0,
  "items": [],
  "metadata": {},
  "answer": "",
  "error": ""
}
```

## Fail-fast rule

If the planner selects a source without a registered provider, Runtime V5 returns:

```json
{
  "status": "error",
  "error": "provider_not_registered",
  "metadata": {
    "v5_only": true,
    "legacy_fallback": false
  }
}
```

The system must not call legacy Agent Runtime as fallback.

## Action protocol

All write actions follow:

```text
intent recognition
task planning
permission check
confirmation
execute
result context
answer compose
```

High-risk actions must use user identity by default.

## Trace requirements

Each V5 execution should expose:

- intent
- plan
- permission
- execution status
- provider result summaries
- composed answer

Provider raw payloads should not be stored in gateway audit unless needed for a bounded debug field.

## TaskProvider atomic operations

## PeopleProvider atomic operations

PeopleProvider follows the `lark-contact` skill boundary for person lookup and uses native Contact OpenAPI tools for organization and department reads.

Registered V5 people operations:

- `search_person`
- `get_person`
- `department_children`
- `department_users`
- `scope_list`
- `get_org_snapshot`
- `list_department_members`

Currently installed underlying tools:

- `search_person -> feishu_contact_user_search`
- `get_person -> feishu_contact_user_get`
- `department_children -> feishu_contact_department_children`
- `department_users -> feishu_contact_department_users`
- `scope_list -> feishu_contact_scope_list`
- `get_org_snapshot -> feishu_contact_organization_snapshot`
- `list_department_members -> feishu_contact_organization_snapshot`

Planner currently routes:

- `people_lookup -> people.search_person`
- `department_members -> people.list_department_members`
- `organization_snapshot -> people.get_org_snapshot`

PeopleProvider is the shared resolver for future IM, Calendar, and Task actions that mention people by name.

TaskProvider follows the `lark-task` skill boundary. The provider owns task-domain atomic operations internally, while Planner only exposes selected strategies.

Registered V5 task operations:

- `list_my_tasks`
- `search_tasks`
- `create_task`
- `update_task`
- `complete_task`
- `reopen_task`
- `delete_task`
- `create_subtask`
- `comment_task`
- `assign_members`
- `update_followers`
- `update_reminders`
- `upload_attachment`
- `add_to_tasklist`
- `set_ancestor`
- `clear_ancestor`
- `tasklist_create`
- `tasklist_update`
- `tasklist_delete`
- `tasklist_update_members`
- `tasklist_set_members`
- `section_create`
- `section_update`
- `section_delete`

Planner currently routes:

- `task_query -> task.list_my_tasks`
- `task_search -> task.search_tasks`
- `task_create -> task.create_task`
- `task_complete -> task.complete_task`

Other task operations are provider-ready but should not be exposed through intent planning until the corresponding strategy and permission rules are added.

## CalendarProvider atomic operations

CalendarProvider follows the `lark-calendar` skill boundary. Calendar operations default to user identity because bot identity cannot read the user's calendar.

Registered V5 calendar operations:

- `list_events`
- `create_event`
- `update_event`
- `delete_event`
- `freebusy`
- `find_room`
- `rsvp`
- `suggest_time`
- `attendee_add`
- `attendee_remove`

Currently installed underlying tools:

- `list_events -> calendar_qa`
- `create_event -> feishu_calendar_create_event`

Planner currently routes:

- `calendar_query -> calendar.list_events`
- `calendar_create -> calendar.create_event`

Calendar create does not guess natural-language time. If `start` and `end` are missing, Runtime V5 asks for explicit time instead of calling the tool.

## MailProvider atomic operations

MailProvider follows the `lark-mail` skill boundary. Mail is a personal resource, so all mail operations default to user identity.

Registered V5 mail operations:

- `list_recent`
- `search_messages`
- `get_message`
- `create_draft`
- `send_draft`
- `send_message`
- `reply`
- `reply_all`
- `forward`
- `delete_message`
- `move_message`
- `mark_message`
- `create_rule`
- `update_rule`
- `delete_rule`

Currently installed underlying tools:

- `list_recent -> mail_qa`
- `search_messages -> mail_qa`
- `get_message -> feishu_mail_message_get`
- `create_draft -> feishu_mail_drafts_create`

Planner currently routes:

- `mail_query -> mail.list_recent`
- `mail_search -> mail.search_messages`
- `mail_draft_create -> mail.create_draft`

Mail draft creation requires explicit `to`, `subject`, and `body`. Runtime V5 creates a draft only; direct send remains registered as high-risk but is not exposed through intent planning yet.

## IMProvider atomic operations

IMProvider follows the `lark-im` skill boundary. Message sending is an action and must pass V5 confirmation before execution.

Registered V5 IM operations:

- `send_message`
- `search_chats`
- `list_messages`
- `create_chat`
- `auto_join_public_chats`
- `reply_message`
- `message_search`
- `chat_members_list`
- `pin_create`
- `reaction_create`
- `flag_create`

Currently installed underlying tools:

- `send_message -> feishu_im_send_message`
- `search_chats -> feishu_im_chat_search`
- `list_messages -> feishu_im_message_list`
- `create_chat -> feishu_im_create_chat`
- `auto_join_public_chats -> feishu_im_auto_join_public_chats`

Planner currently routes:

- `message_send -> im.send_message`
- `chat_search -> im.search_chats`
- `message_query -> im.list_messages`

Message send supports:

- send current/previous result to current chat or self
- send explicit text to a person if the person resolves uniquely
- send explicit text to a chat if the chat resolves uniquely

If person or chat resolution returns zero or multiple candidates, IMProvider must not guess. It returns candidates and does not send.
