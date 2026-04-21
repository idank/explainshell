import datetime
import unittest

from flask import g
from explainshell.models import Option, ParsedManpage, RawManpage
from explainshell.store import Store
from explainshell.web import create_app
from explainshell.web.views import explain_program, manpage_url, render_markdown
from tests.helpers import create_test_store


def _use_store(app: object, store: Store) -> None:
    """Inject *store* into Flask's ``g`` for every request so that
    ``get_store()`` returns it instead of opening a real database.

    The store is popped from ``g`` in ``after_request`` (before the
    teardown hook runs) so that the app's teardown doesn't close the
    shared test connection. Also refreshes ``STARTUP_DISTROS`` from
    *store* so ``get_distros()`` reflects the test fixture.
    """
    app.config["STARTUP_DISTROS"] = list(store.distros())

    @app.before_request
    def _inject() -> None:
        g.store = store

    @app.after_request
    def _preserve(response):
        g.pop("store", None)
        return response


class TestExplainRouter(unittest.TestCase):
    """Route-level tests for the unified explain_router."""

    def setUp(self):
        self.app = create_app()
        _use_store(self.app, create_test_store())
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    # -- Backwards-compatible routes (no distro in URL) --

    def test_explain_cmd_no_distro(self):
        rv = self.client.get("/explain?cmd=bar+-a")
        self.assertEqual(rv.status_code, 200)
        self.assertIn(b"bar", rv.data)

    def test_explain_no_cmd_redirects(self):
        rv = self.client.get("/explain")
        self.assertEqual(rv.status_code, 302)
        self.assertIn("/", rv.headers["Location"])

    def test_explain_empty_cmd_redirects(self):
        rv = self.client.get("/explain?cmd=")
        self.assertEqual(rv.status_code, 302)

    def test_explain_program_no_distro(self):
        rv = self.client.get("/explain/bar")
        self.assertEqual(rv.status_code, 200)
        self.assertIn(b"bar", rv.data)

    def test_explain_section_program_no_distro(self):
        rv = self.client.get("/explain/1/bar")
        self.assertEqual(rv.status_code, 200)
        self.assertIn(b"bar", rv.data)

    # -- Distro-prefixed routes --

    def test_explain_cmd_with_distro(self):
        rv = self.client.get("/explain/ubuntu/26.04?cmd=bar+-a")
        self.assertEqual(rv.status_code, 200)
        self.assertIn(b"bar", rv.data)

    def test_explain_program_with_distro(self):
        rv = self.client.get("/explain/ubuntu/26.04/bar")
        self.assertEqual(rv.status_code, 200)
        self.assertIn(b"bar", rv.data)

    def test_explain_section_program_with_distro(self):
        rv = self.client.get("/explain/ubuntu/26.04/1/bar")
        self.assertEqual(rv.status_code, 200)
        self.assertIn(b"bar", rv.data)

    def test_url_distro_scoping(self):
        rv = self.client.get("/explain/ubuntu/26.04?cmd=bar+-a")
        self.assertEqual(rv.status_code, 200)
        self.assertIn(b"bar", rv.data)

    # -- explain_prefix in rendered HTML --

    def test_explain_prefix_with_distro_in_form(self):
        rv = self.client.get("/explain/ubuntu/26.04?cmd=bar+-a")
        self.assertIn(b"action='/explain/ubuntu/26.04'", rv.data)

    def test_explain_prefix_without_distro_in_form(self):
        rv = self.client.get("/explain?cmd=bar+-a")
        self.assertIn(b"action='/explain'", rv.data)
        self.assertNotIn(b"action='/explain/ubuntu", rv.data)

    def test_explain_prefix_in_command_links(self):
        rv = self.client.get("/explain/ubuntu/26.04?cmd=bar+-a")
        # Command links should use distro prefix
        self.assertIn(b"/explain/ubuntu/26.04/1/bar", rv.data)

    def test_explain_prefix_without_distro_in_command_links(self):
        rv = self.client.get("/explain?cmd=bar+-a")
        self.assertIn(b"/explain/1/bar", rv.data)
        self.assertNotIn(b"/explain/ubuntu/26.04/1/bar", rv.data)

    def test_default_distro_prefers_ubuntu(self):
        """When multiple distros exist, the default should prefer ubuntu so
        that commands available only in ubuntu are found without an explicit
        distro in the URL."""
        store = Store.create(":memory:")
        raw = RawManpage(
            source_text=".TH BAR 1",
            generated_at=datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc),
            generator="test",
        )
        # Only ubuntu has "bar"; arch has a different command.
        store.add_manpage(
            ParsedManpage(
                source="ubuntu/26.04/1/bar.1.gz",
                name="bar",
                synopsis="bar synopsis",
                options=[Option(text="-a desc", short=["-a"], long=[])],
                aliases=[("bar", 10)],
            ),
            raw,
        )
        store.add_manpage(
            ParsedManpage(
                source="arch/latest/1/other.1.gz",
                name="other",
                aliases=[("other", 10)],
            ),
            raw,
        )
        _use_store(self.app, store)

        rv = self.client.get("/explain?cmd=bar+-a")
        self.assertEqual(rv.status_code, 200)
        self.assertNotIn(b"missing man page", rv.data)
        self.assertIn(b"bar", rv.data)

    def test_distro_only_no_cmd_redirects(self):
        rv = self.client.get("/explain/ubuntu/26.04")
        self.assertEqual(rv.status_code, 302)

    def test_suggestion_links_use_distro_prefix(self):
        rv = self.client.get("/explain/ubuntu/26.04/dup")
        self.assertEqual(rv.status_code, 200)
        self.assertIn(b"/explain/ubuntu/26.04/2/dup", rv.data)

    def test_suggestion_links_no_distro(self):
        rv = self.client.get("/explain/dup")
        self.assertEqual(rv.status_code, 200)
        self.assertIn(b"/explain/2/dup", rv.data)
        self.assertNotIn(b"/explain/ubuntu/26.04/2/dup", rv.data)


class TestDistroFallback(unittest.TestCase):
    """Tests for cross-distro fallback when a manpage isn't in the preferred distro."""

    _RAW = RawManpage(
        source_text=".TH TEST 1",
        generated_at=datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc),
        generator="test",
    )

    def _make_app(self, store):
        app = create_app()
        _use_store(app, store)
        app.config["TESTING"] = True
        return app

    def _store_with_ubuntu_and_arch(self) -> Store:
        """Store where 'uonly' is ubuntu-only, 'aonly' is arch-only,
        and 'both' exists in both distros."""
        store = Store.create(":memory:")
        store.add_manpage(
            ParsedManpage(
                source="ubuntu/26.04/1/uonly.1.gz",
                name="uonly",
                synopsis="ubuntu only",
                options=[Option(text="-u desc", short=["-u"], long=[])],
                aliases=[("uonly", 10)],
            ),
            self._RAW,
        )
        store.add_manpage(
            ParsedManpage(
                source="arch/latest/1/aonly.1.gz",
                name="aonly",
                synopsis="arch only",
                options=[Option(text="-a desc", short=["-a"], long=[])],
                aliases=[("aonly", 10)],
            ),
            self._RAW,
        )
        store.add_manpage(
            ParsedManpage(
                source="ubuntu/26.04/1/both.1.gz",
                name="both",
                synopsis="both ubuntu",
                options=[Option(text="-b desc", short=["-b"], long=[])],
                aliases=[("both", 10)],
            ),
            self._RAW,
        )
        store.add_manpage(
            ParsedManpage(
                source="arch/latest/1/both.1.gz",
                name="both",
                synopsis="both arch",
                options=[Option(text="-b desc", short=["-b"], long=[])],
                aliases=[("both", 10)],
            ),
            self._RAW,
        )
        return store

    def test_default_distro_falls_back(self):
        """Default distro is arch (sorts last with reverse), but 'uonly'
        only exists in ubuntu — fallback should find it."""
        app = self._make_app(self._store_with_ubuntu_and_arch())
        with app.test_client() as c:
            # No distro in URL → default picks ubuntu, fallback active.
            rv = c.get("/explain?cmd=aonly+-a")
            self.assertEqual(rv.status_code, 200)
            self.assertNotIn(b"missing man page", rv.data)
            self.assertIn(b"aonly", rv.data)

    def test_url_distro_no_fallback(self):
        """Explicit URL distro should NOT fall back — missing is missing."""
        app = self._make_app(self._store_with_ubuntu_and_arch())
        with app.test_client() as c:
            rv = c.get("/explain/arch/latest?cmd=uonly+-u")
            self.assertEqual(rv.status_code, 200)
            self.assertIn(b"missing man page", rv.data)

    def test_all_distros_miss_still_raises(self):
        """A command that exists nowhere should still show missing."""
        app = self._make_app(self._store_with_ubuntu_and_arch())
        with app.test_client() as c:
            rv = c.get("/explain?cmd=nosuchcmd")
            self.assertEqual(rv.status_code, 200)
            self.assertIn(b"missing man page", rv.data)

    def test_pipeline_anchors_to_fallback_distro(self):
        """Default picks ubuntu. In 'aonly | uonly', the first command
        falls back to arch, anchoring there — so 'uonly' (ubuntu-only)
        should be unknown."""
        app = self._make_app(self._store_with_ubuntu_and_arch())
        with app.test_client() as c:
            rv = c.get("/explain?cmd=aonly+-a+|+uonly+-u")
            self.assertEqual(rv.status_code, 200)
            self.assertIn(b"aonly", rv.data)
            self.assertIn(b"unknown", rv.data)

    def test_program_default_falls_back(self):
        """The /explain/<program> route should fall back with default distro."""
        app = self._make_app(self._store_with_ubuntu_and_arch())
        with app.test_client() as c:
            rv = c.get("/explain/aonly")
            self.assertEqual(rv.status_code, 200)
            self.assertNotIn(b"missing man page", rv.data)
            self.assertIn(b"aonly", rv.data)

    def test_program_url_distro_no_fallback(self):
        """The /explain/<distro>/<release>/<program> route should NOT fall back."""
        app = self._make_app(self._store_with_ubuntu_and_arch())
        with app.test_client() as c:
            rv = c.get("/explain/arch/latest/uonly")
            self.assertEqual(rv.status_code, 200)
            self.assertIn(b"missing man page", rv.data)


class TestExplainCacheHeaders(unittest.TestCase):
    """Manpage views on /explain/<program> should carry ETag +
    Cache-Control so Cloudflare and browsers can cache them."""

    _RAW = RawManpage(
        source_text=".TH BAR 1",
        generated_at=datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc),
        generator="test",
    )

    def _make_mp(self, option_text: str = "-a desc") -> ParsedManpage:
        return ParsedManpage(
            source="ubuntu/26.04/1/bar.1.gz",
            name="bar",
            synopsis="bar synopsis",
            options=[Option(text=option_text, short=["-a"], long=[])],
            aliases=[("bar", 10)],
        )

    def _store_with_manpage(self, mp: ParsedManpage) -> Store:
        store = Store.create(":memory:")
        store.add_manpage(mp, self._RAW)
        return store

    def setUp(self):
        self.app = create_app()
        self.app.config["APP_VERSION"] = "deadbeef"
        self.mp = self._make_mp()
        self.store = self._store_with_manpage(self.mp)
        _use_store(self.app, self.store)
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def test_manpage_view_sets_etag_and_cache_control(self):
        rv = self.client.get("/explain/bar")
        self.assertEqual(rv.status_code, 200)
        # Weak ETag: parsed_sha[:16] + app_ver.
        expected_etag = f'W/"{self.mp.content_sha256()[:16]}-deadbeef"'
        self.assertEqual(rv.headers.get("ETag"), expected_etag)
        self.assertEqual(
            rv.headers.get("Cache-Control"),
            "public, max-age=604800, s-maxage=604800, stale-while-revalidate=86400",
        )

    def test_if_none_match_returns_304(self):
        rv = self.client.get("/explain/bar")
        etag = rv.headers["ETag"]
        rv2 = self.client.get("/explain/bar", headers={"If-None-Match": etag})
        self.assertEqual(rv2.status_code, 304)
        # Cache-Control still sent on 304 so intermediaries update TTL.
        self.assertIn("Cache-Control", rv2.headers)

    def test_reextraction_flips_etag(self):
        """Re-extraction that changes option text (e.g. an LLM returning
        different wording) must produce a different ETag — the whole
        point of parsed_sha256 over source_gz_sha256."""
        etag1 = self.client.get("/explain/bar").headers["ETag"]
        # Simulate a re-extraction: add_manpage overwrites the existing
        # row with the new options, and parsed_sha256 is recomputed.
        self.store.add_manpage(
            self._make_mp(option_text="-a different desc"), self._RAW
        )
        etag2 = self.client.get("/explain/bar").headers["ETag"]
        self.assertNotEqual(etag1, etag2)

    def test_cmd_response_is_not_cached(self):
        rv = self.client.get("/explain?cmd=bar+-a")
        self.assertEqual(rv.status_code, 200)
        self.assertNotIn("ETag", rv.headers)
        self.assertNotIn("Cache-Control", rv.headers)

    def test_missing_sha_falls_back_to_uncached(self):
        # Simulate a pre-migration row: wipe the sha on the existing row.
        self.store._conn.execute(
            "UPDATE parsed_manpages SET parsed_sha256 = NULL WHERE source = ?",
            ("ubuntu/26.04/1/bar.1.gz",),
        )
        self.store._conn.commit()
        rv = self.client.get("/explain/bar")
        self.assertEqual(rv.status_code, 200)
        self.assertNotIn("ETag", rv.headers)
        self.assertNotIn("Cache-Control", rv.headers)

    def test_missing_manpage_is_not_cached(self):
        rv = self.client.get("/explain/nosuchprogram")
        self.assertEqual(rv.status_code, 200)
        self.assertIn(b"missing man page", rv.data)
        self.assertNotIn("ETag", rv.headers)
        self.assertNotIn("Cache-Control", rv.headers)


class TestManpageRoute(unittest.TestCase):
    """Route-level tests for /manpage endpoints."""

    def setUp(self):
        self.app = create_app()
        _use_store(self.app, create_test_store())
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    # -- Single manpage view --

    def test_manpage_returns_200(self):
        rv = self.client.get("/manpage/ubuntu/26.04/1/bar.1")
        self.assertEqual(rv.status_code, 200)
        self.assertIn(b"bar(1)", rv.data)

    def test_manpage_displays_source_text(self):
        rv = self.client.get("/manpage/ubuntu/26.04/1/bar.1")
        self.assertEqual(rv.status_code, 200)
        self.assertIn(b".TH roff content", rv.data)

    def test_manpage_not_found(self):
        rv = self.client.get("/manpage/ubuntu/26.04/1/nosuchpage.1")
        self.assertEqual(rv.status_code, 200)
        self.assertIn(b"missing man page", rv.data)

    def test_manpage_roff_rendered_as_pre(self):
        rv = self.client.get("/manpage/ubuntu/26.04/1/bar.1")
        self.assertIn(b"<pre>", rv.data)

    def test_manpage_markdown_rendered_as_html(self):
        rv = self.client.get("/manpage/ubuntu/26.04/1/markdown-page.1")
        self.assertEqual(rv.status_code, 200)
        # Markdown content should be rendered, not in a <pre> block
        self.assertNotIn(b"<pre>", rv.data)
        self.assertIn(b"markdown content", rv.data)

    # -- Edge-case filenames --

    def test_manpage_mismatched_dir_file_section(self):
        rv = self.client.get("/manpage/ubuntu/26.04/1/cd.1posix")
        self.assertEqual(rv.status_code, 200)
        self.assertIn(b"cd(1posix)", rv.data)

    def test_manpage_filename_with_spaces(self):
        rv = self.client.get("/manpage/ubuntu/26.04/1/pg_autoctl create worker.1")
        self.assertEqual(rv.status_code, 200)
        self.assertIn(b"pg_autoctl create worker(1)", rv.data)

    def test_manpage_filename_with_plus(self):
        rv = self.client.get("/manpage/ubuntu/26.04/1/c++filt.1")
        self.assertEqual(rv.status_code, 200)
        self.assertIn(b"c++filt(1)", rv.data)

    # -- Release index --

    def test_release_index(self):
        rv = self.client.get("/manpage/ubuntu/")
        self.assertEqual(rv.status_code, 200)
        self.assertIn(b"26.04", rv.data)
        self.assertIn(b'href="/manpage/ubuntu/26.04/"', rv.data)

    def test_release_index_unknown_distro(self):
        rv = self.client.get("/manpage/archlinux/")
        self.assertEqual(rv.status_code, 200)
        # Should render but with no release links
        self.assertNotIn(b"/manpage/archlinux/", rv.data)

    # -- Section index --

    def test_section_index(self):
        rv = self.client.get("/manpage/ubuntu/26.04/")
        self.assertEqual(rv.status_code, 200)
        self.assertIn(b"section 1", rv.data)

    # -- Section listing --

    def test_list_filtered_by_section(self):
        rv = self.client.get("/manpage/ubuntu/26.04/1/")
        self.assertEqual(rv.status_code, 200)
        self.assertIn(b"bar(1)", rv.data)

    def test_list_empty_for_unknown_section(self):
        rv = self.client.get("/manpage/ubuntu/26.04/9/")
        self.assertEqual(rv.status_code, 200)
        # No manpage links, but page should render
        self.assertNotIn(b"/manpage/ubuntu/26.04/9/", rv.data)

    def test_list_links_point_to_manpage_view(self):
        rv = self.client.get("/manpage/ubuntu/26.04/1/")
        self.assertIn(b'href="/manpage/ubuntu/26.04/1/bar.1"', rv.data)

    def test_list_links_url_encode_special_chars(self):
        rv = self.client.get("/manpage/ubuntu/26.04/1/")
        # Spaces should be percent-encoded in URLs
        self.assertIn(
            b'href="/manpage/ubuntu/26.04/1/pg_autoctl%20create%20worker.1"',
            rv.data,
        )
        # + should be percent-encoded in URLs
        self.assertIn(b'href="/manpage/ubuntu/26.04/1/c%2B%2Bfilt.1"', rv.data)


class TestManpageUrl(unittest.TestCase):
    def test_matching_prefix(self):
        url = manpage_url("ubuntu/26.04/1/tar.1.gz")
        self.assertRegex(
            url,
            r"https://manpages\.ubuntu\.com/manpages/\w+/en/man1/tar\.1\.html",
        )

    def test_no_match(self):
        self.assertIsNone(manpage_url("custom/distro/1/foo.1.gz"))

    def test_section_8(self):
        url = manpage_url("ubuntu/26.04/8/iptables.8.gz")
        self.assertRegex(
            url,
            r"https://manpages\.ubuntu\.com/manpages/\w+/en/man8/iptables\.8\.html",
        )

    def test_posix_section(self):
        url = manpage_url("ubuntu/26.04/1/crontab.1posix.gz")
        self.assertEqual(
            url,
            "https://manpages.ubuntu.com/manpages/resolute/en/man1/crontab.1posix.html",
        )


class TestExplainProgram(unittest.TestCase):
    def setUp(self):
        self.store = create_test_store()

    def test_explain_program_returns_str_options(self):
        mp, suggestions, *_ = explain_program("bar", self.store)
        self.assertEqual(mp["program"], "bar(1)")
        self.assertEqual(mp["synopsis"], "bar synopsis")
        self.assertEqual(mp["section"], "1")
        self.assertEqual(mp["source"], "bar.1")
        self.assertRegex(
            mp["url"],
            r"https://manpages\.ubuntu\.com/manpages/\w+/en/man1/bar\.1\.html",
        )
        for opt in mp["options"]:
            self.assertIsInstance(opt, str)

    def test_explain_program_no_synopsis(self):
        mp, suggestions, *_ = explain_program("nosynopsis", self.store)
        # The Store replaces NULL synopsis with a placeholder string.
        self.assertEqual(mp["synopsis"], "no synopsis found")

    def test_explain_program_with_suggestions(self):
        mp, suggestions, *_ = explain_program("dup", self.store)
        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0]["text"], "dup(2)")
        self.assertEqual(suggestions[0]["link"], "2/dup")


class TestRenderMarkdown(unittest.TestCase):
    def test_bold_and_italic(self):
        result = render_markdown("**bold** and *italic*")
        self.assertIn("<strong>bold</strong>", result)
        self.assertIn("<em>italic</em>", result)

    def test_bare_angle_brackets_escaped(self):
        result = render_markdown("Use --onto <newbase>")
        self.assertIn("&lt;newbase&gt;", result)
        self.assertNotIn("<newbase>", result)

    def test_already_escaped_entities(self):
        result = render_markdown("Use --onto &lt;newbase&gt;")
        # Should still render as visible angle brackets, not be swallowed
        self.assertIn("&lt;", result)
        self.assertIn("&gt;", result)

    def test_blockquotes_preserved(self):
        result = render_markdown("Description\n\n> indented example")
        self.assertIn("<blockquote>", result)
        self.assertIn("indented example", result)
