# Ollama

Ollama is **not** installed natively anywhere in this homelab — it runs as
part of the Docker Compose stack in
[`../../ai/open-webui/`](../../ai/open-webui/) on `aeacus-dl` (Proxmox
VM 101, GPU passthrough), alongside a LiteLLM proxy (for Claude/Anthropic
access through the same UI) and Open WebUI itself.

See that directory's `README.md` for:

- The full stack architecture and why LiteLLM is there.
- Deployment steps.
- A hard-won list of GPU-passthrough gotchas (cgroup driver, persistence
  mode, VM memory sizing, cold-load timing) that caused real multi-hour
  debugging sessions when not known in advance.

`setup.sh` in this directory is intentionally a no-op placeholder — see
`../../ai/open-webui/host-gpu-prep.sh` for the actual host-prep script.
