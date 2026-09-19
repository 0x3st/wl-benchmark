#!/usr/bin/env python3
"""Optional real-browser UI checks. Requires Playwright, not a runtime dependency.

python3 tools/report_ui_selftest.py --chrome /path/to/chrome --output /tmp/wlb-ui-qa
Without --chrome, uses Playwright's installed Chromium.
"""
import argparse
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from wl_benchmark.site import build_run_page
from test_site import fixture


def main():
    from playwright.sync_api import sync_playwright

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--chrome')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='wlb-report-check-') as tmp:
        root = Path(tmp)
        page_path = root / 'report.html'
        results = fixture(root)
        page_path.write_text(build_run_page(results, 'ui-test-run', str(root)), encoding='utf-8')
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=args.chrome, headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1100})
            errors, external = [], []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.on('request', lambda req: external.append(req.url) if req.url.startswith('http') else None)
            page.goto(page_path.as_uri())
            page.wait_for_selector('html.js')
            assert page.locator('.task-card:visible').count() == 6
            assert not page.evaluate('Boolean(window.MODEL_SCRIPT_RAN)')
            for width in [1440, 1024, 768, 390, 320]:
                page.set_viewport_size({'width': width, 'height': 1000})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'page overflow at {width}'
                overflow = page.locator('.prose pre, .prose p, .prose table').evaluate_all(
                    '(els) => els.filter(el => el.scrollWidth > el.clientWidth + 1).map(el => el.tagName)')
                assert not overflow, f'prose overflow at {width}: {overflow}'
            page.set_viewport_size({'width': 1440, 'height': 1100})
            page.locator('[data-filter="quant"]').click()
            assert page.locator('.task-card:visible').count() == 1
            assert page.locator('#task-1').is_visible()
            if args.output:
                args.output.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(args.output / 'quant-desktop.png'), full_page=True)
                page.set_viewport_size({'width': 390, 'height': 844})
                page.screenshot(path=str(args.output / 'quant-mobile.png'), full_page=True)
                page.set_viewport_size({'width': 1440, 'height': 1100})
            page.locator('.nav-link[href="#task-2"]').click()
            assert page.locator('.task-card:visible').count() == 6
            page.locator('[data-preview-target="task-2-image"]').click()
            assert page.locator('#image-dialog').is_visible()
            page.locator('#zoom-toggle').click()
            assert page.locator('#zoom-canvas.actual').count() == 1
            page.keyboard.press('Escape')
            assert not page.locator('#image-dialog').is_visible()
            assert page.locator('[data-preview-target="task-2-image"]').evaluate('(el) => el === document.activeElement')
            page.locator('#status-filter').select_option('preview')
            assert page.locator('.task-card:visible').count() == 1
            assert page.locator('#task-4').is_visible()
            for status, count in [('error', 1), ('scored', 1), ('pending', 4)]:
                page.locator('#status-filter').select_option(status)
                assert page.locator('.task-card:visible').count() == count
            page.locator('#task-search').fill('nothing-matches')
            assert page.locator('#no-matches').is_visible()
            page.locator('#reset-filters').click()
            page.locator('#task-order').select_option('completion')
            assert page.locator('.task-card').evaluate_all('(els) => els.map(el => el.id)') == [f'task-{i}' for i in range(6)]
            page.locator('#task-order').select_option('group')
            assert page.locator('.task-card').first.get_attribute('id') == 'task-2'
            # Printing includes filtered-out tasks and collapsed outputs, then restores the UI.
            page.locator('[data-filter="svg"]').click()
            page.evaluate("document.querySelector('.reading').open = false")
            page.evaluate("dispatchEvent(new Event('beforeprint'))")
            assert page.locator('.task-card:visible').count() == 6
            assert page.locator('details:not([open])').count() == 0
            page.evaluate("dispatchEvent(new Event('afterprint'))")
            assert page.locator('.task-card:visible').count() == 2
            assert not page.locator('.reading').first.evaluate('(el) => el.open')
            # Hash links work on initial load as well as after filtering.
            page.goto(page_path.as_uri() + '#task-1')
            assert page.locator('.nav-link[aria-current="location"]').get_attribute('href') == '#task-1'
            # The output itself must remain accessible without JavaScript.
            no_js = browser.new_context(java_script_enabled=False, viewport={'width': 390, 'height': 844})
            static = no_js.new_page()
            static.goto(page_path.as_uri())
            assert static.locator('.task-card:visible').count() == 6
            assert static.locator('.prose:visible').count() == 2
            assert static.locator('#task-search').is_hidden()
            assert static.evaluate('document.documentElement.scrollWidth <= innerWidth')
            empty_path = root / 'empty.html'
            empty_path.write_text(build_run_page([], 'empty-run', str(root)), encoding='utf-8')
            page.goto(empty_path.as_uri())
            assert page.locator('#no-matches').is_hidden()
            assert page.get_by_text('No task results yet').is_visible()
            assert not errors, errors
            assert not external, external
            browser.close()
    print('PASS: five viewport widths, wrapping, filters, ordering, anchors, zoom, print restoration, no-JS, empty run, no external requests.')


if __name__ == '__main__':
    main()
