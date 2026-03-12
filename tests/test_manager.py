"""Unit tests for explainshell.manager."""

import argparse
import unittest
from unittest.mock import MagicMock, patch

from explainshell.manager import _build_chunk_aligned_batches


# ---------------------------------------------------------------------------
# TestBuildChunkAlignedBatches
# ---------------------------------------------------------------------------


class TestBuildChunkAlignedBatches(unittest.TestCase):
    """Tests for _build_chunk_aligned_batches: batch boundaries must never
    split a file's chunk group."""

    @staticmethod
    def _make_requests(file_chunk_counts):
        """Build (all_requests, key_to_location) for files with the given
        chunk counts.  E.g. [1, 3, 1] → file 0 has 1 chunk, file 1 has 3, etc.
        """
        all_requests = []
        key_to_location = {}
        for work_idx, n_chunks in enumerate(file_chunk_counts):
            for chunk_idx in range(n_chunks):
                key_str = f"{work_idx}:{chunk_idx}"
                all_requests.append((key_str, f"content-{key_str}"))
                key_to_location[key_str] = (work_idx, chunk_idx)
        return all_requests, key_to_location

    def _work_indices_in_batch(self, batch, key_to_location):
        """Return the set of work_idx values present in a batch."""
        return {key_to_location[key][0] for key, _ in batch}

    # -- basic cases --

    def test_single_chunk_files_even_split(self):
        """4 single-chunk files with batch_size=2 → 2 batches of 2."""
        reqs, k2l = self._make_requests([1, 1, 1, 1])
        batches = _build_chunk_aligned_batches(reqs, k2l, batch_size=2)
        self.assertEqual(len(batches), 2)
        self.assertEqual(len(batches[0]), 2)
        self.assertEqual(len(batches[1]), 2)

    def test_empty_requests(self):
        batches = _build_chunk_aligned_batches([], {}, batch_size=5)
        self.assertEqual(batches, [])

    def test_batch_size_larger_than_total(self):
        """All requests fit in one batch."""
        reqs, k2l = self._make_requests([1, 1, 1])
        batches = _build_chunk_aligned_batches(reqs, k2l, batch_size=100)
        self.assertEqual(len(batches), 1)
        self.assertEqual(len(batches[0]), 3)

    # -- chunk straddling --

    def test_multi_chunk_file_not_split(self):
        """File with 3 chunks straddling a batch_size=2 boundary stays together."""
        # File 0: 1 chunk, File 1: 3 chunks → requests: [0:0, 1:0, 1:1, 1:2]
        reqs, k2l = self._make_requests([1, 3])
        batches = _build_chunk_aligned_batches(reqs, k2l, batch_size=2)
        # Batch boundary at index 2 would split file 1's chunks.
        # The algorithm should extend batch 1 to include all of file 1.
        self.assertEqual(len(batches), 1)
        self.assertEqual(len(batches[0]), 4)

    def test_straddling_extends_batch(self):
        """Batch boundary falls mid-file → batch is extended."""
        # File 0: 2 chunks, File 1: 2 chunks, File 2: 2 chunks
        # batch_size=3 → naive split: [0:0, 0:1, 1:0] | [1:1, 2:0, 2:1]
        # File 1 straddles → extend batch 1 to include 1:1 → size 4
        reqs, k2l = self._make_requests([2, 2, 2])
        batches = _build_chunk_aligned_batches(reqs, k2l, batch_size=3)
        self.assertEqual(len(batches), 2)
        # First batch: file 0 (2) + file 1 (2) = 4
        self.assertEqual(len(batches[0]), 4)
        # Second batch: file 2 (2)
        self.assertEqual(len(batches[1]), 2)

    def test_every_file_stays_in_one_batch(self):
        """Property test: no file's chunks appear in more than one batch."""
        reqs, k2l = self._make_requests([1, 3, 2, 1, 4, 1])
        batches = _build_chunk_aligned_batches(reqs, k2l, batch_size=3)
        seen_files = set()
        for batch in batches:
            batch_files = self._work_indices_in_batch(batch, k2l)
            # No file should appear in a previous batch
            self.assertEqual(
                batch_files & seen_files,
                set(),
                f"Files {batch_files & seen_files} appear in multiple batches",
            )
            seen_files |= batch_files
        # All files accounted for
        self.assertEqual(seen_files, {0, 1, 2, 3, 4, 5})

    def test_all_requests_preserved(self):
        """Every request appears in exactly one batch."""
        reqs, k2l = self._make_requests([2, 3, 1, 2])
        batches = _build_chunk_aligned_batches(reqs, k2l, batch_size=3)
        flat = [item for batch in batches for item in batch]
        self.assertEqual(flat, reqs)

    # -- edge cases --

    def test_single_file_many_chunks(self):
        """One file with many chunks → single batch."""
        reqs, k2l = self._make_requests([10])
        batches = _build_chunk_aligned_batches(reqs, k2l, batch_size=3)
        self.assertEqual(len(batches), 1)
        self.assertEqual(len(batches[0]), 10)

    def test_batch_size_one(self):
        """batch_size=1 with multi-chunk files still groups them."""
        reqs, k2l = self._make_requests([1, 2, 1])
        batches = _build_chunk_aligned_batches(reqs, k2l, batch_size=1)
        # File 0: 1 chunk → batch of 1
        # File 1: 2 chunks → batch of 2 (extended from size 1)
        # File 2: 1 chunk → batch of 1
        self.assertEqual(len(batches), 3)
        self.assertEqual(len(batches[0]), 1)
        self.assertEqual(len(batches[1]), 2)
        self.assertEqual(len(batches[2]), 1)

    def test_exact_boundary(self):
        """Batch boundary falls exactly between files → no extension needed."""
        # File 0: 2 chunks, File 1: 2 chunks → batch_size=2
        reqs, k2l = self._make_requests([2, 2])
        batches = _build_chunk_aligned_batches(reqs, k2l, batch_size=2)
        self.assertEqual(len(batches), 2)
        self.assertEqual(len(batches[0]), 2)
        self.assertEqual(len(batches[1]), 2)
        # Verify each batch has exactly one file
        self.assertEqual(self._work_indices_in_batch(batches[0], k2l), {0})
        self.assertEqual(self._work_indices_in_batch(batches[1], k2l), {1})


# ---------------------------------------------------------------------------
# TestBatchPerBatchDbWrites
# ---------------------------------------------------------------------------


class TestBatchPerBatchDbWrites(unittest.TestCase):
    """Verify the manager writes results to the DB after each batch completes,
    not only after all batches finish."""

    _LLM_RESPONSE = (
        '{"dashless_opts": false, "options": ['
        '{"short": ["-v"], "long": [], "has_argument": false, "lines": [1, 3]}'
        "]}"
    )

    def _make_args(self, batch_size=2):
        return argparse.Namespace(
            mode="llm:openai/test-model",
            db="/tmp/test.db",
            overwrite=False,
            drop=False,
            dry_run=False,
            diff=None,
            debug_dir=None,
            log="WARNING",
            jobs=1,
            batch=batch_size,
            files=[],
        )

    def _make_prepared(self, name):
        """Return a minimal ``prepared`` dict for a single-chunk file."""
        return {
            "chunks": [f"1| **-v**\n2| \n3| Verbose for {name}."],
            "n_chunks": 1,
            "original_lines": {1: "**-v**", 2: "", 3: f"Verbose for {name}."},
            "plain_text": f"**-v**\n\nVerbose for {name}.",
            "numbered_text": f"1| **-v**\n2| \n3| Verbose for {name}.",
            "plain_text_len": 30,
            "basename": name,
            "synopsis": f"a {name} tool",
            "aliases": [(name, 1)],
        }

    @patch("explainshell.manager.llm_extractor")
    @patch("explainshell.manager.store.Store.create")
    @patch("explainshell.manager._collect_gz_files")
    @patch("explainshell.manager.config.source_from_path")
    def test_db_writes_after_each_batch(
        self, mock_source, mock_collect, mock_store_create, mock_llm
    ):
        """4 files, batch_size=2 → 2 batches.  After batch 1 completes the
        store must already have 2 add_manpage calls; after batch 2, 4 total."""
        # -- set up 4 fake gz files --
        gz_files = [
            "/fake/distro/release/1/alpha.1.gz",
            "/fake/distro/release/1/bravo.1.gz",
            "/fake/distro/release/1/charlie.1.gz",
            "/fake/distro/release/1/delta.1.gz",
        ]
        mock_collect.return_value = gz_files
        mock_source.side_effect = lambda p: "/".join(p.split("/")[-4:])

        # -- store mock --
        mock_store = MagicMock()
        mock_store_create.return_value = mock_store
        from explainshell import errors as _errors

        mock_store.find_man_page.side_effect = _errors.ProgramDoesNotExist("x")

        # -- llm_extractor mocks --
        names = ["alpha", "bravo", "charlie", "delta"]
        prepared_map = {gz: self._make_prepared(n) for gz, n in zip(gz_files, names)}
        mock_llm.prepare_extraction.side_effect = lambda gz: prepared_map[gz]
        mock_llm.build_user_content.side_effect = lambda chunk, info: f"prompt:{chunk}"
        mock_llm._SYSTEM_PROMPT = "system"
        mock_llm.make_batch_client.return_value = MagicMock()

        # submit_batch returns a mock job with an .id attribute
        mock_llm.submit_batch.return_value = MagicMock(id="job-1")

        # poll_batch just returns the completed job
        mock_llm.poll_batch.return_value = MagicMock()

        def _collect_side_effect(completed_job, model):
            """Return results for whichever batch we're on, keyed by the
            requests that were submitted in the most recent submit_batch call."""
            # Grab the batch_chunk from the most recent submit_batch call
            batch_chunk = mock_llm.submit_batch.call_args[0][0]
            results = {key: self._LLM_RESPONSE for key, _ in batch_chunk}
            usage = {"input_tokens": 100, "output_tokens": 50}
            return results, usage

        mock_llm.collect_batch_results.side_effect = _collect_side_effect

        # process_llm_result: parse the JSON response
        def _process_side_effect(response_text):
            import json

            data = json.loads(response_text)
            return data, response_text

        mock_llm.process_llm_result.side_effect = _process_side_effect

        # finalize_extraction: return a minimal ParsedManpage + RawManpage
        def _finalize_side_effect(gz_path, prepared, all_chunk_data, debug_dir=None):
            mp = MagicMock()
            mp.options = [MagicMock()]
            raw = MagicMock()
            return mp, raw

        mock_llm.finalize_extraction.side_effect = _finalize_side_effect

        # Snapshot add_manpage call count each time submit_batch is called.
        # Flow per batch: submit → poll → collect → finalize → add_manpage.
        # So when submit is called for batch N, batch N-1's writes are done.
        writes_at_submit = []

        def _submit_side_effect(batch_chunk, model):
            writes_at_submit.append(mock_store.add_manpage.call_count)
            return MagicMock(id="job-1")

        mock_llm.submit_batch.side_effect = _submit_side_effect

        from explainshell.manager import main

        args = self._make_args(batch_size=2)
        main(args)

        # submit_batch is called twice (once per batch):
        # - Submit for batch 1: 0 writes yet
        # - Submit for batch 2: batch 1 finalized → 2 writes
        self.assertEqual(len(writes_at_submit), 2)
        self.assertEqual(writes_at_submit[0], 0)
        self.assertEqual(writes_at_submit[1], 2)
        # After everything: 4 total writes
        self.assertEqual(mock_store.add_manpage.call_count, 4)

    @patch("explainshell.manager.llm_extractor")
    @patch("explainshell.manager.store.Store.create")
    @patch("explainshell.manager._collect_gz_files")
    @patch("explainshell.manager.config.source_from_path")
    def test_batch2_failure_preserves_batch1_writes(
        self, mock_source, mock_collect, mock_store_create, mock_llm
    ):
        """If batch 2 fails, files from batch 1 must still be in the DB."""
        gz_files = [
            "/fake/distro/release/1/alpha.1.gz",
            "/fake/distro/release/1/bravo.1.gz",
            "/fake/distro/release/1/charlie.1.gz",
            "/fake/distro/release/1/delta.1.gz",
        ]
        mock_collect.return_value = gz_files
        mock_source.side_effect = lambda p: "/".join(p.split("/")[-4:])

        mock_store = MagicMock()
        mock_store_create.return_value = mock_store
        from explainshell import errors as _errors

        mock_store.find_man_page.side_effect = _errors.ProgramDoesNotExist("x")

        names = ["alpha", "bravo", "charlie", "delta"]
        prepared_map = {gz: self._make_prepared(n) for gz, n in zip(gz_files, names)}
        mock_llm.prepare_extraction.side_effect = lambda gz: prepared_map[gz]
        mock_llm.build_user_content.side_effect = lambda chunk, info: f"prompt:{chunk}"
        mock_llm._SYSTEM_PROMPT = "system"
        mock_llm.make_batch_client.return_value = MagicMock()

        # submit_batch: succeed on first call, succeed on second (failure comes
        # from collect_batch_results)
        mock_llm.submit_batch.return_value = MagicMock(id="job-1")
        mock_llm.poll_batch.return_value = MagicMock()

        call_count = {"n": 0}

        def _collect_side_effect(completed_job, model):
            call_count["n"] += 1
            if call_count["n"] == 1:
                # Batch 1 succeeds
                batch_chunk = mock_llm.submit_batch.call_args[0][0]
                results = {key: self._LLM_RESPONSE for key, _ in batch_chunk}
                return results, {"input_tokens": 100, "output_tokens": 50}
            # Batch 2 fails
            raise _errors.ExtractionError("billing limit reached")

        mock_llm.collect_batch_results.side_effect = _collect_side_effect

        def _process_side_effect(response_text):
            import json

            return json.loads(response_text), response_text

        mock_llm.process_llm_result.side_effect = _process_side_effect

        def _finalize_side_effect(gz_path, prepared, all_chunk_data, debug_dir=None):
            mp = MagicMock()
            mp.options = [MagicMock()]
            return mp, MagicMock()

        mock_llm.finalize_extraction.side_effect = _finalize_side_effect

        from explainshell.manager import main

        args = self._make_args(batch_size=2)
        ret = main(args)

        # Batch 1's 2 files were written; batch 2 failed so its 2 were not.
        self.assertEqual(mock_store.add_manpage.call_count, 2)
        # Return code is non-zero because some files failed
        self.assertNotEqual(ret, 0)


# ---------------------------------------------------------------------------
# TestLlmManagerDryRun
# ---------------------------------------------------------------------------


class TestLlmManagerDryRun(unittest.TestCase):
    """Tests for --dry-run: LLM is called, DB is not written."""

    def _make_args(self, dry_run=True, overwrite=False, mode="llm:test-model"):
        args = argparse.Namespace(
            mode=mode,
            db="/tmp/test.db",
            overwrite=overwrite,
            drop=False,
            dry_run=dry_run,
            diff=None,
            debug_dir="debug-output",
            log="WARNING",
            jobs=1,
            batch=None,
            files=[],
        )
        return args

    @patch("explainshell.manager.llm_extractor.extract")
    @patch("explainshell.manager.store.Store.create")
    @patch("explainshell.manager._collect_gz_files")
    def test_dry_run_calls_llm_but_not_store(
        self, mock_collect, mock_store_create, mock_extract
    ):
        mock_collect.return_value = ["/fake/echo.1.gz"]
        fake_mp = MagicMock()
        fake_mp.options = [MagicMock(), MagicMock()]
        fake_raw = MagicMock()
        mock_extract.return_value = (fake_mp, fake_raw)

        from explainshell.manager import main

        args = self._make_args(dry_run=True)
        ret = main(args)

        mock_extract.assert_called_once_with(
            "/fake/echo.1.gz",
            "test-model",
            debug_dir="debug-output",
            fail_dir="debug-output",
        )
        mock_store_create.assert_not_called()
        self.assertEqual(ret, 0)

    @patch("explainshell.manager.llm_extractor.extract")
    @patch("explainshell.manager.store.Store.create")
    @patch("explainshell.manager._collect_gz_files")
    def test_normal_run_writes_to_store(
        self, mock_collect, mock_store_create, mock_extract
    ):
        mock_collect.return_value = ["/fake/echo.1.gz"]
        fake_mp = MagicMock()
        fake_mp.options = [MagicMock()]
        fake_mp.source = "echo.1.gz"
        fake_raw = MagicMock()
        mock_extract.return_value = (fake_mp, fake_raw)

        mock_store = MagicMock()
        mock_store_create.return_value = mock_store
        # simulate page not already stored
        from explainshell import errors

        mock_store.find_man_page.side_effect = errors.ProgramDoesNotExist("echo")

        from explainshell.manager import main

        args = self._make_args(dry_run=False)
        main(args)

        mock_extract.assert_called_once()
        mock_store.add_manpage.assert_called_once_with(fake_mp, fake_raw)


if __name__ == "__main__":
    unittest.main()
