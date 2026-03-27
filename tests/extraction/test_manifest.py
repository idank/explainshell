"""Tests for explainshell.extraction.manifest."""

import json
import os
import tempfile
import unittest

from pydantic import ValidationError

from explainshell.extraction.manifest import (
    BatchManifest,
    BatchManifestEntry,
    ManifestData,
    failed_batches,
    load_manifest,
)


def _make_manifest_data(**overrides: object) -> ManifestData:
    """Build a ManifestData with sensible defaults."""
    defaults: dict = {
        "version": 1,
        "model": "openai/gpt-5-mini",
        "batch_size": 50,
        "total_batches": 0,
        "batches": [],
    }
    defaults.update(overrides)
    return ManifestData(**defaults)


class TestBatchManifest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.manifest_path = os.path.join(self.tmpdir, "batch-manifest.json")

    def tearDown(self) -> None:
        if os.path.exists(self.manifest_path):
            os.unlink(self.manifest_path)
        tmp = self.manifest_path + ".tmp"
        if os.path.exists(tmp):
            os.unlink(tmp)
        os.rmdir(self.tmpdir)

    def test_writes_valid_json(self) -> None:
        m = BatchManifest(self.manifest_path, model="openai/gpt-5-mini", batch_size=50)
        m.set_total_batches(2)
        m.record_batch(
            batch_idx=1,
            batch_id="batch_abc",
            status="completed",
            files=["/path/a.gz", "/path/b.gz"],
        )

        data = load_manifest(self.manifest_path, expected_model="openai/gpt-5-mini")
        self.assertEqual(data.version, 1)
        self.assertEqual(data.model, "openai/gpt-5-mini")
        self.assertEqual(data.batch_size, 50)
        self.assertEqual(data.total_batches, 2)
        self.assertEqual(len(data.batches), 1)
        self.assertEqual(data.batches[0].batch_idx, 1)
        self.assertEqual(data.batches[0].batch_id, "batch_abc")
        self.assertEqual(data.batches[0].status, "completed")
        self.assertIsNone(data.batches[0].error)
        self.assertEqual(data.batches[0].files, ["/path/a.gz", "/path/b.gz"])

    def test_records_incrementally(self) -> None:
        m = BatchManifest(self.manifest_path, model="openai/gpt-5-mini", batch_size=50)
        m.set_total_batches(3)

        m.record_batch(batch_idx=1, batch_id="b1", status="completed", files=["/a.gz"])
        data1 = load_manifest(self.manifest_path, expected_model="openai/gpt-5-mini")
        self.assertEqual(len(data1.batches), 1)

        m.record_batch(
            batch_idx=2,
            batch_id="b2",
            status="failed",
            files=["/b.gz"],
            error="expired",
        )
        data2 = load_manifest(self.manifest_path, expected_model="openai/gpt-5-mini")
        self.assertEqual(len(data2.batches), 2)

        m.record_batch(batch_idx=3, batch_id="b3", status="completed", files=["/c.gz"])
        data3 = load_manifest(self.manifest_path, expected_model="openai/gpt-5-mini")
        self.assertEqual(len(data3.batches), 3)

    def test_failed_batch_recorded(self) -> None:
        m = BatchManifest(self.manifest_path, model="openai/gpt-5-mini", batch_size=50)
        m.set_total_batches(1)
        m.record_batch(
            batch_idx=1,
            batch_id="batch_fail",
            status="failed",
            files=["/x.gz"],
            error="Batch job expired",
        )

        data = load_manifest(self.manifest_path, expected_model="openai/gpt-5-mini")
        entry = data.batches[0]
        self.assertEqual(entry.status, "failed")
        self.assertEqual(entry.error, "Batch job expired")

    def test_null_batch_id_for_submit_failure(self) -> None:
        m = BatchManifest(self.manifest_path, model="openai/gpt-5-mini", batch_size=50)
        m.set_total_batches(1)
        m.record_batch(
            batch_idx=1,
            batch_id=None,
            status="failed",
            files=["/x.gz"],
            error="submit failed",
        )

        data = load_manifest(self.manifest_path, expected_model="openai/gpt-5-mini")
        self.assertIsNone(data.batches[0].batch_id)

    def test_atomic_write_no_tmp_left(self) -> None:
        """After a successful write, no .tmp file should remain."""
        m = BatchManifest(self.manifest_path, model="openai/gpt-5-mini", batch_size=50)
        m.set_total_batches(1)
        m.record_batch(batch_idx=1, batch_id="b1", status="completed", files=["/a.gz"])

        self.assertTrue(os.path.exists(self.manifest_path))
        self.assertFalse(os.path.exists(self.manifest_path + ".tmp"))

    def test_creates_parent_directory(self) -> None:
        """BatchManifest creates the parent directory if it doesn't exist."""
        import shutil

        nested_dir = tempfile.mkdtemp()
        os.rmdir(nested_dir)  # remove so BatchManifest has to create it
        nested = os.path.join(nested_dir, "sub", "batch-manifest.json")
        m = BatchManifest(nested, model="openai/gpt-5-mini", batch_size=50)
        m.set_total_batches(1)
        m.record_batch(batch_idx=1, batch_id="b1", status="completed", files=["/a.gz"])

        self.assertTrue(os.path.isfile(nested))
        shutil.rmtree(nested_dir)

    def test_record_replaces_existing_entry(self) -> None:
        """A second record_batch with the same batch_idx replaces the first."""
        m = BatchManifest(self.manifest_path, model="openai/gpt-5-mini", batch_size=50)
        m.set_total_batches(1)
        m.record_batch(batch_idx=1, batch_id="b1", status="submitted", files=["/a.gz"])

        data1 = load_manifest(self.manifest_path, expected_model="openai/gpt-5-mini")
        self.assertEqual(len(data1.batches), 1)
        self.assertEqual(data1.batches[0].status, "submitted")

        m.record_batch(
            batch_idx=1,
            batch_id="b1",
            status="completed",
            files=["/a.gz"],
        )

        data2 = load_manifest(self.manifest_path, expected_model="openai/gpt-5-mini")
        self.assertEqual(len(data2.batches), 1)
        self.assertEqual(data2.batches[0].status, "completed")


class TestLoadManifest(unittest.TestCase):
    def test_load_roundtrip(self) -> None:
        raw = {
            "version": 1,
            "model": "openai/gpt-5-mini",
            "batch_size": 50,
            "total_batches": 1,
            "batches": [
                {
                    "batch_idx": 1,
                    "batch_id": "b1",
                    "status": "completed",
                    "error": None,
                    "files": ["/a.gz"],
                }
            ],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(raw, f)
            path = f.name

        try:
            loaded = load_manifest(path, expected_model="openai/gpt-5-mini")
            self.assertIsInstance(loaded, ManifestData)
            self.assertEqual(loaded.version, 1)
            self.assertEqual(len(loaded.batches), 1)
        finally:
            os.unlink(path)

    def test_bad_version_raises(self) -> None:
        raw = {
            "version": 999,
            "model": "m",
            "batch_size": 1,
            "total_batches": 0,
            "batches": [],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(raw, f)
            path = f.name

        try:
            with self.assertRaises(ValidationError):
                load_manifest(path, expected_model="m")
        finally:
            os.unlink(path)

    def test_model_mismatch_raises(self) -> None:
        raw = {
            "version": 1,
            "model": "openai/gpt-5-mini",
            "batch_size": 50,
            "total_batches": 0,
            "batches": [],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(raw, f)
            path = f.name

        try:
            with self.assertRaises(ValueError, msg="does not match"):
                load_manifest(path, expected_model="openai/gpt-4o")
        finally:
            os.unlink(path)

    def test_missing_field_raises(self) -> None:
        raw = {"version": 1, "model": "m"}  # missing batch_size, batches, etc.
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(raw, f)
            path = f.name

        try:
            with self.assertRaises(ValidationError):
                load_manifest(path, expected_model="m")
        finally:
            os.unlink(path)


class TestFailedBatches(unittest.TestCase):
    def test_filters_failed_entries(self) -> None:
        data = _make_manifest_data(
            total_batches=4,
            batches=[
                {
                    "batch_idx": 1,
                    "batch_id": "b1",
                    "status": "completed",
                    "error": None,
                    "files": ["/a.gz"],
                },
                {
                    "batch_idx": 2,
                    "batch_id": "b2",
                    "status": "failed",
                    "error": "expired",
                    "files": ["/b.gz"],
                },
                {
                    "batch_idx": 3,
                    "batch_id": "b3",
                    "status": "completed",
                    "error": None,
                    "files": ["/c.gz"],
                },
                {
                    "batch_idx": 4,
                    "batch_id": None,
                    "status": "failed",
                    "error": "submit err",
                    "files": ["/d.gz"],
                },
            ],
        )
        result = failed_batches(data)
        self.assertEqual(len(result), 2)
        self.assertIsInstance(result[0], BatchManifestEntry)
        self.assertEqual(result[0].batch_idx, 2)
        self.assertEqual(result[0].batch_id, "b2")
        self.assertEqual(result[0].error, "expired")
        self.assertEqual(result[1].batch_idx, 4)
        self.assertIsNone(result[1].batch_id)

    def test_no_failures(self) -> None:
        data = _make_manifest_data(
            total_batches=1,
            batches=[
                {
                    "batch_idx": 1,
                    "batch_id": "b1",
                    "status": "completed",
                    "error": None,
                    "files": ["/a.gz"],
                },
            ],
        )
        result = failed_batches(data)
        self.assertEqual(result, [])

    def test_empty_batches(self) -> None:
        data = _make_manifest_data()
        result = failed_batches(data)
        self.assertEqual(result, [])

    def test_submitted_status_treated_as_failed(self) -> None:
        """Batches still in 'submitted' state (interrupted run) are salvageable."""
        data = _make_manifest_data(
            total_batches=2,
            batches=[
                {
                    "batch_idx": 1,
                    "batch_id": "b1",
                    "status": "submitted",
                    "error": None,
                    "files": ["/a.gz"],
                },
                {
                    "batch_idx": 2,
                    "batch_id": "b2",
                    "status": "completed",
                    "error": None,
                    "files": ["/b.gz"],
                },
            ],
        )
        result = failed_batches(data)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].batch_idx, 1)
        self.assertEqual(result[0].status, "submitted")


if __name__ == "__main__":
    unittest.main()
