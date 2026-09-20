# Setting up the server, step by step

Written for someone who has not done this before. Every step says what to click
and what you should see. If something does not match, stop there and say so —
do not improvise past it.

Total time: about 30 minutes of your attention, plus waiting.

---

## Why the Hetzner page said "not available"

Nothing was wrong. **That page is the price list, not the order form.** It has
a location filter, and the CX and CAX server lines only exist in the European
datacentres (Nuremberg, Falkenstein, Helsinki). The banner at the top — *"We
noticed you're browsing from a different part of the world"* — means the page
had defaulted to a non-EU location, so every EU-only server showed as
unavailable.

Notice that when you expanded CX23, it did show `eu-central 🇩🇪NBG1 🇫🇮HEL1`.
Those are Nuremberg and Helsinki. It is available; the filter was just pointed
somewhere else.

You order from **`console.hetzner.cloud`**, which is a different site. That is
where the real availability lives.

---

## Step 1 — Create the account

1. Go to **`https://accounts.hetzner.com/signUp`**
2. Sign up with your email, confirm the link they send.
3. Add a payment method (card or SEPA direct debit).

**Expect a delay here.** Hetzner verifies new accounts, and for a first-time
customer they sometimes ask for ID before they will let you create anything.
This can take a few hours, occasionally a day. It is normal and it is not a
problem with your account. Start this step early, then come back.

---

## Step 2 — Make an SSH key on your iPad

You need a key to log in. Passwords are switched off on this server by design —
it will hold credentials to your brokerage account.

1. Install **Termius** from the App Store (free tier is enough).
2. Open it → **Keychain** (bottom bar) → **+** → **Generate key**.
3. Type: **ED25519**. Name it `tradelab`. Tap **Generate**.
4. Tap the key → **Export** / **Copy public key**. You now have a line of text
   starting `ssh-ed25519 AAAA…`. That is the **public** half and is safe to
   paste into websites.

> The **private** key never leaves the iPad and is never pasted anywhere. If a
> site ever asks for a key beginning `-----BEGIN OPENSSH PRIVATE KEY-----`,
> something is wrong.

---

## Step 3 — Create the server

1. Go to **`https://console.hetzner.cloud`** and log in.
2. **+ New project** → name it `trading` → open it.
3. Click **Add Server**.

Now fill in the form:

| Field | Choose | Why |
|---|---|---|
| **Location** | **Helsinki (hel1)** 🇫🇮 | Finland. Latency is irrelevant for daily bars, but there is no reason not to |
| **Image** | **Ubuntu 24.04** | What the setup script expects |
| **Type** | **Shared vCPU** → **x86 (Intel/AMD)** → **CX23** | 2 vCPU, 4 GB RAM, 40 GB. **4 GB is the number that matters** — IB Gateway is Java and wants 1–2 GB to itself. A 2 GB server will run out of memory mid-session |
| **Networking** | Leave **IPv4** ticked | Included in the price |
| **SSH keys** | **Add SSH key** → paste the `ssh-ed25519 AAAA…` line from Step 2 | This is how you log in |
| **Volumes, Firewalls, Backups** | Skip | The script sets up its own firewall |
| **Cloud config** | Open it and **paste the entire contents of `deploy/cloud-init.yaml`** | This is the whole setup, done automatically |
| **Name** | `tradelab` | |

Click **Create & Buy now**.

> **The Cloud config box is the important one.** Paste the file exactly as it
> is — do not edit it, and do not retype it. It installs Docker, sets the
> timezone to Helsinki, turns off password login, enables the firewall and
> creates your user, all on first boot. You will not have to type any of it.

You should see a server appear with an IP address like `95.216.x.x`. **Write
that IP down.**

---

## Step 4 — Wait, then log in

Give it **5 minutes**. The script is installing packages.

In Termius:

1. **Hosts** → **+** → **New Host**
2. Address: your server's IP · Username: `tradelab` · Key: `tradelab`
3. Tap to connect. Accept the fingerprint warning the first time.

You should land at a prompt like `tradelab@tradelab:~$`.

Check the setup actually finished:

```bash
cat /var/log/tradelab-ready
```

**If it prints `setup complete …`, everything worked.** If the file does not
exist, wait two more minutes and try again; if it still isn't there, stop and
tell me.

> **Locked out?** You cannot be, permanently. Hetzner Console → your server →
> **Console** button opens a screen-share into the machine that does not use
> SSH at all.

---

## Step 5 — Install the trading system

Still in Termius, paste these one block at a time:

```bash
git clone https://github.com/Jere-stack/Trading.git /opt/tradelab
cd /opt/tradelab
git checkout claude/stock-trading-system-build-0jpmir
```

If it asks for a username and password, the repository is private — tell me and
I will give you the access-token route instead.

```bash
source ~/.local/bin/env
uv venv
uv pip install -e ".[ibkr,research]"
```

That takes a minute or two. Then check it works:

```bash
.venv/bin/python -m pytest tests/ -q
```

**You should see a row of dots and no failures.**

---

## Step 6 — Add your IBKR credentials

```bash
cp deploy/.env.example deploy/.env
chmod 600 deploy/.env
nano deploy/.env
```

`nano` is a text editor. Arrow keys to move, type normally. Fill in:

```
TWS_USERID=your_ibkr_username
TWS_PASSWORD=your_ibkr_password
TRADING_MODE=paper
API_PORT=4002
READ_ONLY_API=yes
```

Save and exit: **Ctrl-O**, **Enter**, **Ctrl-X**.

> `TRADING_MODE=paper` and `READ_ONLY_API=yes` together mean this cannot place
> a real order even if everything else is misconfigured. Leave both until the
> smoke test passes.

---

## Step 7 — Start IB Gateway

```bash
cd /opt/tradelab/deploy
docker compose --env-file .env up -d
docker compose logs -f
```

Watch the log. **Your phone will get an IBKR 2FA prompt — approve it.**

This is the step that might not work, and it is the real reason to do all this
now rather than later: an automated process cannot tap your phone every day. We
need to find out whether the approval sticks across the gateway's nightly
restart. If it does, the system can run unattended. If it does not, we need a
different approach — and it is much better to learn that tonight than in week
three.

Press **Ctrl-C** to stop watching the log (the gateway keeps running).

---

## Step 8 — Prove it works

```bash
cd /opt/tradelab
.venv/bin/python scripts/ibkr_smoke.py
```

This is read-only and **cannot place an order**. It checks the five things that
actually break: the connection, that you are connected to the account you think
you are, contract lookup, positions, and cash balances.

**You want to see `SMOKE TEST PASSED`.** If it names problems instead, paste
the output to me — that output is exactly what I need.

---

## Step 9 — Turn on the nightly run

Only after Step 8 passes:

```bash
sudo cp deploy/systemd/tradelab-session.* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tradelab-session.timer
systemctl list-timers tradelab-session
```

The last command prints when it will next run — **Mon–Fri at 23:30 Helsinki**,
after the US close.

That is the whole point of the server: the decision happens at 23:30 every
trading night, and now it happens whether you are awake, travelling, or your
laptop is shut.

---

## What this costs

**€5.99/month excluding VAT → €7.52/month with Finnish VAT at 25.5% → €90/year.**

Cancel any time; Hetzner bills by the hour, so a server you delete after a week
costs about €1.50.
