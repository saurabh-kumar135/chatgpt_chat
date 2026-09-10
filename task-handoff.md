# Task Handoff — HavenTo CFG-Constrained Tool Calling

## Goal
Implement grammar/CFG-constrained decoding for HavenTo so the LLM generates only one valid structured tool-call JSON object. The generated object is parsed by a backend adapter and routed into the existing HavenTo `execute_tool(tool_name, args, user_id)` path. Final natural-language response generation remains unrestricted.

Target model: `mistralai/Mistral-7B-Instruct-v0.3` in Kaggle. Current notebook inference stack is Hugging Face Transformers with `model.generate()` and `device_map="auto"`, not vLLM.

## Confirmed architecture — NO `[TOOL_CALLS]` marker

The constrained output is exactly one plain JSON object:

```json
{"name":"searchHomes","arguments":{"location":"Bijnor"}}
```

Do NOT use:
- `[TOOL_CALLS]` prefix
- enclosing array
- provider-native `tool_calls` response structure
- natural-language wrapper around the object
- multiple tool-call objects in one generation

Current production Groq path uses provider-native `assistant_msg["tool_calls"]`; the new Kaggle CFG path intentionally replaces that model-side protocol with plain JSON plus a backend adapter.

## Verified HavenTo backend
Repository: `saurabh-kumar135/havento-accomodation-booking-platform`
Important file: `backend/services/agentService.py`.

The public `TOOLS` list contains exactly 7 tools:
1. `searchHomes`
2. `getHomeDetails`
3. `getUserBookings`
4. `createBooking`
5. `cancelBooking`
6. `manageFavourites`
7. `predictDynamicPricing`

`process_chat()` currently sends `TOOLS` to Groq, reads `assistant_msg.get("tool_calls")`, parses function arguments with `json.loads`, calls `execute_tool(fn_name, fn_args, user_id)`, then sends tool results back for a follow-up natural-language response. fileciteturn21file0

## Public tool contract — verified

### `searchHomes`
Properties:
- `location`: string
- `maxPrice`: number
- `minRating`: number

Required: none.

Backend searches `location`, `houseName`, and `description`, then applies price/rating filters. fileciteturn21file0

### `getHomeDetails`
Properties:
- `homeId`: string
- `homeName`: string

Required in the public schema: none.

Backend tries `homeId` first, then `homeName`. fileciteturn23file0

**Grammar decision:** keep the public schema exactly as currently declared. Do not invent a new formal required field unless the backend/API contract is deliberately changed.

### `getUserBookings`
No arguments:

```json
{"name":"getUserBookings","arguments":{}}
```

Authentication remains backend-controlled. fileciteturn23file0

### `createBooking`
Public properties:
- `homeId`: string
- `homeName`: string
- `checkIn`: string
- `checkOut`: string
- `guests`: integer

Required in the current public schema: none.

Backend behavior:
- authentication required;
- resolves the property using `homeId` or `homeName`;
- internally also reads `location`, but `location` is NOT in the public `TOOLS` schema;
- `guests` defaults to 1;
- omitted dates create a flexible-date booking. fileciteturn23file0turn24file0

**CFG decision:** do NOT allow undocumented `location`. Do not make `homeId`/`homeName` formally required unless we deliberately change the public API contract. Backend still determines whether the operation can actually succeed.

### `cancelBooking`
Public properties:
- `bookingId`: string
- `homeName`: string
- `reason`: string enum
- `reasonDetails`: string

Required:
- `reason`
- `reasonDetails`

Exact reason enum:
- `Change of travel plans`
- `Found alternative accommodation`
- `Medical or personal emergency`
- `Accidental / duplicate booking`
- `Host requested cancellation`
- `Other solid reason`

Backend additionally enforces `reasonDetails` length >= 15 and the 24-hour cancellation policy. These are backend/business rules, not CFG rules. fileciteturn23file0turn24file0

Important backend-only behavior: `execute_tool()` also supports an internal `cancelAll` argument and can permanently delete an already-cancelled booking. Neither is in the public `TOOLS` schema.

**CFG decision:** reject undocumented `cancelAll` and do not expose permanent deletion as a grammar operation unless the public tool contract is deliberately expanded first.

### `manageFavourites`
Properties:
- `action`: string enum `list | add | remove`
- `homeId`: string
- `homeName`: string

Public required field: `action`.

Backend behavior:
- `list` can work without a target home;
- `add`/`remove` need a target home to resolve successfully. fileciteturn23file0turn25file0

**CFG decision:** preserve the public schema. Conditional target requirements may be represented in the grammar if desired, but they are ultimately backend execution requirements unless the public schema is changed to encode them formally.

### `predictDynamicPricing`
Properties:
- `location`: string — required
- `category`: string — optional, backend default `Trending`
- `guests`: integer — optional, backend default `2`
- `amenities`: array of strings — optional, backend default `[]`

This maps directly to the CFG. fileciteturn25file0

## Confirmed CFG target

Conceptually:

```text
TOOL_CALL → '{' NAME_FIELD ',' ARGUMENTS_FIELD '}'
NAME_FIELD → '"name":"' TOOL_NAME '"'
ARGUMENTS_FIELD → '"arguments":' ARG_OBJECT
```

`TOOL_NAME` is exactly one of the seven public tool names.

The grammar must be tokenizer-aware because Mistral tokenizer tokens may split tool names, JSON fragments, keys, strings, or punctuation across multiple tokens.

## CFG MUST enforce

- one top-level JSON object;
- exact top-level fields `name` and `arguments`;
- exact seven-tool whitelist;
- tokenizer-aware tool-name alternatives;
- only public argument keys for the selected tool;
- valid JSON syntax;
- correct JSON value types;
- exact enum values;
- public-schema required fields;
- legal strings, numbers, arrays, commas, braces and brackets;
- immediate completion after the complete object;
- no trailing natural language;
- no second tool-call object.

## CFG MUST reject

- `[TOOL_CALLS]` prefix;
- arrays around the tool call;
- unknown tools;
- unknown argument keys;
- malformed JSON;
- wrong types;
- invalid enum values;
- missing `cancelBooking.reason`;
- missing `cancelBooking.reasonDetails`;
- missing `manageFavourites.action`;
- missing `predictDynamicPricing.location`;
- undocumented `cancelAll`;
- undocumented `createBooking.location`;
- arbitrary natural language inside the structured object;
- trailing natural-language text;
- multiple tool-call objects.

## Important correction about conditional fields

Earlier discussion suggested making `homeId OR homeName` formally required for `getHomeDetails` and `createBooking`, and making a target required for `manageFavourites.add/remove`.

After the latest backend verification, the implementation decision is:

**Do not silently change the public `TOOLS` schema.**

The current public schemas explicitly declare `required: []` for `getHomeDetails` and `createBooking`, and only `action` for `manageFavourites`. Therefore the initial CFG should represent the verified public contract rather than inventing stricter required fields.

Backend execution can still reject an operation when no resolvable target exists.

If we later decide that these conditional requirements should be part of the formal public contract, first update the API/tool schema and then regenerate the CFG from that new contract.

## CFG MUST NOT enforce

- real MongoDB IDs;
- real home names/locations;
- authentication;
- database existence/state;
- booking existence;
- date availability;
- cancellation 15-character semantic rule;
- cancellation 24-hour rule;
- actual business validity;
- ML prediction correctness;
- user ownership beyond backend checks.

These remain backend/application responsibilities.

## Backend validation recommendation

The new Kaggle adapter should add a validation layer before calling `execute_tool()`:

```text
Mistral
  ↓
CFG-constrained JSON
  ↓
JSON parse
  ↓
public-schema validation
  ↓
tool whitelist validation
  ↓
execute_tool(tool_name, args, user_id)
```

This is defense in depth. CFG protects the generation path; backend validation protects the application if the adapter is called with malformed or manually crafted input.

## Backend issues identified for later hardening

These are NOT reasons to complicate the CFG:

1. `createBooking` internally reads undocumented `location`.
2. `cancelBooking` internally supports undocumented `cancelAll` and permanent deletion of already-cancelled bookings.
3. `guests` is cast to integer but has no explicit `>= 1` validation.
4. Date parsing falls back to raw strings if parsing fails; invalid date ordering can therefore reach booking creation.
5. `maxPrice`/`minRating` use truthiness checks, so zero is treated as absent.
6. Search values are inserted directly into MongoDB regex queries and should be escaped if regex syntax is not intentionally supported.

These should be handled separately from CFG work.

## Tool-selection vs grammar

The HavenTo system prompt determines when a tool should be selected. CFG determines whether the selected output has legal structure.

Examples:
- user asks for homes in Bijnor → model selects `searchHomes`;
- user asks to see bookings → `getUserBookings`;
- user explicitly books a home → `createBooking`;
- user asks about a specific property → `getHomeDetails`;
- user manages saved homes → `manageFavourites`;
- user asks for dynamic pricing → `predictDynamicPricing`.

The CFG does not need to understand the user's natural-language intent or MongoDB state.

## Current notebook problem

The existing `ToolCallLogitMaskingProcessor` is only a bracket/depth state machine. It previously forced `[TOOL_CALLS]`, decoded generated text each step, tracked nesting, and eventually allowed EOS. It is not a complete CFG for the seven-tool JSON contract.

Do not patch the old `[TOOL_CALLS]` logic. Replace it with tokenizer-aware constrained decoding for the plain JSON object.

## Exact next implementation

1. Keep Kaggle + Transformers + `model.generate()`.
2. Remove all `[TOOL_CALLS]` assumptions.
3. Define the grammar from the verified public `TOOLS` contract above.
4. Start generation at `{`.
5. Generate exactly one object with `name` and `arguments`.
6. Use tokenizer-aware continuation states for the seven tool names.
7. Switch to the selected tool's argument grammar after the tool name is complete.
8. Enforce public keys, JSON types, enums, and public required fields.
9. Prevent all trailing text after `}`.
10. Parse the generated JSON.
11. Validate it again in the backend adapter.
12. Call `execute_tool(tool_name, args, user_id)`.
13. Return tool results to an unrestricted response-generation step.
14. Test all seven tools and all invalid cases.

## Testing priorities

### Valid
- `searchHomes`: `{}`, location, maxPrice, minRating, combinations
- `getHomeDetails`: `{}`, homeId, homeName
- `getUserBookings`: `{}`
- `createBooking`: `{}`, full arguments, flexible/no-date booking
- `cancelBooking`: all six reason enums plus reasonDetails
- `manageFavourites`: list/add/remove
- `predictDynamicPricing`: location only and all optional fields

### Invalid
- `[TOOL_CALLS]` prefix
- array wrapper
- unknown tool
- unknown argument key
- wrong type
- invalid enum
- missing public required field
- undocumented `cancelAll`
- undocumented `createBooking.location`
- malformed JSON
- trailing natural language
- multiple objects/tool calls

## Live log

### 2026-09-10 — Notebook inspection
Confirmed current notebook uses Transformers, not vLLM, and existing constraint logic is bracket-based rather than a real grammar.

### 2026-09-10 — HavenTo architecture verification
Re-inspected `backend/services/agentService.py`, including public `TOOLS`, `execute_tool()`, and `process_chat()`. Confirmed production uses provider-native `tool_calls`, while the new Kaggle experiment should emit plain JSON and adapt it into `execute_tool()`. fileciteturn21file0turn23file0turn24file0turn25file0

### 2026-09-10 — Grammar clarification completed
Confirmed that the high-level grammar architecture is clear. The implementation will use one plain JSON tool-call object, one tool per generation, tokenizer-aware constrained decoding, public-schema validation in CFG, and backend/business validation after parsing.

### 2026-09-10 — Handoff updated
This handoff now records the final grammar decisions and the additional backend/schema discrepancies found during the second backend verification.

## Clarification workflow
If a genuinely unresolved implementation decision remains, ask the user **one question at a time in MCP format**, wait for the answer, then continue. Do not ask multiple clarification questions simultaneously.
