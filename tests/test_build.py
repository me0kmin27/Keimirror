"""Deterministic static output and cache invalidation contracts."""
import importlib.util
from pathlib import Path
import re
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('build', Path(__file__).resolve().parents[1] / 'tools/build.py')
assert SPEC is not None and SPEC.loader is not None
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)


class BuildTests(unittest.TestCase):
    def test_runtime_and_styles_have_content_fingerprints(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for folder in ['templates', 'components', 'assets/css', 'assets/js']:
                (root / folder).mkdir(parents=True)
            (root / 'templates/index.html').write_text('<link href="assets/css/styles.css"><script src="assets/js/app.js"></script>')
            (root / 'assets/css/styles.css').write_text('body { color: black; }')
            (root / 'assets/js/i18n.js').write_text('export const translations = {en:{title:"Hello"}};')
            (root / 'assets/js/app.js').write_text("import { translations } from './i18n.js';\nconsole.log(translations);")
            first = build.build(root)
            self.assertRegex(first, r'assets/css/styles\.css\?v=[a-f0-9]{16}')
            self.assertRegex(first, r'assets/js/site\.js\?v=[a-f0-9]{16}')
            (root / 'assets/js/i18n.js').write_text('export const translations = {en:{title:"Updated"}};')
            self.assertNotEqual(first, build.build(root), 'Translation edits must invalidate the runtime URL too')

    def test_inline_runtime_fingerprints_every_local_dependency_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for folder in ['templates', 'components', 'assets/css', 'assets/js']:
                (root / folder).mkdir(parents=True)
            (root / 'templates/index.html').write_text('<script src="assets/js/app.js"></script>')
            (root / 'assets/css/styles.css').write_text('body {}')
            (root / 'assets/js/i18n.js').write_text('export const translations = {};')
            (root / 'assets/js/directory.js').write_text("import { translations } from './i18n.js';\nexport function enhanceDirectory() { return translations; }")
            (root / 'assets/js/app.js').write_text("import { translations } from './i18n.js';\nimport { enhanceDirectory } from './directory.js';\nenhanceDirectory();")
            first = build.build(root)
            runtime = build.bundle(root)
            self.assertNotIn('import ', runtime, 'The served bundle must not fetch modules at runtime')
            self.assertEqual(runtime.count('const translations'), 1)
            self.assertIn('function enhanceDirectory', runtime)
            (root / 'assets/js/directory.js').write_text("import { translations } from './i18n.js';\nexport function enhanceDirectory() { return translations.en; }")
            self.assertNotEqual(first, build.build(root), 'Dependency edits must invalidate the runtime URL')

    def test_delivered_frontend_has_no_retired_telemetry(self):
        self.assertFalse((build.ROOT / 'assets/js/telemetry.js').exists())
        self.assertNotIn('telemetry', build.bundle().lower())
        self.assertNotIn('/status/mirrors.json', build.bundle())
        html = build.build()
        for marker in ['data-mirror-cell', 'data-sync-state', 'data-disk-usage', 'data-sync-rx', 'data-sync-tx']:
            self.assertNotIn(marker, html)
        self.assertEqual(html.count('class="project-row"'), 10)
        self.assertEqual(html.count('class="project-links"'), 10)
        self.assertEqual(len(re.findall(r'<a href="https://mirror2?\.keiminem\.com/[a-z]+/"', html)), 20)


if __name__ == '__main__':
    unittest.main()
