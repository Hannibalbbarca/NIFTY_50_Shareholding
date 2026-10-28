# Nifty 50 Shareholding Pipeline

Code for maintaining original shareholding filings, twelve-category company ownership, quarter-end market-cap-weighted Nifty 50 ownership and a private chart. Daily FII/DII cash-market activity is excluded; FPI/FII ownership remains one of the twelve ownership groups.

## Current milestone

The offline integration controller is implemented and tested. It preserves missing values, validates company-quarter membership and category totals, builds a website-compatible candidate, records classification issues and reuses unchanged verified inputs. It does not publish data or create a schedule.

The membership tracker now supports exact-date official CSV snapshots and reconstruction from a reviewed effective-dated event ledger. The approved workbook baseline independently replays 28 membership periods and matches all 2,000 targets in the selected 40-quarter plan. Its history ends on 8 October 2026; unreviewed dates are reported as gaps. A backed-up installer connects this adapter to the existing PC collector without adding a schedule. See [membership method and limits](docs/MEMBERSHIP.md).

Two GitHub workflows are provided:

- **Pipeline tests:** runs the offline Python and JavaScript tests on pushes and pull requests. All test data is synthetic.
- **Read-only cloud source check:** a manual Actions check of ordinary browser access to official NSE/BSE sources. It makes at most six explicit source checks (normal page subresource requests are additional), uses no secrets, and publishes only status/schema diagnostics. A blocked source fails the check and keeps cloud collection unapproved. It does not retry access denials, collect the archive or update the website.

There is **no recurring collection workflow yet**. Actual source data, browser profiles, generated datasets, local reports, credentials and the private Site configuration are excluded from this public repository.

### Cloud test result and chosen collection route

The initial GitHub-hosted check on 28 October 2026 installed and launched Chromium successfully. NSE navigation failed with `ERR_HTTP2_PROTOCOL_ERROR`; BSE returned **HTTP 403**. The check stopped rather than attempting a bypass. [Initial source-check run](https://github.com/Hannibalbbarca/NIFTY_50_Shareholding/actions/runs/40632512245).

Use the **existing Windows PC for exchange collection**. The owner selected keeping the original-file archive on that PC, with **no new storage service**. GitHub holds and tests the code; the existing website remains separately owner-private. The probe is now manual-only, so source denials do not trigger repeated automatic attempts. The PC-to-private-Site update path is still to be connected.

## Run the tests

Python 3.11+ and Node 22+:

```sh
python -m unittest -v test_pipeline.py test_membership_tracker.py
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

1. Extend verified index-change history as new notices arrive; exact-date snapshots remain the first membership source.
2. Finalize the reviewed classification policy and the incremental source-to-input adapter.
3. Connect the existing PC archive to a portable Excel renderer.
4. Connect an authenticated updater for the existing owner-private chart.
5. Verify a complete live update and failure recovery within the existing daily schedule.

Routine processing and chart use require no AI calls. No paid plans, custom domains or billable storage have been configured by these workflows.
