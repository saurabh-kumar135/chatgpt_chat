#!/usr/bin/env python3
"""
Qwen 3.8 27B Autonomous Vercel MCP Agent Bridge
------------------------------------------------
Connects Qwen 3.8 27B (hosted on Groq) to the Vercel Model Context Protocol (MCP) Server.
Orchestrates the complete MCP handshake over stdio JSON-RPC 2.0, translates MCP tools into
Qwen/OpenAI function schemas, and executes autonomous tool-calling loops.

Usage:
  1. One-shot query:
     python qwen_vercel_agent.py "How many projects do I have on Vercel?"
  2. Interactive Chat REPL:
     python qwen_vercel_agent.py
"""

import sys
import os
import json
import asyncio
import argparse
from typing import Any, Dict, List, Optional
import httpx

# ── Configuration Defaults ───────────────────────────────────────────────────
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_MODEL = "qwen/qwen3.8-27b"

# Locate script directory and vercel_mcp_server.py
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

def _load_env_fallback(key: str, default: str = "") -> str:
    """Reads from environment, .env files, or known local test scripts."""
    val = os.getenv(key)
    if val and val.strip():
        return val.strip()

    # Search common .env locations
    candidates = [
        os.path.join(os.getcwd(), ".env"),
        os.path.join(SCRIPT_DIR, ".env"),
        os.path.join(os.path.dirname(SCRIPT_DIR), ".env"),
        "/home/saurabh-kumar123/Desktop/Desktop/express/.env",
        "/home/saurabh-kumar123/Desktop/Desktop/express/chatgpt_chat/.env",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        if line.startswith(f"{key}="):
                            extracted = line.split("=", 1)[1].strip("\"' ")
                            if extracted:
                                return extracted
            except Exception:
                pass

    # Secondary fallback for GROQ_API_KEY from test_api.sh
    if key == "GROQ_API_KEY":
        test_api_paths = [
            "/home/saurabh-kumar123/Desktop/Desktop/express/ai_agent/test_api.sh",
            os.path.join(os.getcwd(), "ai_agent", "test_api.sh"),
        ]
        for t_path in test_api_paths:
            if os.path.exists(t_path):
                try:
                    with open(t_path, "r", encoding="utf-8") as f:
                        content = f.read()
                        import re
                        m = re.search(r"Bearer\s+(gsk_[A-Za-z0-9]+)", content)
                        if m:
                            return m.group(1)
                except Exception:
                    pass

    return default

DEFAULT_GROQ_KEY = _load_env_fallback("GROQ_API_KEY", "")
DEFAULT_VERCEL_TOKEN = _load_env_fallback("VERCEL_TOKEN", "")

# Locate vercel_mcp_server.py relative to this script
MCP_SERVER_PATH = os.path.join(SCRIPT_DIR, "vercel_mcp_server.py")
if not os.path.exists(MCP_SERVER_PATH):
    # Fallback to parent directory if running inside a subfolder
    alt_path = os.path.join(os.path.dirname(SCRIPT_DIR), "vercel_mcp_server.py")
    if os.path.exists(alt_path):
        MCP_SERVER_PATH = alt_path


class VercelMCPClient:
    """Manages the subprocess lifecycle and stdio JSON-RPC 2.0 communication with vercel_mcp_server.py."""

    def __init__(self, server_script_path: str, vercel_token: str, vercel_team_id: str = ""):
        self.server_path = server_script_path
        self.vercel_token = vercel_token
        self.vercel_team_id = vercel_team_id
        self.process: Optional[asyncio.subprocess.Process] = None
        self.msg_id = 0
        self.tools: List[Dict[str, Any]] = []

    async def start(self):
        """Spawns the MCP server subprocess with injected environment and completes the handshake."""
        if not os.path.exists(self.server_path):
            raise FileNotFoundError(f"Vercel MCP server script not found at: {self.server_path}")

        env = os.environ.copy()
        env["VERCEL_TOKEN"] = self.vercel_token
        if self.vercel_team_id:
            env["VERCEL_TEAM_ID"] = self.vercel_team_id
        env["PYTHONUNBUFFERED"] = "1"

        # Spawn Python subprocess with bidirectional stdio pipes
        self.process = await asyncio.create_subprocess_exec(
            sys.executable,
            self.server_path,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env
        )

        # 1. MCP Handshake: initialize
        init_res = await self._send_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {
                "name": "Qwen-27B-Agent-Client",
                "version": "1.0.0"
            }
        })

        # 2. MCP Handshake: notifications/initialized
        await self._send_notification("notifications/initialized", {})

        # 3. Discover Tools: tools/list
        tools_res = await self._send_request("tools/list", {})
        self.tools = tools_res.get("result", {}).get("tools", [])
        return self.tools

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> str:
        """Executes tools/call on the MCP server and returns the raw string content."""
        res = await self._send_request("tools/call", {
            "name": name,
            "arguments": arguments
        })
        result = res.get("result", {})
        content_list = result.get("content", [])
        if content_list and isinstance(content_list, list):
            text_blocks = [c.get("text", "") for c in content_list if c.get("type") == "text"]
            return "\n".join(text_blocks)
        return json.dumps(result)

    async def _send_request(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        self.msg_id += 1
        req = {
            "jsonrpc": "2.0",
            "id": self.msg_id,
            "method": method,
            "params": params
        }
        raw_msg = json.dumps(req) + "\n"
        self.process.stdin.write(raw_msg.encode("utf-8"))
        await self.process.stdin.drain()

        # Read JSON-RPC response from server's stdout
        line = await self.process.stdout.readline()
        if not line:
            stderr_out = await self.process.stderr.read()
            raise RuntimeError(f"Vercel MCP Server terminated unexpectedly. Stderr: {stderr_out.decode()}")

        return json.loads(line.decode("utf-8").strip())

    async def _send_notification(self, method: str, params: Dict[str, Any]):
        req = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params
        }
        raw_msg = json.dumps(req) + "\n"
        self.process.stdin.write(raw_msg.encode("utf-8"))
        await self.process.stdin.drain()

    async def stop(self):
        """Gracefully terminates the MCP server subprocess."""
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                self.process.kill()


def convert_mcp_tools_to_qwen_schema(mcp_tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Converts standard MCP tool definitions into OpenAI/Groq function-calling schemas."""
    schemas = []
    for tool in mcp_tools:
        schemas.append({
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("inputSchema", {
                    "type": "object",
                    "properties": {},
                    "required": []
                })
            }
        })
    return schemas


class QwenVercelAgent:
    """Autonomous Agent powered by Qwen 3.8 27B on Groq."""

    def __init__(self, mcp_client: VercelMCPClient, groq_key: str, model: str = DEFAULT_MODEL):
        self.mcp = mcp_client
        self.groq_key = groq_key
        self.model = model
        self.qwen_tools: List[Dict[str, Any]] = []
        self.system_prompt = (
            "You are Qwen Vercel Ops Assistant — an autonomous, intelligent cloud DevOps agent "
            "powered by Qwen 3.8 27B. You have direct control over the user's Vercel platform "
            "infrastructure via your provided Vercel MCP tools.\n\n"
            "OPERATIONAL GUIDELINES:\n"
            "1. Whenever the user asks questions about their Vercel account, projects, deployments, "
            "build logs, or environment variables, ALWAYS use the relevant tool to fetch real data.\n"
            "2. Never guess or hallucinate project names, deployment IDs, or errors. Query the Vercel API.\n"
            "3. Multi-turn reasoning: If a deployment has failed (state: 'ERROR'), automatically retrieve its "
            "build logs using vercel_get_build_logs to pinpoint the exact failure line and explain how to fix it.\n"
            "4. Keep explanations concise, professional, and formatted in clean GitHub-flavored markdown."
        )

    async def initialize(self):
        """Starts the MCP server and registers tools."""
        mcp_tools = await self.mcp.start()
        self.qwen_tools = convert_mcp_tools_to_qwen_schema(mcp_tools)
        return len(self.qwen_tools)

    async def run_query(self, user_query: str, history: Optional[List[Dict[str, Any]]] = None) -> str:
        """Executes an agentic reasoning loop for a user query."""
        messages: List[Dict[str, Any]] = []
        if history:
            messages.extend(history)
        else:
            messages.append({"role": "system", "content": self.system_prompt})

        messages.append({"role": "user", "content": user_query})

        if not self.groq_key or not self.groq_key.strip():
            return (
                "❌ Configuration Error: GROQ_API_KEY is missing!\n\n"
                "To resolve this, choose one of the following:\n"
                "  1. Create a `.env` file with: GROQ_API_KEY=gsk_...\n"
                "  2. Export it in your shell: export GROQ_API_KEY=gsk_...\n"
                "  3. Run with flag: python3 qwen_vercel_agent.py --groq-key gsk_...\n"
            )

        headers = {
            "Authorization": f"Bearer {self.groq_key.strip()}",
            "Content-Type": "application/json"
        }

        # Autonomous ReAct Loop (up to 6 turns)
        async with httpx.AsyncClient(timeout=45.0) as http_client:
            for turn in range(6):
                payload = {
                    "model": self.model,
                    "temperature": 0.3,
                    "max_tokens": 800,
                    "messages": messages,
                    "tools": self.qwen_tools,
                    "tool_choice": "auto"
                }

                response = await http_client.post(GROQ_API_URL, headers=headers, json=payload)
                if response.status_code >= 400:
                    return f"❌ Groq API Error ({response.status_code}): {response.text}"

                data = response.json()
                choice = data["choices"][0]
                assistant_msg = choice["message"]
                messages.append(assistant_msg)

                # Check if Qwen decided to call tools
                tool_calls = assistant_msg.get("tool_calls")
                if not tool_calls:
                    # Final natural language answer reached!
                    return assistant_msg.get("content", "").strip()

                # Process all tool calls requested by Qwen
                for tc in tool_calls:
                    func = tc["function"]
                    tool_name = func["name"]
                    call_id = tc["id"]
                    try:
                        args = json.loads(func.get("arguments", "{}"))
                    except json.JSONDecodeError:
                        args = {}

                    print(f"\n⚙️  [Qwen 3.8 27B] ➔ Calling MCP Tool: {tool_name}({json.dumps(args)})")
                    tool_result_str = await self.mcp.call_tool(tool_name, args)
                    # Defensive guardrail: prevent any single tool output from exceeding Groq's 7,000 token limit
                    if len(tool_result_str) > 3500:
                        tool_result_str = tool_result_str[:3500] + "\n... [truncated to fit token budget]"
                    print(f"📥 [Vercel MCP Server] ➔ Received {len(tool_result_str)} bytes of data")

                    messages.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": tool_result_str
                    })

            return "⚠️ Agent exceeded maximum reasoning iterations."


async def main():
    parser = argparse.ArgumentParser(description="Qwen 3.8 27B Vercel MCP Agent")
    parser.add_argument("query", nargs="?", help="Optional one-shot query to execute")
    parser.add_argument("--model", default=os.getenv("QWEN_MODEL", DEFAULT_MODEL), help="Groq model ID")
    parser.add_argument("--token", default=os.getenv("VERCEL_TOKEN", DEFAULT_VERCEL_TOKEN), help="Vercel access token")
    parser.add_argument("--groq-key", default=os.getenv("GROQ_API_KEY", DEFAULT_GROQ_KEY), help="Groq API key")
    args = parser.parse_args()

    print("=" * 70)
    print("🚀 Initializing Qwen 3.8 27B Vercel MCP Agent...")
    print(f"🧠 Model: {args.model} (via Groq Cloud)")
    print(f"🔌 MCP Server: {MCP_SERVER_PATH}")
    print("=" * 70)

    mcp_client = VercelMCPClient(MCP_SERVER_PATH, vercel_token=args.token)
    agent = QwenVercelAgent(mcp_client, groq_key=args.groq_key, model=args.model)

    try:
        tool_count = await agent.initialize()
        print(f"✅ Handshake successful! Registered {tool_count} Vercel tools with Qwen.\n")

        # One-shot mode
        if args.query:
            print(f"👤 User: {args.query}\n")
            ans = await agent.run_query(args.query)
            print(f"\n🤖 Qwen 27B:\n{ans}\n")
            return

        # Interactive REPL mode
        print("💬 Entering Interactive Chat Mode (type 'exit' or 'quit' to stop):")
        history: List[Dict[str, Any]] = [
            {"role": "system", "content": agent.system_prompt}
        ]
        while True:
            try:
                prompt = input("\nqwen-vercel> ").strip()
                if not prompt:
                    continue
                if prompt.lower() in ("exit", "quit"):
                    print("Goodbye!")
                    break

                ans = await agent.run_query(prompt, history=history)
                print(f"\n🤖 Qwen 27B:\n{ans}")
            except (KeyboardInterrupt, EOFError):
                break

    finally:
        await mcp_client.stop()


if __name__ == "__main__":
    asyncio.run(main())
