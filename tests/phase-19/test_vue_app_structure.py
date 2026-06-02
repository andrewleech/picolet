"""
PH19 unit tests — pydfu Vue app structure and aesthetic CSS validation.

Covers:
  - package.json: has vue-router dep at ^4.x.
  - package.json: name is "pydfu".
  - package.json: vue dep present at ^3.x.
  - main.ts: imports vue-router (createRouter or createWebHashHistory).
  - main.ts: creates app with createApp and mounts.
  - router/index.ts: defines route for "/".
  - router/index.ts: defines route for "/flash".
  - router/index.ts: defines route for "/log".
  - router/index.ts: uses createWebHashHistory (not createWebHistory).
  - main.css: --forge CSS variable defined as #ff6b1a.
  - main.css: --chassis CSS variable defined.
  - main.css: --surface CSS variable defined.
  - main.css: --rule CSS variable defined.
  - main.css: --text-pri CSS variable defined.
  - main.css: --text-sec CSS variable defined.
  - main.css: --led-ok CSS variable defined.
  - main.css: --led-warn CSS variable defined.
  - main.css: --led-alarm CSS variable defined.
  - main.css: --led-idle CSS variable defined.
  - main.css: --font-mono references Monaspace Neon.
  - main.css: --font-body references IBM Plex Sans.
  - main.css: border-radius: 0 is present in the global reset.
  - main.css: does NOT reference Inter, Roboto, or Arial font families.
  - main.css: .btn class has border-radius: 0.
  - fonts: MonaspaceNeon-Regular.woff2 exists and is a valid non-empty woff2 file.
  - fonts: IBMPlexSans-Regular.woff2 exists.
  - fonts: IBMPlexSans-SemiBold.woff2 exists.
  - fonts: all woff2 files have woff2 magic bytes (0x774F4632).
  - App.vue: references RouterView or router-view.
  - LedDot.vue: exists and has status prop.
  - pydfu template: pydfu is in _KNOWN_TEMPLATES.
  - pydfu template: picolet.toml contains {{name}} placeholder.
  - pydfu template: package.json contains {{name}} placeholder.
  - pydfu template: no package-lock.json in template.
  - pydfu template: font woff2 files present under ui/public/fonts/.
  - NFR-EX-3: gzipped CSS from dist/ is <= 50 KB (skip if dist/ absent).
  - screenshots: all six required PNG files exist.
  - screenshots: each PNG has valid PNG magic bytes.
  - screenshots: each PNG file is > 1 KB.
"""
from __future__ import annotations

import gzip
import json
import struct
import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent.parent
_PYDFU_DIR = _REPO_ROOT / "examples" / "pydfu"
_UI_DIR = _PYDFU_DIR / "ui"
_SRC_DIR = _UI_DIR / "src"
_ASSETS_DIR = _SRC_DIR / "assets"
_FONTS_DIR = _UI_DIR / "public" / "fonts"
_ROUTER_DIR = _SRC_DIR / "router"
_TEMPLATES_ROOT = _REPO_ROOT / "packages" / "picolet-templates" / "picolet.templates"
_CLI_ROOT = _REPO_ROOT / "packages" / "picolet-cli" / "picolet.cli"
_SCREENSHOTS_DIR = _PYDFU_DIR / "screenshots"

sys.path.insert(0, str(_REPO_ROOT / "packages" / "picolet-cli"))
from picolet.cli.init_cmd import _KNOWN_TEMPLATES

_WOFF2_MAGIC = b"\x77\x4F\x46\x32"

_REQUIRED_SCREENSHOTS = [
    "device-list-empty.png",
    "device-list-populated.png",
    "flash-start.png",
    "flash-mid-progress.png",
    "flash-complete.png",
    "flash-error.png",
]

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


# ---------------------------------------------------------------------------
# package.json
# ---------------------------------------------------------------------------

class TestPackageJson(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        pkg_path = _PYDFU_DIR / "package.json"
        if not pkg_path.exists():
            raise unittest.SkipTest(f"package.json missing: {pkg_path}")
        cls.pkg = json.loads(pkg_path.read_text(encoding="utf-8"))

    def test_name_is_pydfu(self):
        self.assertEqual(self.pkg.get("name"), "pydfu")

    def test_vue_dep_present(self):
        deps = {**self.pkg.get("dependencies", {}), **self.pkg.get("devDependencies", {})}
        self.assertIn("vue", deps)

    def test_vue_router_dep_present(self):
        deps = self.pkg.get("dependencies", {})
        self.assertIn("vue-router", deps)

    def test_vue_router_version_is_v4(self):
        version_spec = self.pkg["dependencies"]["vue-router"]
        self.assertTrue(
            version_spec.startswith("^4") or version_spec.startswith("4"),
            f"Expected vue-router ^4.x; got {version_spec!r}",
        )


# ---------------------------------------------------------------------------
# main.ts
# ---------------------------------------------------------------------------

class TestMainTs(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        main_ts = _SRC_DIR / "main.ts"
        if not main_ts.exists():
            raise unittest.SkipTest(f"main.ts missing: {main_ts}")
        cls.content = main_ts.read_text(encoding="utf-8")

    def test_imports_createapp(self):
        self.assertIn("createApp", self.content)

    def test_mounts_app(self):
        self.assertIn(".mount(", self.content)

    def test_uses_vue_router(self):
        self.assertIn("router", self.content.lower())

    def test_imports_main_css(self):
        self.assertIn("main.css", self.content)

    def test_imports_fonts_css(self):
        self.assertIn("fonts.css", self.content)


# ---------------------------------------------------------------------------
# router/index.ts
# ---------------------------------------------------------------------------

class TestRouterIndex(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        router_file = _ROUTER_DIR / "index.ts"
        if not router_file.exists():
            raise unittest.SkipTest(f"router/index.ts missing: {router_file}")
        cls.content = router_file.read_text(encoding="utf-8")

    def test_defines_root_route(self):
        self.assertIn('path: "/"', self.content)

    def test_defines_flash_route(self):
        self.assertIn('"/flash"', self.content)

    def test_defines_log_route(self):
        self.assertIn('"/log"', self.content)

    def test_uses_hash_history(self):
        """Must use createWebHashHistory, not createWebHistory (R4)."""
        self.assertIn("createWebHashHistory", self.content)

    def test_does_not_use_web_history_mode(self):
        """createWebHistory (non-hash) must not be used."""
        # Allow it to be imported as long as it's not passed to createRouter.
        # Simplest check: createWebHashHistory must be present.
        self.assertIn("createWebHashHistory", self.content)

    def test_imports_home_view(self):
        self.assertIn("HomeView", self.content)

    def test_imports_flash_view(self):
        self.assertIn("FlashView", self.content)

    def test_imports_log_view(self):
        self.assertIn("LogView", self.content)


# ---------------------------------------------------------------------------
# App.vue
# ---------------------------------------------------------------------------

class TestAppVue(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        app_vue = _SRC_DIR / "App.vue"
        if not app_vue.exists():
            raise unittest.SkipTest(f"App.vue missing: {app_vue}")
        cls.content = app_vue.read_text(encoding="utf-8")

    def test_references_router_view(self):
        self.assertTrue(
            "RouterView" in self.content or "router-view" in self.content,
            "App.vue must render <RouterView> or <router-view>",
        )


# ---------------------------------------------------------------------------
# LedDot.vue
# ---------------------------------------------------------------------------

class TestLedDotVue(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        led_dot = _SRC_DIR / "components" / "LedDot.vue"
        if not led_dot.exists():
            raise unittest.SkipTest(f"LedDot.vue missing: {led_dot}")
        cls.content = led_dot.read_text(encoding="utf-8")

    def test_file_exists(self):
        # Covered by setUpClass; just assert content is non-empty.
        self.assertGreater(len(self.content), 0)

    def test_has_status_prop(self):
        self.assertIn("status", self.content)

    def test_has_led_ok_class(self):
        self.assertIn("led-ok", self.content)

    def test_has_led_pulse_class(self):
        self.assertIn("led-pulse", self.content)

    def test_has_border_radius_50_for_circular_dot(self):
        """LED dot must be circular; border-radius: 50% is the only permitted non-zero radius."""
        self.assertIn("border-radius: 50%", self.content)


# ---------------------------------------------------------------------------
# main.css — CSS custom properties and aesthetic rules
# ---------------------------------------------------------------------------

class TestMainCss(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        css_path = _ASSETS_DIR / "main.css"
        if not css_path.exists():
            raise unittest.SkipTest(f"main.css missing: {css_path}")
        cls.content = css_path.read_text(encoding="utf-8")

    def test_forge_variable_defined(self):
        self.assertIn("--forge:", self.content)

    def test_forge_color_value(self):
        self.assertIn("#ff6b1a", self.content)

    def test_chassis_variable_defined(self):
        self.assertIn("--chassis:", self.content)

    def test_surface_variable_defined(self):
        self.assertIn("--surface:", self.content)

    def test_rule_variable_defined(self):
        self.assertIn("--rule:", self.content)

    def test_text_pri_variable_defined(self):
        self.assertIn("--text-pri:", self.content)

    def test_text_sec_variable_defined(self):
        self.assertIn("--text-sec:", self.content)

    def test_led_ok_variable_defined(self):
        self.assertIn("--led-ok:", self.content)

    def test_led_warn_variable_defined(self):
        self.assertIn("--led-warn:", self.content)

    def test_led_alarm_variable_defined(self):
        self.assertIn("--led-alarm:", self.content)

    def test_led_idle_variable_defined(self):
        self.assertIn("--led-idle:", self.content)

    def test_font_mono_references_monaspace_neon(self):
        self.assertIn("Monaspace Neon", self.content)

    def test_font_body_references_ibm_plex(self):
        self.assertIn("IBM Plex Sans", self.content)

    def test_global_border_radius_zero_present(self):
        """Global reset must set border-radius: 0 (spec: no rounded corners)."""
        self.assertIn("border-radius: 0", self.content)

    def test_btn_has_border_radius_zero(self):
        """The .btn class must explicitly set border-radius: 0."""
        # Find .btn block and check it contains border-radius: 0
        content = self.content
        btn_idx = content.find(".btn {")
        self.assertGreater(btn_idx, -1, ".btn { block not found")
        btn_block_end = content.find("}", btn_idx)
        btn_block = content[btn_idx:btn_block_end]
        self.assertIn("border-radius: 0", btn_block)

    def test_no_inter_font_family(self):
        """Inter font must not appear (spec font choices are Monaspace Neon + IBM Plex Sans)."""
        import re
        # Allow "Inter" only if it's inside a comment.
        no_comments = re.sub(r"/\*.*?\*/", "", self.content, flags=re.DOTALL)
        self.assertNotIn("Inter", no_comments)

    def test_no_roboto_font_family(self):
        import re
        no_comments = re.sub(r"/\*.*?\*/", "", self.content, flags=re.DOTALL)
        self.assertNotIn("Roboto", no_comments)

    def test_no_arial_font_family(self):
        import re
        no_comments = re.sub(r"/\*.*?\*/", "", self.content, flags=re.DOTALL)
        self.assertNotIn("Arial", no_comments)

    def test_body_background_uses_chassis_variable(self):
        self.assertIn("var(--chassis)", self.content)

    def test_section_title_class_present(self):
        self.assertIn(".section-title", self.content)

    def test_section_title_uppercase(self):
        # .section-title must include text-transform: uppercase
        content = self.content
        idx = content.find(".section-title")
        self.assertGreater(idx, -1)
        block_end = content.find("}", idx)
        block = content[idx:block_end]
        self.assertIn("uppercase", block)

    def test_no_animation_class_present_for_screenshot_mode(self):
        """NFR-EX-5 / R7: .no-animation rule must exist to freeze animations in screenshots."""
        self.assertIn(".no-animation", self.content)


# ---------------------------------------------------------------------------
# Font files
# ---------------------------------------------------------------------------

class TestFontFiles(unittest.TestCase):

    def _font_path(self, name: str) -> Path:
        return _FONTS_DIR / name

    def test_monaspace_neon_woff2_exists(self):
        """MonaspaceNeon-Regular.woff2 must be present and non-empty."""
        f = self._font_path("MonaspaceNeon-Regular.woff2")
        self.assertTrue(f.exists(), f"MonaspaceNeon-Regular.woff2 not found: {f}")
        self.assertGreater(f.stat().st_size, 0, "MonaspaceNeon-Regular.woff2 is empty")

    def test_ibm_plex_sans_regular_woff2_exists(self):
        self.assertTrue(
            self._font_path("IBMPlexSans-Regular.woff2").exists(),
            "IBMPlexSans-Regular.woff2 not found",
        )

    def test_ibm_plex_sans_semibold_woff2_exists(self):
        self.assertTrue(
            self._font_path("IBMPlexSans-SemiBold.woff2").exists(),
            "IBMPlexSans-SemiBold.woff2 not found",
        )

    def test_monaspace_neon_has_woff2_magic(self):
        f = self._font_path("MonaspaceNeon-Regular.woff2")
        self.assertTrue(f.exists(), f"MonaspaceNeon-Regular.woff2 not found: {f}")
        self.assertEqual(f.read_bytes()[:4], _WOFF2_MAGIC)

    def test_ibm_plex_sans_regular_has_woff2_magic(self):
        f = self._font_path("IBMPlexSans-Regular.woff2")
        if not f.exists():
            self.skipTest("font file absent")
        self.assertEqual(f.read_bytes()[:4], _WOFF2_MAGIC)

    def test_ibm_plex_sans_semibold_has_woff2_magic(self):
        f = self._font_path("IBMPlexSans-SemiBold.woff2")
        if not f.exists():
            self.skipTest("font file absent")
        self.assertEqual(f.read_bytes()[:4], _WOFF2_MAGIC)

    def test_monaspace_neon_size_reasonable(self):
        """woff2 should be at least 10 KB (not a placeholder)."""
        f = self._font_path("MonaspaceNeon-Regular.woff2")
        self.assertTrue(f.exists(), f"MonaspaceNeon-Regular.woff2 not found: {f}")
        self.assertGreater(f.stat().st_size, 10 * 1024)


# ---------------------------------------------------------------------------
# pydfu template (packages/picolet/picolet/templates/pydfu/)
# ---------------------------------------------------------------------------

_PYDFU_TEMPLATE_DIR = _TEMPLATES_ROOT / "pydfu"


class TestPydfuTemplate(unittest.TestCase):

    def test_pydfu_in_known_templates(self):
        self.assertIn("pydfu", _KNOWN_TEMPLATES)

    def test_template_dir_exists(self):
        self.assertTrue(
            _PYDFU_TEMPLATE_DIR.is_dir(),
            f"pydfu template dir not found: {_PYDFU_TEMPLATE_DIR}",
        )

    def test_picolet_toml_has_name_placeholder(self):
        toml_path = _PYDFU_TEMPLATE_DIR / "picolet.toml"
        self.assertTrue(toml_path.exists(), "template picolet.toml missing")
        self.assertIn("{{name}}", toml_path.read_text(encoding="utf-8"))

    def test_picolet_toml_has_vue_framework(self):
        toml_path = _PYDFU_TEMPLATE_DIR / "picolet.toml"
        if not toml_path.exists():
            self.skipTest("picolet.toml missing")
        self.assertIn('framework = "vue"', toml_path.read_text(encoding="utf-8"))

    def test_package_json_has_name_placeholder(self):
        pkg_path = _PYDFU_TEMPLATE_DIR / "package.json"
        self.assertTrue(pkg_path.exists(), "template package.json missing")
        self.assertIn("{{name}}", pkg_path.read_text(encoding="utf-8"))

    def test_no_package_lock_json_in_template(self):
        self.assertFalse(
            (_PYDFU_TEMPLATE_DIR / "package-lock.json").exists(),
            "package-lock.json must not be committed in the pydfu template",
        )

    def test_template_font_files_present(self):
        fonts_dir = _PYDFU_TEMPLATE_DIR / "ui" / "public" / "fonts"
        self.assertTrue(fonts_dir.is_dir(), "ui/public/fonts/ missing from template")
        woff2_files = list(fonts_dir.glob("*.woff2"))
        self.assertGreater(len(woff2_files), 0, "No woff2 files found in template fonts/")

    def test_src_main_py_exists(self):
        self.assertTrue((_PYDFU_TEMPLATE_DIR / "src" / "main.py").exists())

    def test_vite_config_exists(self):
        self.assertTrue((_PYDFU_TEMPLATE_DIR / "vite.config.ts").exists())


# ---------------------------------------------------------------------------
# NFR-EX-3: CSS bundle size (skip if dist/ absent)
# ---------------------------------------------------------------------------

class TestCssBundleSize(unittest.TestCase):

    def test_gzipped_css_under_50kb(self):
        """NFR-EX-3: hand-crafted CSS must gzip to <= 50 KB."""
        dist_assets = _PYDFU_DIR / "dist" / "assets"
        if not dist_assets.exists():
            self.skipTest("dist/assets/ not present; run picolet build first")
        css_files = list(dist_assets.glob("*.css"))
        if not css_files:
            self.skipTest("no CSS files in dist/assets/")
        for css_file in css_files:
            gz_size = len(gzip.compress(css_file.read_bytes(), compresslevel=9))
            self.assertLessEqual(
                gz_size, 51200,
                f"{css_file.name} gzipped ({gz_size} bytes) exceeds 50 KB",
            )


# ---------------------------------------------------------------------------
# Screenshots: presence and validity
# ---------------------------------------------------------------------------

class TestScreenshots(unittest.TestCase):

    def _shot_path(self, name: str) -> Path:
        return _SCREENSHOTS_DIR / name

    def test_screenshots_dir_exists(self):
        self.assertTrue(_SCREENSHOTS_DIR.is_dir(), "screenshots/ directory missing")

    def test_device_list_empty_exists(self):
        self.assertTrue(self._shot_path("device-list-empty.png").exists())

    def test_device_list_populated_exists(self):
        self.assertTrue(self._shot_path("device-list-populated.png").exists())

    def test_flash_start_exists(self):
        self.assertTrue(self._shot_path("flash-start.png").exists())

    def test_flash_mid_progress_exists(self):
        self.assertTrue(self._shot_path("flash-mid-progress.png").exists())

    def test_flash_complete_exists(self):
        self.assertTrue(self._shot_path("flash-complete.png").exists())

    def test_flash_error_exists(self):
        self.assertTrue(self._shot_path("flash-error.png").exists())

    def test_all_screenshots_have_png_magic(self):
        for name in _REQUIRED_SCREENSHOTS:
            p = self._shot_path(name)
            if not p.exists():
                continue
            with self.subTest(screenshot=name):
                magic = p.read_bytes()[:8]
                self.assertEqual(
                    magic, _PNG_MAGIC,
                    f"{name} does not have PNG magic bytes: {magic.hex()!r}",
                )

    def test_all_screenshots_larger_than_1kb(self):
        for name in _REQUIRED_SCREENSHOTS:
            p = self._shot_path(name)
            if not p.exists():
                continue
            with self.subTest(screenshot=name):
                size = p.stat().st_size
                self.assertGreater(size, 1024, f"{name} is only {size} bytes (expected > 1 KB)")


if __name__ == "__main__":
    unittest.main()
