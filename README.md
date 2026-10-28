# Nifty 50 Shareholding Pipeline

Code for maintaining original shareholding filings, twelve-category company ownership, quarter-end market-cap-weighted Nifty 50 ownership and a private chart. Daily FII/DII cash-market activity is excluded; FPI/FII ownership remains one of the twelve ownership groups.

## Current milestone

The offline integration controller is implemented and tested. It preserves missing values, validates company-quarter membership and category totals, builds a website-compatible candidate, records classification issues and reuses unchanged verified inputs. It does not publish data or create a schedule.

Two GitHub workflows are provided:

- **Pipeline tests:** runs the offline Python and JavaScript tests on pushes and pull requests. All test data is synthetic.
- **Read-only cloud source check:** checks ordinary browser access to official NSE/BSE sources on changes to the probe, or manually from Actions. It makes at most six explicit source checks (normal page subresource requests are additional), uses no secrets, and publishes only status/schema diagnostics. A blocked source fails the check and keeps cloud collection unapproved. It does not retry access denials, collect the archive or update the website.

There is **no recurring collection workflow yet**. Actual source data, browser profiles, generated datasets, local reports, credentials and the private Site configuration are excluded from this public repository.

## Run the tests

Python 3.11+ and Node 22+:

```sh
python -m unittest -v test_pipeline.py
node --test test_source_probe.cjs
```

No third-party dependencies or network access are required for those tests.

## Run the bounded source check

```sh
npm ci --ignore-scripts
npx playwright install --with-deps chromium
npm run probe
```

Chromium runs with its sandbox enabled, a fresh context and normal browser behavior. There are no personal cookies, stealth changes, proxies, CAPTCHA solvers or security-disabling arguments. A successful probe establishes basic access/schema compatibility, not complete production collector correctness.

## Process the existing private local archive

```sh
python pipeline.py bootstrap --project /path/to/existing/project
python pipeline.py stage
python pipeline.py status
python verify_existing_snapshot.py --project /path/to/existing/project
```

Bootstrap verifies prepared company inputs and the source workbooks, imports only official Nifty index-price files from the historical project, and checks for stale filings against its source database. It requires the existing private archive; that archive is not in this repository.

Staging saves candidates beneath `data/candidates`, keeps input versions by hash, prevents overlapping runs and changes the latest-candidate pointer atomically. A failed run leaves the previous successful candidate available. Real zeros stay zero; missing values are not imputed as zero.

## Valuation and classification

Aggregate ownership uses **full ordinary fully-paid market capitalisation**, including promoter holdings. For each group: sum of group-owned ordinary-equity value divided by included company market caps. Incomplete quarters use the requested available-company rescaling and carry estimate/exclusion labels. Daily index prices do not change historical quarter-end company weights.

The current v0 classification is retained for reproducibility and remains under review. In particular, legacy FII labels in other-institution buckets and ambiguous UTI entities must be resolved before unattended data publication. Classification and float eligibility are separate decisions. See [activation and architecture](docs/ACTIVATION.md).

## Next connection stages

1. Verify the cloud source-check result and choose a cloud runner or the existing PC collector.
2. Finalize the reviewed classification policy and the incremental source-to-input adapter.
3. Connect approved private persistent storage and a portable Excel renderer.
4. Connect an authenticated updater for the existing owner-private chart.
5. Verify a complete live update and failure recovery; integrate a single daily schedule.

Routine processing and chart use require no AI calls. No paid plans, custom domains or billable storage have been configured by these workflows.
