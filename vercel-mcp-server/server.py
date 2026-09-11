"""
Vercel MCP Server
=================
Model Context Protocol (MCP) server providing deep integration with the Vercel REST API.

Enables AI assistants (Antigravity IDE, Claude Desktop, Cursor, etc.) to:
- Inspect and manage Projects (list, details, create, delete)
- Manage Deployments (list, inspect, cancel, delete)
- Fetch and analyze Build Logs / Events
- Manage Project Environment Variables (list, add, delete across production, preview, dev)
- Manage Domains and verify DNS/SSL status
- Inspect authenticated User & Teams

Configuration:
  VERCEL_TOKEN    - Vercel Personal Access Token (https://vercel.com/account/tokens)
  VERCEL_TEAM_ID  - (Optional) Team ID or team slug for team-scoped resources
"""

import json
import os
import sys
from typing import Any, Dict, List, Optional
import httpx
from mcp.server.fastmcp import FastMCP

# Optional dotenv loading if python-dotenv is installed
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

VERCEL_API_BASE = "https://api.vercel.com"
VERCEL_TOKEN = os.environ.get("VERCEL_TOKEN", "").strip()
VERCEL_TEAM_ID = os.environ.get("VERCEL_TEAM_ID", "").strip()

# Initialize FastMCP server
mcp = FastMCP(
    name="vercel",
    description="MCP Server for managing Vercel projects, deployments, build logs, environment variables, and domains."
)


def _get_auth_headers() -> Dict[str, str]:
    token = os.environ.get("VERCEL_TOKEN", VERCEL_TOKEN).strip()
    if not token:
        raise ValueError(
            "VERCEL_TOKEN is not configured. Please set the VERCEL_TOKEN environment "
            "variable with your Vercel personal access token from https://vercel.com/account/tokens"
        )
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "vercel-mcp-server/1.0"
    }


async def _api_request(
    method: str,
    path: str,
    params: Optional[Dict[str, Any]] = None,
    json_data: Optional[Dict[str, Any]] = None,
    team_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute an HTTP request against the Vercel REST API."""
    try:
        headers = _get_auth_headers()
    except ValueError as e:
        return {
            "error": True,
            "code": "AUTH_CONFIG_MISSING",
            "message": str(e),
        }

    url = f"{VERCEL_API_BASE}{path}" if path.startswith("/") else f"{VERCEL_API_BASE}/{path}"

    query_params: Dict[str, Any] = {}
    if params:
        for k, v in params.items():
            if v is not None:
                query_params[k] = v

    effective_team = team_id if team_id is not None else os.environ.get("VERCEL_TEAM_ID", VERCEL_TEAM_ID).strip()
    if effective_team and "teamId" not in query_params and "slug" not in query_params:
        query_params["teamId"] = effective_team

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.request(
                method=method.upper(),
                url=url,
                headers=headers,
                params=query_params if query_params else None,
                json=json_data if json_data is not None else None,
            )

            if response.status_code == 204:
                return {"status": 204, "message": "Success (No Content)"}

            try:
                data = response.json()
            except Exception:
                data = {"raw_text": response.text}

            if not response.is_success:
                error_msg = data.get("error", {}).get("message", response.text) if isinstance(data, dict) else response.text
                error_code = data.get("error", {}).get("code", f"HTTP_{response.status_code}") if isinstance(data, dict) else f"HTTP_{response.status_code}"
                return {
                    "error": True,
                    "status_code": response.status_code,
                    "code": error_code,
                    "message": error_msg,
                    "details": data,
                }

            return data

        except httpx.RequestError as exc:
            return {
                "error": True,
                "message": f"Network error connecting to Vercel API: {str(exc)}",
                "url": url,
            }


# ============================================================================
# ACCOUNT & TEAM TOOLS
# ============================================================================

@mcp.tool()
async def vercel_get_user() -> str:
    """Get information about the currently authenticated Vercel user and verify API connection."""
    res = await _api_request("GET", "/v2/user")
    return json.dumps(res, indent=2)


@mcp.tool()
async def vercel_list_teams(limit: int = 20) -> str:
    """List all teams that the authenticated user belongs to.

    Args:
        limit: Maximum number of teams to return (default: 20).
    """
    res = await _api_request("GET", "/v2/teams", params={"limit": limit})
    return json.dumps(res, indent=2)


# ============================================================================
# PROJECT TOOLS
# ============================================================================

@mcp.tool()
async def vercel_list_projects(
    limit: int = 20,
    search: Optional[str] = None,
    team_id: Optional[str] = None
) -> str:
    """List Vercel projects for the authenticated user or team.

    Args:
        limit: Number of projects to return (default: 20).
        search: Optional search term to filter projects by name.
        team_id: Optional team ID or slug to override the default team.
    """
    params: Dict[str, Any] = {"limit": limit}
    if search:
        params["search"] = search

    res = await _api_request("GET", "/v10/projects", params=params, team_id=team_id)
    return json.dumps(res, indent=2)


@mcp.tool()
async def vercel_get_project(
    project_id_or_name: str,
    team_id: Optional[str] = None
) -> str:
    """Get detailed information about a specific Vercel project.

    Args:
        project_id_or_name: The ID or name of the project.
        team_id: Optional team ID or slug to override the default team.
    """
    res = await _api_request("GET", f"/v10/projects/{project_id_or_name}", team_id=team_id)
    return json.dumps(res, indent=2)


@mcp.tool()
async def vercel_create_project(
    name: str,
    framework: Optional[str] = None,
    root_directory: Optional[str] = None,
    build_command: Optional[str] = None,
    output_directory: Optional[str] = None,
    team_id: Optional[str] = None
) -> str:
    """Create a new Vercel project.

    Args:
        name: Name of the project.
        framework: Framework preset (e.g., 'nextjs', 'vite', 'remix', 'svelte', 'nuxt').
        root_directory: Root directory in the repository where source code is located.
        build_command: Custom build command.
        output_directory: Custom output directory.
        team_id: Optional team ID or slug to create the project under.
    """
    payload: Dict[str, Any] = {"name": name}
    if framework:
        payload["framework"] = framework
    if root_directory:
        payload["rootDirectory"] = root_directory
    if build_command:
        payload["buildCommand"] = build_command
    if output_directory:
        payload["outputDirectory"] = output_directory

    res = await _api_request("POST", "/v10/projects", json_data=payload, team_id=team_id)
    return json.dumps(res, indent=2)


@mcp.tool()
async def vercel_delete_project(
    project_id_or_name: str,
    team_id: Optional[str] = None
) -> str:
    """Delete a Vercel project.

    Args:
        project_id_or_name: The ID or name of the project to delete.
        team_id: Optional team ID or slug.
    """
    res = await _api_request("DELETE", f"/v10/projects/{project_id_or_name}", team_id=team_id)
    return json.dumps(res, indent=2)


# ============================================================================
# DEPLOYMENT TOOLS
# ============================================================================

@mcp.tool()
async def vercel_list_deployments(
    project_id_or_name: Optional[str] = None,
    limit: int = 20,
    state: Optional[str] = None,
    target: Optional[str] = None,
    team_id: Optional[str] = None
) -> str:
    """List deployments for a project or account.

    Args:
        project_id_or_name: Optional filter for a specific project ID or name.
        limit: Number of deployments to return (default: 20, max: 100).
        state: Filter by state ('BUILDING', 'ERROR', 'INITIALIZING', 'QUEUED', 'READY', 'CANCELED').
        target: Filter by target environment ('production' or 'preview').
        team_id: Optional team ID or slug.
    """
    params: Dict[str, Any] = {"limit": limit}
    if project_id_or_name:
        params["projectId"] = project_id_or_name
    if state:
        params["state"] = state.upper()
    if target:
        params["target"] = target

    res = await _api_request("GET", "/v7/deployments", params=params, team_id=team_id)
    return json.dumps(res, indent=2)


@mcp.tool()
async def vercel_get_deployment(
    deployment_id_or_url: str,
    team_id: Optional[str] = None
) -> str:
    """Get details of a specific deployment by ID or deployment URL.

    Args:
        deployment_id_or_url: Deployment ID (e.g. 'dpl_...') or deployment host URL.
        team_id: Optional team ID or slug.
    """
    res = await _api_request("GET", f"/v13/deployments/{deployment_id_or_url}", team_id=team_id)
    return json.dumps(res, indent=2)


@mcp.tool()
async def vercel_cancel_deployment(
    deployment_id: str,
    team_id: Optional[str] = None
) -> str:
    """Cancel a currently queued or building deployment.

    Args:
        deployment_id: The unique ID of the deployment to cancel.
        team_id: Optional team ID or slug.
    """
    res = await _api_request("PATCH", f"/v12/deployments/{deployment_id}/cancel", team_id=team_id)
    return json.dumps(res, indent=2)


@mcp.tool()
async def vercel_delete_deployment(
    deployment_id: str,
    team_id: Optional[str] = None
) -> str:
    """Delete a deployment.

    Args:
        deployment_id: The ID of the deployment to delete.
        team_id: Optional team ID or slug.
    """
    res = await _api_request("DELETE", f"/v13/deployments/{deployment_id}", team_id=team_id)
    return json.dumps(res, indent=2)


@mcp.tool()
async def vercel_get_build_logs(
    deployment_id_or_url: str,
    limit: int = 100,
    team_id: Optional[str] = None
) -> str:
    """Get build logs and events for a deployment (essential for debugging build failures).

    Args:
        deployment_id_or_url: The deployment ID or URL.
        limit: Maximum number of log events to retrieve (default: 100).
        team_id: Optional team ID or slug.
    """
    params = {"limit": limit}
    res = await _api_request("GET", f"/v3/deployments/{deployment_id_or_url}/events", params=params, team_id=team_id)

    # Format events for clean readability if list of events
    if isinstance(res, list):
        formatted_lines = []
        for event in res:
            if isinstance(event, dict):
                payload = event.get("payload", {})
                text = payload.get("text") if isinstance(payload, dict) else str(payload)
                if not text:
                    text = event.get("text", "")
                timestamp = event.get("created", "")
                formatted_lines.append(f"[{timestamp}] {text}" if timestamp else text)
            else:
                formatted_lines.append(str(event))
        return "\n".join(formatted_lines) if formatted_lines else "No build events found."

    return json.dumps(res, indent=2)


# ============================================================================
# ENVIRONMENT VARIABLES TOOLS
# ============================================================================

@mcp.tool()
async def vercel_list_env(
    project_id_or_name: str,
    team_id: Optional[str] = None
) -> str:
    """List environment variables for a project.

    Args:
        project_id_or_name: The ID or name of the project.
        team_id: Optional team ID or slug.
    """
    res = await _api_request("GET", f"/v9/projects/{project_id_or_name}/env", team_id=team_id)
    return json.dumps(res, indent=2)


@mcp.tool()
async def vercel_create_env(
    project_id_or_name: str,
    key: str,
    value: str,
    target: Optional[List[str]] = None,
    env_type: str = "encrypted",
    team_id: Optional[str] = None
) -> str:
    """Create an environment variable for a project.

    Args:
        project_id_or_name: The ID or name of the project.
        key: Environment variable name (e.g., 'DATABASE_URL').
        value: Environment variable value.
        target: Target environments. List containing any of: ['production', 'preview', 'development'].
                Defaults to ['production', 'preview', 'development'].
        env_type: Type of variable: 'encrypted', 'plain', or 'secret' (default: 'encrypted').
        team_id: Optional team ID or slug.
    """
    effective_targets = target if target else ["production", "preview", "development"]
    payload = {
        "key": key,
        "value": value,
        "type": env_type,
        "target": effective_targets,
    }
    res = await _api_request("POST", f"/v10/projects/{project_id_or_name}/env", json_data=payload, team_id=team_id)
    return json.dumps(res, indent=2)


@mcp.tool()
async def vercel_delete_env(
    project_id_or_name: str,
    env_id: str,
    team_id: Optional[str] = None
) -> str:
    """Delete an environment variable from a project.

    Args:
        project_id_or_name: The ID or name of the project.
        env_id: The ID of the environment variable (from vercel_list_env).
        team_id: Optional team ID or slug.
    """
    res = await _api_request("DELETE", f"/v9/projects/{project_id_or_name}/env/{env_id}", team_id=team_id)
    return json.dumps(res, indent=2)


# ============================================================================
# DOMAIN MANAGEMENT TOOLS
# ============================================================================

@mcp.tool()
async def vercel_list_domains(
    project_id_or_name: str,
    limit: int = 20,
    team_id: Optional[str] = None
) -> str:
    """List domains associated with a project.

    Args:
        project_id_or_name: The ID or name of the project.
        limit: Number of domains to return (default: 20).
        team_id: Optional team ID or slug.
    """
    res = await _api_request("GET", f"/v9/projects/{project_id_or_name}/domains", params={"limit": limit}, team_id=team_id)
    return json.dumps(res, indent=2)


@mcp.tool()
async def vercel_add_domain(
    project_id_or_name: str,
    domain: str,
    redirect: Optional[str] = None,
    git_branch: Optional[str] = None,
    team_id: Optional[str] = None
) -> str:
    """Add a custom domain to a project.

    Args:
        project_id_or_name: The ID or name of the project.
        domain: Domain name to assign (e.g. 'app.example.com').
        redirect: Optional domain to redirect traffic to.
        git_branch: Optional git branch to link this domain to (for branch preview domains).
        team_id: Optional team ID or slug.
    """
    payload: Dict[str, Any] = {"name": domain}
    if redirect:
        payload["redirect"] = redirect
    if git_branch:
        payload["gitBranch"] = git_branch

    res = await _api_request("POST", f"/v10/projects/{project_id_or_name}/domains", json_data=payload, team_id=team_id)
    return json.dumps(res, indent=2)


@mcp.tool()
async def vercel_verify_domain(
    project_id_or_name: str,
    domain: str,
    team_id: Optional[str] = None
) -> str:
    """Verify DNS and SSL certificate configuration for a project domain.

    Args:
        project_id_or_name: The ID or name of the project.
        domain: Domain name to verify.
        team_id: Optional team ID or slug.
    """
    res = await _api_request("POST", f"/v9/projects/{project_id_or_name}/domains/{domain}/verify", team_id=team_id)
    return json.dumps(res, indent=2)


@mcp.tool()
async def vercel_remove_domain(
    project_id_or_name: str,
    domain: str,
    team_id: Optional[str] = None
) -> str:
    """Remove a domain from a project.

    Args:
        project_id_or_name: The ID or name of the project.
        domain: Domain name to remove.
        team_id: Optional team ID or slug.
    """
    res = await _api_request("DELETE", f"/v9/projects/{project_id_or_name}/domains/{domain}", team_id=team_id)
    return json.dumps(res, indent=2)


# ============================================================================
# ARBITRARY API ESCAPE HATCH
# ============================================================================

@mcp.tool()
async def vercel_raw_api(
    method: str,
    endpoint: str,
    params: Optional[Dict[str, Any]] = None,
    body: Optional[Dict[str, Any]] = None,
    team_id: Optional[str] = None
) -> str:
    """Execute an arbitrary Vercel REST API endpoint not covered by specific tools.

    Args:
        method: HTTP method (GET, POST, PATCH, PUT, DELETE).
        endpoint: API path (e.g. '/v1/edge-config', '/v2/deployments').
        params: Optional query parameters dictionary.
        body: Optional JSON request payload dictionary.
        team_id: Optional team ID or slug.
    """
    res = await _api_request(method, endpoint, params=params, json_data=body, team_id=team_id)
    return json.dumps(res, indent=2)


if __name__ == "__main__":
    # Runs the MCP server with stdio transport
    mcp.run()
