"""Regression tests for portable HTML reports (no browser dependency).

Run: python3 -m unittest discover -s tests -p 'test_site.py' -v
"""
import base64
import copy
from html.parser import HTMLParser
from pathlib import Path
import tempfile
import unittest

from wl_benchmark.site import build_index, build_run_page, manifest_entry


class Elements(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.tags = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def find(self, tag=None, **attrs):
        return [a for t, a in self.tags if (tag is None or t == tag)
                and all(a.get(k) == v for k, v in attrs.items())]


def fixture(root):
    """Interleaved tasks, including untrusted markup and very long model output."""
    root = Path(root)
    md = root / 'note.md'
    md.write_text('# Research note\n\n'
                  + '中文说明与 English prose. ' * 40 + '\n\n'
                  + '2.485962' * 400 + '\n\n```json\n'
                  + '{"data":"' + '1234567890' * 300 + '"}\n```\n\n'
                  + '| Metric | Value |\n| --- | --- |\n| alpha | ' + 'x' * 800 + ' |\n\n'
                  + '<script>window.MODEL_SCRIPT_RAN=true</script>', encoding='utf-8')
    png = root / 'drawing.png'
    png.write_bytes(base64.b64decode(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a7msAAAAASUVORK5CYII='))
    svg = root / 'drawing.svg'
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"><script>bad()</script></svg>')
    def result(kind, tid, **kwargs):
        return dict(task_id=tid, task_type=kind, model='fixture-model', provider='local',
                    latency=12.3, score=None, detail={}, artifacts=[], **kwargs)
    essay = result('essay', '01-storytelling')
    essay['artifacts'] = [str(md)]
    quant = result('quant', 'quant-fe-mining-01')
    quant['artifacts'] = [str(md)]
    quant['detail'] = dict(auto_score=0.75, auto_weight=0.8, note_weight=0.2,
                           per_question=[dict(id='alpha', given=0.2, answer=0.2, correct=True),
                                         dict(id='beta', given=0.8, answer=0.5, correct=False)],
                           sampling=dict(mode='best_of_3', attempt_scores=[0.2, 0.75, 0.4],
                                         reasoning_effort='high'))
    drawing = result('svg', 'svg-stage1-riding')
    drawing['artifacts'] = [str(svg), str(png)]
    drawing['detail'] = dict(stage='riding', picks={'a': 'Robot', 'b': 'Bicycle'},
                             constraints=[dict(constraint='valid XML', ok=True, detail='parsed')])
    missing = result('svg', 'svg-missing')
    missing['artifacts'] = [str(svg)]
    missing['detail'] = dict(raster_error='Chrome <capture> failed', raster_blank=True)
    sched = result('scheduling', 'term-plan-2627t1-01')
    sched['score'] = 0
    sched['detail'] = dict(checks={'no_conflicts': False}, conflicts=['Monday 10:00'],
                           enrollments=['MAT1001'])
    failed = result('essay', 'failed-task')
    failed['error'] = '<script>alert("error")</script>'
    return [essay, quant, drawing, sched, missing, failed]


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.results = fixture(self.tmp.name)
        self.page = build_run_page(self.results, '20260919-145745', self.tmp.name)
        self.dom = Elements(self.page)

    def test_all_tasks_have_stable_unique_anchors(self):
        ids = [a['id'] for _, a in self.dom.tags if 'id' in a]
        self.assertEqual(len(ids), len(set(ids)))
        for i in range(len(self.results)):
            self.assertEqual(len(self.dom.find('article', id=f'task-{i}')), 1)
            self.assertTrue(self.dom.find('a', href=f'#task-{i}'))

    def test_display_grouping_does_not_mutate_completion_order(self):
        original = copy.deepcopy(self.results)
        page = build_run_page(self.results, 'test-run', self.tmp.name)
        self.assertEqual(self.results, original)
        self.assertLess(page.index("<article class='task-card' id='task-2'"),
                        page.index("<article class='task-card' id='task-0'"))
        self.assertIn('Finished #03', page)
        self.assertIn('Completion order', page)

    def test_quant_render_and_sampling_no_undefined_det(self):
        self.assertIn('Research note', self.page)
        self.assertIn('Sampling disclosure', self.page)
        self.assertIn('best_of_3', self.page)
        self.assertIn('0.2 / 0.75 / 0.4', self.page)
        self.assertIn('The automatic component alone is not the final task score.', self.page)
        self.assertEqual(self.dom.find('article', id='task-1')[0]['data-status'], 'pending')

    def test_zero_score_is_scored_not_pending(self):
        self.assertEqual(self.dom.find('article', id='task-3')[0]['data-status'], 'scored')
        self.assertIn("<span class='score'>0.00", self.page)
        self.assertIn('Recorded score mean: 0.000', self.page)
        self.assertEqual(manifest_entry(self.results, '20260919-145745')['pending'], 4)

    def test_model_markup_is_text_and_svg_only_a_download(self):
        self.assertEqual(len(self.dom.find('script')), 1)  # Only trusted report UI script.
        for tag in ['svg', 'iframe', 'object', 'embed']:
            self.assertFalse(self.dom.find(tag))
        images = self.dom.find('img')
        self.assertTrue(all(not a.get('src') or a['src'].startswith('data:image/png;base64,') for a in images))
        svg_links = [a for a in self.dom.find('a') if a.get('href', '').startswith('data:image/svg+xml')]
        self.assertEqual(len(svg_links), 2)
        self.assertTrue(all('download' in a for a in svg_links))

    def test_metadata_cannot_break_attributes_or_html(self):
        malicious = copy.deepcopy(self.results)
        for r in malicious:
            r['task_id'] = '\"><img src=x onerror=alert(1)>'
            r['task_type'] = '\"><script>bad()</script>'
            r['provider'] = '<iframe>evil</iframe>'
        page = build_run_page(malicious, '</title><script>bad()</script>', self.tmp.name)
        dom = Elements(page)
        self.assertEqual(len(dom.find('script')), 1)
        self.assertFalse(dom.find('iframe'))
        self.assertFalse(any(a.get('onerror') for _, a in dom.tags))

    def test_artifacts_outside_run_directory_are_never_embedded(self):
        with tempfile.TemporaryDirectory() as other:
            secret = Path(other) / 'private.md'
            secret.write_text('NOT_FOR_PUBLICATION_7dc51')
            self.results[0]['artifacts'] = [str(secret)]
            # Symlinks must not bypass the existing artifact boundary either.
            link = Path(self.tmp.name) / 'outside.md'
            link.symlink_to(secret)
            self.results[0]['artifacts'].append(str(link))
            page = build_run_page(self.results, 'test-run', self.tmp.name)
            self.assertNotIn('NOT_FOR_PUBLICATION_7dc51', page)
            self.assertIn('Text artifact unavailable', page)

    def test_missing_preview_and_error_are_distinct(self):
        self.assertEqual(self.dom.find('article', id='task-4')[0]['data-preview'], 'missing')
        self.assertEqual(self.dom.find('article', id='task-4')[0]['data-status'], 'pending')
        self.assertEqual(self.dom.find('article', id='task-5')[0]['data-status'], 'error')
        self.assertIn('PNG preview unavailable', self.page)
        self.assertIn('Chrome &lt;capture&gt; failed', self.page)

    def test_full_note_is_retained_and_wrap_rules_present(self):
        self.assertIn('2.485962' * 400, self.page)
        self.assertIn('1234567890' * 300, self.page)
        self.assertIn('white-space:pre-wrap', self.page)
        self.assertIn('overflow-wrap:anywhere', self.page)
        self.assertTrue(self.dom.find('details', **{'class': 'reading', 'open': None}))

    def test_empty_run(self):
        page = build_run_page([], 'empty-run', self.tmp.name)
        self.assertIn('No task results yet', page)
        self.assertNotIn('nan', page.lower())
        self.assertFalse(Elements(page).find('article'))

    def test_no_external_resources(self):
        for _, attrs in self.dom.tags:
            src = attrs.get('src', '')
            self.assertFalse(src.startswith(('https://', 'http://', '//')))
        self.assertNotIn('@import', self.page)
        self.assertNotIn('fetch(', self.page)

    def test_static_index_does_not_interpolate_untrusted_html(self):
        page = build_index()
        self.assertNotIn('__CSS__', page)
        self.assertNotIn('innerHTML', page)
        self.assertIn('textContent', page)


if __name__ == '__main__':
    unittest.main()
