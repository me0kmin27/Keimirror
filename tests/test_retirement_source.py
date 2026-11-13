"""Offline source contract for the retired custom mirror telemetry feature.

These checks do not establish live host cleanup or mirror availability.
"""
import hashlib
import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
BASE = "9c1d08a6c5fee2925fcfbbd720321df7df9ac127"


class RetirementSourceTests(unittest.TestCase):
    def test_retired_implementation_and_installable_trees_are_absent(self):
        for relative in ("telemetry", "ops/telemetry"):
            with self.subTest(path=relative):
                path = ROOT / relative
                self.assertFalse(
                    path.exists() or path.is_symlink(),
                    f"retired implementation/installables remain at {relative}",
                )

    def test_current_guidance_explains_retirement_not_installation(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("## 텔레메트리 종료", readme)
        self.assertIn("docs/telemetry-retirement.md", readme)
        self.assertIn("docs/archive/telemetry/README.md", readme)
        for old_current_link in (
            "docs/telemetry-operations.md", "docs/telemetry-contract.md",
            "docs/operations-history-2026-10.md",
        ):
            self.assertNotIn(old_current_link, readme)
        guidance = ROOT / "docs/telemetry-retirement.md"
        self.assertTrue(guidance.is_file(), "current retirement runbook is missing")
        text = guidance.read_text(encoding="utf-8")
        for boundary in (
            "No live cleanup or deployment is established by this document.",
            "nginx -t", "graceful reload", "chrony", "Grafana",
            "No USR2", "native synchronization", "fresh inventory",
        ):
            with self.subTest(boundary=boundary):
                self.assertIn(boundary, " ".join(text.split()))

    def test_archive_is_hash_pinned_evidence_not_installable_code(self):
        archive = ROOT / "docs/archive/telemetry"
        manifest_path = archive / "manifest.json"
        self.assertTrue(manifest_path.is_file(), "historical evidence manifest is missing")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["base_commit"], BASE)
        self.assertFalse(manifest["deployment_authorized"])
        self.assertTrue(manifest["files"])
        sources = [entry["original_path"] for entry in manifest["files"]]
        self.assertEqual(len(sources), len(set(sources)), "duplicate historical paths")
        expected = {"README.md", "manifest.json"}
        for entry in manifest["files"]:
            relative = Path(entry["original_path"])
            with self.subTest(path=str(relative)):
                self.assertFalse(relative.is_absolute())
                self.assertNotIn("..", relative.parts)
                expected.add(relative.as_posix())
                saved = archive / relative
                self.assertFalse(saved.is_symlink())
                self.assertTrue(saved.is_file())
                self.assertIn(saved.suffix, (".md", ".json"))
                self.assertEqual(saved.stat().st_mode & 0o111, 0)
                self.assertEqual(hashlib.sha256(saved.read_bytes()).hexdigest(), entry["sha256"])
        actual = {p.relative_to(archive).as_posix() for p in archive.rglob("*") if not p.is_dir()}
        self.assertEqual(actual, expected)
        index = (archive / "README.md").read_text(encoding="utf-8")
        self.assertIn("Historical only", index)
        self.assertIn("not current installation instructions", index)
        for source in sources:
            self.assertIn(f"blob/{BASE}/{source}", index)

    def test_retired_python_runtime_tests_and_fixtures_are_absent(self):
        retired_patterns = (
            "test_telemetry_*", "test_rpc_*", "test_http_reexec*", "test_harness*",
            "test_private_reasons.py", "test_live_deployment.py",
            "holder_recovery_runtime.py", "http_reexec_capture.py",
            "http_reexec_nchan_runtime.py", "proc_status_seek_fixture.c",
        )
        for pattern in retired_patterns:
            with self.subTest(pattern=pattern):
                self.assertEqual(list((ROOT / "tests").glob(pattern)), [])

    def test_time_sync_configuration_is_preserved(self):
        path = ROOT / "ops/time/chrony.conf"
        self.assertEqual(
            hashlib.sha256(path.read_bytes()).hexdigest(),
            "c5e8f6903e6970714a3ce6ea64de16127a50db86917b4e03ca96429ba26b028a",
            "telemetry retirement must not alter the retained time configuration",
        )

    def test_active_documentation_links_resolve(self):
        # Historical document bytes/links are deliberately frozen; their index
        # links to the immutable Git version for removed implementation targets.
        current = (
            ROOT / "README.md", ROOT / "docs/telemetry-retirement.md",
            ROOT / "docs/archive/telemetry/README.md",
        )
        for path in current:
            text = path.read_text(encoding="utf-8")
            for target in re.findall(r"\]\(([^)]+)\)", text):
                if "://" in target or target.startswith(("mailto:", "#")):
                    continue
                with self.subTest(document=str(path.relative_to(ROOT)), target=target):
                    self.assertTrue((path.parent / target.split("#", 1)[0]).exists())


if __name__ == "__main__":
    unittest.main()
