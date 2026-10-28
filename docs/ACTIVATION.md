# Activation state and architecture

The owner has approved implementation and pushing code to `Hannibalbbarca/NIFTY_50_Shareholding`. The repository is public. This approval does not turn on unvalidated data publication or authorize paid services.

```text
GitHub code + rules -> runner -> original-file archive / processing state
                                 -> validated company-quarter inputs
                                 -> ownership calculations / Excel outputs
                                 -> private chart data endpoint
```

## Implemented

- Offline, hash-verified input bootstrap and staging.
- Full market-cap ownership aggregation and explicitly labelled incomplete-quarter estimates.
- Classification review queue, overlapping-run rejection and atomic candidate pointers.
- Offline test workflow and a bounded, read-only ordinary-browser cloud source check.

## Confirmed execution and storage choice

The initial cloud check installed its ordinary browser successfully, but NSE navigation failed with `ERR_HTTP2_PROTOCOL_ERROR` and BSE returned HTTP 403. Cloud collection is not ready; use the existing PC collector. The check is manual-only following this result.

The owner chose to keep the original-file archive on the PC and add no new storage service. GitHub remains the code/test host and the existing owner-private Site remains the chart host. The production PC coordinator and authenticated update path to that Site are still outstanding; the chart is currently a saved snapshot.

## Before unattended publication

- Confirm ordinary NSE/BSE access on the intended runner; an access denial remains a failure.
- Review explicit FII labels and verify UTI entity identities before remapping them. Never resolve an entity solely from an acronym.
- Use dated/effective membership evidence; never apply today's membership retrospectively.
- Implement incremental live filing and price adapters, source-version retention and a portable Excel rendering path.
- Retain the PC archive, source versions and processing manifest. Connect the existing private Site's data-update path without exposing source data or credentials through this public repository; do not add a new storage service.
- Configure an authenticated data-update route for the existing private website.
- Demonstrate one successful end-to-end update and preservation of the last good release after a failure.
- Integrate/replace the existing collector schedule without duplicate overlapping jobs. No new daily schedule is enabled here.

## Privacy

The browser must not receive repository, storage or deployment credentials. A private source repository does not itself make a website private; use the hosting platform's access controls. This public repository contains code and synthetic test fixtures only.

The cloud source report contains HTTP status, URL, schema/count checks, response size and SHA-256 diagnostics. Cookies, response bodies and original financial data are not uploaded as Actions artifacts.
