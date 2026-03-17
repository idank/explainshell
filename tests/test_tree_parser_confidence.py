"""Tests for tree_parser confidence assessment."""

import unittest

from explainshell import models
from explainshell.tree_parser import (
    ExtractionResult,
    ConfidenceResult,
    assess_confidence,
)


def _make_option(short=None, long=None, text="flag\ndescription"):
    return models.Option(
        text=text,
        short=short or [],
        long=long or [],
        has_argument=False,
        positional=None,
        nested_cmd=False,
    )


class TestAssessConfidence(unittest.TestCase):
    def test_good_extraction_is_confident(self):
        result = ExtractionResult(
            options=[_make_option(short=["-v"]), _make_option(long=["--help"])],
            option_sections_found=1,
            option_sections_empty=0,
            total_body_children=10,
            unrecognized_children=1,
            empty_description_count=0,
        )
        conf = assess_confidence(result)
        self.assertTrue(conf.confident)
        self.assertEqual(conf.reasons, [])

    def test_all_option_sections_empty(self):
        result = ExtractionResult(
            options=[],
            option_sections_found=2,
            option_sections_empty=2,
            total_body_children=0,
            unrecognized_children=0,
        )
        conf = assess_confidence(result)
        self.assertFalse(conf.confident)
        self.assertIn("all option sections empty", conf.reasons)

    def test_no_option_sections_found(self):
        result = ExtractionResult(
            options=[],
            option_sections_found=0,
            option_sections_empty=0,
        )
        conf = assess_confidence(result)
        self.assertFalse(conf.confident)
        self.assertIn("no option sections found", conf.reasons)

    def test_no_sections_but_has_options_is_confident(self):
        """Options found in non-option sections — still confident."""
        result = ExtractionResult(
            options=[_make_option(short=["-a"])],
            option_sections_found=0,
            option_sections_empty=0,
            total_body_children=5,
            unrecognized_children=0,
        )
        conf = assess_confidence(result)
        self.assertTrue(conf.confident)

    def test_high_unrecognized_ratio(self):
        result = ExtractionResult(
            options=[_make_option(short=["-x"])],
            option_sections_found=1,
            option_sections_empty=0,
            total_body_children=20,
            unrecognized_children=15,
        )
        conf = assess_confidence(result)
        self.assertFalse(conf.confident)
        self.assertTrue(any("unrecognized" in r for r in conf.reasons))

    def test_low_unrecognized_absolute_is_confident(self):
        """High ratio but low absolute count (<=5) should still be confident."""
        result = ExtractionResult(
            options=[_make_option(short=["-x"])],
            option_sections_found=1,
            option_sections_empty=0,
            total_body_children=6,
            unrecognized_children=4,
        )
        conf = assess_confidence(result)
        self.assertTrue(conf.confident)

    def test_high_empty_descriptions(self):
        opts = [
            _make_option(short=[f"-{chr(97 + i)}"], text=f"-{chr(97 + i)}")
            for i in range(10)
        ]
        result = ExtractionResult(
            options=opts,
            option_sections_found=1,
            option_sections_empty=0,
            total_body_children=10,
            unrecognized_children=0,
            empty_description_count=8,
        )
        conf = assess_confidence(result)
        self.assertFalse(conf.confident)
        self.assertTrue(any("empty descriptions" in r for r in conf.reasons))

    def test_low_empty_descriptions_absolute_is_confident(self):
        """High ratio but low absolute count (<=3) should still be confident."""
        opts = [
            _make_option(short=[f"-{chr(97 + i)}"], text=f"-{chr(97 + i)}")
            for i in range(4)
        ]
        result = ExtractionResult(
            options=opts,
            option_sections_found=1,
            option_sections_empty=0,
            total_body_children=4,
            unrecognized_children=0,
            empty_description_count=3,
        )
        conf = assess_confidence(result)
        self.assertTrue(conf.confident)

    def test_mandoc_stderr_error(self):
        result = ExtractionResult(
            options=[_make_option(short=["-v"])],
            mandoc_stderr="mandoc: test.1:5:2: ERROR: skipping ...",
            option_sections_found=1,
            option_sections_empty=0,
        )
        conf = assess_confidence(result)
        self.assertFalse(conf.confident)
        self.assertTrue(any("ERROR" in r for r in conf.reasons))

    def test_mandoc_stderr_unsupp(self):
        result = ExtractionResult(
            options=[_make_option(short=["-v"])],
            mandoc_stderr="mandoc: test.1:3:1: UNSUPP: ...",
            option_sections_found=1,
            option_sections_empty=0,
        )
        conf = assess_confidence(result)
        self.assertFalse(conf.confident)
        self.assertTrue(any("UNSUPP" in r for r in conf.reasons))

    def test_mandoc_stderr_warning_is_confident(self):
        """Warnings in stderr should not trigger low confidence."""
        result = ExtractionResult(
            options=[_make_option(short=["-v"])],
            mandoc_stderr="mandoc: test.1:3:1: WARNING: ...",
            option_sections_found=1,
            option_sections_empty=0,
        )
        conf = assess_confidence(result)
        self.assertTrue(conf.confident)

    def test_multiple_reasons(self):
        result = ExtractionResult(
            options=[],
            mandoc_stderr="FATAL: something",
            option_sections_found=1,
            option_sections_empty=1,
        )
        conf = assess_confidence(result)
        self.assertFalse(conf.confident)
        self.assertGreaterEqual(len(conf.reasons), 2)

    def test_str_confident(self):
        conf = ConfidenceResult(confident=True, reasons=[])
        self.assertEqual(str(conf), "confident")

    def test_str_not_confident(self):
        conf = ConfidenceResult(confident=False, reasons=["a", "b"])
        self.assertEqual(str(conf), "not confident: a; b")


if __name__ == "__main__":
    unittest.main()
