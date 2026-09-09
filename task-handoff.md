# Task Handoff — Mistral 7B CFG-Constrained Tool Calling

## Overall goal
Implement Context-Free Grammar (CFG) / grammar-constrained decoding for the HavenTo agent so that **only tool-call generation is grammar constrained**, while normal final natural-language responses remain unrestricted.

The target model is **Mistral 7B Instruct v0.3**, running remotely in a Kaggle notebook. HavenTo's FastAPI backend calls the Kaggle endpoint through ngrok.

## Confirmed architecture
- HavenTo backend: FastAPI.
- Mistral 7B Instruct v0.3 inference: Kaggle notebook.
- Inference library: **vLLM**.
- Kaggle endpoint is reached by HavenTo through ngrok.
- Grammar constraint applies only while generating the structured tool call.
- Final natural-language response should not be grammar constrained.

## HavenTo tool set confirmed from the project
The backend currently exposes 7 valid function tools:

1. `searchHomes`
2. `getHomeDetails`
3. `getUserBookings`
4. `createBooking`
5. `cancelBooking`
6. `manageFavourites`
7. `predictDynamicPricing`

Important: the CFG is **not** a global whitelist containing only these seven token IDs. At each generation step, the grammar engine must allow every tokenizer token that can legally continue the current grammar state, including JSON punctuation, quotes, colons, commas, argument keys, and valid argument values. Tool names are the semantic alternatives inside the grammar.

## Tool schemas confirmed
### `searchHomes`
Properties:
- `location`: string
- `maxPrice`: number
- `minRating`: number
Required: none.

### `getHomeDetails`
Properties:
- `homeId`: string
- `homeName`: string
Required: none.

### `getUserBookings`
No properties.

### `createBooking`
Properties:
- `homeId`: string
- `homeName`: string
- `checkIn`: string
- `checkOut`: string
- `guests`: integer
Required: none.

### `cancelBooking`
Properties:
- `bookingId`: string
- `homeName`: string
- `reason`: string enum:
  - `Change of travel plans`
  - `Found alternative accommodation`
  - `Medical or personal emergency`
  - `Accidental / duplicate booking`
  - `Host requested cancellation`
  - `Other solid reason`
- `reasonDetails`: string
Required:
- `reason`
- `reasonDetails`

### `manageFavourites`
Properties:
- `action`: string enum: `list`, `add`, `remove`
- `homeId`: string
- `homeName`: string
Required:
- `action`

### `predictDynamicPricing`
Properties:
- `location`: string (required)
- `category`: string
- `guests`: integer
- `amenities`: array[string]

## Existing HavenTo backend behavior
The backend currently uses `backend/services/agentService.py`.

`process_chat(...)` currently sends an OpenAI-compatible payload containing:
- `model`
- `messages`
- `tools: TOOLS`
- `tool_choice: "auto"`
- `temperature: 0.5`
- `max_tokens: 800`

The model can return tool calls. The backend executes them through:
`execute_tool(tool_name, args, user_id)`.

After tool execution, the tool result is appended and the model is asked for the final natural-language response.

The current `TOOLS` list and `execute_tool` implementation therefore define the semantics that the new constrained decoder must preserve.

## Conceptual CFG/logit masking model
At generation step `t`, define:

`A_t = {tokens that can legally continue the current grammar prefix}`

Given model logits `z_t(i)`:

- if token `i` is legal: keep `z_t(i)` unchanged
- if token `i` is illegal: set `z_t(i) = -infinity`
- then apply softmax/sampling to the masked logits

Therefore self-attention still determines the model's preferences; grammar masking is an additional constraint applied to the next-token distribution.

CFG guarantees syntactic/structural validity, not factual correctness. Database truth and business rules still come from HavenTo's backend tools.

## Current confirmed state
- The user selected **vLLM** for the Mistral inference stack.
- The exact vLLM loading/server method has **not yet been confirmed**.

## What was ruled out / clarified
- Do not grammar-constrain ordinary final answers.
- Do not treat the seven tool names as the only globally legal tokens.
- Do not assume a simple seven-token-ID mask is sufficient; Mistral's tokenizer may split tool names and JSON strings into multiple subword tokens.
- Do not unnecessarily redesign HavenTo's backend architecture; the constrained decoder belongs on the Kaggle/Mistral side under the confirmed architecture.

## Live log

### 2026-09-10 — Initial handoff
**Action:** Established the target architecture and scope for CFG-constrained Mistral tool calling.
**Result:** Confirmed Mistral 7B Instruct v0.3 runs in Kaggle, HavenTo FastAPI calls it through ngrok, and only tool-call generation should be grammar constrained.

**Action:** Confirmed inference framework.
**Result:** User corrected the earlier answer and confirmed **B — vLLM**.

**Action:** Recorded the actual HavenTo tool set and schemas.
**Result:** Seven tools and their argument constraints are captured above for use when constructing the grammar.

## Current blocker
The exact vLLM integration mode is still unknown: offline `LLM(...)` inference versus vLLM's OpenAI-compatible server/API (or another setup). This determines the cleanest way to attach grammar-constrained decoding.

## Exact next step to resume
Determine how Mistral is currently exposed through vLLM in the Kaggle notebook. Then choose the grammar-constrained decoding integration compatible with that mode and implement the grammar so it exactly covers the seven HavenTo tool schemas.

## Environment quirks / gotchas
- The user wants exact, step-by-step implementation and does not want unnecessary architecture changes.
- The grammar must be tokenizer-aware because tool names and JSON components may span multiple Mistral tokens.
- Keep the distinction clear between **model preference** (logits/attention) and **grammar legality** (logit masking).
- The user explicitly requested that task handoff information be maintained in the GitHub repository `saurabh-kumar135/chatgpt_chat`.
