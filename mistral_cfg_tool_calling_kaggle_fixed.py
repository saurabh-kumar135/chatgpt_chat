# Fixed Mistral CFG tool-calling implementation.
# Critical fix: candidate tokens are validated by decoding the COMPLETE generated token sequence.
# This avoids the false '{ "' dead-end caused by decoding candidate tokens independently.

!pip install -q -U transformers accelerate bitsandbytes sentencepiece regex

import itertools
import json
import re
from typing import Any, Dict, Tuple

import regex
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM, LogitsProcessor

MODEL_NAME = "mistralai/Mistral-7B-Instruct-v0.3"
tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
model = AutoModelForCausalLM.from_pretrained(MODEL_NAME, torch_dtype=torch.bfloat16, device_map="auto")
model.eval()

TOOLS = {
    "searchHomes": {"properties": {"location":{"type":"string"},"maxPrice":{"type":"number"},"minRating":{"type":"number"}},"required":[]},
    "getHomeDetails": {"properties": {"homeId":{"type":"string"},"homeName":{"type":"string"}},"required":[]},
    "getUserBookings": {"properties": {}, "required": []},
    "createBooking": {"properties": {"homeId":{"type":"string"},"homeName":{"type":"string"},"checkIn":{"type":"string"},"checkOut":{"type":"string"},"guests":{"type":"integer"}},"required":[]},
    "cancelBooking": {"properties": {"bookingId":{"type":"string"},"homeName":{"type":"string"},"reason":{"type":"string","enum":["Change of travel plans","Found alternative accommodation","Medical or personal emergency","Accidental / duplicate booking","Host requested cancellation","Other solid reason"]},"reasonDetails":{"type":"string"}},"required":["reason","reasonDetails"]},
    "manageFavourites": {"properties": {"action":{"type":"string","enum":["list","add","remove"]},"homeId":{"type":"string"},"homeName":{"type":"string"}},"required":["action"]},
    "predictDynamicPricing": {"properties": {"location":{"type":"string"},"category":{"type":"string"},"guests":{"type":"integer"},"amenities":{"type":"array","items":{"type":"string"}}},"required":["location"]},
}

JSON_STRING = r'"(?:\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4})|[^"\\\x00-\x1F])*"'
JSON_NUMBER = r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?'
JSON_INTEGER = r'-?(?:0|[1-9][0-9]*)'

def literal(value):
    return re.escape(json.dumps(value, ensure_ascii=False, separators=(",", ":")))

def value_regex(spec):
    if "enum" in spec:
        return "(?:" + "|".join(literal(v) for v in spec["enum"]) + ")"
    if spec["type"] == "string": return JSON_STRING
    if spec["type"] == "number": return JSON_NUMBER
    if spec["type"] == "integer": return JSON_INTEGER
    if spec["type"] == "array": return rf"\[(?:{JSON_STRING}(?:,{JSON_STRING})*)?\]"
    raise ValueError(spec)

def argument_regex(schema):
    props, required = schema["properties"], set(schema["required"])
    keys = list(props)
    if not keys: return r"\{\}"
    alternatives = []
    for n in range(len(keys)+1):
        for chosen in itertools.permutations(keys, n):
            if not required.issubset(chosen): continue
            fields = [literal(k)+":"+value_regex(props[k]) for k in chosen]
            alternatives.append(r"\{"+",".join(fields)+r"\}")
    return "(?:"+"|".join(sorted(set(alternatives), key=len, reverse=True))+")"

GRAMMAR = regex.compile("(?:"+"|".join(r'\{"name":'+literal(name)+r',"arguments":'+argument_regex(schema)+r'\}' for name,schema in TOOLS.items())+")")

def grammar_accepts_complete(text):
    return GRAMMAR.fullmatch(text) is not None

def grammar_accepts_prefix(text):
    return GRAMMAR.fullmatch(text, partial=True) is not None

def validate_tool_call(obj):
    if not isinstance(obj, dict) or set(obj) != {"name","arguments"}:
        raise ValueError("Top level must contain exactly name and arguments")
    name, args = obj["name"], obj["arguments"]
    if name not in TOOLS or not isinstance(args, dict): raise ValueError("Invalid tool or arguments object")
    schema = TOOLS[name]
    unknown = set(args)-set(schema["properties"])
    if unknown: raise ValueError(f"Unknown argument(s): {sorted(unknown)}")
    missing = [k for k in schema["required"] if k not in args]
    if missing: raise ValueError(f"Missing required argument(s): {missing}")
    for k,v in args.items():
        spec=schema["properties"][k]; t=spec["type"]
        if t=="string": ok=isinstance(v,str)
        elif t=="number": ok=isinstance(v,(int,float)) and not isinstance(v,bool)
        elif t=="integer": ok=isinstance(v,int) and not isinstance(v,bool)
        elif t=="array": ok=isinstance(v,list) and all(isinstance(x,str) for x in v)
        else: raise ValueError(f"Unsupported type: {t}")
        if not ok: raise ValueError(f"arguments.{k} must be {t}")
        if "enum" in spec and v not in spec["enum"]: raise ValueError(f"arguments.{k} has invalid enum value")
    return {"name":name,"arguments":args}

for x in ['{"name":"searchHomes","arguments":{}}','{"name":"searchHomes","arguments":{"location":"Bijnor","maxPrice":2000}}','{"name":"cancelBooking","arguments":{"reason":"Found alternative accommodation","reasonDetails":"I found another accommodation yesterday"}}']:
    assert grammar_accepts_complete(x), x
for x in ['[TOOL_CALLS]{"name":"searchHomes","arguments":{}}','[{"name":"searchHomes","arguments":{}}]','{"name":"unknownTool","arguments":{}}','{"name":"searchHomes","arguments":{"price":2000}}','{"name":"createBooking","arguments":{"location":"Bijnor"}}','{"name":"cancelBooking","arguments":{"cancelAll":true,"reason":"Other solid reason","reasonDetails":"Need to cancel"}}','{"name":"cancelBooking","arguments":{"reason":"Found another place","reasonDetails":"I found another accommodation"}}','{"name":"cancelBooking","arguments":{"reason":"Other solid reason"}}','{"name":"manageFavourites","arguments":{}}','{"name":"predictDynamicPricing","arguments":{}}']:
    assert not grammar_accepts_prefix(x), x
print("Grammar regression tests: PASS")

class HavenToCFGLogitsProcessor(LogitsProcessor):
    def __init__(self, tokenizer, prompt_len):
        self.tokenizer=tokenizer; self.prompt_len=prompt_len
        self.vocab_size=len(tokenizer); self.eos_token_id=tokenizer.eos_token_id
        self.prefix_cache={}

    def _candidate_text(self, generated_ids, token_id):
        # CRITICAL: decode the complete sequence, not prefix + decode([token]).
        return self.tokenizer.decode(generated_ids+[token_id], skip_special_tokens=False, clean_up_tokenization_spaces=False)

    def __call__(self, input_ids, scores):
        if input_ids.shape[0] != 1: raise ValueError("Batch size 1 is required")
        generated_ids=input_ids[0,self.prompt_len:].tolist()
        prefix=self.tokenizer.decode(generated_ids, skip_special_tokens=False, clean_up_tokenization_spaces=False)
        if grammar_accepts_complete(prefix):
            masked=torch.full_like(scores,float("-inf")); masked[0,self.eos_token_id]=scores[0,self.eos_token_id]; return masked
        scores=scores.clone(); scores[0,self.eos_token_id]=float("-inf")
        allowed=torch.zeros(self.vocab_size,dtype=torch.bool,device=scores.device)
        key_prefix=tuple(generated_ids)
        for tid in range(self.vocab_size):
            if not torch.isfinite(scores[0,tid]): continue
            key=(key_prefix,tid); ok=self.prefix_cache.get(key)
            if ok is None:
                ok=grammar_accepts_prefix(self._candidate_text(generated_ids,tid)); self.prefix_cache[key]=ok
            if ok: allowed[tid]=True
        if not bool(allowed.any()): raise RuntimeError(f"CFG dead-end at generated prefix: {prefix!r}")
        scores[0,~allowed]=float("-inf")
        return scores

def prompt_for(user_text):
    return f"""You are HavenTo's tool router.
Convert the user's request into exactly ONE tool call.
Output ONLY the JSON object.
No Markdown, no [TOOL_CALLS], no array, no explanation, and no text before or after it.
Use only public HavenTo tool names and public argument names.
Use exact enum values. Do not invent argument values.

User request:
{user_text}

JSON tool call:
"""

def generate_tool_call(user_text, max_new_tokens=256):
    prompt=prompt_for(user_text)
    inputs=tokenizer(prompt, return_tensors="pt", add_special_tokens=True)
    device=model.get_input_embeddings().weight.device
    input_ids=inputs["input_ids"].to(device); attention_mask=inputs["attention_mask"].to(device)
    prompt_len=input_ids.shape[1]
    processor=HavenToCFGLogitsProcessor(tokenizer,prompt_len)
    output=model.generate(input_ids=input_ids,attention_mask=attention_mask,max_new_tokens=max_new_tokens,do_sample=False,logits_processor=[processor],eos_token_id=tokenizer.eos_token_id,pad_token_id=tokenizer.eos_token_id,use_cache=True)
    raw=tokenizer.decode(output[0,prompt_len:],skip_special_tokens=True,clean_up_tokenization_spaces=False)
    if not raw: raise ValueError("Model generated no tool call")
    try: obj=json.loads(raw)
    except json.JSONDecodeError as exc: raise ValueError(f"Constrained output was not JSON: {raw!r}") from exc
    validate_tool_call(obj)
    if not grammar_accepts_complete(raw): raise ValueError(f"Output is outside CFG: {raw!r}")
    return obj

def to_backend_payload(tool_call):
    validated=validate_tool_call(tool_call)
    return validated["name"], validated["arguments"]

TEST_PROMPTS=["suggest me some homes","suggest me some homes in Bijnor","show me cheap homes under 2000","show my bookings","show my favourites","predict the dynamic price for homes in Bijnor"]
for user_text in TEST_PROMPTS:
    print("\nUSER:",user_text)
    try: print(json.dumps(generate_tool_call(user_text),indent=2,ensure_ascii=False))
    except Exception as exc: print("ERROR:",type(exc).__name__,exc)
