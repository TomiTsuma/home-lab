# mail-relay upstream: Cloudflare -> Brevo free plan (2026-11-22)

**Request:** the owner does not want to pay for Workers Paid, which Cloudflare Email Service needs to send to arbitrary recipients
(see `221126-110000_truenas_mail_relay_changelog.md`). The owner chose a free provider that can send to anyone.

## What changed
- `docker-compose.yml`: `RELAYHOST=[smtp-relay.brevo.com]:587` with STARTTLS (`smtp_tls_security_level=encrypt`). Implicit-TLS wrappermode is no longer used.
  Size limit is now 20 MiB (Brevo advertises `SIZE 20971520`). Same image, port, sender policy and networks.
- Credentials are now `BREVO_SMTP_LOGIN` and `BREVO_SMTP_KEY` (`.env.example`, `deploy.sh`). The README is rewritten for Brevo.
- Redeployed with `deploy.sh` (`app.update`) using placeholder credentials.
- Deleted the stale Cloudflare test message `00A8AEA565` from the queue (`postsuper -d`).

## Verified
- From TrueNAS: `smtp-relay.brevo.com` (172.246.243.66) offers STARTTLS on 587 with TLS 1.3 and `AUTH CRAM-MD5 PLAIN LOGIN`.
- After the redeploy: container healthy, 0 restarts. The running relayhost is `[smtp-relay.brevo.com]:587` and wrappermode is `no`. A new test message
  (`E65E6EACE7`, From `homelab@ereuna.org`, To `tomitsuma@ereuna.org`) was accepted. It is deferred with `tls=encrypt` and SASL failed, which is
  expected with placeholder credentials. It will go out once real credentials are deployed.

## Needs the owner
- Brevo signup, authenticate `ereuna.org` (add Brevo's TXT records in Cloudflare DNS), create an SMTP key, fill `.env`, run `./deploy.sh`.
- Not verified, because the Brevo help center returned 403: the free-plan daily cap (believed to be 300/day) and whether new accounts need activation before
  SMTP works.

## Go-live (same day, 11:08 EAT)
- Owner supplied the Brevo SMTP login and key. They are in `messaging/mail-relay/.env` on the laptop (gitignored, chmod 600), deployed with `./deploy.sh`.
- The queued test `E65E6EACE7` was delivered: `status=sent (250 2.0.0 OK: queued as <…@smtp-relay.sendinblue.com>)`, `tls=encrypt`. The queue is empty.
- DNS was already authenticated by the owner: root TXT `brevo-code:…`, DKIM `brevo1._domainkey` CNAME → `b1.ereuna-org.dkim.brevo.com`, and
  `_dmarc` = `v=DMARC1; p=none; rua=mailto:rua@dmarc.brevo.com`. The apex SPF only includes Cloudflare. That is fine, because DMARC passes on the
  aligned Brevo DKIM signature, and the envelope sender is Brevo's own domain.
- Brevo daily cap and account activation status were still not checked from the dashboard.
