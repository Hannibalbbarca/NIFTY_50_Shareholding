# Evidence-bounded Nifty membership

The PC collector first looks for an archived official constituent CSV observed on the requested quarter-end. If that is missing, it can reconstruct the **approved core 50 company universe** from a dated anchor and a reviewed change ledger. It does not infer membership from today's list, publication dates, a company-name match, or a count of 50 alone.

## Imported baseline

The owner's existing `NIFTY50_2016_to_2026-10-08.xlsx` is the approved reconstruction input. The importer requires its expected SHA-256, checks all period tabs and supporting tables, validates adjacent period deltas against the raw ledger and explicit exceptions, reconstructs every period boundary backwards, and replays all changes forwards to the anchor. Source URLs and hashes are retained from its 43-source manifest. Importing the manifest does **not** independently re-download or rehash those original PDFs.

The baseline has 28 contiguous periods, 27 effective-dated transitions, a 50-company anchor observed on 8 October 2026 India time, and coverage from 1 January 2016 through that observation. The initial 2016 list was reconstructed; there is no independent contemporaneous full-list anchor. The selected 40-quarter scope begins 31 December 2016 and ends 30 September 2026.

This preserves the previously approved company-universe policy. Tata Motors DVR is collapsed as another share class. Explicit temporary demerger additions (Jio Financial, ITC Hotels, TMCV and Kwality Wall's) are excluded during the intervals recorded in the workbook. Therefore this universe is **not always the full official security basket** during temporary 51/52-security periods. A 51-row snapshot is rejected for review, never trimmed automatically. Jio Financial's later regular membership is included normally.

ETERNAL and TMPV retain the workbook's planning identities ZOMATO and TATAMOTORS. These aliases do not make securities interchangeable or transfer holdings across mergers. Historical member records deliberately omit current ISINs. BSE mappings and corporate-action price adjustments remain separate checks.

## Resolution and failure behavior

- Membership changes on its effective date, inclusively. Published dates remain evidence metadata.
- Forward and reverse replay require exact prior member records and preserve 50 distinct company identities and symbols after each atomic event.
- Supersession references suppress original notices when a reviewed correction or accelerated notice replaces them. Cycles, unknown references and uncombined same-date events are rejected.
- The whole interval between requested date and anchor must have explicit reviewed coverage. Matching endpoint snapshots do not prove the intervening history. A gap stops reconstruction.
- An exact-date snapshot can resolve a quarter independently of the ledger. Its original CSV bytes are rehashed; records, India observation date, official source URL and company mappings are checked. Conflicting same-date snapshots require review.
- The adapter adds only complete quarters after the approved historical cutoff and before today's India date. Each new target carries its membership evidence. It leaves historical targets unchanged, flags conflicting future targets, and never adds a partial 49-company plan or guesses a BSE code.
- Target files are replaced atomically only when new complete quarters exist. No-op checks preserve their exact bytes. Reports explicitly list gaps and conflicts.

## Use with a private local archive

These commands read local files; they make no network or AI calls. Paths below are placeholders. The source workbook and resulting ledger stay outside tracked Git files.

```sh
python membership_tracker.py import-workbook /path/to/approved.xlsx --sha256 EXPECTED_SHA256 --out data/membership/verified-ledger.json
python membership_tracker.py verify-targets data/membership/verified-ledger.json /path/to/bse-system/inputs/targets.json --out reports/membership-verification.json
python verify_membership.py --collector-root /path/to/bse-system --ledger data/membership/verified-ledger.json --out reports/membership-recovery-check.json
python membership_tracker.py resolve data/membership/verified-ledger.json 2025-03-31
python future_targets.py install --root /path/to/bse-system --ledger data/membership/verified-ledger.json
python /path/to/bse-system/extend_targets.py
```

The installer backs up the existing adapter and copies the two reviewed Python modules plus the private ledger. It refuses installation while the collector lock exists. It changes no Windows schedule, browser code, archive files, database or website. The existing daily collector calls the new adapter at its existing membership-target stage.

## What still needs fresh evidence

The imported history ends on **8 October 2026**. The matching official CSV observed on 28 October validates that observation only; it does not extend continuous change-history coverage. New quarters can use their exact-date official snapshots. If one is missed, the ledger needs a reviewed coverage extension, effective changes, corrections and temporary-security exceptions before reconstruction is allowed.

Automatic discovery and interpretation of new index notices is **not implemented by this milestone**. Future operation is consequently conditional on successful exact-date snapshots or a subsequently reviewed ledger update. This membership component does not finish the company classification, Excel refresh or authenticated private-website updater.

Run the synthetic tests with:

```sh
python -m unittest -v test_pipeline.py test_membership_tracker.py
node --test test_source_probe.cjs
```
