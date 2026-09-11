#!/usr/bin/env python3
"""
Vercel Model Context Protocol (MCP) Server
-----------------------------------------
A standards-compliant MCP server that exposes Vercel platform capabilities
(Projects, Deployments, Logs, Environment Variables) to any MCP-enabled AI client
(Antigravity IDE, Claude Desktop, Cursor, Custom Agents) over stdio JSON-RPC 2.0.

Environment Variables:
  VERCEL_TOKEN     (Required) - Your personal Vercel access token from https://vercel.com/account/tokens
  VERCEL_TEAM_ID   (Optional) - Team/Scope ID if managing team projects
"""

import sys
import os
import json
import logging
from typing import Any, Dict, List, Optional
import httpx

# Configure logging to stderr so stdout remains clean for JSON-RPC 2.0 protocol
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(message)s",
    stream=sys.stderr
)
logger = logging.getLogger("vercel-mcp-server")

VERCEL_API_BASE = "https://api.vercel.com"
VERCEL_TOKEN = os.getenv("VERCEL_TOKEN", "")
VERCEL_TEAM_ID = os.getenv("VERCEL_TEAM_ID", "")

# ── Vercel HTTP Client ─────────────────────────────────────────────────────────

def get_headers() -> Dict[str, str]:
    if not VERCEL_TOKEN:
        raise ValueError("VERCEL_TOKEN environment variable is not set. Get one at https://vercel.com/account/tokens")
    return {
        "Authorization": f"Bearer {VERCEL_TOKEN}",
        "Content-Type": "application/json"
    }

def get_params(extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    params = {}
    if VERCEL_TEAM_ID:
        params["teamId"] = VERCEL_TEAM_ID
    if extra:
        params.update(extra)
    return params

async def vercel_request(method: str, endpoint: str, params: Optional[Dict[str, Any]] = None, json_body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    headers = get_headers()
    all_params = get_params(params)
    url = f"{VERCEL_API_BASE}{endpoint}"
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.request(
            method=method,
            url=url,
            headers=headers,
            params=all_params,
            json=json_body
        )
        if response.status_code >= 400:
            return {
                "error": True,
                "status_code": response.status_code,
                "message": response.text
            }
        return response.json()

# ── Tool Definitions ──────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "vercel_list_projects",
        "description": "List all projects in your Vercel account, including their framework, git repository, and live production domains.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Number of projects to return (default 20, max 100)"
                }
            },
            "required": []
        }
    },
    {
        "name": "vercel_get_project",
        "description": "Get detailed configuration for a specific Vercel project by name or projectId.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "projectIdOrName": {
                    "type": "string",
                    "description": "The unique project ID or project name on Vercel"
                }
            },
            "required": ["projectIdOrName"]
        }
    },
    {
        "name": "vercel_list_deployments",
        "description": "List recent deployments for a Vercel project, showing state (READY, BUILDING, ERROR), branch, commit, and live URLs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "projectId": {
                    "type": "string",
                    "description": "Filter deployments by project ID or name"
                },
                "limit": {
                    "type": "integer",
                    "description": "Number of deployments to return (default 10)"
                },
                "state": {
                    "type": "string",
                    "enum": ["BUILDING", "ERROR", "INITIALIZING", "QUEUED", "READY", "CANCELED"],
                    "description": "Filter by deployment status"
                }
            },
            "required": ["projectId"]
        }
    },
    {
        "name": "vercel_get_deployment",
        "description": "Get full details, diagnostics, error codes, and URLs for a specific deployment ID or URL.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "deploymentIdOrUrl": {
                    "type": "string",
                    "description": "The deployment ID (e.g. dpl_xyz) or deployment host URL"
                }
            },
            "required": ["deploymentIdOrUrl"]
        }
    },
    {
        "name": "vercel_get_build_logs",
        "description": "Retrieve build and runtime error logs for a deployment to diagnose compilation, bundling, or runtime failures.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "deploymentId": {
                    "type": "string",
                    "description": "The deployment ID to fetch logs for"
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum log events to return (default 50)"
                }
            },
            "required": ["deploymentId"]
        }
    },
    {
        "name": "vercel_list_env_vars",
        "description": "List all environment variable keys and target environments (production, preview, development) for a Vercel project.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "projectIdOrName": {
                    "type": "string",
                    "description": "The project ID or name to inspect"
                }
            },
            "required": ["projectIdOrName"]
        }
    },
    {
        "name": "vercel_create_deployment",
        "description": "Trigger a new deployment for an existing project on Vercel from a Git branch.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "The project name on Vercel"
                },
                "gitSource": {
                    "type": "object",
                    "description": "Git repository reference (type: 'github', ref: 'main', repoId: '...')",
                    "properties": {
                        "type": {"type": "string", "enum": ["github", "gitlab", "bitbucket"]},
                        "ref": {"type": "string", "description": "Branch name (e.g. 'main')"},
                        "repoId": {"type": "string", "description": "Repository ID or full repo slug"}
                    },
                    "required": ["type", "ref"]
                }
            },
            "required": ["name"]
        }
    }
]

# ── Tool Execution Handler ───────────────────────────────────────────────────

async def handle_tool_call(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        if name == "vercel_list_projects":
            limit = args.get("limit", 20)
            res = await vercel_request("GET", "/v9/projects", params={"limit": limit})
            if res.get("error"):
                return {"error": res}
            projects = [
                {
                    "id": p.get("id"),
                    "name": p.get("name"),
                    "framework": p.get("framework"),
                    "updatedAt": p.get("updatedAt"),
                    "latestDeployments": [
                        {
                            "id": d.get("id"),
                            "url": d.get("url"),
                            "readyState": d.get("readyState")
                        }
                        for d in p.get("latestDeployments", [])[:2]
                    ]
                }
                for p in res.get("projects", [])
            ]
            return {"count": len(projects), "projects": projects}

        elif name == "vercel_get_project":
            project_id = args["projectIdOrName"]
            res = await vercel_request("GET", f"/v9/projects/{project_id}")
            return res

        elif name == "vercel_list_deployments":
            project_id = args["projectId"]
            limit = args.get("limit", 10)
            params = {"projectId": project_id, "limit": limit}
            if "state" in args:
                params["state"] = args["state"]
            res = await vercel_request("GET", "/v6/deployments", params=params)
            if res.get("error"):
                return {"error": res}
            deployments = [
                {
                    "id": d.get("uid"),
                    "name": d.get("name"),
                    "url": d.get("url"),
                    "state": d.get("state"),
                    "creator": d.get("creator", {}).get("username"),
                    "created": d.get("created"),
                    "meta": {
                        "branch": d.get("meta", {}).get("githubCommitRef"),
                        "commit": d.get("meta", {}).get("githubCommitSha", "")[:7],
                        "message": d.get("meta", {}).get("githubCommitMessage")
                    }
                }
                for d in res.get("deployments", [])
            ]
            return {"count": len(deployments), "deployments": deployments}

        elif name == "vercel_get_deployment":
            dep_id = args["deploymentIdOrUrl"]
            res = await vercel_request("GET", f"/v13/deployments/{dep_id}")
            return res

        elif name == "vercel_get_build_logs":
            dep_id = args["deploymentId"]
            limit = args.get("limit", 50)
            res = await vercel_request("GET", f"/v2/deployments/{dep_id}/events", params={"limit": limit})
            if isinstance(res, list):
                logs = [
                    f"[{e.get('type')}] {e.get('text') or e.get('payload', {}).get('text', '')}"
                    for e in res
                    if e.get("text") or e.get("payload", {}).get("text")
                ]
                return {"deploymentId": dep_id, "logCount": len(logs), "logs": logs}
            return res

        elif name == "vercel_list_env_vars":
            project_id = args["projectIdOrName"]
            res = await vercel_request("GET", f"/v9/projects/{project_id}/env")
            if res.get("error"):
                return {"error": res}
            envs = [
                {
                    "id": e.get("id"),
                    "key": e.get("key"),
                    "target": e.get("target"),
                    "type": e.get("type"),
                    "updatedAt": e.get("updatedAt")
                }
                for e in res.get("envs", [])
            ]
            return {"count": len(envs), "variables": envs}

        elif name == "vercel_create_deployment":
            res = await vercel_request("POST", "/v13/deployments", json_body=args)
            return res

        else:
            return {"error": f"Unknown tool: {name}"}

    except Exception as e:
        logger.error(f"Error executing tool {name}: {e}")
        return {"error": str(e)}

# ── JSON-RPC 2.0 MCP Protocol Processor ──────────────────────────────────────

async def process_mcp_message(msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    msg_id = msg.get("id")
    method = msg.get("method")
    params = msg.get("params", {})

    logger.info(f"Received JSON-RPC method: {method}")

    # 1. Handshake: initialize
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {
                        "listChanged": False
                    }
                },
                "serverInfo": {
                    "name": "vercel-mcp-server",
                    "version": "1.0.0"
                }
            }
        }

    # 2. Notification: initialized
    elif method == "notifications/initialized":
        logger.info("Client successfully initialized MCP session.")
        return None

    # 3. Ping
    elif method == "ping":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {}
        }

    # 4. Tool Discovery: tools/list
    elif method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "tools": TOOLS
            }
        }

    # 5. Tool Invocation: tools/call
    elif method == "tools/call":
        tool_name = params.get("name")
        tool_args = params.get("arguments", {})
        result_data = await handle_tool_call(tool_name, tool_args)
        
        is_error = bool(isinstance(result_data, dict) and result_data.get("error"))
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(result_data, indent=2)
                    }
                ],
                "isError": is_error
            }
        }

    # Unknown method
    else:
        if msg_id is not None:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {
                    "code": -32601,
                    "message": f"Method not found: {method}"
                }
            }
        return None

# ── Main Event Loop ──────────────────────────────────────────────────────────

async def main():
    import asyncio
    
    # Standalone diagnostic mode: python vercel_mcp_server.py --test
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("🔍 Testing Vercel MCP Server...")
        print(f"VERCEL_TOKEN configured: {'YES' if VERCEL_TOKEN else 'NO (Set VERCEL_TOKEN=...)'}")
        print(f"Available Tools ({len(TOOLS)}): {[t['name'] for t in TOOLS]}")
        sys.exit(0)

    logger.info("Vercel MCP Server started on stdio. Waiting for JSON-RPC messages...")
    loop = asyncio.get_event_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin)

    while True:
        line = await reader.readline()
        if not line:
            break
        raw_text = line.decode().strip()
        if not raw_text:
            continue
        try:
            msg = json.loads(raw_text)
            response = await process_mcp_message(msg)
            if response:
                sys.stdout.write(json.dumps(response) + "\n")
                sys.stdout.flush()
        except json.JSONDecodeError:
            logger.warning(f"Malformed JSON received: {raw_text[:100]}")
        except Exception as e:
            logger.error(f"Unexpected error: {e}")

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
