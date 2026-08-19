# Helm for Home Assistant

A HACS custom integration for the Helm household planning API. It pulls meals, exercise, events, chores and habits into Home
Assistant calendars, exposes the shared shopping list as a to-do list, and puts
today's counts on sensors — all configured through the UI, so no API keys end up
in `configuration.yaml` or `secrets.yaml`.

> Built against Helm API `v1`. The token, base URL and polling window live in the
> config entry, encrypted at rest with the rest of Home Assistant's storage.

---

## What you get

| Platform | Entities |
|---|---|
| **Calendar** | One per household member, plus Household, a merged Schedule, and one per type (meals, exercise, events, chores, habits) |
| **To-do** | `todo.helm_shopping_list` — the shared household list, with add, rename, tick and delete |
| **Sensor** | Next up, today's totals per type, chores/habits still outstanding, shopping list counts, credential expiry |
| **Card** | `custom:helm-shopping-card` — a shopping list card with quantities, categories and recipe links |

Every entity is attached to a single **Helm** service device, so they group
together in the UI.

### Calendars

Three ways to slice the same data:

| Calendar | Holds |
|---|---|
| `calendar.helm_<name>` | Everything that person is involved in — one per household member |
| `calendar.helm_household` | Items with nobody attached, like an unassigned family event |
| `calendar.helm_schedule` | Everything, merged |
| `calendar.helm_meals` *(and exercise, events, chores, habits)* | One planning type each |

**An occurrence appears on the calendar of every person attached to it.** A
dinner you both eat shows on both calendars; separate lunches show on one each.
Nothing is duplicated in Helm — it's the same occurrence, filtered per person.

Who counts as attached depends on the type: meals and exercises use `owner` plus
`participants`, events and chores use `assignees`, and habits use `owner`.

If you'd rather read one merged agenda than several columns, the **Show who
items are for** option adds names to titles on the Schedule and per-type
calendars — `Chicken wrap — Luke`, or `Luke — Chicken wrap` if you prefer to
scan down the left. Per-person calendars are left alone, since naming Luke on
Luke's own calendar is just noise.

The member list comes from `team.members` on `/me`, so calendars stay put
whether or not someone has anything scheduled that week. People are keyed by
type **and** ID — a `user` with ID 4 and a `family_member` with ID 4 are
different people.

Add a household member in Helm and they appear after the integration reloads
(`/me` is re-read on every load, so restarting Home Assistant is enough).

### Sensors

| Entity | State | Notable attributes |
|---|---|---|
| `sensor.helm_next_up` | Title of the next thing starting | `occurrence` (the full record), `timezone` |
| `sensor.helm_today` | Count of everything scheduled today | `items` |
| `sensor.helm_meals_today` | Count | `items` |
| `sensor.helm_exercise_today` | Count | `items` |
| `sensor.helm_events_today` | Count | `items` |
| `sensor.helm_chores_today` | Count | `items` |
| `sensor.helm_habits_today` | Count | `items` |
| `sensor.helm_chores_outstanding` | Count not yet ticked today | `items` |
| `sensor.helm_habits_outstanding` | Count not yet ticked today | `items` |
| `sensor.helm_shopping_list_outstanding` | Count not yet bought | `items` (full records, including `qty`, `type` and `meal`) |
| `sensor.helm_shopping_list_total` | Every item on the list | `items` — disabled by default |
| `sensor.helm_credential_expires` | When the API token lapses | `credential`, `user`, `team` — only created for tokens that expire |

---

## Installation

### HACS (recommended)

1. HACS → **⋮** → **Custom repositories**
2. Repository `https://github.com/lukeketchen/helm-homeassistant`, type **Integration**
3. Find **Helm** in HACS, install it, and restart Home Assistant

### Manual

Copy `custom_components/helm` into your Home Assistant `config/custom_components/`
directory and restart.

---

## Setup

1. In Helm, go to **Settings → Account → API credentials** and issue a token.
   Grant the abilities you want:

   | Ability | Unlocks |
   |---|---|
   | `planning:read` | The six calendars and the planning sensors |
   | `shopping:read` | The to-do list, read-only |
   | `shopping:write` | Adding, renaming, ticking and deleting to-do items |

   The token is shown **once**. Copy it before closing the dialog.

2. In Home Assistant: **Settings → Devices & services → Add integration → Helm**.
3. Paste the token, and your Helm server's API root — the versioned base, ending
   in `/api/v1`.

During setup the integration calls `GET /me` to find out who the token belongs
to and what it may do, and only creates the platforms it can feed. A token with
just `shopping:read` gets a read-only to-do list and no calendars; adding
`planning:read` later means reissuing the token and reconnecting.

Because `/me` identifies the user and team, two people in the same household can
each add their own credential against the same server. Entries are named after
the household and the credential label, e.g. *Ketchen (Home Assistant)*.

Servers that predate `/me` still work: the integration falls back to probing each
ability with a harmless call, which yields abilities but no identity or
timezone.

If the token is revoked or expires, Home Assistant raises a **reauthentication**
prompt rather than silently going stale — paste a fresh token and everything
resumes.

Tokens issued with an expiry get ahead of that: `sensor.helm_credential_expires`
carries the date, and a **repair notification** appears 14 days out so you can
reissue before anything breaks. The expiry is fixed when the credential is
created, so this check costs no API requests.

> **Child accounts cannot authenticate**, so a token issued to one will be
> rejected at setup. Extended-family tokens work, but see only records flagged
> visible to extended family.

### Options

**Settings → Devices & services → Helm → Configure**

| Option | Default | Notes |
|---|---|---|
| Days ahead | 7 | How far forward calendars and sensors keep data cached |
| Days of history | 0 | Useful if you want to look back at what was done |
| Update interval | 5 min | Each poll is 6 requests against a 60/minute limit |
| Show quantity in item names | on | Renders `Milk ×2`, and reads `Milk x2` back into `qty` when you type it |

Changing an option reloads the entry.

---

## The shopping list

### The card (recommended)

The built-in to-do card can't show quantities — Home Assistant's `TodoItem` has
exactly six fields (`summary`, `uid`, `status`, `due`, `description`,
`completed`) and none of them is a quantity. So this integration ships its own
card:

```yaml
type: custom:helm-shopping-card
entity: todo.helm_shopping_list
title: Shopping
group_by_category: true
show_completed: true
```

| Option | Default | Notes |
|---|---|---|
| `entity` | *required* | Your Helm to-do entity |
| `title` | `Shopping list` | Card heading |
| `group_by_category` | `false` | Group outstanding items under their Helm category |
| `show_completed` | `true` | Show a collapsible completed section |

It gives you quantity steppers, category grouping, recipe links and delete,
writing through the `helm.*` services. Quantity changes are debounced by 600 ms,
so holding down **+** sends one request, not ten.

**No resource setup needed.** The integration serves the card itself and
registers it with the frontend, so it appears in the card picker as *Helm
Shopping List* after a restart — no manual Lovelace resource, and no second HACS
repository. It has a visual editor, and no external dependencies (it never
fetches from a CDN).

### The to-do entity

Still worth keeping, because it's what Assist voice control, the standard to-do
card and every `todo.*` service speak. The mapping:

| Helm | Home Assistant |
|---|---|
| `name` | Item summary |
| `completed` | Needs action / completed |
| `qty` | Appended as `×2` when greater than 1 (optional) |
| `type`, `meal`, `url`, `visible_to_extended` | The card, the `items` attribute, and the services below |

Ticking an item sends **only** `completed`, which is the API's completion-toggle
path — so accounts that may tick items but not edit them still work.

Adds are sent with an `Idempotency-Key`, so a retried request cannot produce a
duplicate item.

The `items` attributes are excluded from the recorder, so a long shopping list
doesn't bloat your database.

### Known limitations

- **No drag-to-reorder.** `sort_order` is writable, but reordering a long list
  would mean one `PATCH` per shifted item and would blow through the rate limit.
  Use the `helm.update_shopping_item` service to set `sort_order` directly.
- **No due dates.** Helm shopping items do not have one.

---

## Services

Every service takes either a `config_entry_id` **or** an `entity_id` from the
same connection — the card uses the latter, and it's usually the easier one to
write by hand too.

### `helm.add_shopping_item`

Reaches the fields the built-in to-do card cannot.

```yaml
action: helm.add_shopping_item
data:
  config_entry_id: 01J0000000000000000000000
  name: Oat milk
  qty: 2
  url: https://example.com/oat-milk
  idempotency_key: "weekly-oat-milk-2026-W34"
```

### `helm.update_shopping_item` / `helm.delete_shopping_item`

Take an `item_id`, which you can read off the `items` attribute of
`sensor.helm_shopping_list_outstanding`.

```yaml
action: helm.update_shopping_item
data:
  entity_id: todo.helm_shopping_list
  item_id: 91
  completed: true
```

### `helm.get_planning`

A response service that returns occurrences for any range. Ranges longer than the
API's 31-day limit are chunked automatically.

```yaml
action: helm.get_planning
data:
  config_entry_id: 01J0000000000000000000000
  from: "2026-08-18"
  to: "2026-09-30"
  types: [chore, habit]
response_variable: planning
```

```yaml
{% for item in planning.occurrences %}
  {{ item.date }} — {{ item.title }}
{% endfor %}
```

---

## Automation examples

Announce the first thing on tomorrow's calendar at bedtime:

```yaml
triggers:
  - trigger: time
    at: "20:30:00"
actions:
  - action: helm.get_planning
    data:
      config_entry_id: !input helm_entry
      from: "{{ (now() + timedelta(days=1)).date() }}"
    response_variable: tomorrow
  - action: tts.speak
    target:
      entity_id: tts.piper
    data:
      message: >
        Tomorrow starts with {{ tomorrow.occurrences[0].title | default('nothing') }}.
```

Nag about unticked chores after dinner:

```yaml
triggers:
  - trigger: numeric_state
    entity_id: sensor.helm_chores_outstanding
    above: 0
conditions:
  - condition: time
    after: "19:00:00"
actions:
  - action: notify.family
    data:
      message: >
        Still to do: {{ state_attr('sensor.helm_chores_outstanding', 'items')
                          | map(attribute='title') | join(', ') }}
```

Add to the shopping list by voice, via the standard to-do intents:

```yaml
# "Add milk to the shopping list" works out of the box once
# todo.helm_shopping_list is exposed to Assist.
```

---

## How it talks to the API

- **One `/me` call per load**, for identity, the member roster, abilities and
  the household timezone. Nothing after that, so a roster change costs one
  request at the next reload rather than ongoing polling.
- **Two coordinators.** One polls the five typed planning endpoints in parallel
  and merges them locally; the other polls the shopping list. Six requests per
  cycle, ~1.2/minute at the default interval, against a 60/minute budget.
- **The typed endpoints, not `/schedule`.** They cost the same number of requests
  and carry more: `meal_time` and recipe `url` for meals, `duration_minutes` and
  `category` for exercise, `completed` for chores and habits. The merged
  `calendar.helm_schedule` is assembled from them in Home Assistant.
- **Dates resolve in the household's timezone.** It comes from `/me` before the
  first poll (falling back to `meta.timezone`), so "today" is computed in the
  household's zone rather than Home Assistant's from the very first request.
- **Ranges are chunked.** The calendar panel asks for about 42 days when you open
  a month view; that becomes two calls, transparently.
- **Cache first.** A calendar range already inside the cached window is served
  without a request. Only ranges outside it hit the network.
- **Identical fetches are shared.** Opening a calendar view asks every calendar
  entity for the same window at once, and the per-person calendars all need the
  same underlying data. Those requests are collapsed into one per planning type
  and briefly reused, so a month view across ten calendars costs ten requests
  rather than sixty. Raising **Days ahead** to 30 makes most month views free
  entirely, at no extra polling cost.
- **Recurring occurrences.** `id` repeats across days, so calendar UIDs are
  `{type}-{id}-{date}`.
- **Rate limits and errors** surface as `UpdateFailed` (entities go unavailable
  and retry) or, for auth failures, as a reauth prompt.

Diagnostics are available from the device page and redact the token along with
item names, titles and notes.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| "The token was rejected" at setup | Unknown, revoked, expired, or issued to a child account |
| Card missing from the picker | Restart Home Assistant, then hard-refresh the browser |
| No calendars appear | The token lacks `planning:read` — reissue it |
| To-do list is read-only | The token lacks `shopping:write` |
| Entities go unavailable periodically | Rate limiting; raise the update interval |
| Wrong day boundaries | Check the household timezone in Helm — the API resolves dates in the token owner's zone |

Turn on debug logging:

```yaml
logger:
  default: warning
  logs:
    custom_components.helm: debug
```

---

## Development

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-test.txt
```

```bash
.venv/bin/python -m pytest && .venv/bin/ruff check . && .venv/bin/ruff format --check .
```

The suite runs against a mocked Helm API and covers `/me` plus the legacy probe
fallback, the config and reauth flows, entity setup for each ability
combination, date-range chunking, the calendar conversions (timed, all-day and
duration-only), the shopping list write paths, credential-expiry warnings, card
registration, and every service.

CI runs [hassfest](https://developers.home-assistant.io/docs/creating_integration_manifest),
the HACS validation action, ruff and the tests on every push.

## Licence

MIT — see [LICENSE](LICENSE).
