"""Regression coverage for generated app-template CI workflows."""

from pathlib import Path
import tempfile
import unittest

from picolet.cli.init_cmd import _copy_template


_REPO_ROOT = Path(__file__).resolve().parents[1]
_TEMPLATE_ROOT = _REPO_ROOT / "packages" / "picolet" / "picolet" / "templates"
_FRONTEND_TEMPLATES = {"hello-vue", "pydfu", "notes", "config-editor", "dashboard"}
_MIRRORED_TEMPLATES = {"pydfu", "notes", "config-editor", "dashboard"}


class TemplateWorkflowTests(unittest.TestCase):
    def test_workflows_are_generated_with_windows_build(self):
        for template in sorted(
            path.name
            for path in _TEMPLATE_ROOT.iterdir()
            if path.is_dir() and (path / "picolet.toml").is_file()
        ):
            with self.subTest(template=template), tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp) / "generated-app"
                _copy_template(_TEMPLATE_ROOT / template, output, "generated-app")
                workflow = (output / ".github" / "workflows" / "build.yml").read_text()

                self.assertIn("name: windows-x64", workflow)
                self.assertNotIn("{{name}}", workflow)
                self.assertIn("runs-on: ubuntu-latest", workflow)
                self.assertIn("uv tool install mpy-cross", workflow)
                self.assertIn("picolet build --target windows-x64", workflow)
                self.assertIn("path: target/windows-x64/**", workflow)
                self.assertEqual("node-version: '22'" in workflow, template in _FRONTEND_TEMPLATES)
                self.assertEqual("npm run build" in workflow, template in _FRONTEND_TEMPLATES)

    def test_mirrored_templates_match_example_workflows(self):
        example_root = _REPO_ROOT / "examples"
        for template in sorted(_MIRRORED_TEMPLATES):
            with self.subTest(template=template):
                template_workflow = (
                    _TEMPLATE_ROOT / template / ".github" / "workflows" / "build.yml"
                ).read_bytes()
                example_workflow = (
                    example_root / template / ".github" / "workflows" / "build.yml"
                ).read_bytes()
                self.assertEqual(template_workflow, example_workflow)



if __name__ == "__main__":
    unittest.main()
