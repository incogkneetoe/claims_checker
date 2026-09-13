# Claim Watch

Daily scan of open US class action settlements, published as a static
landing page. No API keys, no notification services, $0 to run.

## How it works
- `scraper.py` scrapes Top Class Actions and ClassAction.org open
  settlement listings, flags ones matching `vendors.txt`, and merges
  into `docs/data.json` (existing entries keep their firstSeen date).
- `.github/workflows/scan.yml` runs it daily at 10:00 UTC and commits.
- `docs/index.html` is the landing page; GitHub Pages serves it.

## Setup (one time, ~5 minutes)
1. Create a repo (public is required for free GitHub Pages) and push
   these files.
2. Settings -> Pages -> Source: "Deploy from a branch",
   Branch: `main`, folder: `/docs`. Save.
3. Edit `vendors.txt` with your companies, or (public repo) keep the
   list private: Settings -> Secrets and variables -> Actions -> New
   repository secret named `VENDORS`, one vendor per line. The secret
   wins over the file.
4. Actions tab -> "Claim Watch daily scan" -> Run workflow, to do the
   first scan now.
5. Your page is at `https://<username>.github.io/<repo>/`. Bookmark it.

## Notes
- Dismissed items are stored in your browser (localStorage), per device.
- The scrapers are best-effort HTML parsing; if a site redesigns, that
  source contributes 0 entries until selectors in `scraper.py` are
  updated, but existing data is never wiped.
- Entries auto-prune 30 days after their deadline passes.
