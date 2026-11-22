# mail-relay: outbound email for the homelab

A Postfix relay on TrueNAS. Anything in the homelab can send mail as `…@ereuna.org`
by talking plain SMTP to **`100.100.133.10:587`** (tailnet) or **`192.168.100.10:587`** (LAN).
Postfix hands the mail to **Cloudflare Email Service**, which signs it (DKIM) and delivers it.

```
app / script / NAS alert ──SMTP:587, no auth──▶ mail-relay (TrueNAS, Postfix)
                                                     │ implicit TLS :465, AUTH api_token:<CF token>
                                                     ▼
                                     smtp.mx.cloudflare.net ──▶ recipient inbox
```

## Why a relay and not a direct mail server
Checked 2026-11-22: outbound port 25 is open, but the home IP (`102.209.76.168`) has **no
reverse DNS** and is a residential range. Gmail and Outlook reject or spam-folder mail from
an IP like that, and only the ISP can set reverse DNS. A relay leaves the sending reputation to
Cloudflare. `ereuna.org` DNS and inbound mail (Email Routing) are already on Cloudflare,
so no new vendor is needed. Alternatives considered: Amazon SES (cheapest at volume),
Brevo (300/day free), Gmail app password (From must be Gmail).

## Using it
| Setting | Value |
|---|---|
| Host | `100.100.133.10` (tailnet) / `192.168.100.10` (LAN) / `10.77.0.1` (wgbak, if routed) |
| Port | `587` |
| Encryption | none (plain SMTP inside the private network; the hop to Cloudflare is TLS) |
| Auth | none. Access is by source network (`POSTFIX_mynetworks`) |
| From | must be `something@ereuna.org`. Other sender domains are rejected with `554 Access denied` |

```sh
# quick test from any homelab host
python3 - <<'EOF'
import smtplib; from email.message import EmailMessage
m = EmailMessage(); m["From"]="homelab@ereuna.org"; m["To"]="you@example.com"
m["Subject"]="hello from the homelab"; m.set_content("it works")
smtplib.SMTP("100.100.133.10", 587).send_message(m)
EOF
```

## One-time Cloudflare setup (dashboard, account owner)
1. **Workers Paid plan** on the account. Without it, Email Sending only delivers to
   verified destination addresses. With it, 3,000 emails/month are included, then $0.35 per 1,000.
2. **Email Service → Email Sending → onboard `ereuna.org`**. This adds `cf-bounce` MX/SPF/DKIM records and a
   DMARC record if none exists. It doesn't touch the apex MX/SPF used by Email Routing.
3. **Create an API token**: Manage Account → Account API Tokens → *Email Sending: Edit*.
4. Put it in `.env` (copy `.env.example`) and run `./deploy.sh`. This updates the app in place.
   Anything still in the queue is retried automatically (or run `postqueue -f` in the container).

## Deploy / recreate
`./deploy.sh` reads `.env`, fills the token into `docker-compose.yml` and calls
`midclt call --job app.create|app.update` over SSH as `truenas_admin`. TrueNAS custom apps
don't read `.env` files, so the token ends up only in TrueNAS's root-only app config.

## Operating it
Docker logs need root. `truenas_admin` can't reach the docker socket, so go through Proxmox:
```sh
ssh root@100.124.198.112 'qm guest exec 102 -- docker logs --tail 50 mail-relay'
ssh root@100.124.198.112 'qm guest exec 102 -- docker exec mail-relay postqueue -p'   # queue
ssh root@100.124.198.112 'qm guest exec 102 -- docker exec mail-relay postqueue -f'   # retry now
```
- `status=sent`: Cloudflare accepted it. `status=deferred ... 535 5.7.8`: token wrong or missing.
- Limits (Cloudflare): 50 recipients/message, 25 MiB/message, new accounts get a small daily quota that grows.
  Transactional mail only, not newsletters.
- Port 587 is **not** forwarded on the router. Keep it that way: the relay has no auth.
