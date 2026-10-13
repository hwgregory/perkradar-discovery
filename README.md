# PerkRadar Discovery Engine (prototype)

This is the **public-offer discovery backend** that v2.4 was missing. It is a starting architecture, **not** proof that all membership perks have been harvested. It is not automatically connected to the PerkRadar browser extension yet.

## How it works

1. Reads first-party source URLs from `sources.json`.
2. Checks `robots.txt` and uses polite, rate-limited HTTPS requests. It does **not** sign in, bypass blocks or CAPTCHAs, nor send membership details.
3. Discovers linked eGift cards and bookable attractions from public NRMA directories, with source URLs. It also records whether Qantas, Everyday, Amex or Optus programme pages could be accessed, but tries published partner links and records candidates for review, but does not claim to harvest logged-in or inaccessible inventories. JavaScript-heavy sources can use the optional browser renderer.
4. Deduplicates by stable offer IDs. Keeps previous records on retrieval failure and marks old ones stale.
5. Refuses to infer a savings percentage from a gift card denomination, or a points rate without an exact published rate.
6. Writes the auditable `catalogue/public-offers.json` plus `catalogue/coverage.json`, including unsuccessful sources and a review queue.

This current collector has **2 structured category adapters, a conservative partner-link candidate adapter, optional Playwright rendering, and public page monitors**. Additional source-specific adapters and a way to handle JavaScript-rendered listings with permission are still required for high coverage. The daily automation is supplied, but will not run until you put the project in GitHub and enable it.

## Run on Windows

Extract the folder, install Python 3.12+, then run in PowerShell from the project folder:

```powershell
py -m pip install -r requirements.txt
py -m unittest discover -s tests -v
py -m discovery.engine --out catalogue
```

If a site disallows crawling, the engine records that and moves on. GitHub Actions can run daily in a public repository via `.github/workflows/daily-discovery.yml`; it must be enabled/configured in a repository you control. No login credentials are needed for this public-source mode.

## Browse the inventory

Open `catalogue/public-offers.json` (data) and `catalogue/coverage.json` (progress/errors). **Coverage is measured per source, not represented as 100%.** `published_unpriced` means a public listing exists; it does **not** mean a discount amount is confirmed.

## Next development pieces

- Add official or permitted partner feeds for Qantas earn rates, NRMA dynamic product listings, and public Everyday Rewards partner promotions.
- Add extraction tests for discount/pricing fields on stable, merchant-specific pages. Queue ambiguous pricing for manual review.
- Add an audited domain alias map and optional broad site-matching permission in the extension. A catalogue of 1,000 deals isn't useful if the extension still only recognises 26 domains.
- Add a read-only JSON sync in the extension with daily caching, validation, expiry, rollback and no telemetry. Do not fetch JavaScript into the extension.
- Build a simple review interface for candidates and source failures, and publish only confirmed offers with provenance.

## Data limitations

- Public listing is not proof that a discount exists for your specific cart, tier or account.
- Person-specific Amex Offers or Everyday boosters cannot be harvested from public pages.
- A failed provider/JS-only directory cannot be counted as covered.
- Some shopping portal/affiliate websites restrict automation; respect their terms and access rules.
- A GitHub Actions scheduled job may be delayed or skipped. Check `coverage.json` for the last actual run before displaying freshness claims.

## Connecting the daily feed to PerkRadar v2.5

1. Create a **public** GitHub repository and upload the **contents of this Discovery Engine folder** to its root (including the hidden `.github/workflows/daily-discovery.yml` file). Do not upload member IDs, account credentials or personal offers.
2. In repository Settings → Actions → General, allow GitHub Actions and grant **Read and write permissions** to the workflow token. Keep the repo on its default branch (normally `main`).
3. Open Actions → **PerkRadar public perk discovery** → **Run workflow**. Check the log and `catalogue/coverage.json`; any blocked/dynamic sources must be fixed or explicitly counted as missing.
4. Copy the raw link to your published `catalogue/public-offers.json`, for example `https://raw.githubusercontent.com/YOUR_USERNAME/YOUR_REPO/main/catalogue/public-offers.json`.
5. In the PerkRadar v2.5 extension, open Settings → **Daily public-offer catalogue**. Paste the URL, save, and click **Check for updates now**.

The job then attempts a daily public-source refresh. **This cannot be activated until you create/enable the repository and connect its URL.** The existing 94 source-linked listings are bundled separately as the initial seed and will be suppressed as stale after 14 days unless a fresh successful verification occurs.

## Coverage goals before public release

Report per-source counts and source failures; dedupe merchant offers across providers; track 14-day freshness; check rates only when explicitly tied to an individual offer; flag click-through/eligibility restrictions; monitor source structure changes. A blocked or logged-in source is an unresolved integration, not a successful scan. The current code is a prototype of the collector and data pipeline, not full production coverage or a guarantee of every perk.
