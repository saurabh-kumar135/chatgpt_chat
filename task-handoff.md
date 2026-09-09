# Task Handoff — HavenTo CFG-Constrained Tool Calling

## Goal
Implement grammar/CFG-constrained decoding for HavenTo so ONLY structured tool-call generation is constrained. Final natural-language responses remain unrestricted.

Target: Mistral 7B Instruct v0.3 in Kaggle. Current inference stack is Hugging Face Transformers with `model.generate()` and `device_map="auto"`, not vLLM. HavenTo is a FastAPI backend exposed to the model endpoint through ngrok.

## HavenTo source verified
Repository: `saurabh-kumar135/havento-accomodation-booking-platform`
Important file: `backend/services/agentService.py`.
The source contains the public `TOOLS` schemas and `execute_tool(tool_name, args, user_id)`. The system prompt also defines when each tool should be selected. fileciteturn9file0

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
Backend searches location against location/houseName/description and applies price/rating filters. fileciteturn9file0

### getHomeDetails
Properties: `homeId:string`, `homeName:string`. Required: none.
Backend tries ID first, then home name. fileciteturn9file0

### getUserBookings
No properties.
Authentication is checked by backend. fileciteturn10file0

### createBooking
Public properties: `homeId:string`, `homeName:string`, `checkIn:string`, `checkOut:string`, `guests:integer`. Required: none.

Verified backend behavior:
- authentication required;
- home is resolved using `homeId` or `homeName`;
- backend also internally reads `location`, but `location` is NOT in the public `TOOLS` schema;
- `guests` defaults to 1;
- omitted dates produce a flexible-date booking;
- therefore `{}` is structurally allowed by the current public contract, although a target home must ultimately be resolvable for the operation to succeed. fileciteturn10file0 fileciteturn11file0

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

Backend additionally requires `reasonDetails` length >= 15 and enforces the cancellation time window: dated bookings cannot be cancelled within 24 hours of check-in; flexible bookings can only be cancelled within 24 hours of creation. These are backend/business rules, NOT CFG rules. fileciteturn11file0 fileciteturn12file0

### manageFavourites
Properties: `action:string enum`, `homeId:string`, `homeName:string`.
Required: `action`.
Exact action enum: `list`, `add`, `remove`.
`list` does not need a home ID/name; add/remove need a target home at backend level. fileciteturn12file0

### predictDynamicPricing
Properties: `location:string`, `category:string`, `guests:integer`, `amenities:array[string]`.
Required: `location`.
Backend defaults category to `Trending`, guests to `2`, and amenities to `[]` if omitted. fileciteturn12file0

## Important backend/schema discrepancies
1. `createBooking.execute_tool()` internally reads `location`, but public `TOOLS` does not declare it. CFG follows the public contract, so `location` is currently forbidden for createBooking. fileciteturn10file0
2. `cancelBooking.execute_tool()` internally supports `cancelAll`, but public `TOOLS` does not declare it. CFG must currently reject `cancelAll`. If "cancel all" becomes official, first add it to `TOOLS`, then update the grammar. fileciteturn11file0

## Section 15 — grammar requirement
Canonical structure:
`[TOOL_CALLS][{"name":"<valid tool>","arguments":{...}}]`

Initially exactly ONE tool call is allowed.

CFG MUST enforce:
- exact `[TOOL_CALLS]` marker;
- valid JSON structure;
- exact 7-tool whitelist;
- tokenizer-aware tool-name alternatives;
- only public-schema argument keys for the selected tool;
- correct JSON types;
- exact enum values;
- required public-schema fields;
- legal quotes, punctuation, commas, brackets and braces;
- completion immediately after the tool-call structure.

CFG MUST reject:
- unknown tool names;
- unknown keys;
- malformed JSON;
- wrong types;
- invalid enum values;
- missing cancel reason/reasonDetails;
- missing manageFavourites action;
- missing predictDynamicPricing location;
- undocumented `cancelAll`;
- undocumented createBooking `location`;
- arbitrary natural language inside the tool-call object;
- trailing text such as `I found...` during constrained generation;
- multiple tool calls initially.

CFG MUST NOT enforce:
- real MongoDB IDs/names/locations;
- whether a home exists;
- authentication;
- cancellation 15-character rule;
- cancellation 24-hour rule;
- date availability/business rules;
- price/business constraints.

Those are backend responsibilities.

## Tool-selection vs CFG
The HavenTo system prompt says, among other things:
- use searchHomes for stay/location/budget/rating searches;
- use getHomeDetails for specific properties;
- use createBooking for explicit booking requests;
- ask for cancellation reason/details before cancellation when they are missing;
- use getUserBookings for existing bookings;
- use manageFavourites for saved homes;
- never invent homes.

These are primarily **tool-selection and policy rules**, not grammar syntax. The CFG constrains what a selected tool call is allowed to look like; the model/system prompt decides which tool to select. fileciteturn9file0

## Correct implementation model
At each generation step `t`:
`A_t = tokens that can legally continue the current grammar prefix`.
Keep logits for tokens in `A_t`; set all other logits to `-inf`; then sample.

The seven tool names are NOT the only globally legal token IDs. JSON punctuation, argument keys, strings, numbers, etc. must also be legal when their grammar state permits them.

Tool names and JSON fragments may be split into multiple Mistral tokenizer tokens. The implementation must therefore be tokenizer-aware.

## Current notebook problem
The existing `ToolCallLogitMaskingProcessor` is only a bracket/depth state machine. It forces `[TOOL_CALLS]`, decodes generated text every step, tracks nesting, and eventually allows EOS. It does not enforce the full tool whitelist/schema/type/enum/required-field language and previously allowed trailing hallucinated natural-language/property content.

## Exact next implementation
1. Keep Kaggle + Transformers + `model.generate()`.
2. Build grammar from the VERIFIED public `TOOLS` contract above.
3. Implement states for marker, JSON structure, tool-name prefixes, per-tool keys, values, enums, arrays, required-field tracking, and closure.
4. Map legal grammar continuations to tokenizer token IDs.
5. Replace the bracket-only logits processor.
6. Stop after exactly one complete valid tool call.
7. Parse the call and send it through the existing HavenTo `execute_tool()` path.
8. Keep final natural-language generation unrestricted.
9. Test all 7 tools plus invalid tool/key/type/enum/required-field cases.

## Testing priorities
- searchHomes: location, price, rating, combinations
- getHomeDetails: ID/name
- getUserBookings
- createBooking: full args and flexible/no-date booking
- cancelBooking: all six valid reasons; short reasonDetails should be rejected by backend, not CFG
- manageFavourites: list/add/remove
- predictDynamicPricing: location only and all optional fields
- unknown tool/key
- wrong JSON type
- invalid enum
- missing required fields
- undocumented `cancelAll`
- undocumented createBooking `location`
- trailing natural language

## Live log
### 2026-09-10 — Notebook inspection
Confirmed current notebook uses Transformers, not vLLM, and existing constraint logic is bracket-based rather than a real grammar.

### 2026-09-10 — Section 15 design
Defined `[TOOL_CALLS][{"name":"<valid tool>","arguments":{...}}]`, strict 7-tool whitelist, per-tool schemas, enums, required fields, exactly one initial call, and no trailing natural language during constrained generation.

### 2026-09-10 — HavenTo repository verification
Inspected `backend/services/agentService.py` and verified actual `TOOLS` plus `execute_tool()` behavior. Key corrections:
- `createBooking` is genuinely permissive in the public schema and supports flexible dates/default guests; do not invent required fields. fileciteturn10file0 fileciteturn11file0
- backend internally reads undocumented createBooking `location`; CFG should reject it under the current public contract. fileciteturn10file0
- backend internally supports undocumented `cancelAll`; CFG should reject it under the current public contract. fileciteturn11file0
- cancellation's 15-character and 24-hour constraints belong to backend validation, not CFG. fileciteturn11file0 fileciteturn12file0

## Clarification workflow
If an implementation decision genuinely cannot be determined from the code/current requirements, ask the user **one question at a time in MCP format**, wait for the answer, then continue. Do not ask multiple clarification questions simultaneously.
