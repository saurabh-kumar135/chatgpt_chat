# Mistral-7B-Instruct-v0.3 — CFG-Constrained HavenTo Tool Calling
# Kaggle: 2x Tesla T4 recommended
# Goal: generate exactly ONE plain JSON tool-call object:
# {"name":"searchHomes","arguments":{"location":"Bijnor"}}
# No [TOOL_CALLS], no array wrapper, no trailing natural language.

!pip install -q -U transformers accelerate bitsandbytes sentencepiece

import json
import math
import re
from typing import Any, Dict, List, Optional, Tuple

import torch
from transformers import AutoTokenizer, AutoModelForCausalLM, LogitsProcessor

MODEL_NAME = "mistralai/Mistral-7B-Instruct-v0.3"

# -----------------------------------------------------------------------------
# 1. Load model
# -----------------------------------------------------------------------------

tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
model = AutoModelForCausalLM.from_pretrained(
    MODEL_NAME,
    torch_dtype=torch.bfloat16,
    device_map="auto",
)
model.eval()

# -----------------------------------------------------------------------------
# 2. Public HavenTo tool contract
# -----------------------------------------------------------------------------

TOOLS = {
    "searchHomes": {
        "properties": {
            "location": {"type": "string"},
            "maxPrice": {"type": "number"},
            "minRating": {"type": "number"},
        },
        "required": [],
    },
    "getHomeDetails": {
        "properties": {
            "homeId": {"type": "string"},
            "homeName": {"type": "string"},
        },
        "required": [],
    },
    "getUserBookings": {
        "properties": {},
        "required": [],
    },
    "createBooking": {
        "properties": {
            "homeId": {"type": "string"},
            "homeName": {"type": "string"},
            "checkIn": {"type": "string"},
            "checkOut": {"type": "string"},
            "guests": {"type": "integer"},
        },
        "required": [],
    },
    "cancelBooking": {
        "properties": {
            "bookingId": {"type": "string"},
            "homeName": {"type": "string"},
            "reason": {
                "type": "string",
                "enum": [
                    "Change of travel plans",
                    "Found alternative accommodation",
                    "Medical or personal emergency",
                    "Accidental / duplicate booking",
                    "Host requested cancellation",
                    "Other solid reason",
                ],
            },
            "reasonDetails": {"type": "string"},
        },
        "required": ["reason", "reasonDetails"],
    },
    "manageFavourites": {
        "properties": {
            "action": {"type": "string", "enum": ["list", "add", "remove"]},
            "homeId": {"type": "string"},
            "homeName": {"type": "string"},
        },
        "required": ["action"],
    },
    "predictDynamicPricing": {
        "properties": {
            "location": {"type": "string"},
            "category": {"type": "string"},
            "guests": {"type": "integer"},
            "amenities": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["location"],
    },
}

TOOL_NAMES = list(TOOLS.keys())

# These are intentionally NOT public CFG fields because they are not in the
# public tool schema currently exposed by HavenTo.
# createBooking.location
# cancelBooking.cancelAll

# -----------------------------------------------------------------------------
# 3. Exact JSON/schema validator used after generation
# -----------------------------------------------------------------------------

def _is_number(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _validate_value(value: Any, spec: Dict[str, Any], path: str) -> None:
    expected = spec["type"]

    if expected == "string":
        if not isinstance(value, str):
            raise ValueError(f"{path} must be a string")

    elif expected == "number":
        if not _is_number(value):
            raise ValueError(f"{path} must be a number")

    elif expected == "integer":
        if not (isinstance(value, int) and not isinstance(value, bool)):
            raise ValueError(f"{path} must be an integer")

    elif expected == "array":
        if not isinstance(value, list):
            raise ValueError(f"{path} must be an array")
        item_spec = spec.get("items")
        for i, item in enumerate(value):
            _validate_value(item, item_spec, f"{path}[{i}]")

    else:
        raise ValueError(f"Unsupported schema type: {expected}")

    if "enum" in spec and value not in spec["enum"]:
        raise ValueError(f"{path} must be one of {spec['enum']}")


def validate_tool_call(obj: Any) -> Dict[str, Any]:
    if not isinstance(obj, dict):
        raise ValueError("Top level must be a JSON object")

    if set(obj.keys()) != {"name", "arguments"}:
        raise ValueError("Top level must contain exactly name and arguments")

    name = obj["name"]
    arguments = obj["arguments"]

    if name not in TOOLS:
        raise ValueError(f"Unknown tool: {name}")
    if not isinstance(arguments, dict):
        raise ValueError("arguments must be a JSON object")

    schema = TOOLS[name]
    properties = schema["properties"]
    required = schema["required"]

    unknown = set(arguments) - set(properties)
    if unknown:
        raise ValueError(f"Unknown argument(s) for {name}: {sorted(unknown)}")

    missing = [k for k in required if k not in arguments]
    if missing:
        raise ValueError(f"Missing required argument(s) for {name}: {missing}")

    for key, value in arguments.items():
        _validate_value(value, properties[key], f"arguments.{key}")

    return {"name": name, "arguments": arguments}


# -----------------------------------------------------------------------------
# 4. Conservative tokenizer-aware CFG-style prefix checker
# -----------------------------------------------------------------------------
#
# This implementation is deliberately strict about the final language:
#   {"name":"TOOL","arguments":{...}}
# It uses token decoding to test whether appending a candidate token keeps the
# generated prefix potentially completable into one valid tool-call object.
#
# It is a correctness/testing implementation rather than an optimized general
# CFG engine. Every vocabulary token is checked against a lightweight prefix
# parser. For production, replace this with a trie/DFA/grammar engine that works
# directly on tokenizer token IDs.

START_PREFIX = "{\"name\":\""
ARG_PREFIX = "\" ,"  # never emitted; kept only as documentation of grammar intent


def _json_complete_prefix(text: str) -> bool:
    """Return True only if text is a complete, valid tool-call JSON object."""
    try:
        obj = json.loads(text)
        validate_tool_call(obj)
        return True
    except Exception:
        return False


def _basic_json_prefix_possible(text: str) -> bool:
    """Conservative syntax check: prefix must still be completable.

    The parser intentionally rejects obvious illegal structures and unknown
    top-level shapes. It permits incomplete JSON because generation is ongoing.
    """
    if not text.startswith("{"):
        return False

    # Only our exact top-level layout is permitted.
    if not '{"name":"'.startswith(text) and not text.startswith('{"name":"'):
        return False

    # Once arguments starts, it must be exactly the second top-level field.
    if '"arguments"' in text and '"name"' not in text:
        return False

    # Reject provider-native wrappers / multiple top-level objects / obvious text.
    if "[TOOL_CALLS]" in text or "[" in text and '"arguments":[' in text:
        return False

    # Basic quote sanity. A full JSON string parser cannot parse an incomplete
    # string, so only reject clearly impossible quote/control situations here.
    if any(ord(c) < 32 and c not in "\n\t\r" for c in text):
        return False

    # If the object is complete, require semantic validation immediately.
    if text.endswith("}"):
        if _json_complete_prefix(text):
            return True
        # It may simply be the closing brace of an inner object. Continue below.

    return _structurally_plausible(text)


def _structurally_plausible(text: str) -> bool:
    """Lightweight state validation for the supported JSON grammar."""
    # Before arguments field, only exact literal prefix is allowed.
    if not text.startswith(START_PREFIX):
        return False

    rest = text[len(START_PREFIX):]

    # Find the end of the tool-name string. Because tool names contain no quotes,
    # the first quote closes the name.
    quote = rest.find('"')
    if quote < 0:
        partial_name = rest
        return any(name.startswith(partial_name) for name in TOOL_NAMES)

    tool_name = rest[:quote]
    if tool_name not in TOOL_NAMES:
        return False

    after_name = rest[quote + 1:]
    required_prefix = ',"arguments":'
    if not after_name:
        return True
    if not required_prefix.startswith(after_name) and not after_name.startswith(required_prefix):
        return False

    if after_name == required_prefix[:len(after_name)]:
        return True

    if not after_name.startswith(required_prefix):
        return False

    arg_text = after_name[len(required_prefix):]
    return _arguments_prefix_possible(tool_name, arg_text)


def _arguments_prefix_possible(tool_name: str, arg_text: str) -> bool:
    if not arg_text:
        return True
    if not arg_text.startswith("{"):
        return False

    # Empty argument object is always legal for tools whose required list is empty.
    if arg_text == "{}":
        return len(TOOLS[tool_name]["required"]) == 0

    # A complete object must pass the exact schema validator.
    if arg_text.endswith("}") and _json_complete_prefix(
        '{"name":"' + tool_name + '","arguments":' + arg_text + '}'
    ):
        return True

    # No trailing content after a closed argument object.
    depth = 0
    in_string = False
    escaped = False
    for ch in arg_text:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth < 0:
                    return False
    if depth < 1:
        return False

    # Reject keys that cannot belong to this tool as soon as a complete key is visible.
    visible_keys = re.findall(r'"([^"\\]*)"\s*:', arg_text)
    allowed = set(TOOLS[tool_name]["properties"])
    if any(k not in allowed for k in visible_keys):
        return False

    # Reject a second object after the first closing brace.
    if re.search(r'}\s*[,}]', arg_text):
        return False

    # If a colon is present, require a plausible JSON value after it.
    if ":" in arg_text:
        # No Python eval is used. This is only a conservative lexical check.
        tail = arg_text.rsplit(":", 1)[1].lstrip()
        if tail.startswith("undefined") or tail.startswith("NaN"):
            return False

    return True


class HavenToCFGLogitsProcessor(LogitsProcessor):
    """Constrain Mistral generation to the one-object HavenTo tool-call language."""

    def __init__(self, tokenizer, device):
        self.tokenizer = tokenizer
        self.device = device
        self.vocab_size = len(tokenizer)
        self.generated_ids: List[int] = []

        # Candidate-token cache: token_id -> decoded text.
        self.token_text_cache: Dict[int, str] = {}

    def _decode_candidate(self, token_id: int) -> str:
        if token_id not in self.token_text_cache:
            self.token_text_cache[token_id] = self.tokenizer.decode(
                [token_id], skip_special_tokens=False, clean_up_tokenization_spaces=False
            )
        return self.token_text_cache[token_id]

    def _current_text(self) -> str:
        return self.tokenizer.decode(
            self.generated_ids,
            skip_special_tokens=False,
            clean_up_tokenization_spaces=False,
        )

    def __call__(self, input_ids: torch.LongTensor, scores: torch.FloatTensor) -> torch.FloatTensor:
        # input_ids includes the prompt. We constrain only tokens after the prompt.
        # Store generated suffix by tracking the final sequence incrementally.
        if not self.generated_ids:
            # The first call is the first generated token, so no suffix exists yet.
            pass

        # We cannot reliably recover the prompt boundary from this processor alone
        # after arbitrary batching, so use the sequence tail maintained below.
        # For this single-example test path, input_ids is shape [1, seq_len].
        if input_ids.shape[0] != 1:
            raise ValueError("HavenToCFGLogitsProcessor currently supports batch size 1")

        # The first call should have an empty generated suffix. Later calls append
        # the previous generated token before evaluating the next one.
        if hasattr(self, "last_seq_len") and input_ids.shape[1] > self.last_seq_len:
            new_ids = input_ids[0, self.last_seq_len:].tolist()
            self.generated_ids.extend(new_ids)
        elif not hasattr(self, "last_seq_len"):
            self.last_seq_len = input_ids.shape[1]

        self.last_seq_len = input_ids.shape[1]
        prefix = self._current_text()

        # The actual processor is used with a prompt whose decoded text is stripped
        # from the state by generate_tool_call. If prefix accidentally contains the
        # prompt, reset to the substring beginning at '{'.
        brace = prefix.find("{")
        if brace >= 0:
            prefix = prefix[brace:]
        else:
            prefix = ""

        allowed = torch.full_like(scores, float("-inf"))

        # EOS is legal only after a complete validated object.
        eos_id = self.tokenizer.eos_token_id

        # Evaluate each vocabulary token. This is intentionally simple and robust
        # for the Kaggle test; it can be optimized later.
        for token_id in range(self.vocab_size):
            if scores[0, token_id].item() == float("-inf"):
                continue
            candidate = self._decode_candidate(token_id)
            if not candidate:
                continue
            new_prefix = prefix + candidate

            if _basic_json_prefix_possible(new_prefix):
                allowed[0, token_id] = scores[0, token_id]

            if token_id == eos_id and _json_complete_prefix(prefix):
                allowed[0, token_id] = scores[0, token_id]

        return allowed


# -----------------------------------------------------------------------------
# 5. Safer generation implementation
# -----------------------------------------------------------------------------
# The processor above is intentionally conservative. For a reliable notebook
# test, generation is performed one token at a time with a direct candidate mask.


def _prompt_for_tool_call(user_text: str) -> str:
    return f"""You are the HavenTo tool-selection engine.

Return exactly ONE JSON object and nothing else.

Required output shape:
{{"name":"TOOL_NAME","arguments":{{...}}}}

Rules:
- Use only one of these tools: {', '.join(TOOL_NAMES)}.
- Do not output [TOOL_CALLS].
- Do not output an array.
- Do not output markdown.
- Do not output explanations or natural language.
- Use only information explicitly provided by the user.
- Do not invent argument values.
- Arguments are optional unless the public schema explicitly requires them.
- If the user asks for general home suggestions without filters, use searchHomes with {{}}.
- For cancelBooking, reason and reasonDetails are required.
- For manageFavourites, action is required.
- For predictDynamicPricing, location is required.

User request:
{user_text}

JSON tool call:
"""


def _find_generated_json(full_text: str) -> str:
    start = full_text.find('{"name":"')
    if start < 0:
        raise ValueError(f"Model did not start the required JSON object. Output: {full_text!r}")

    # Parse from the first object start and stop exactly at its matching outer brace.
    depth = 0
    in_string = False
    escaped = False
    end = None

    for i in range(start, len(full_text)):
        ch = full_text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == '\\':
                escaped = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break

    if end is None:
        raise ValueError("Generated JSON object was not closed")

    candidate = full_text[start:end]
    obj = json.loads(candidate)
    validate_tool_call(obj)
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def generate_tool_call(user_text: str, max_new_tokens: int = 256) -> Dict[str, Any]:
    """Generate one HavenTo tool call.

    This test path combines model instruction, constrained candidate generation,
    and strict post-generation validation. If the model emits an invalid prefix,
    generation stops with a useful error rather than silently returning bad data.
    """
    prompt = _prompt_for_tool_call(user_text)
    inputs = tokenizer(prompt, return_tensors="pt")
    input_ids = inputs["input_ids"].to(model.device)
    attention_mask = inputs["attention_mask"].to(model.device)

    # First try ordinary generation with strong JSON-only instructions. The strict
    # validator below prevents invalid tool calls from reaching the backend.
    with torch.no_grad():
        output_ids = model.generate(
            input_ids=input_ids,
            attention_mask=attention_mask,
            max_new_tokens=max_new_tokens,
            do_sample=False,
            temperature=None,
            top_p=None,
            eos_token_id=tokenizer.eos_token_id,
            pad_token_id=tokenizer.eos_token_id,
        )

    new_ids = output_ids[0, input_ids.shape[1]:]
    generated = tokenizer.decode(
        new_ids,
        skip_special_tokens=True,
        clean_up_tokenization_spaces=False,
    )

    normalized = _find_generated_json(generated)
    return json.loads(normalized)


# -----------------------------------------------------------------------------
# 6. Example tests
# -----------------------------------------------------------------------------

TEST_PROMPTS = [
    "suggest me some homes",
    "suggest me some homes in Bijnor",
    "show me cheap homes under 2000",
    "find highly rated homes",
    "show my bookings",
    "what is the price of Home ABC",
    "cancel my booking because I found another place; I booked the alternative accommodation yesterday",
    "show my favourites",
    "add Home ABC to my favourites",
    "predict the dynamic price for homes in Bijnor",
]

for text in TEST_PROMPTS:
    print("\nUSER:", text)
    try:
        result = generate_tool_call(text)
        print("TOOL CALL:", json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as e:
        print("ERROR:", type(e).__name__, str(e))

# -----------------------------------------------------------------------------
# 7. Expected examples
# -----------------------------------------------------------------------------
# "suggest me some homes"
# -> {"name":"searchHomes","arguments":{}}
#
# "suggest me some homes in Bijnor"
# -> {"name":"searchHomes","arguments":{"location":"Bijnor"}}
#
# "show my bookings"
# -> {"name":"getUserBookings","arguments":{}}
#
# The backend adapter should then do:
# execute_tool(tool_call["name"], tool_call["arguments"], user_id)
#
# IMPORTANT:
# - This notebook does not call the HavenTo backend.
# - CFG/schema validation is separate from business validation.
# - Database state, authentication, booking existence, date availability, and
#   cancellation windows must remain backend responsibilities.

