"""Tests for tools/postprocess_ubuntu_archive.py."""

from __future__ import annotations

import os
from pathlib import Path

# Allow importing from tools/
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from postprocess_ubuntu_archive import postprocess


def _make_src(tmp_path: Path) -> Path:
    """Create a minimal source directory with man1/ and man8/."""
    src = tmp_path / "src"
    (src / "man1").mkdir(parents=True)
    (src / "man8").mkdir(parents=True)
    return src


def _write(path: Path, content: str = "content") -> None:
    path.write_text(content)


def test_regular_files_copied(tmp_path: Path) -> None:
    src = _make_src(tmp_path)
    _write(src / "man1" / "tar.1.gz", "tar-data")
    _write(src / "man8" / "mount.8.gz", "mount-data")

    dst = tmp_path / "dst"
    stats = postprocess(src, dst)

    assert (dst / "1" / "tar.1.gz").read_text() == "tar-data"
    assert (dst / "8" / "mount.8.gz").read_text() == "mount-data"
    assert stats.files_copied == 2
    assert stats.symlinks_copied == 0
    assert stats.symlinks_skipped == 0


def test_same_section_symlink_preserved(tmp_path: Path) -> None:
    src = _make_src(tmp_path)
    _write(src / "man1" / "coqc.1.gz", "coqc-data")
    os.symlink("coqc.1.gz", src / "man1" / "coqc.byte.1.gz")

    dst = tmp_path / "dst"
    stats = postprocess(src, dst)

    result = dst / "1" / "coqc.byte.1.gz"
    assert result.is_symlink()
    assert os.readlink(result) == "coqc.1.gz"
    assert result.read_text() == "coqc-data"
    assert stats.symlinks_copied == 1


def test_cross_section_symlink_rewritten(tmp_path: Path) -> None:
    src = _make_src(tmp_path)
    _write(src / "man1" / "foomatic-rip.1.gz", "foomatic-data")
    os.symlink("../man1/foomatic-rip.1.gz", src / "man8" / "lpdomatic.8.gz")

    dst = tmp_path / "dst"
    stats = postprocess(src, dst)

    result = dst / "8" / "lpdomatic.8.gz"
    assert result.is_symlink()
    assert os.readlink(result) == "../1/foomatic-rip.1.gz"
    assert result.read_text() == "foomatic-data"
    assert stats.symlinks_rewritten == 1


def test_broken_same_section_symlink_skipped(tmp_path: Path) -> None:
    src = _make_src(tmp_path)
    os.symlink("nonexistent.1.gz", src / "man1" / "missing.1.gz")

    dst = tmp_path / "dst"
    stats = postprocess(src, dst)

    assert not (dst / "1" / "missing.1.gz").exists()
    assert stats.symlinks_skipped == 1


def test_cross_section_to_deleted_section_skipped(tmp_path: Path) -> None:
    src = _make_src(tmp_path)
    # man7 exists in source but is not a kept section.
    (src / "man7").mkdir()
    _write(src / "man7" / "topcom.7.gz", "topcom-data")
    os.symlink("../man7/topcom.7.gz", src / "man1" / "Calculator.1.gz")

    dst = tmp_path / "dst"
    stats = postprocess(src, dst)

    assert not (dst / "1" / "Calculator.1.gz").exists()
    assert stats.symlinks_skipped == 1


def test_deep_path_symlink_skipped(tmp_path: Path) -> None:
    src = _make_src(tmp_path)
    os.symlink(
        "../../../../../share/man/man1/bio-eagle.1.gz",
        src / "man1" / "eagle.1.gz",
    )

    dst = tmp_path / "dst"
    stats = postprocess(src, dst)

    assert not (dst / "1" / "eagle.1.gz").exists()
    assert stats.symlinks_skipped == 1


def test_absolute_path_symlink_skipped(tmp_path: Path) -> None:
    src = _make_src(tmp_path)
    os.symlink("/usr/share/man/man1/foo.1.gz", src / "man1" / "foo.1.gz")

    dst = tmp_path / "dst"
    stats = postprocess(src, dst)

    assert not (dst / "1" / "foo.1.gz").exists()
    assert stats.symlinks_skipped == 1


def test_nested_directory_skipped(tmp_path: Path) -> None:
    src = _make_src(tmp_path)
    (src / "man1" / "man1").mkdir()
    _write(src / "man1" / "man1" / "nested.1.gz")
    _write(src / "man1" / "top-level.1.gz")

    dst = tmp_path / "dst"
    stats = postprocess(src, dst)

    assert (dst / "1" / "top-level.1.gz").is_file()
    assert not (dst / "1" / "man1").exists()
    assert stats.dirs_skipped == 1
    assert stats.files_copied == 1


def test_non_kept_sections_ignored(tmp_path: Path) -> None:
    src = _make_src(tmp_path)
    (src / "man3").mkdir()
    _write(src / "man3" / "libfoo.3.gz")
    _write(src / "man1" / "foo.1.gz")

    dst = tmp_path / "dst"
    stats = postprocess(src, dst)

    assert not (dst / "3").exists()
    assert (dst / "1" / "foo.1.gz").is_file()
    assert stats.files_copied == 1


def test_idempotent(tmp_path: Path) -> None:
    src = _make_src(tmp_path)
    _write(src / "man1" / "tar.1.gz", "tar-data")
    os.symlink("tar.1.gz", src / "man1" / "gtar.1.gz")
    os.symlink("nonexistent.1.gz", src / "man1" / "broken.1.gz")

    dst = tmp_path / "dst"

    stats1 = postprocess(src, dst)
    stats2 = postprocess(src, dst)

    assert stats1 == stats2
    assert (dst / "1" / "tar.1.gz").read_text() == "tar-data"
    result = dst / "1" / "gtar.1.gz"
    assert result.is_symlink()
    assert os.readlink(result) == "tar.1.gz"
    assert not (dst / "1" / "broken.1.gz").exists()


def test_dry_run(tmp_path: Path) -> None:
    src = _make_src(tmp_path)
    _write(src / "man1" / "tar.1.gz", "tar-data")
    os.symlink("tar.1.gz", src / "man1" / "gtar.1.gz")

    dst = tmp_path / "dst"
    stats = postprocess(src, dst, dry_run=True)

    assert not dst.exists()
    assert stats.files_copied == 1
    assert stats.symlinks_copied == 1


def test_stats_counts(tmp_path: Path) -> None:
    src = _make_src(tmp_path)
    # 2 regular files
    _write(src / "man1" / "a.1.gz")
    _write(src / "man8" / "b.8.gz")
    # 1 valid same-section symlink
    os.symlink("a.1.gz", src / "man1" / "alias-a.1.gz")
    # 1 valid cross-section symlink
    _write(src / "man1" / "rip.1.gz")
    os.symlink("../man1/rip.1.gz", src / "man8" / "cross.8.gz")
    # 1 broken symlink
    os.symlink("missing.1.gz", src / "man1" / "broken.1.gz")
    # 1 nested dir
    (src / "man1" / "subdir").mkdir()

    dst = tmp_path / "dst"
    stats = postprocess(src, dst)

    assert stats.files_copied == 3
    assert stats.symlinks_copied == 1
    assert stats.symlinks_rewritten == 1
    assert stats.symlinks_skipped == 1
    assert stats.dirs_skipped == 1


def test_missing_section_handled(tmp_path: Path) -> None:
    src = tmp_path / "src"
    (src / "man1").mkdir(parents=True)
    # man8 does not exist.
    _write(src / "man1" / "foo.1.gz")

    dst = tmp_path / "dst"
    stats = postprocess(src, dst)

    assert (dst / "1" / "foo.1.gz").is_file()
    assert not (dst / "8").exists()
    assert stats.files_copied == 1


def test_cross_section_missing_target_skipped(tmp_path: Path) -> None:
    src = _make_src(tmp_path)
    # Cross-section link to man1, but target doesn't exist there.
    os.symlink("../man1/nonexistent.1.gz", src / "man8" / "alias.8.gz")

    dst = tmp_path / "dst"
    stats = postprocess(src, dst)

    assert not (dst / "8" / "alias.8.gz").exists()
    assert stats.symlinks_skipped == 1
