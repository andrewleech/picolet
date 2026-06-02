"""
Phase 20 unit tests — notes app structure, CSS aesthetic, and screenshots.

Covers:
  - package.json: name is "notes".
  - package.json: vue dep present at ^3.x.
  - package.json: vue-router dep present at ^4.x.
  - package.json: marked dep present.
  - main.ts: imports createApp and mounts.
  - main.ts: imports fonts.css.
  - main.ts: imports main.css.
  - router/index.ts: uses createWebHashHistory.
  - router/index.ts: defines route for "/".
  - router/index.ts: defines route for "/edit/:slug".
  - router/index.ts: defines route for "/about".
  - router/index.ts: imports ListView, EditView, AboutView.
  - App.vue: renders RouterView or router-view.
  - main.css: --paper defined as #f7f3ed.
  - main.css: --ink defined as #1a1715.
  - main.css: --mark defined as #c4392b.
  - main.css: --ink-soft defined.
  - main.css: --rule defined.
  - main.css: --surface defined.
  - main.css: --font-serif references Source Serif 4.
  - main.css: --font-sans references Source Sans 3.
  - main.css: h1 has font-style: italic.
  - main.css: no border-radius value > 2px (except unsaved-dot's 50%).
  - main.css: no Inter, Roboto, Arial, or system-ui as a bare font-family declaration.
  - main.css: no box-shadow on layout panels (.list-pane, .editor-pane, .app-columns).
  - main.css: --mark / #c4392b appears only in .unsaved-dot rule.
  - main.css: .no-animation class present (screenshot mode NFR-EX-5).
  - EditView.vue: no <button> with visible text "save" (case-insensitive).
  - EditView.vue: references unsaved-dot class.
  - EditView.vue: uses Ctrl/metaKey + s keyboard shortcut.
  - EditView.vue: uses rename_note IPC command for title saves.
  - fonts.css: @font-face for Source Serif 4.
  - fonts.css: @font-face for Source Sans 3.
  - fonts: source-serif-4-roman.woff2 exists and is non-empty.
  - fonts: source-serif-4-italic.woff2 exists and is non-empty.
  - fonts: source-sans-3.woff2 exists and is non-empty.
  - fonts: all woff2 files have woff2 magic bytes (0x774F4632).
  - fonts: woff2 files are > 10 KB (not placeholder stubs).
  - notes template: "notes" is in _KNOWN_TEMPLATES.
  - notes template: picolet.toml contains {{name}} placeholder.
  - notes template: package.json contains {{name}} placeholder.
  - notes template: no package-lock.json in template.
  - notes template: font woff2 files present under ui/public/fonts/.
  - notes template: src/main.py and src/notes_store.py present.
  - notes template: vite.config.ts present.
  - screenshots: all six required PNG files exist.
  - screenshots: each PNG has valid PNG magic bytes.
  - screenshots: each PNG file is > 1 KB.
  - screenshots: dimensions >= 1000x700.
  - screenshots: each PNG contains warm paper-colour pixels (~#f7f3ed).
  - screenshots: each PNG contains ink-dark pixels (~#1a1715).
  - screenshots: edit-unsaved.png contains mark-red pixels (~#c4392b).
  - screenshots: edit-typing-mid.png contains mark-red pixels (~#c4392b).
  - screenshots: edit-pristine.png does NOT contain mark-red pixels.
  - NFR-EX-3: gzipped CSS from dist/ <= 50 KB (skip if dist/ absent).
"""
from __future__ import annotations

import gzip
import json
import re
import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent.parent
_NOTES_DIR = _REPO_ROOT / "examples" / "notes"
_UI_DIR = _NOTES_DIR / "ui"
_SRC_DIR = _UI_DIR / "src"
_ASSETS_DIR = _SRC_DIR / "assets"
_FONTS_DIR = _UI_DIR / "public" / "fonts"
_ROUTER_DIR = _SRC_DIR / "router"
_TEMPLATES_ROOT = _REPO_ROOT / "packages" / "picolet-templates" / "picolet.templates"
_CLI_ROOT = _REPO_ROOT / "packages" / "picolet-cli" / "picolet.cli"
_SCREENSHOTS_DIR = _NOTES_DIR / "screenshots"

sys.path.insert(0, str(_REPO_ROOT / "packages" / "picolet-cli"))
from picolet.cli.init_cmd import _KNOWN_TEMPLATES

_WOFF2_MAGIC = b"\x77\x4F\x46\x32"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

_REQUIRED_SCREENSHOTS = [
    "list-empty.png",
    "list-populated.png",
    "edit-pristine.png",
    "edit-unsaved.png",
    "edit-typing-mid.png",
    "search-active.png",
]


# ---------------------------------------------------------------------------
# package.json
# ---------------------------------------------------------------------------

class TestPackageJson(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        pkg_path = _NOTES_DIR / "package.json"
        if not pkg_path.exists():
            raise unittest.SkipTest(f"package.json missing: {pkg_path}")
        cls.pkg = json.loads(pkg_path.read_text(encoding="utf-8"))

    def test_name_is_notes(self):
        self.assertEqual(self.pkg.get("name"), "notes")

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

    def test_marked_dep_present(self):
        deps = self.pkg.get("dependencies", {})
        self.assertIn("marked", deps, "marked must be a dependency (FR-EX-2: markdown rendering)")


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

    def test_imports_fonts_css(self):
        self.assertIn("fonts.css", self.content)

    def test_imports_main_css(self):
        self.assertIn("main.css", self.content)

    def test_uses_router(self):
        self.assertIn("router", self.content.lower())


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

    def test_uses_hash_history(self):
        self.assertIn("createWebHashHistory", self.content)

    def test_defines_root_route(self):
        self.assertIn('path: "/"', self.content)

    def test_defines_edit_slug_route(self):
        self.assertIn('"/edit/:slug"', self.content)

    def test_defines_about_route(self):
        self.assertIn('"/about"', self.content)

    def test_imports_list_view(self):
        self.assertIn("ListView", self.content)

    def test_imports_edit_view(self):
        self.assertIn("EditView", self.content)

    def test_imports_about_view(self):
        self.assertIn("AboutView", self.content)


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
# EditView.vue — behavioural assertions
# ---------------------------------------------------------------------------

class TestEditView(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        edit_vue = _SRC_DIR / "views" / "EditView.vue"
        if not edit_vue.exists():
            raise unittest.SkipTest(f"EditView.vue missing: {edit_vue}")
        cls.content = edit_vue.read_text(encoding="utf-8")

    def test_no_save_button_text(self):
        """Spec mandates no visible save button (F6 in plan)."""
        # Strip script/style blocks; check template section only.
        template_match = re.search(r"<template>(.*?)</template>", self.content, re.DOTALL)
        template = template_match.group(1) if template_match else self.content
        # Look for <button ...> elements containing "save" as visible text.
        buttons = re.findall(r"<button[^>]*>([^<]*)</button>", template, re.IGNORECASE)
        save_buttons = [b for b in buttons if "save" in b.lower()]
        self.assertEqual(
            save_buttons, [],
            f"Found button(s) with visible 'save' text: {save_buttons!r}",
        )

    def test_references_unsaved_dot_class(self):
        self.assertIn("unsaved-dot", self.content)

    def test_ctrl_s_keyboard_shortcut_present(self):
        """The save-on-Ctrl+S handler must be implemented (F6 in plan)."""
        self.assertIn("ctrlKey", self.content)
        self.assertIn("metaKey", self.content)

    def test_uses_rename_note_ipc(self):
        """rename_note is the extra command the dev added beyond the original 5."""
        self.assertIn("rename_note", self.content)

    def test_loading_false_before_nexttick_in_try(self):
        """loading.value = false must precede await nextTick() in the try block.

        The editor-pane is guarded by v-if="!loading", so titleEl is only
        mounted after loading becomes false.  Setting innerText before that
        yields a null ref and a silently empty title on every note open.
        """
        script_match = re.search(r"<script[^>]*>(.*?)</script>", self.content, re.DOTALL)
        self.assertIsNotNone(script_match, "No <script> block found in EditView.vue")
        script = script_match.group(1)

        # Locate the try block that contains load_note.
        try_start = script.find("try {")
        catch_start = script.find("} catch", try_start)
        self.assertGreater(try_start, -1, "No try block found")
        self.assertGreater(catch_start, try_start, "No catch block found after try")
        try_body = script[try_start:catch_start]

        loading_pos = try_body.find("loading.value = false")
        nexttick_pos = try_body.find("await nextTick()")
        self.assertGreater(
            loading_pos, -1,
            "loading.value = false not found in try block — title DOM write will fail",
        )
        self.assertGreater(
            nexttick_pos, -1,
            "await nextTick() not found in try block",
        )
        self.assertLess(
            loading_pos,
            nexttick_pos,
            "loading.value = false must come before await nextTick() in try block "
            "so that the editor-pane v-if renders before titleEl.innerText is set",
        )


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
        # Strip comments once for reuse.
        cls.content_no_comments = re.sub(r"/\*.*?\*/", "", cls.content, flags=re.DOTALL)

    def test_paper_variable_defined(self):
        self.assertIn("--paper:", self.content)

    def test_paper_color_value(self):
        self.assertIn("#f7f3ed", self.content)

    def test_ink_variable_defined(self):
        self.assertIn("--ink:", self.content)

    def test_ink_color_value(self):
        self.assertIn("#1a1715", self.content)

    def test_mark_variable_defined(self):
        self.assertIn("--mark:", self.content)

    def test_mark_color_value(self):
        self.assertIn("#c4392b", self.content)

    def test_ink_soft_variable_defined(self):
        self.assertIn("--ink-soft:", self.content)

    def test_rule_variable_defined(self):
        self.assertIn("--rule:", self.content)

    def test_surface_variable_defined(self):
        self.assertIn("--surface:", self.content)

    def test_font_serif_references_source_serif_4(self):
        self.assertIn("Source Serif 4", self.content)

    def test_font_sans_references_source_sans_3(self):
        self.assertIn("Source Sans 3", self.content)

    def test_h1_has_font_style_italic(self):
        """h1 must be italic per aesthetic spec."""
        h1_idx = self.content.find("h1 {")
        self.assertGreater(h1_idx, -1, "h1 { block not found")
        block_end = self.content.find("}", h1_idx)
        block = self.content[h1_idx:block_end]
        self.assertIn("font-style: italic", block)

    def test_no_border_radius_above_2px_outside_dot(self):
        """No layout panel should have rounded corners (spec: sharp, editorial).
        The only permitted non-zero radius is border-radius: 50% on .unsaved-dot."""
        # Find all border-radius declarations.
        # Acceptable: 0, 0px, 50% (the circular dot), border-box (not a radius).
        forbidden_pattern = re.compile(
            r"border-radius:\s*([3-9]\d*px|\d{3,}px)",  # 3px or more
            re.IGNORECASE,
        )
        matches = forbidden_pattern.findall(self.content_no_comments)
        self.assertEqual(
            matches, [],
            f"Found border-radius values > 2px: {matches!r}",
        )

    def test_no_inter_font_family(self):
        self.assertNotIn("Inter", self.content_no_comments)

    def test_no_roboto_font_family(self):
        self.assertNotIn("Roboto", self.content_no_comments)

    def test_no_arial_font_family(self):
        self.assertNotIn("Arial", self.content_no_comments)

    def test_no_box_shadow_on_layout_panels(self):
        """Editor and list panes must have no box-shadow (spec: flat, no card elevation)."""
        # Find .editor-pane, .list-pane, .app-columns blocks and assert no box-shadow.
        panels = [".editor-pane", ".list-pane", ".app-columns"]
        for panel in panels:
            with self.subTest(panel=panel):
                idx = self.content_no_comments.find(panel + " {")
                if idx == -1:
                    idx = self.content_no_comments.find(panel + "{")
                if idx == -1:
                    continue  # panel not in this file; skip
                block_end = self.content_no_comments.find("}", idx)
                block = self.content_no_comments[idx:block_end]
                self.assertNotIn(
                    "box-shadow", block,
                    f"{panel} has box-shadow — spec forbids it",
                )

    def test_mark_color_only_in_unsaved_dot(self):
        """var(--mark) must appear only in the .unsaved-dot rule (not as a general accent).
        The spec states: 'The dot is the only place --mark appears in the entire app.'
        The variable definition itself (--mark: #c4392b in :root) is excluded from this check;
        only uses of var(--mark) as a property value are checked."""
        no_comments = self.content_no_comments
        # Check only var(--mark) *uses* — the --mark: definition is the variable declaration,
        # not a use. A use looks like "background: var(--mark)" or "color: var(--mark)".
        mark_uses = [
            m.start() for m in re.finditer(r"var\(--mark\)", no_comments)
        ]
        for pos in mark_uses:
            snippet_start = max(0, pos - 300)
            snippet = no_comments[snippet_start:pos]
            last_brace = snippet.rfind("{")
            rule_text = snippet[last_brace - 100: last_brace + 1] if last_brace > 0 else snippet
            self.assertIn(
                "unsaved-dot", rule_text,
                f"var(--mark) used outside .unsaved-dot context near pos {pos}: {rule_text!r}",
            )

    def test_no_animation_class_present(self):
        """NFR-EX-5: .no-animation rule must exist for screenshot mode."""
        self.assertIn(".no-animation", self.content)


# ---------------------------------------------------------------------------
# fonts.css — @font-face declarations
# ---------------------------------------------------------------------------

class TestFontsCss(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        fonts_css = _ASSETS_DIR / "fonts.css"
        if not fonts_css.exists():
            raise unittest.SkipTest(f"fonts.css missing: {fonts_css}")
        cls.content = fonts_css.read_text(encoding="utf-8")

    def test_source_serif_4_font_face_declared(self):
        self.assertIn("Source Serif 4", self.content)

    def test_source_sans_3_font_face_declared(self):
        self.assertIn("Source Sans 3", self.content)

    def test_font_display_block_set(self):
        """font-display: block prevents invisible text flash (F10 plan footnote)."""
        self.assertIn("font-display: block", self.content)


# ---------------------------------------------------------------------------
# Font woff2 files
# ---------------------------------------------------------------------------

class TestFontFiles(unittest.TestCase):

    def _font_path(self, name: str) -> Path:
        return _FONTS_DIR / name

    def test_source_serif_4_roman_exists(self):
        self.assertTrue(
            self._font_path("source-serif-4-roman.woff2").exists(),
            "source-serif-4-roman.woff2 not found in ui/public/fonts/",
        )

    def test_source_serif_4_italic_exists(self):
        self.assertTrue(
            self._font_path("source-serif-4-italic.woff2").exists(),
            "source-serif-4-italic.woff2 not found in ui/public/fonts/",
        )

    def test_source_sans_3_exists(self):
        self.assertTrue(
            self._font_path("source-sans-3.woff2").exists(),
            "source-sans-3.woff2 not found in ui/public/fonts/",
        )

    def test_source_serif_4_roman_has_woff2_magic(self):
        f = self._font_path("source-serif-4-roman.woff2")
        if not f.exists():
            self.skipTest("font file absent")
        self.assertEqual(f.read_bytes()[:4], _WOFF2_MAGIC)

    def test_source_serif_4_italic_has_woff2_magic(self):
        f = self._font_path("source-serif-4-italic.woff2")
        if not f.exists():
            self.skipTest("font file absent")
        self.assertEqual(f.read_bytes()[:4], _WOFF2_MAGIC)

    def test_source_sans_3_has_woff2_magic(self):
        f = self._font_path("source-sans-3.woff2")
        if not f.exists():
            self.skipTest("font file absent")
        self.assertEqual(f.read_bytes()[:4], _WOFF2_MAGIC)

    def test_source_serif_4_roman_size_reasonable(self):
        """woff2 must be > 10 KB (not a placeholder stub)."""
        f = self._font_path("source-serif-4-roman.woff2")
        if not f.exists():
            self.skipTest("font file absent")
        self.assertGreater(f.stat().st_size, 10 * 1024)

    def test_source_serif_4_italic_size_reasonable(self):
        f = self._font_path("source-serif-4-italic.woff2")
        if not f.exists():
            self.skipTest("font file absent")
        self.assertGreater(f.stat().st_size, 10 * 1024)

    def test_source_sans_3_size_reasonable(self):
        f = self._font_path("source-sans-3.woff2")
        if not f.exists():
            self.skipTest("font file absent")
        self.assertGreater(f.stat().st_size, 10 * 1024)


# ---------------------------------------------------------------------------
# notes template (packages/picolet/picolet/templates/notes/)
# ---------------------------------------------------------------------------

_NOTES_TEMPLATE_DIR = _TEMPLATES_ROOT / "notes"


class TestNotesTemplate(unittest.TestCase):

    def test_notes_in_known_templates(self):
        self.assertIn("notes", _KNOWN_TEMPLATES)

    def test_template_dir_exists(self):
        self.assertTrue(
            _NOTES_TEMPLATE_DIR.is_dir(),
            f"notes template dir not found: {_NOTES_TEMPLATE_DIR}",
        )

    def test_picolet_toml_has_name_placeholder(self):
        toml_path = _NOTES_TEMPLATE_DIR / "picolet.toml"
        self.assertTrue(toml_path.exists(), "template picolet.toml missing")
        self.assertIn("{{name}}", toml_path.read_text(encoding="utf-8"))

    def test_picolet_toml_has_vue_framework(self):
        toml_path = _NOTES_TEMPLATE_DIR / "picolet.toml"
        if not toml_path.exists():
            self.skipTest("picolet.toml missing")
        self.assertIn('framework = "vue"', toml_path.read_text(encoding="utf-8"))

    def test_package_json_has_name_placeholder(self):
        pkg_path = _NOTES_TEMPLATE_DIR / "package.json"
        self.assertTrue(pkg_path.exists(), "template package.json missing")
        self.assertIn("{{name}}", pkg_path.read_text(encoding="utf-8"))

    def test_no_package_lock_json_in_template(self):
        self.assertFalse(
            (_NOTES_TEMPLATE_DIR / "package-lock.json").exists(),
            "package-lock.json must not be committed in the notes template",
        )

    def test_template_font_files_present(self):
        fonts_dir = _NOTES_TEMPLATE_DIR / "ui" / "public" / "fonts"
        self.assertTrue(fonts_dir.is_dir(), "ui/public/fonts/ missing from template")
        woff2_files = list(fonts_dir.glob("*.woff2"))
        self.assertGreater(len(woff2_files), 0, "No woff2 files found in template fonts/")

    def test_src_main_py_exists(self):
        self.assertTrue((_NOTES_TEMPLATE_DIR / "src" / "main.py").exists())

    def test_src_notes_store_py_exists(self):
        self.assertTrue((_NOTES_TEMPLATE_DIR / "src" / "notes_store.py").exists())

    def test_vite_config_exists(self):
        self.assertTrue((_NOTES_TEMPLATE_DIR / "vite.config.ts").exists())


# ---------------------------------------------------------------------------
# NFR-EX-3: CSS bundle size (skip if dist/ absent)
# ---------------------------------------------------------------------------

class TestCssBundleSize(unittest.TestCase):

    def test_gzipped_css_under_50kb(self):
        """NFR-EX-3: hand-crafted CSS must gzip to <= 50 KB."""
        dist_assets = _NOTES_DIR / "dist" / "assets"
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
# Screenshots: presence, validity, and pixel content
# ---------------------------------------------------------------------------

class TestScreenshots(unittest.TestCase):

    def _shot_path(self, name: str) -> Path:
        return _SCREENSHOTS_DIR / name

    def test_screenshots_dir_exists(self):
        self.assertTrue(_SCREENSHOTS_DIR.is_dir(), "screenshots/ directory missing")

    def test_list_empty_exists(self):
        self.assertTrue(self._shot_path("list-empty.png").exists())

    def test_list_populated_exists(self):
        self.assertTrue(self._shot_path("list-populated.png").exists())

    def test_edit_pristine_exists(self):
        self.assertTrue(self._shot_path("edit-pristine.png").exists())

    def test_edit_unsaved_exists(self):
        self.assertTrue(self._shot_path("edit-unsaved.png").exists())

    def test_edit_typing_mid_exists(self):
        self.assertTrue(self._shot_path("edit-typing-mid.png").exists())

    def test_search_active_exists(self):
        self.assertTrue(self._shot_path("search-active.png").exists())

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
                self.assertGreater(size, 1024, f"{name} is only {size} bytes")

    def test_all_screenshots_dimensions_at_least_1000x700(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow not installed; skipping pixel checks")
        for name in _REQUIRED_SCREENSHOTS:
            p = self._shot_path(name)
            if not p.exists():
                continue
            with self.subTest(screenshot=name):
                img = Image.open(p)
                w, h = img.size
                self.assertGreaterEqual(w, 1000, f"{name}: width {w} < 1000")
                self.assertGreaterEqual(h, 700, f"{name}: height {h} < 700")

    def test_all_screenshots_contain_paper_colour(self):
        """Each PNG must contain warm off-white paper pixels (~#f7f3ed)."""
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow not installed")
        for name in _REQUIRED_SCREENSHOTS:
            p = self._shot_path(name)
            if not p.exists():
                continue
            with self.subTest(screenshot=name):
                img = Image.open(p).convert("RGB")
                pixels = list(img.getdata())
                has_paper = any(
                    abs(r - 247) <= 20 and abs(g - 243) <= 20 and abs(b - 237) <= 20
                    for r, g, b in pixels
                )
                self.assertTrue(has_paper, f"{name}: no paper-colour pixels found")

    def test_all_screenshots_contain_ink_dark_pixels(self):
        """Each PNG must have near-black ink pixels (~#1a1715)."""
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow not installed")
        for name in _REQUIRED_SCREENSHOTS:
            p = self._shot_path(name)
            if not p.exists():
                continue
            with self.subTest(screenshot=name):
                img = Image.open(p).convert("RGB")
                pixels = list(img.getdata())
                has_ink = any(r < 60 and g < 60 and b < 60 for r, g, b in pixels)
                self.assertTrue(has_ink, f"{name}: no ink-dark pixels found")

    def test_edit_unsaved_contains_mark_red(self):
        """edit-unsaved.png must contain the red unsaved-dot pixel (~#c4392b)."""
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow not installed")
        p = self._shot_path("edit-unsaved.png")
        if not p.exists():
            self.skipTest("edit-unsaved.png absent")
        img = Image.open(p).convert("RGB")
        pixels = list(img.getdata())
        has_red = any(
            abs(r - 196) <= 30 and abs(g - 57) <= 30 and abs(b - 43) <= 30
            for r, g, b in pixels
        )
        self.assertTrue(has_red, "edit-unsaved.png: no mark-red (#c4392b) pixels found")

    def test_edit_typing_mid_contains_mark_red(self):
        """edit-typing-mid.png must contain the red unsaved-dot pixel."""
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow not installed")
        p = self._shot_path("edit-typing-mid.png")
        if not p.exists():
            self.skipTest("edit-typing-mid.png absent")
        img = Image.open(p).convert("RGB")
        pixels = list(img.getdata())
        has_red = any(
            abs(r - 196) <= 30 and abs(g - 57) <= 30 and abs(b - 43) <= 30
            for r, g, b in pixels
        )
        self.assertTrue(has_red, "edit-typing-mid.png: no mark-red (#c4392b) pixels found")

    def test_edit_pristine_does_not_contain_mark_red(self):
        """edit-pristine.png must NOT contain the red dot (no unsaved changes)."""
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow not installed")
        p = self._shot_path("edit-pristine.png")
        if not p.exists():
            self.skipTest("edit-pristine.png absent")
        img = Image.open(p).convert("RGB")
        pixels = list(img.getdata())
        has_red = any(
            abs(r - 196) <= 30 and abs(g - 57) <= 30 and abs(b - 43) <= 30
            for r, g, b in pixels
        )
        self.assertFalse(
            has_red,
            "edit-pristine.png contains mark-red pixels — unsaved dot should not appear",
        )


if __name__ == "__main__":
    unittest.main()
