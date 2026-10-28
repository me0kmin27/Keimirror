"""Deterministic static output and cache invalidation contracts."""
import importlib.util
from pathlib import Path
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


if __name__ == '__main__':
    unittest.main()
