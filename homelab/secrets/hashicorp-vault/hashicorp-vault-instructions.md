# HashiCorp Vault – Practical Setup & Troubleshooting Notes

This document summarizes **everything learned** while setting up HashiCorp Vault **natively (no Docker)** for a **homelab / commercial backend** use case (e.g. ClickHouse credentials, FastAPI, SaaS platform).

It is written as a **memory aid** so future setup is fast and mistake‑free.

---

## 1. What Vault Is Used For (In This Project)

* Centralized **secrets management**
* Secure storage of:

  * Database usernames/passwords (ClickHouse)
  * API keys
  * Tokens
* **Credential rotation** (future)
* Auth backend for:

  * Humans (Vault UI)
  * Services (FastAPI, workers)

---

## 2. Installation Model Chosen

* **Native Linux install** (no Docker)
* Runs as a **systemd service**
* Dedicated `vault` user
* File storage backend
* TLS enabled (self‑signed, homelab‑safe)

Key directories:

```text
/usr/local/bin/vault          # Vault binary
/etc/vault                    # Config + TLS
/var/lib/vault                # Data (keys, storage)
/var/log/vault                # Logs
```

---

## 3. TLS Is Mandatory (And Why Errors Happen)

Vault was configured with:

```hcl
listener "tcp" {
  address = "0.0.0.0:8200"
  tls_disable = 0
}
```

This means:

* Vault **expects HTTPS only**
* Any HTTP request → error

### Common TLS Errors & Meaning

| Error                                      | Meaning                               |
| ------------------------------------------ | ------------------------------------- |
| `Client sent HTTP request to HTTPS server` | Using `http://` instead of `https://` |
| `certificate doesn't contain IP SANs`      | Cert CN/SAN mismatch (IP vs hostname) |
| `certificate signed by unknown authority`  | CA not trusted                        |
| `permission denied opening vault.crt`      | File permissions / wrong path         |

---

## 4. Correct Way to Handle TLS (Golden Path)

### Use a Hostname (Recommended)

```text
vault.local
```

Add to `/etc/hosts`:

```text
127.0.0.1 vault.local
```

### Install Vault Cert into System Trust Store

```bash
sudo cp /etc/vault/tls/vault.crt /usr/local/share/ca-certificates/vault.crt
sudo update-ca-certificates
```

Then **do not use** `VAULT_CACERT` anymore.

### Always Use:

```bash
export VAULT_ADDR=https://vault.local:8200
```

---

## 5. Vault CLI Rules (Very Important)

### ❌ Do NOT

* Run `vault` with `sudo`
* Use `VAULT_SKIP_VERIFY` long‑term
* Use IPs that don’t match cert SANs

### ✅ Do

* Run Vault CLI as your normal user
* Use system CA trust
* Use DNS names

---

## 6. Initialization & Unsealing

### Initialize (Once)

```bash
vault operator init
```

You receive:

* Unseal keys
* Initial **root token**

### Unseal (Every Restart)

```bash
vault operator unseal
vault operator unseal
vault operator unseal
```

---

## 7. Tokens – Core Concept

> **Tokens are issued AFTER authentication**

You do not manually create tokens in normal usage.

### Ways to Get a Token

| Method     | Use case              |
| ---------- | --------------------- |
| Root token | Bootstrap / emergency |
| Userpass   | Human login / UI      |
| AppRole    | APIs & services       |

Token stored locally at:

```text
~/.vault-token
```

---

## 8. Why Everything Was Returning 403

If **every command returns**:

```text
Code: 403 permission denied
```

It means:

* You are **not logged in**
* Or token expired / revoked
* Or token only has `default` policy

### Fix

```bash
vault login <ROOT_TOKEN>
vault token lookup
```

You must see:

```text
policies: ["root"]
```

---

## 9. Setting Up Username + Password Login (Vault UI)

### Step 1 – Login as Root

```bash
vault login <ROOT_TOKEN>
```

### Step 2 – Enable `userpass`

```bash
vault auth enable userpass
```

### Step 3 – Create Admin Policy

```hcl
path "*" {
  capabilities = ["create", "read", "update", "delete", "list", "sudo"]
}
```

```bash
vault policy write admin admin.hcl
```

### Step 4 – Create User

```bash
vault write auth/userpass/users/thomas \
  password="StrongPassword123!" \
  policies="admin"
```

### Step 5 – Login to UI

Open:

```text
https://vault.local:8200
```

Select:

* Method: **Username**
* Enter credentials

---

## 10. Auth Methods – Best Practice Matrix

| Actor           | Auth Method |
| --------------- | ----------- |
| Humans / UI     | userpass    |
| FastAPI backend | AppRole     |
| Background jobs | AppRole     |
| CI/CD           | AppRole     |
| Emergency       | Root token  |

---

## 11. Why Root Token Is Dangerous

* Unlimited permissions
* Single point of failure

### After Setup:

```bash
vault token revoke <ROOT_TOKEN>
```

Then:

* Use admin user instead

---

## 12. Common Pitfalls (Checklist)

* ⛔ Using `sudo vault`
* ⛔ Forgetting to login
* ⛔ Wrong `VAULT_ADDR`
* ⛔ Cert not trusted
* ⛔ Missing `sudo` capability in policy
* ⛔ Using default policy only

---

## 13. When Things Go Really Wrong

### Nuclear Reset (Data Loss!)

```bash
sudo systemctl stop vault
sudo rm -rf /var/lib/vault /etc/vault /var/log/vault
sudo rm /usr/local/bin/vault
```

Then reinstall.

---

## 14. Recommended Next Steps (Future)

* Enable **AppRole** for FastAPI
* Configure **ClickHouse dynamic credentials**
* Add **credential rotation**
* Enable **audit logging**
* Optional: OIDC (Google/GitHub login)

---

## One‑Sentence Mental Model

> Vault is strict by design: if TLS, auth, token, or policy is wrong, it denies everything — once those are correct, it is extremely reliable.
