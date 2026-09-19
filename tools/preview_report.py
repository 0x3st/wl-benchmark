#!/usr/bin/env python3
"""Build a local report preview from a run, without publishing or deleting data."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import webbrowser

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wl_benchmark.site import build_run_page


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_dir', type=Path, help='Run directory containing results.json')
    parser.add_argument('--output', type=Path,
                        default=Path(tempfile.gettempdir()) / 'wlb-report-preview.html')
    parser.add_argument('--open', action='store_true', help='Open the local file in your browser')
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    output = args.output.resolve()
    if output.suffix.lower() != '.html':
        parser.error('--output must have an .html extension')
    with (run_dir / 'results.json').open(encoding='utf-8') as f:
        results = json.load(f)
    page = build_run_page(results, run_dir.name, str(run_dir))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(page, encoding='utf-8')
    print(f'Local preview: {output.as_uri()}')
    print('Nothing was uploaded; run data is unchanged.')
    if args.open:
        webbrowser.open(output.as_uri())


if __name__ == '__main__':
    main()
