"""Guard production delivery against stale or unverified static output."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DeliveryTests(unittest.TestCase):
    def test_deploy_waits_for_browser_and_build_validation(self):
        deploy = (ROOT / '.github/workflows/deploy-on-pr-merge.yml').read_text()
        self.assertIn('needs: validate', deploy)
        self.assertIn('python3 tools/build.py --check', deploy)
        self.assertIn('npm test', deploy)
        self.assertIn('cancel-in-progress: false', deploy)
        self.assertIn('--delay-updates --delete-delay', deploy)
        for folder in ['tools', 'templates', '__pycache__', 'docs']:
            self.assertIn(f'--exclude "{folder}"', deploy)

    def test_deploy_checks_out_the_exact_validated_commit(self):
        workflow = (ROOT / '.github/workflows/deploy-on-pr-merge.yml').read_text()
        validate, deploy = workflow.split('  validate:\n', 1)[1].split('  deploy:\n', 1)
        # PRs validate the merge commit; manual runs resolve main only here.
        self.assertEqual(re.findall(r'^\s+ref: (.+)$', validate, re.MULTILINE), [
            "${{ github.event.pull_request.merge_commit_sha || 'main' }}",
        ])
        self.assertRegex(validate, re.compile(
            r'^    outputs:\n'
            r'      commit_sha: \$\{\{ steps\.validated_commit\.outputs\.commit_sha \}\}$',
            re.MULTILINE,
        ))
        record_sha = 'run: echo "commit_sha=$(git rev-parse HEAD)" >> "$GITHUB_OUTPUT"'
        self.assertIn(f'        id: validated_commit\n        {record_sha}', validate)
        self.assertLess(validate.index('uses: actions/checkout@'), validate.index(record_sha))
        self.assertLess(validate.index(record_sha), validate.index('run: python3 tools/build.py --check'))
        self.assertIn('    needs: validate\n', deploy)
        self.assertEqual(re.findall(r'^\s+ref: (.+)$', deploy, re.MULTILINE), [
            '${{ needs.validate.outputs.commit_sha }}',
        ])


if __name__ == '__main__':
    unittest.main()
