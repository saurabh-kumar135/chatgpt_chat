# Task Handoff — HavenTo CFG-Constrained Tool Calling

## Goal
Implement grammar/CFG-constrained decoding for HavenTo so the LLM generates **only a valid structured tool-call object**. The generated object is then sent to the HavenTo backend, which validates/parses it, executes the selected tool against MongoDB/ML services, and returns the tool result. Final natural-language response generation remains unrestricted.

Target model: Mistral 7B Instruct v0.3 in Kaggle. Current notebook inference stack is Hugging Face Transformers with `model.generate()` and `device_map="auto"`, not vLLM.

## Critical architecture correction — NO `[TOOL_CALLS]` marker

The previous handoff incorrectly defined the constrained output as:
`[TOOL_CALLS][{"name":"<valid tool>","arguments":{...}}]`

**This is removed. Do NOT build the HavenTo CFG around `[TOOL_CALLS]`.**

After re-checking the HavenTo backend, the actual application architecture is:

```text
User message
    ↓
LLM
    ↓
structured tool-call object
    ↓
HavenTo backend
    ↓
parse / validate tool name + arguments
    ↓
execute_tool(tool_name, args, user_id)
    ↓
MongoDB / pricing service
    ↓
tool result
    ↓
LLM or backend response generation
    ↓
user
```

The existing production backend uses the OpenAI-compatible Groq tool-calling protocol: it sends `TOOLS` to the model, reads `assistant_msg.get("tool_calls")`, extracts `tc["function"]["name"]` and parses `tc["function"]["arguments"]` with `json.loads`, then calls `execute_tool(fn_name, fn_args, user_id)`. The backend then sends the tool result back to the model as a `role: "tool"` message for the follow-up response. fileciteturn18file0 fileciteturn19file0

For the **new Kaggle CFG experiment**, the clean design is therefore to make the constrained LLM output a plain structured JSON tool-call object, for example:

```json
{"name":"searchHomes","arguments":{"location":"Bijnor"}}
```

There should be no `[TOOL_CALLS]` prefix and no natural-language wrapper around this object.

If the Kaggle endpoint is used as a replacement for the model currently inside `process_chat`, the backend adapter should parse this JSON object and invoke the existing `execute_tool(tool_name, args, user_id)` path. The exact HTTP adapter/endpoint can be implemented later; the grammar itself should represent the structured object, not an invented text marker.

## HavenTo source verified
Repository: `saurabh-kumar135/havento-accomodation-booking-platform`
Important file: `backend/services/agentService.py`.
The source contains the public `TOOLS` schemas and `execute_tool(tool_name, args, user_id)`. The system prompt also defines when each tool should be selected. fileciteturn14file0

## Actual backend execution path
The current `process_chat()` sends the public `TOOLS` definitions to the Groq model. When the model returns tool calls, the backend:

1. Reads `assistant_msg.get("tool_calls")`.
2. For each tool call, reads the function name.
3. Parses the function arguments with `json.loads`.
4. Calls `execute_tool(fn_name, fn_args, user_id)`.
5. Logs the tool invocation/result.
6. Performs a follow-up model call with the tool result as a `role: "tool"` message.
7. Uses the follow-up assistant content as the natural-language response. If needed, it synthesizes a response directly from the tool result. fileciteturn18file0 fileciteturn19file0

The CFG experiment should preserve this **semantic pipeline**, while replacing the model-side tool-call generation with our own constrained JSON generation and an adapter that feeds the parsed object into `execute_tool()`.

Important distinction:

```text
Current Groq path:
LLM → provider-native tool_calls structure → backend → execute_tool()

New Kaggle CFG path:
Mistral → CFG-constrained JSON object → backend adapter → execute_tool()
```

The `[TOOL_CALLS]` string marker is not required by the HavenTo backend and should not be treated as part of the target grammar.

## Public tool contract
Exactly 7 public tools:
1. `searchHomes`
2. `getHomeDetails`
3. `getUserBookings`
4. `createBooking`
5. `cancelBooking`
6. `manageFavourites`
7. `predictDynamicPricing`

### searchHomes
Properties: `location:string`, `maxPrice:number`, `minRating:number`. Required: none.
Backend searches location against location/houseName/description and applies price/rating filters. fileciteturn14file0

### getHomeDetails
Properties: `homeId:string`, `homeName:string`. Required: none.
Backend tries ID first, then home name. fileciteturn14file0

### getUserBookings
No properties.
Authentication is checked by backend. fileciteturn15file0

### createBooking
Public properties: `homeId:string`, `homeName:string`, `checkIn:string`, `checkOut:string`, `guests:integer`. Required: none.

Verified backend behavior:
- authentication required;
- home is resolved using `homeId` or `homeName`;
- backend also internally reads `location`, but `location` is NOT in the public `TOOLS` schema;
- `guests` defaults to 1;
- omitted dates produce a flexible-date booking;
- therefore `{}` is structurally allowed by the current public contract, although a target home must ultimately be resolvable for the operation to succeed. fileciteturn15file0

**CFG rule:** do NOT invent required fields for createBooking and do NOT allow undocumented `location` unless the public tool schema is deliberately changed.

### cancelBooking
Public properties: `bookingId:string`, `homeName:string`, `reason:string enum`, `reasonDetails:string`.
Required: `reason`, `reasonDetails`.

Exact reason enum:
- `Change of travel plans`
- `Found alternative accommodation`
- `Medical or personal emergency`
- `Accidental / duplicate booking`
- `Host requested cancellation`
- `Other solid reason`

Backend additionally requires `reasonDetails` length >= 15 and enforces the cancellation time window: dated bookings cannot be cancelled within 24 hours of check-in; flexible bookings can only be cancelled within 24 hours of creation. These are backend/business rules, NOT CFG rules. fileciteturn15file0 fileciteturn18file0

### manageFavourites
Properties: `action:string enum`, `homeId:string`, `homeName:string`.
Required: `action`.
Exact action enum: `list`, `add`, `remove`.
`list` does not need a home ID/name; add/remove need a target home at backend level. fileciteturn18file0

### predictDynamicPricing
Properties: `location:string`, `category:string`, `guests:integer`, `amenities:array[string]`.
Required: `location`.
Backend defaults category to `Trending`, guests to `2`, and amenities to `[]` if omitted. fileciteturn18file0

## Important backend/schema discrepancies
1. `createBooking.execute_tool()` internally reads `location`, but public `TOOLS` does not declare it. CFG follows the public contract, so `location` is currently forbidden for createBooking. fileciteturn15file0
2. `cancelBooking.execute_tool()` internally supports `cancelAll`, but public `TOOLS` does not declare it. CFG must currently reject `cancelAll`. If "cancel all" becomes official, first add it to `TOOLS`, then update the grammar. fileciteturn15file0

## Correct target grammar
The constrained output is exactly one JSON object:

```text
TOOL_CALL → '{' NAME_FIELD ',' ARGUMENTS_FIELD '}'
```

Conceptually:

```text
{"name":"<valid tool>","arguments":{...}}
```

There is **no** `[TOOL_CALLS]` prefix.
There is **no** enclosing JSON array unless the backend contract is deliberately changed later.
There is **no** natural-language text before or after the object during constrained generation.

The initial implementation should allow exactly ONE tool call.

## CFG MUST enforce
- exact JSON object structure;
- exact required top-level fields: `name` and `arguments`;
- exact 7-tool whitelist;
- tokenizer-aware tool-name alternatives;
- only public-schema argument keys for the selected tool;
- correct JSON value types;
- exact enum values;
- required public-schema fields;
- legal quotes, punctuation, commas, brackets and braces;
- valid JSON string/number/array syntax;
- completion immediately after the complete tool-call object.

Example valid outputs:

```json
{"name":"getUserBookings","arguments":{}}
```

```json
{"name":"searchHomes","arguments":{"location":"Bijnor","maxPrice":2000}}
```

```json
{"name":"cancelBooking","arguments":{"reason":"Other solid reason","reasonDetails":"My travel plans have changed."}}
```

## CFG MUST reject
- `[TOOL_CALLS]...` because it is not part of the new target contract;
- unknown tool names;
- unknown argument keys;
- malformed JSON;
- wrong JSON types;
- invalid enum values;
- missing `cancelBooking.reason`;
- missing `cancelBooking.reasonDetails`;
- missing `manageFavourites.action`;
- missing `predictDynamicPricing.location`;
- undocumented `cancelAll`;
- undocumented `createBooking.location`;
- arbitrary natural language inside the JSON object;
- trailing natural-language text after the JSON object;
- multiple tool-call objects initially.

## CFG MUST NOT enforce
- real MongoDB IDs/names/locations;
- whether a home exists;
- authentication;
- cancellation 15-character rule;
- cancellation 24-hour rule;
- date availability/business rules;
- price/business constraints;
- whether a booking target resolves successfully;
- MongoDB state;
- ML pricing output correctness.

Those remain backend/tool execution responsibilities.

## Tool-selection vs CFG
The HavenTo system prompt defines when a tool should be selected:
- use searchHomes for stay/location/budget/rating searches;
- use getHomeDetails for specific properties;
- use createBooking for explicit booking requests;
- ask for cancellation reason/details before cancellation when missing;
- use getUserBookings for existing bookings;
- use manageFavourites for saved homes;
- never invent homes.

These are primarily **tool-selection and policy rules**, not grammar syntax. The CFG validates the structure of the selected tool call. The model/prompt or an upstream decision mechanism determines which tool is selected. fileciteturn14file0

## Correct implementation model
At each generation step `t`:

`A_t = tokens that can legally continue the current grammar prefix`.

Keep logits for tokens in `A_t`; set all other logits to `-inf`; then sample.

The seven tool names are NOT the only globally legal token IDs. JSON punctuation, argument keys, strings, numbers, arrays, etc. must also be legal when their grammar state permits them.

Tool names and JSON fragments may be split into multiple Mistral tokenizer tokens. The implementation must therefore be tokenizer-aware.

Example:

```text
Current prefix:
{"name":"sea

Legal continuation:
searchHomes

Illegal completions:
cancelBooking
getHomeDetails
```

The grammar should operate on tokenizer continuations rather than assuming one token equals one word/field/value.

## Backend validation vs CFG validation
There are two separate validation layers:

### Layer 1 — CFG
Checks whether the generated object belongs to the allowed formal language.

Example:

```json
{"name":"searchHomes","arguments":{"maxPrice":2000}}
```

CFG answer: **valid structure**.

### Layer 2 — backend
Checks whether the requested operation can actually be executed.

For example:
- does the home ID exist?
- is the user logged in?
- does the booking exist?
- is cancellation allowed right now?
- is the property target resolvable?
- what records are in MongoDB?

Therefore:

```text
CFG valid
   ↓
backend execute_tool()
   ↓
actual application state / business rules
```

CFG is not a replacement for backend validation.

## Current notebook problem
The existing `ToolCallLogitMaskingProcessor` is only a bracket/depth state machine. It previously forced `[TOOL_CALLS]`, decoded generated text every step, tracked nesting, and eventually allowed EOS. It does not enforce the full tool whitelist/schema/type/enum/required-field language and previously allowed trailing hallucinated natural-language/property content.

Because the target contract is now a plain JSON object, the old `[TOOL_CALLS]` forcing logic must be removed rather than repaired.

## Exact next implementation
1. Keep Kaggle + Transformers + `model.generate()`.
2. Remove all assumptions that `[TOOL_CALLS]` is required.
3. Build the grammar from the VERIFIED public `TOOLS` contract above.
4. Start grammar at `{`, not `[TOOL_CALLS]`.
5. Implement top-level fields `name` and `arguments`.
6. Implement tokenizer-aware tool-name prefixes for the seven tools.
7. After the tool name is known, switch to the corresponding per-tool argument grammar.
8. Enforce allowed keys, JSON types, enums, and required fields.
9. Map legal grammar continuations to tokenizer token IDs.
10. Stop after exactly one complete valid JSON tool-call object.
11. Parse the resulting JSON object.
12. Send `tool_name` and `arguments` through the HavenTo backend adapter into the existing `execute_tool(tool_name, args, user_id)` path.
13. Keep final natural-language generation unrestricted.
14. Test all 7 tools plus invalid tool/key/type/enum/required-field cases.

## Testing priorities
### Valid
- `searchHomes`: location, price, rating, combinations, `{}`
- `getHomeDetails`: ID/name and `{}` as structurally valid
- `getUserBookings`: `{}`
- `createBooking`: full args and flexible/no-date booking
- `cancelBooking`: all six valid reasons with reasonDetails
- `manageFavourites`: list/add/remove
- `predictDynamicPricing`: location only and all optional fields

### Invalid
- `[TOOL_CALLS]` prefix
- unknown tool
- unknown argument key
- wrong JSON type
- invalid enum
- missing required field
- undocumented `cancelAll`
- undocumented createBooking `location`
- malformed JSON
- trailing natural language
- multiple objects/tool calls

## Live log
### 2026-09-10 — Notebook inspection
Confirmed current notebook uses Transformers, not vLLM, and existing constraint logic is bracket-based rather than a real grammar.

### 2026-09-10 — Section 15 design
Originally defined `[TOOL_CALLS][{"name":"<valid tool>","arguments":{...}}]`; this was later identified as incorrect for the intended Kaggle → HavenTo backend architecture.

### 2026-09-10 — HavenTo repository verification
Inspected `backend/services/agentService.py`, including both `TOOLS` and `process_chat()`/`execute_tool()`. Confirmed the production path consumes provider-native `tool_calls`, parses function arguments as JSON, and invokes `execute_tool()`. fileciteturn18file0 fileciteturn19file0

### 2026-09-10 — Architecture correction
Confirmed that the new CFG experiment should generate a **plain JSON tool-call object**, e.g.:

```json
{"name":"searchHomes","arguments":{"location":"Bijnor"}}
```

The `[TOOL_CALLS]` marker is removed from the grammar. The backend adapter will parse the JSON object and route it to the existing `execute_tool(tool_name, args, user_id)` path.

## Clarification workflow
If an implementation decision genuinely cannot be determined from the code/current requirements, ask the user **one question at a time in MCP format**, wait for the answer, then continue. Do not ask multiple clarification questions simultaneously.
