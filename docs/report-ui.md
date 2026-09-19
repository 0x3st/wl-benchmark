# Run report UI

The CLI generates each run report in `wl_benchmark/site.py`; presentation lives
in `wl_benchmark/site_ui.py`. CSS, a small enhancement script and PNG artifacts
are embedded in the HTML. There is no framework, CDN, font download, client
storage or new runtime dependency.

## Reading model

1. **Run overview**: model/provider, recorded task count, scored count, human
   review count and errors. The recorded-score mean is explicitly **not** an
   overall benchmark score. Pending tasks and errors are not zero scores.
2. **Task directory**: grouped anchors with the original completion indices.
   Cards default to SVG / essay / quant / scheduling groups. Display order can
   be switched back to completion order; `results.json` is never reordered.
3. **Evidence cards**: status and latency, then the relevant artifact. SVGs use
   a fixed PNG preview, an accessible zoom dialog and source downloads. Missing
   or suspiciously blank previews have an explicit diagnostic state, separate
   from task failure and human review status.
4. **Long-form reading**: essays and research notes use a bounded text column,
   wrapped code/formula strings and native disclosure controls. Model output
   is not summarized or truncated. Quant automatic components, per-question
   checks and sampling disclosure remain separate from the final score.
5. **Review controls**: task metadata search, type/status filters, a completion
   log, Markdown/PNG/SVG downloads and printing. Print temporarily reveals all
   tasks and disclosures, then restores the current view.

The desktop directory is sticky. On narrow screens it becomes a collapsible
menu; summary tiles become a two-column grid and wide result tables scroll
inside their own container. PASS/FAIL labels never break in the middle.

## Safety and progressive enhancement

- Model Markdown is escaped by the existing `md_to_html` renderer. No raw SVG,
  iframe or model-generated script is inserted in the report DOM.
- Artifacts still pass through `run_artifacts`, including its realpath/symlink
  boundary checks. SVG is a download, **not** an inline preview.
- Scripts receive metadata through escaped HTML attributes, not interpolated
  JavaScript. The legacy static archive index uses `textContent` for metadata.
- Without JavaScript, all cards, outputs, downloads and anchor links remain
  readable. Native `details` controls still work. Enhancement-only controls
  are hidden rather than appearing broken.
- The zoom dialog uses native focus handling and Escape dismissal. Buttons,
  fields, anchors and disclosures have visible keyboard focus states.

## Local preview (no upload)

Run from the repository root so existing relative artifact paths resolve:

```sh
python3 tools/preview_report.py results/<run-id> \
  --output /tmp/wlb-report-preview.html --open
```

This reads `results.json` and artifacts, writes only the selected HTML file,
never calls the publisher and never deletes run data.

## Verification

Standard-library generator/security regression suite:

```sh
python3 -m unittest discover -s tests -p 'test_site.py' -v
```

Optional real-browser checks (install Playwright in a development environment;
use an existing Chrome, or install Playwright's Chromium):

```sh
python3 tools/report_ui_selftest.py --chrome /path/to/chrome \
  --output /tmp/wlb-ui-qa
```

The browser suite checks 1440/1024/768/390/320px widths; long unbroken formulas,
JSON and Markdown tables; filters/order/anchors; zoom and focus restoration;
print state; empty runs; no-JavaScript reading; and absence of external requests.
Its screenshots use synthetic fixtures, not benchmark results.

## Delivery scope

This changes newly built **single-run reports** plus both run indexes: the
live Worker homepage (`site/src/worker.js`, applied on `wrangler deploy`) and
the compatibility static index (`build_index()`, safe metadata via
`textContent`, same design language). Existing uploaded reports contain the
old HTML/CSS and do not update when the CLI changes. Updating them requires
rebuilding and explicitly re-uploading; no upload, Worker deployment, release
or history migration is part of this UI change. The separately generated
reviewer PDF retains its own print layout.
