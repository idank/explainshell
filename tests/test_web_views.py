import unittest

from explainshell.web import create_app
from explainshell.web.views import explain_program, manpage_url, render_markdown
from tests import helpers


class TestExplainRouter(unittest.TestCase):
    """Route-level tests for the unified explain_router."""

    def setUp(self):
        self.app = create_app()
        self.app.store = helpers.MockStore()
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
        rv = self.client.get("/explain/ubuntu/25.10?cmd=bar+-a")
        self.assertEqual(rv.status_code, 200)
        self.assertIn(b"bar", rv.data)

    def test_explain_program_with_distro(self):
        rv = self.client.get("/explain/ubuntu/25.10/bar")
        self.assertEqual(rv.status_code, 200)
        self.assertIn(b"bar", rv.data)

    def test_explain_section_program_with_distro(self):
        rv = self.client.get("/explain/ubuntu/25.10/1/bar")
        self.assertEqual(rv.status_code, 200)
        self.assertIn(b"bar", rv.data)

    # -- URL precedence over cookies --

    def test_url_distro_takes_precedence_over_cookies(self):
        self.client.set_cookie("distro", "debian")
        self.client.set_cookie("release", "12")
        rv = self.client.get("/explain/ubuntu/25.10?cmd=bar+-a")
        self.assertEqual(rv.status_code, 200)
        # The page should render correctly (using ubuntu/25.10 from URL)
        self.assertIn(b"bar", rv.data)

    # -- explain_prefix in rendered HTML --

    def test_explain_prefix_with_distro_in_form(self):
        rv = self.client.get("/explain/ubuntu/25.10?cmd=bar+-a")
        self.assertIn(b"action='/explain/ubuntu/25.10'", rv.data)

    def test_explain_prefix_without_distro_in_form(self):
        rv = self.client.get("/explain?cmd=bar+-a")
        self.assertIn(b"action='/explain'", rv.data)
        self.assertNotIn(b"action='/explain/ubuntu", rv.data)

    def test_explain_prefix_in_command_links(self):
        rv = self.client.get("/explain/ubuntu/25.10?cmd=bar+-a")
        # Command links should use distro prefix
        self.assertIn(b"/explain/ubuntu/25.10/1/bar", rv.data)

    def test_explain_prefix_without_distro_in_command_links(self):
        rv = self.client.get("/explain?cmd=bar+-a")
        self.assertIn(b"/explain/1/bar", rv.data)
        self.assertNotIn(b"/explain/ubuntu/25.10/1/bar", rv.data)

    def test_distro_only_no_cmd_redirects(self):
        rv = self.client.get("/explain/ubuntu/25.10")
        self.assertEqual(rv.status_code, 302)

    def test_suggestion_links_use_distro_prefix(self):
        rv = self.client.get("/explain/ubuntu/25.10/dup")
        self.assertEqual(rv.status_code, 200)
        self.assertIn(b"/explain/ubuntu/25.10/2/dup", rv.data)

    def test_suggestion_links_no_distro(self):
        rv = self.client.get("/explain/dup")
        self.assertEqual(rv.status_code, 200)
        self.assertIn(b"/explain/2/dup", rv.data)
        self.assertNotIn(b"/explain/ubuntu/25.10/2/dup", rv.data)


class TestManpageRoute(unittest.TestCase):
    """Route-level tests for /manpage endpoints."""

    def setUp(self):
        self.app = create_app()
        self.app.store = helpers.MockStore()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    # -- Single manpage view --

    def test_manpage_returns_200(self):
        rv = self.client.get("/manpage/ubuntu/25.10/1/bar.1")
        self.assertEqual(rv.status_code, 200)
        self.assertIn(b"bar(1)", rv.data)

    def test_manpage_displays_source_text(self):
        rv = self.client.get("/manpage/ubuntu/25.10/1/bar.1")
        self.assertEqual(rv.status_code, 200)
        self.assertIn(b".TH roff content", rv.data)

    def test_manpage_not_found(self):
        rv = self.client.get("/manpage/ubuntu/25.10/1/nosuchpage.1")
        self.assertEqual(rv.status_code, 200)
        self.assertIn(b"missing man page", rv.data)

    def test_manpage_roff_rendered_as_pre(self):
        rv = self.client.get("/manpage/ubuntu/25.10/1/bar.1")
        self.assertIn(b"<pre>", rv.data)

    def test_manpage_markdown_rendered_as_html(self):
        rv = self.client.get("/manpage/ubuntu/25.10/1/markdown-page.1")
        self.assertEqual(rv.status_code, 200)
        # Markdown content should be rendered, not in a <pre> block
        self.assertNotIn(b"<pre>", rv.data)
        self.assertIn(b"markdown content", rv.data)

    # -- Edge-case filenames --

    def test_manpage_mismatched_dir_file_section(self):
        rv = self.client.get("/manpage/ubuntu/25.10/1/cd.1posix")
        self.assertEqual(rv.status_code, 200)
        self.assertIn(b"cd(1posix)", rv.data)

    def test_manpage_filename_with_spaces(self):
        rv = self.client.get("/manpage/ubuntu/25.10/1/pg_autoctl create worker.1")
        self.assertEqual(rv.status_code, 200)
        self.assertIn(b"pg_autoctl create worker(1)", rv.data)

    def test_manpage_filename_with_plus(self):
        rv = self.client.get("/manpage/ubuntu/25.10/1/c++filt.1")
        self.assertEqual(rv.status_code, 200)
        self.assertIn(b"c++filt(1)", rv.data)

    # -- Release index --

    def test_release_index(self):
        rv = self.client.get("/manpage/ubuntu/")
        self.assertEqual(rv.status_code, 200)
        self.assertIn(b"25.10", rv.data)
        self.assertIn(b'href="/manpage/ubuntu/25.10/"', rv.data)

    def test_release_index_unknown_distro(self):
        rv = self.client.get("/manpage/archlinux/")
        self.assertEqual(rv.status_code, 200)
        # Should render but with no release links
        self.assertNotIn(b"/manpage/archlinux/", rv.data)

    # -- Section index --

    def test_section_index(self):
        rv = self.client.get("/manpage/ubuntu/25.10/")
        self.assertEqual(rv.status_code, 200)
        self.assertIn(b"section 1", rv.data)

    # -- Section listing --

    def test_list_filtered_by_section(self):
        rv = self.client.get("/manpage/ubuntu/25.10/1/")
        self.assertEqual(rv.status_code, 200)
        self.assertIn(b"bar(1)", rv.data)

    def test_list_empty_for_unknown_section(self):
        rv = self.client.get("/manpage/ubuntu/25.10/9/")
        self.assertEqual(rv.status_code, 200)
        # No manpage links, but page should render
        self.assertNotIn(b"/manpage/ubuntu/25.10/9/", rv.data)

    def test_list_links_point_to_manpage_view(self):
        rv = self.client.get("/manpage/ubuntu/25.10/1/")
        self.assertIn(b'href="/manpage/ubuntu/25.10/1/bar.1"', rv.data)

    def test_list_links_url_encode_special_chars(self):
        rv = self.client.get("/manpage/ubuntu/25.10/1/")
        # Spaces should be percent-encoded in URLs
        self.assertIn(
            b'href="/manpage/ubuntu/25.10/1/pg_autoctl%20create%20worker.1"',
            rv.data,
        )
        # + should be percent-encoded in URLs
        self.assertIn(b'href="/manpage/ubuntu/25.10/1/c%2B%2Bfilt.1"', rv.data)


class TestManpageUrl(unittest.TestCase):
    def test_matching_prefix(self):
        url = manpage_url("ubuntu/25.10/1/tar.1.gz")
        self.assertRegex(
            url,
            r"https://manpages\.ubuntu\.com/manpages/\w+/en/man1/tar\.1\.html",
        )

    def test_no_match(self):
        self.assertIsNone(manpage_url("custom.1.gz"))

    def test_section_8(self):
        url = manpage_url("ubuntu/25.10/8/iptables.8.gz")
        self.assertRegex(
            url,
            r"https://manpages\.ubuntu\.com/manpages/\w+/en/man8/iptables\.8\.html",
        )


class TestExplainProgram(unittest.TestCase):
    def setUp(self):
        self.store = helpers.MockStore()

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
        self.assertIsNone(mp["synopsis"])

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
