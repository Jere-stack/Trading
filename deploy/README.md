# Running this on a server

The US close is **23:00 Helsinki time**, every trading night. That is the whole
argument: a laptop that has to be awake at 23:00 for the decision, and awake
again for the fills, will eventually not be — and the night it isn't will not
be a night you chose.

A €4/month virtual server removes the problem entirely.

---

## What to buy

| Provider | Spec | ~Price/mo | Notes |
|---|---|---|---|
| **Hetzner CX22** ← recommended | 2 vCPU, 4 GB, 40 GB NVMe | **€3.79 + €0.50 IPv4** | German company, EU jurisdiction, **Helsinki datacenter**. Best price/performance in Europe by a clear margin |
| UpCloud | 2 vCPU, 4 GB, 80 GB | ~€20 | Finnish company, Helsinki. Same country as you, ~4× the price |
| DigitalOcean | 2 vCPU, 4 GB | ~$24 | Better docs, worse value |
| Oracle Cloud Free | 4 ARM cores, 24 GB | €0 | Genuinely free, and genuinely reclaimable without notice. Not for something that holds broker credentials and must run tonight |

*Prices drift — check before ordering.*

**Take Hetzner CX22, Helsinki region, Ubuntu LTS.** 4 GB is the number that
matters: IB Gateway is a Java application that wants 1–2 GB to itself, and a
1 GB instance will OOM in the middle of a session rather than at a convenient
moment.

**Do not pay for latency.** This system trades daily bars. A server in Helsinki
and a server in Virginia will make identical decisions. Co-location is for a
different business.

## What it costs against the edges we are looking for

€4.29/mo is **€51/yr, or 0.51% of a €10,000 account**. Set against the
register's data-cost table (`docs/06-strategy-hypotheses.md` §6), which already
carries €199/yr for EODHD:

| | Net edge/yr | Break-even, data only | Break-even, **+hosting** |
|---|---|---|---|
| H4 Post-earnings drift | 3.36% | €5,923 | **€7,455** |
| H5 Index deletion | 3.30% | €6,030 | **€7,590** |
| H7 Spin-off selling | 5.55% | €3,586 | **€4,513** |
| H9 Fund fire-sales | 4.25% | €4,682 | **€5,894** |

All four stay under €10,000, so the server is affordable — but note what the
table is really saying. **Fixed costs are now €250/yr, 2.50% of the account,
paid whether or not anything trades.** At €50,000 the same €250 is 0.50%. The
economics of this project improve faster with account size than with any
plausible improvement in the strategies, and that is worth remembering when
deciding where effort goes.

---

## The server is the easy part

Provisioning takes twenty minutes. The thing that will actually cost you an
evening is **IB Gateway**, for two reasons:

**1. Two-factor authentication.** IBKR requires it, and an unattended process
cannot tap your phone. In practice: authenticate once via IBKR Mobile, and the
session persists across the gateway's restarts for a period. Whether that
period is long enough to run unattended is the first thing to establish — and
it costs nothing to establish, because the paper account behaves the same way.
**Test this before you trust it.** If it turns out to need a daily tap, you
want to know that now and not in week three.

**2. The forced daily restart.** IBKR restarts the gateway every day. This is
normal, expected, and handled — `AUTO_RESTART_TIME` is set to 03:00 Helsinki,
after the US close and before Europe opens, and the adapter treats a disconnect
as operational rather than exceptional. But it means the connection is *not*
permanent, and any code assuming otherwise is wrong.

IBC (bundled in the container below) handles the login typing and the restart.

---

## Setup

### 1. Provision

Hetzner console → CX22, Helsinki, Ubuntu LTS, **add your SSH public key**
(never enable password login). Then, as root:

```bash
adduser --disabled-password --gecos "" tradelab
usermod -aG sudo,docker tradelab
rsync -a /root/.ssh/ /home/tradelab/.ssh/ && chown -R tradelab:tradelab /home/tradelab/.ssh

# Docker
curl -fsSL https://get.docker.com | sh

# Firewall: SSH only. Nothing else is exposed, ever.
ufw default deny incoming && ufw default allow outgoing
ufw allow OpenSSH && ufw --force enable

# Disable password and root SSH login
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
systemctl restart ssh

# Unattended security updates
apt update && apt install -y unattended-upgrades fail2ban
dpkg-reconfigure -plow unattended-upgrades
```

Optional but recommended: [Tailscale](https://tailscale.com) (free tier), so
you can reach the machine without SSH being exposed to the internet at all.

### 2. Deploy

```bash
sudo -iu tradelab
git clone <this repo> /opt/tradelab && cd /opt/tradelab
curl -LsSf https://astral.sh/uv/install.sh | sh
uv venv && uv pip install -e ".[ibkr,research]"

cp deploy/.env.example deploy/.env
chmod 600 deploy/.env
nano deploy/.env          # credentials go here and nowhere else
```

### 3. Start the gateway

```bash
cd /opt/tradelab/deploy
docker compose --env-file .env up -d
docker compose logs -f     # watch the login; approve 2FA on your phone
```

### 4. Prove it works before trusting it

```bash
cd /opt/tradelab
.venv/bin/python scripts/ibkr_smoke.py
```

Read-only, cannot place an order, and checks the five things that actually
break: connection, **account identity**, contract qualification, positions, and
currency balances. It prints `SMOKE TEST PASSED` or names every problem.

Do not skip this. "The gateway is up" and "the adapter agrees with the gateway"
are different claims, and the gap between them is where the money goes.

### 5. Schedule the nightly run

```bash
sudo cp deploy/systemd/tradelab-session.* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tradelab-session.timer
systemctl list-timers tradelab-session    # confirm the next run
```

The timer fires **Mon–Fri 23:30 Europe/Helsinki**, after the US close.
`Persistent=true` means a run missed to a reboot executes on the next boot
rather than vanishing; the session script is resumable and replays only
sessions it has not already recorded, so a late run is correct rather than a
double-trade.

---

## Security

The server holds credentials to a brokerage account. Treat it accordingly.

- **Never publish the API port.** It is bound to `127.0.0.1` in the compose
  file and that is not a style choice: the TWS API has *no authentication of
  its own*. Anything that can reach the port can trade the account. Reach it
  over Tailscale or `ssh -L`, never `0.0.0.0`.
- **`READ_ONLY_API=yes` until you have decided to trade.** It blocks order
  submission at the gateway, so the first connection is incapable of damage
  whatever the code does.
- **Paper first.** `TRADING_MODE=paper` with `API_PORT=4002`. The adapter
  cross-checks mode against port and refuses to start on a mismatch — including
  the Gateway ports, not just TWS's.
- **Credentials live in `deploy/.env`, mode 600, gitignored.** Not in the repo,
  not in a command line (shell history), not in a screen-shared terminal.
- **SSH keys only**, root login disabled, ufw default-deny, unattended upgrades.
- **Back up `state/`.** It is the track record. `git push` from the server after
  each run is enough, and has the useful property that the history is
  tamper-evident.

## Going live, later

Three changes, made deliberately and separately:

1. `TRADING_MODE=live` **and** `API_PORT=4001` — both, in the same edit. The
   adapter refuses to start if they disagree.
2. `READ_ONLY_API=no`.
3. `mode=RunMode.LIVE` in whatever script the timer runs.

Before any of that, `docs/02-architecture.md` sets the promotion gates. The
server being ready is not one of them.
