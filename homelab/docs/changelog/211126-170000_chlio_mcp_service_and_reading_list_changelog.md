# Chlio: MCP as its own compose service, reading list, merged to main (2026-11-21)

- **Live stack on minos** (`~/Chlio`, branch `main` at 1a9477c) now runs two containers from one image: `clio-app` (frontend + API) and
  `clio-mcp` (MCP server, loopback :4192, health-checked, read-only, no capabilities, only the Jira/Google env). `/mcp` on
  https://chlio.ereuna.org is unchanged: the app forwards it. Claude Desktop / Open WebUI configs need no change.
- **Reading list:** MCP group `papers` (`paper_add` with project name, DOI/arXiv/title de-duplication, `papers_list`, `paper_update`,
  `paper_remove`) plus a "Reading list" section on each project page. Chlio stores papers; it does not search databases, so Claude
  Desktop needs a paper-search MCP or web search next to Chlio.
- **Incident during rollout:** a stale browser tab on the old frontend wiped the new `readingList` key (it saves its whole state without
  keys it does not know). Fixed in the server (keys a client never sends are kept); regression test added. Reload open Chlio tabs after deploys.
- **Rollback:** `docker tag ttsu/chlio:split-1 ttsu/chlio:latest` (or `:mcp-1` plus the old compose in `/tmp/docker-compose.pre-split.yml`),
  `docker compose up -d`; DB backups `clio.db.bak-pre-split` / `-pre-mcp` in the `clio-data` volume.
- **Not done:** Docker Hub push (needs `docker login -u ttsu` on minos).
- Details: `Chlio/docs/changelog/211126-143000_mcp_service_compose_changelog.md` and `211126-163000_reading_list_papers_changelog.md`.
