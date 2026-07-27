# Deploying on AWS

> **New to AWS or to the command line? Read START-HERE.md instead.** It walks the same
> deployment step by step using EC2 Instance Connect, which gives a browser terminal and needs
> no SSH key file. This file assumes you're comfortable with EC2, systemd, and nginx.

Two decisions determine whether this works. Get them right first; the rest is mechanics.

---

## Decision 1 — Region

**Binance returns HTTP 451 to US IP addresses**, and the Futures API has no public mirror
for restricted regions (`data-api.binance.vision` covers spot only). Deploy in
`us-east-1` and nothing downstream can function.

| region | verdict |
|---|---|
| `ap-south-1` (Mumbai) | recommended — permitted, and lowest latency to you |
| `ap-northeast-1` (Tokyo) | permitted, and Binance's own matching engines are here |
| `ap-southeast-1` (Singapore) | permitted |
| `eu-central-1`, `eu-west-1` | generally permitted |
| `us-*`, `ca-central-1` | **blocked** — US, and Canada/Ontario is restricted too |

Verify before building anything else. Launch a throwaway instance in your chosen region
and run:

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://fapi.binance.com/fapi/v1/ping
```

`200` means go. `451` means pick another region or set `BINANCE_PROXY`.
`install.sh` runs this check and refuses to install if it fails.

Binance's blocklist has shifted overnight before — a 2022 wave caught AWS, GCP and
PythonAnywhere users who'd been fine for a year. If it ever happens to you, the app
raises `GeoBlockedError`, the dashboard says exactly what's wrong instead of going blank,
and a `BINANCE_PROXY` value gets you running again with no code change.

## Decision 2 — Which service

The scan loop must stay resident and SQLite needs a real disk. That rules out more
options than it looks like.

| service | verdict |
|---|---|
| **EC2** `t4g.small` + EBS | recommended — ARM, ~$12/mo, persistent disk, full control |
| **Lightsail** $10 instance | simpler, fixed price, static IP included, same result |
| **ECS/Fargate** | works, but the task filesystem is ephemeral — you must mount EFS at `/data` or lose all accumulated open-interest history on every restart |
| **App Runner** | scales to zero between requests, killing the loop mid-cycle |
| **Lambda** | no resident process, no local disk — wrong shape entirely |

`t4g.small` (2 vCPU, 2 GB) is comfortable. `t4g.micro` (1 GB) works if you drop `TOP_N`
to around 80 — pandas feature-building across 150 symbols is the memory peak.

That ephemeral-filesystem note is the one that actually bites. Binance retains only 30
days of open-interest history, so `store.py` accumulating snapshots is the *only* way you
ever get a backtestable OI panel. Losing `radar.db` isn't an inconvenience; it's the one
thing in this project that can't be re-fetched from anywhere.

---

## EC2 setup

**1. Launch.** Ubuntu 24.04 ARM64, `t4g.small`, 20 GB gp3, in `ap-south-1`.

**2. Security group.** Inbound SSH (22) from your IP only. Do **not** open 8080 to
`0.0.0.0/0` — see the access section below for the two sane options.

Note that `main.py` binds `127.0.0.1` by default. Set `HOST=0.0.0.0` in `/etc/radar.env`
to expose it, which requires `RADAR_PASSWORD`; the app refuses to start otherwise.

**3. Install.**

```bash
sudo mkdir -p /opt/radar && sudo chown $USER /opt/radar
# either: git clone <your-repo> /opt/radar
# or:     scp -r crypto-radar/* ubuntu@<ip>:/opt/radar/
cd /opt/radar && sudo bash deploy/install.sh
```

The script checks Binance reachability first and stops if the region is blocked, creates
a `radar` system user, builds a venv, generates a random dashboard password, installs the
systemd unit, and starts it. It prints the login once — save it.

**4. Verify.**

```bash
systemctl status radar
journalctl -u radar -f          # first scan takes about a minute
curl -s localhost:8080/healthz
```

## Getting to the dashboard

The app **refuses to start** bound to a public interface without `RADAR_PASSWORD` set.
That's deliberate: an open dashboard publishes your levels and positioning to anyone who
scans the port, and the failure is silent until someone finds it.

**Option A — SSH tunnel.** Nothing exposed, no TLS to manage. Best if you're the only user.

```bash
# in /etc/radar.env
HOST=127.0.0.1
# from your laptop
ssh -N -L 8080:localhost:8080 ubuntu@<instance-ip>
# then open http://localhost:8080
```

**Option B — nginx + TLS.** Use this if you want it on your phone without a tunnel.

```bash
sudo apt install -y nginx certbot python3-certbot-nginx
sudo cp deploy/nginx.conf /etc/nginx/sites-available/radar
sudo sed -i 's/radar.example.com/your.domain/' /etc/nginx/sites-available/radar
sudo ln -s /etc/nginx/sites-available/radar /etc/nginx/sites-enabled/
sudo certbot --nginx -d your.domain
sudo nginx -t && sudo systemctl reload nginx
```

Then open 80/443 in the security group and leave 8080 closed — the app stays on
loopback and only nginx reaches it. Basic auth over TLS is adequate for one user; over
plain HTTP it is not, which is why the nginx config redirects to HTTPS.

## Docker

```bash
docker build -t radar .
docker run -d --name radar --restart unless-stopped -p 127.0.0.1:8080:8080 \
  -e RADAR_PASSWORD='...' -e RADAR_DB=/data/radar.db \
  -v radar-data:/data radar
```

The named volume matters for the reason above. On Fargate, mount EFS at `/data`.

## Configuration

All of it is environment variables in `/etc/radar.env` (`chmod 600`). See
`deploy/radar.env.example`. Nothing is required except `RADAR_PASSWORD` when binding
publicly — every other value has a working default.

## Operations

```bash
journalctl -u radar -f              # logs
sudo systemctl restart radar        # after config changes
sqlite3 /opt/radar/data/radar.db "select count(*) from snapshots;"
```

Point a CloudWatch alarm or any uptime monitor at `/healthz` — it returns 503 once no
scan has completed in three intervals, which is how you learn the loop died without
watching logs. It's the one route that stays unauthenticated, and it exposes no market
data.

Back up `radar.db`. A nightly `aws s3 cp` is enough:

```bash
0 3 * * * sqlite3 /opt/radar/data/radar.db ".backup '/tmp/radar.db'" && \
  aws s3 cp /tmp/radar.db s3://your-bucket/radar/$(date +\%F).db
```

Watch `used_weight_1m` in the dashboard header. Climbing toward 2000 means you're near
the Binance rate limit — raise `SCAN_INTERVAL_S` or lower `TOP_N`.

---

# Optional: Arkham on-chain overlay

Set `ARKHAM_API_KEY` and the scanner adds whale-flow context. Leave it unset and
everything works as before.

Access is gated — Arkham calls the API enterprise-grade and you apply at `arkm.com/api`.
Check what your tier covers before wiring it in; `/transfers` is a heavy endpoint some
plans restrict.

| variable | default | meaning |
|---|---|---|
| `ARKHAM_API_KEY` | — | enables the overlay |
| `WHALE_MIN_USD` | `1000000` | sweep threshold per transfer |
| `DORMANCY_DAYS` | `180` | inactivity before a wallet counts as dormant |
| `DORMANCY_MIN_USD` | `5000000` | only check dormancy on moves this large |

`/transfers` is capped at 1 request/second, which drives the design: one broad sweep per
cycle joined locally to your universe, rather than per-symbol queries that would eat half
the cycle. Dormancy checks cost an extra request each, so they're budgeted to 10 per
cycle and increasingly answered free from your own SQLite as it fills.

On-chain flow annotates candidates and fires its own events. It never moves the score,
because it can't be backtested — and a backtested number stops meaning anything the
moment you mix an unvalidated signal into it.
