# uniroom

Unofficial room/schedule looker-upper for the University of Vienna, built on the
public u:find calendar API. A tiny static site (no backend) that fetches
pre-generated JSON to show which course is in which room and when.

- **Scrape** (`build_data.py`) — downloads the semester calendars from the
  m-ufind API, parses them, deduplicates, builds a room catalog and per-building
  shards, and writes everything into `site/data/`.
- **Site** (`site/`) — static HTML/CSS/JS with three views: **Tag** (a room's
  day), **Woche** (a room's week), **Gebäude** (a whole building as a room × time
  matrix).
- **Deploy** — `wrangler pages deploy` uploads `site/` straight to Cloudflare
  Pages (the "direct upload" flow, automated).

## Repo layout

```
build_data.py        scraper (has --fresh / --no-download flags)
roomfind.py          iCal parsing helper (imported by build_data.py)
site/                the static site
  index.html
  app.js
  data/              generated — gitignored (only .gitkeep is committed)
run_daily.sh         daily scrape + deploy entry point (for cron/systemd)
deploy/
  uniroom.service    systemd unit (alternative to cron)
  uniroom.timer      systemd timer — daily at 03:00
  crontab.example    classic cron equivalent
```

The scraped data (`site/data/*.json`, `rooms_review.md`) and the raw downloads
(`data_raw/`) are **not** committed — they're regenerated every day and then
uploaded. Git only holds source, so the history stays small.

---

## One-time setup

### 1. Cloudflare Pages

1. In the Cloudflare dashboard → **Workers & Pages → Create → Pages**.
2. Choose **Upload assets** (direct upload) and give it a name (e.g. `uniroom`).
   You don't need to upload anything yet.
3. Note the **project name**.
4. Create an API token: **My Profile → API Tokens → Create Token →**
   *"Cloudflare Pages: Update (Edit)"* template, scoped to your account.
5. Note your **Account ID** (dashboard sidebar → right side of Workers & Pages).

### 2. Wrangler CLI (on the machine that runs the daily job)

```bash
# needs Node.js first
npm install --global wrangler
```

Then create an env file next to the repo (never commit it — it's gitignored):

```bash
cat > /opt/uniroom/.env <<'EOF'
CLOUDFLARE_API_TOKEN=your_token_here
CLOUDFLARE_ACCOUNT_ID=your_account_id_here
EOF
chmod 600 /opt/uniroom/.env
```

### 3. Python (for the scraper)

Debian already has `python3`; the script is stdlib-only:

```bash
sudo apt update && sudo apt install -y python3 ca-certificates
```

(`ca-certificates` supplies the TLS roots; that's why the Windows-only certifi
workaround isn't needed on Debian. If you *do* hit an SSL error, `pip3 install certifi`
makes the script use certifi.)

---

## Run it once, manually

```bash
cd /opt/uniroom
python3 build_data.py --fresh        # download + rebuild site/data/*
wrangler pages deploy site --project-name uniroom
```

You should see the site update in the Cloudflare dashboard. Flags:

- `--fresh` / `--redownload` — delete the cached `.ics` and re-download.
- `--no-download` — rebuild from whatever `.ics` files are already in `data_raw/`
  (no network).

---

## Automate it daily

Pick **one** of these (systemd is the modern default; cron is fine too).
Both just call `run_daily.sh`.

### Option A — systemd timer (recommended)

```bash
sudo cp deploy/uniroom.service deploy/uniroom.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now uniroom.timer
sudo systemctl status uniroom.timer      # verify it's scheduled
systemctl list-timers                     # see next run time
```

To trigger immediately / test:

```bash
sudo systemctl start uniroom.service
journalctl -u uniroom.service            # see the log
```

### Option B — cron

```bash
sudo cp deploy/crontab.example /etc/cron.d/uniroom
# or: crontab -e  and add the line from crontab.example
```

Logs land in `/opt/uniroom/logs/daily.log` either way.

---

## Developing / running on Windows

Same repo works on Windows; the scraper needs a Python. If you use Cherry
Studio's `uv` (which is how this was developed), note `uv`'s bundled CPython
sometimes can't verify m-ufind's TLS cert on Windows — the script already falls
back to `certifi`, so run it with certifi installed:

```powershell
& "C:\Users\admin\.cherrystudio\bin\uv.exe" run --with certifi python build_data.py --fresh
```

On macOS/Linux with a normal `python3`, just `python3 build_data.py --fresh`.