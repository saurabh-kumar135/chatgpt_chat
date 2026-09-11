# Vercel MCP Server

A high-performance Model Context Protocol (MCP) server that connects AI assistants (Antigravity IDE, Claude Desktop, Cursor) directly to the **Vercel REST API**.

With this MCP server, AI assistants can query and manage your Vercel projects, inspect deployments, diagnose build failures through raw build logs, manage environment variables across environments (production, preview, dev), and configure custom domains.

---

## Features & Tool Reference

| Category | Tool | Description |
| :--- | :--- | :--- |
| **Identity & Teams** | `vercel_get_user` | Get profile of authenticated user & verify connectivity |
| | `vercel_list_teams` | List teams and roles accessible to the token |
| **Projects** | `vercel_list_projects` | List projects with filtering, search, and pagination |
| | `vercel_get_project` | Get project details, framework presets, repo links |
| | `vercel_create_project` | Create a new project (specify framework, build cmd, root dir) |
| | `vercel_delete_project` | Delete a project |
| **Deployments & Logs** | `vercel_list_deployments` | List deployments (filter by project, state, target) |
| | `vercel_get_deployment` | Inspect deployment details, aliases, and status |
| | `vercel_cancel_deployment` | Cancel an active or queued deployment build |
| | `vercel_delete_deployment` | Delete a deployment |
| | `vercel_get_build_logs` | Fetch real-time/historical build event logs for debugging failures |
| **Environment Variables** | `vercel_list_env` | List all environment variables for a project |
| | `vercel_create_env` | Add env variable (`production`, `preview`, `development`) |
| | `vercel_delete_env` | Remove an environment variable by ID |
| **Domains & DNS** | `vercel_list_domains` | List domains assigned to a project |
| | `vercel_add_domain` | Add a custom domain or branch preview domain |
| | `vercel_verify_domain` | Trigger verification of DNS records and SSL certs |
| | `vercel_remove_domain` | Remove a domain from a project |
| **Escape Hatch** | `vercel_raw_api` | Execute any arbitrary Vercel REST API endpoint |

---

## Installation & Requirements

### 1. Requirements
- Python 3.10+
- Dependencies: `mcp`, `httpx`, `python-dotenv`

Install dependencies:
```bash
pip install -r requirements.txt
```

### 2. Vercel Token Configuration
1. Go to [Vercel Account Tokens](https://vercel.com/account/tokens) and generate a **Personal Access Token**.
2. Set `VERCEL_TOKEN` in your environment or copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```
   Edit `.env`:
   ```env
   VERCEL_TOKEN=your_personal_access_token
   # Optional: If you work under a team account
   # VERCEL_TEAM_ID=team_xxxxxxxxxxxxxxxxxxxx
   ```

---

## Configuration for MCP Clients

### 1. Antigravity IDE
Add the server to your `C:\Users\Saura\.gemini\config\mcp_config.json`:

```json
{
  "mcpServers": {
    "vercel": {
      "command": "python",
      "args": [
        "c:/Users/Saura/claude_writing/vercel-mcp-server/server.py"
      ],
      "env": {
        "VERCEL_TOKEN": "your_token_here",
        "VERCEL_TEAM_ID": ""
      }
    }
  }
}
```

### 2. Claude Desktop
Add the server to your `claude_desktop_config.json` (`%APPDATA%\Claude\claude_desktop_config.json` on Windows):

```json
{
  "mcpServers": {
    "vercel": {
      "command": "python",
      "args": [
        "C:\\Users\\Saura\\claude_writing\\vercel-mcp-server\\server.py"
      ],
      "env": {
        "VERCEL_TOKEN": "your_token_here",
        "VERCEL_TEAM_ID": ""
      }
    }
  }
}
```

### 3. Cursor
In Cursor Settings > Features > MCP:
- **Type**: `command`
- **Command**: `python c:/Users/Saura/claude_writing/vercel-mcp-server/server.py`

---

## Running Locally / Testing

You can test the server directly from the command line:

```bash
# Check tool list
python -c "import asyncio; from server import mcp; print(asyncio.run(mcp.list_tools()))"

# Run server in stdio mode (default for MCP clients)
python server.py
```
