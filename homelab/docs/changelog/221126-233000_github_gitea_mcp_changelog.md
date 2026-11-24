# GitHub and Gitea MCP for Open WebUI (2026-11-22)

**Request:** install the GitHub MCP and the Gitea MCP, give Open WebUI access; connect Gitea to the instance on TrueNAS.

## Done and verified
- Compose services `github-mcp` (github/github-mcp-server v2.2.0, `http --read-only`) and `gitea-mcp` (gitea-mcp 1.13.0, `-t http -r`, host
  `http://100.100.133.10:30008`), images pinned by digest, private networks `ghmcp`/`gtmcp`, hardened, no secrets in the containers
  (stateless credential passthrough: Open WebUI sends the token per request). Probed with temporary containers first, then deployed.
- Gitea: dedicated read-only token `open-webui-mcp` created on user `aeacus` through the admin CLI inside the Gitea container (root path via the Proxmox
  guest agent), scopes read:repository/issue/organization/user/misc/notification/package; stored only in Open WebUI's connection. Tool list 28 tools,
  none write-capable; `get_me` and a repository search return real data; a write with the token returns HTTP 403.
- Open WebUI: connection `gitea` enabled; connection `github` registered DISABLED with a 13-tool allow-list (the full list is ~15-18k tokens);
  both default-on for the 4 Claude models only (`git-mcp/register_in_openwebui.py`, tokens read from stdin).

## Waiting on the owner
- A GitHub token: the GitHub MCP is installed and reachable (a dummy token reaches api.github.com and gets "401 Bad credentials") but cannot be used
  until a fine-grained read-only PAT is pasted into the connection key and the connection is enabled. Not tested with a real token.

## Notes
- Gitea 1.25.5's `ROOT_URL` has no port, so links in MCP results omit :30008.
- The temporary token file on the laptop was deleted; probe containers removed.
- Rollback: `docker compose rm -sf github-mcp gitea-mcp`, delete both connections in Open WebUI, revoke the Gitea token (Settings > Applications).

## Update: GitHub connected
The owner supplied a fine-grained token; it was registered in the Open WebUI `github` connection through stdin (never written to a file or the repo) and the
connection enabled. Verified through Open WebUI's network: `get_me` = TomiTsuma, `search_repositories` returned 83 public repos. The token is public-repositories
read-only: 0 private repos visible and no organizations (REST `user/orgs` empty), expires 2027-02-20. Public org repos are visible (coreoutline 13, DataExpert-io-Community
57, ereuna 1, Circularity-Space 1). The token appeared in chat; it can be revoked in GitHub > Settings > Developer settings.
