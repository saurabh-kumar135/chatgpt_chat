# Task Handoff — Mistral 7B CFG-Constrained Tool Calling

## Overall goal
Implement Context-Free Grammar (CFG) / grammar-constrained decoding for the HavenTo agent so that **only tool-call generation is grammar constrained**, while normal final natural-language responses remain unrestricted.

The target model is **Mistral 7B Instruct v0.3**, running remotely in a Kaggle notebook. HavenTo's FastAPI backend calls the Kaggle endpoint through ngrok.

## Confirmed architecture
- HavenTo backend: FastAPI.
- Mistral 7B Instruct v0.3 inference: Kaggle notebook.
- **Current notebook actually uses Hugging Face Transformers, not vLLM.**
- Model is loaded with `AutoModelForCausalLM.from_pretrained(...)` and `device_map="auto"`.
- Kaggle runtime has 2× Tesla T4 GPUs (~15.6 GB each); the model is split automatically across them.
- Grammar constraint applies only while generating the structured tool call.
- Final natural-language response should not be grammar constrained.
- Earlier handoff recorded vLLM as selected, but inspection of the uploaded notebook supersedes that assumption: the currently working implementation is Transformers-based. vLLM should not be introduced unless deliberately chosen later.

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

## Uploaded Kaggle notebook findings — `final_agent.ipynb`
The uploaded notebook uses:
- `transformers`
- `accelerate`
- `bitsandbytes`
- `sentencepiece`
- `AutoTokenizer`
- `AutoModelForCausalLM`
- `LogitsProcessor`
- `LogitsProcessorList`
- `StoppingCriteria`
- `StoppingCriteriaList`

Model:
`mistralai/Mistral-7B-Instruct-v0.3`

Loading code uses:
`AutoModelForCausalLM.from_pretrained(model_name, torch_dtype=torch.bfloat16, device_map="auto")`.

The notebook applies the Mistral chat template with `tools=tools` and `add_generation_prompt=True`.

The current experimental tools in the notebook are only three simplified tools:
- `search_homes`
- `get_home_details`
- `create_booking`

The test prompt is:
`Find me a stay in Tarapur under 500 and show me its details`

## Current experimental constrained decoder
`ToolCallLogitMaskingProcessor` currently:
1. Forces `[TOOL_CALLS]` at the first generated step when `force_tool=True`.
2. Decodes generated token IDs into text at each generation step.
3. Searches for `[TOOL_CALLS]`, then tracks `[`/`]` and `{`/`}` nesting while attempting to ignore brackets inside strings.
4. When nesting depth reaches zero, masks all vocabulary logits to `-inf` except EOS.

`ToolCallStoppingCriteria` similarly decodes the generated sequence at every step and stops when the outer JSON structure closes.

The notebook's generation call uses:
- `max_new_tokens=200`
- `do_sample=True`
- `temperature=0.2`
- `top_p=0.9`
- the custom logits processor
- the custom stopping criterion

The observed output still contained the structured tool call followed by hallucinated natural-language chatter, including a fabricated home ID, location, price, rating, amenities, and host information. This shows the current implementation does **not yet provide reliable grammar-constrained tool-call-only generation**.

## Important technical assessment of current notebook
The current processor is **not a full CFG implementation**. It is a bracket/depth state machine plus a first-token force and an EOS condition.

It does not yet enforce:
- the exact set of valid tool names;
- tokenizer-aware prefixes for multi-token tool names;
- exact JSON key ordering/structure as desired;
- per-tool argument schemas;
- string/number/integer/array lexical constraints;
- enum constraints for `cancelBooking.reason` and `manageFavourites.action`;
- required-field constraints;
- valid JSON separators and token-level continuation states throughout the complete call.

Also, decoding the complete generated sequence to text on every step is inefficient and is not the desired tokenizer-aware mechanism.

## Conceptual CFG/logit masking model
At generation step `t`, define:

`A_t = {tokens that can legally continue the current grammar prefix}`

Given model logits `z_t(i)`:

- if token `i` is legal: keep `z_t(i)` unchanged
- if token `i` is illegal: set `z_t(i) = -infinity`
- then apply softmax/sampling to the masked logits

Therefore self-attention still determines the model's preferences; grammar masking is an additional constraint applied to the next-token distribution.

CFG guarantees syntactic/structural validity, not factual correctness. Database truth and business rules still come from HavenTo's backend tools.

## What was ruled out / clarified
- Do not grammar-constrain ordinary final answers.
- Do not treat the seven tool names as the only globally legal tokens.
- Do not assume a simple seven-token-ID mask is sufficient; Mistral's tokenizer may split tool names and JSON strings into multiple subword tokens.
- Do not unnecessarily redesign HavenTo's backend architecture.
- Do not assume vLLM is already running: the uploaded notebook proves the current implementation is direct Transformers `model.generate()`.

## Live log

### 2026-09-10 — Initial handoff
**Action:** Established the target architecture and scope for CFG-constrained Mistral tool calling.
**Result:** Confirmed Mistral 7B Instruct v0.3 runs in Kaggle, HavenTo FastAPI calls it through ngrok, and only tool-call generation should be grammar constrained.

**Action:** Confirmed inference framework during planning.
**Result:** User selected **B — vLLM**, but this was later contradicted by inspection of the actual notebook; current notebook uses Transformers.

**Action:** Recorded the actual HavenTo tool set and schemas.
**Result:** Seven tools and their argument constraints are captured above for use when constructing the grammar.

### 2026-09-10 — Uploaded notebook inspected
**Action:** Inspected `final_agent.ipynb` supplied by the user.
**Result:** Confirmed the actual runtime uses Hugging Face Transformers with `AutoModelForCausalLM.from_pretrained`, `device_map="auto"`, and `model.generate()` rather than vLLM. Two Tesla T4 GPUs are available and the model is automatically split across them.

**Action:** Inspected the existing CFG/logit-masking experiment.
**Result:** Confirmed it is a bracket/depth state machine, not a complete CFG. It forces `[TOOL_CALLS]`, attempts to stop after JSON closure, but the recorded execution still produced trailing hallucinated text. The next implementation should use a tokenizer-aware grammar state and legal-token mask rather than decoding the whole sequence at every step.

## Current blocker
The immediate blocker is no longer identifying the inference framework: the uploaded notebook establishes that the current implementation is Transformers-based. The blocker is implementing a real tokenizer-aware grammar-constrained decoder compatible with `model.generate()` / `LogitsProcessor`, covering the seven HavenTo tool schemas while restricting the constraint to tool-call generation.

## Exact next step to resume
1. Preserve the current Kaggle + Transformers architecture.
2. Replace the current bracket-only `ToolCallLogitMaskingProcessor` with a real grammar/state-machine implementation that represents the JSON/tool-call language.
3. At each generation step, derive the set of tokenizer token IDs whose decoded token text can legally continue the current grammar prefix.
4. Mask every other vocabulary logit to `-inf`.
5. Stop exactly after the valid tool-call structure closes.
6. Test against all seven HavenTo tools and invalid-generation cases.
7. Keep final natural-language generation unrestricted after tool execution.

## Environment quirks / gotchas
- The user wants exact, step-by-step implementation and does not want unnecessary architecture changes.
- The grammar must be tokenizer-aware because tool names and JSON strings may span multiple Mistral tokens.
- Keep the distinction clear between **model preference** (logits/attention) and **grammar legality** (logit masking).
- The user explicitly requested that task handoff information be maintained in the GitHub repository `saurabh-kumar135/chatgpt_chat`.
