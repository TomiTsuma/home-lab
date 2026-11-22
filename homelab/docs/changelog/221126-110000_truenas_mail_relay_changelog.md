# Outbound mail relay on TrueNAS (2026-11-22)

**Request:** install a mail server on TrueNAS for sending email.

## Decision
- Checked first: outbound port 25 from TrueNAS is open, but the home IP `102.209.76.168` has **no reverse DNS**.
  Direct sending from a residential IP without reverse DNS gets rejected or spam-foldered by Gmail and Outlook, and only the ISP can fix it.
- Owner chose a **relay through a provider**, with **Cloudflare Email Service** as the provider. `ereuna.org` DNS and Email Routing are already
  on Cloudflare. Its SMTP endpoint is `smtp.mx.cloudflare.net:465` (implicit TLS only), user `api_token`, password = API token with
  *Email Sending: Edit*. Sending to arbitrary recipients needs Workers Paid (3,000/month included).

## What was done
- New TrueNAS **custom app** `mail-relay` (the first custom app on this box): `boky/postfix:5.2.1-alpine` (MIT), host port 587,
  queue on named volume `mail-relay-spool`, 256 MB memory cap. Only `@ereuna.org` senders are allowed. Open to private ranges plus Tailscale `100.64.0.0/10`.
- Repo: `homelab/messaging/mail-relay/` with `docker-compose.yml`, `.env.example`, `deploy.sh` (midclt `app.create`/`app.update` over SSH)
  and `README.md`. TrueNAS README apps table updated.
- `deploy.sh` first failed on two things, both fixed: this midclt spells the flag `--job` (not `-job`), and a comment in the compose mentioned
  the token variable, so the token would also have been written into the comment.

## Verified
- Container healthy, 0 restarts. SMTP from the laptop over the tailnet: an `@ereuna.org` sender is accepted, a `@gmail.com` sender is rejected (`554 5.7.1`).
- Upstream: TLS to Cloudflare on 465 works (`tls=encrypt`). Auth with the **placeholder** token fails as expected (`535 5.7.8`), and the test
  message stays deferred in the queue.

## Not done / needs the owner
- Cloudflare dashboard: Workers Paid, onboard `ereuna.org` for Email Sending, create the token. Then put it in `.env` and run `./deploy.sh`. The queued
  test message to `tomitsuma@ereuna.org` will then go out (delete it with `postsuper -d ALL` if unwanted).
- `wrangler` (npx, latest) crashes on this Windows laptop (workerd binary load error), so onboarding couldn't be done from the CLI.
- TrueNAS's own alert email (`mail.config`) is still unset. It could point at `localhost:587`, from `truenas@ereuna.org`.
- Unrelated, noticed while grepping: `homelab/data-services/superset/docker-compose.yml` line 42 has a plaintext admin password in a comment.
