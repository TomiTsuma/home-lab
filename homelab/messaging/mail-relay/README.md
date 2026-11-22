# mail-relay: outbound email for the homelab

A Postfix relay on TrueNAS. Anything in the homelab can send mail as `…@ereuna.org`
by talking plain SMTP to **`100.100.133.10:587`** (tailnet) or **`192.168.100.10:587`** (LAN).
Postfix hands the mail to **Brevo (free plan)**, which signs it with DKIM for ereuna.org and delivers it.

```
app / script / NAS alert ──SMTP:587, no auth──▶ mail-relay (TrueNAS, Postfix)
                                                     │ STARTTLS :587, AUTH <login>:<SMTP key>
                                                     ▼
                                       smtp-relay.brevo.com ──▶ recipient inbox
```

## Why a relay, and why Brevo
- Checked 2026-11-22: outbound port 25 is open, but the home IP (`102.209.76.168`) has **no reverse DNS**
  and is a residential range. Gmail and Outlook reject or spam-folder mail like that, and only the ISP can set reverse DNS.
- Cloudflare Email Service was tried first (DNS and Email Routing are already on Cloudflare). Sending to arbitrary
  recipients needs **Workers Paid**, which the owner declined. On the free plan it only delivers to verified addresses.
- Brevo's free plan sends to anyone from an authenticated custom domain. Other options considered: Amazon SES
  (about $0.10 per 1,000, not free) and a Gmail app password (From has to be Gmail).
- Swapping providers later only means changing `RELAYHOST` and the credentials in `docker-compose.yml`.

## Using it
| Setting | Value |
|---|---|
| Host | `100.100.133.10` (tailnet) / `192.168.100.10` (LAN) |
| Port | `587` |
| Encryption | none (plain SMTP inside the private network; the hop to Brevo is TLS) |
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

## One-time Brevo setup (owner)
1. Sign up at brevo.com (free plan).
2. **Senders, Domains & Dedicated IPs → Domains → Add `ereuna.org` → Authenticate**. Brevo shows TXT records
   to add in Cloudflare DNS: `brevo-code:…` at the root, DKIM as CNAMEs `brevo1._domainkey` / `brevo2._domainkey` → `*.dkim.brevo.com`,
   and DMARC at `_dmarc` if none exists. Don't change the existing apex SPF/MX records used by Cloudflare Email Routing,
   except to add `include:spf.brevo.com` to the SPF record if Brevo asks for it.
3. **SMTP & API → SMTP tab**: copy the **Login** and generate an **SMTP key** (not an API key).
4. Copy `.env.example` to `.env`, fill in both values, and run `./deploy.sh`. This updates the app in place. Queued mail
   is retried automatically (or run `postqueue -f`).

## Deploy / recreate
`./deploy.sh` reads `.env`, fills the credentials into `docker-compose.yml` and calls
`midclt call --job app.create|app.update` over SSH as `truenas_admin`. TrueNAS custom apps
don't read `.env` files, so the credentials end up only in TrueNAS's root-only app config.

## Operating it
Docker logs need root. `truenas_admin` can't reach the docker socket, so go through Proxmox:
```sh
ssh root@100.124.198.112 'qm guest exec 102 -- docker logs --tail 50 mail-relay'
ssh root@100.124.198.112 'qm guest exec 102 -- docker exec mail-relay postqueue -p'   # queue
ssh root@100.124.198.112 'qm guest exec 102 -- docker exec mail-relay postqueue -f'   # retry now
```
- `status=sent`: Brevo accepted it. `status=deferred ... SASL authentication failed`: login or SMTP key wrong or missing.
- Brevo's free plan has a daily send cap (300/day at the time of writing; check the Brevo dashboard). Max message size is 20 MiB.
- Port 587 is **not** forwarded on the router. Keep it that way: the relay has no auth.
