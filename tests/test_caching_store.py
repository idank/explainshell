import datetime
from collections.abc import Generator, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Protocol

import pytest

from explainshell import errors
from explainshell.caching_store import CachingStore
from explainshell.models import Option, ParsedManpage, RawManpage
from explainshell.store import Store


class _CachedStoreFactory(Protocol):
    def __call__(
        self,
        manpages: Sequence[ParsedManpage],
        *,
        max_cache_bytes: int | None = None,
        max_entry_bytes: int | None = None,
        max_entries: int | None = None,
    ) -> CachingStore: ...


def _make_raw() -> RawManpage:
    return RawManpage(
        source_text="test manpage content",
        generated_at=datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc),
        generator="test",
    )


@pytest.fixture
def cached_store_factory(
    tmp_path: Path,
) -> Generator[_CachedStoreFactory, None, None]:
    """Build file-backed read-only CachingStore instances for tests."""
    stores: list[CachingStore] = []

    def make(
        manpages: Sequence[ParsedManpage],
        *,
        max_cache_bytes: int | None = None,
        max_entry_bytes: int | None = None,
        max_entries: int | None = None,
    ) -> CachingStore:
        db_path = tmp_path / f"cache-{len(stores)}.db"
        writable = Store.create(str(db_path))
        for manpage in manpages:
            writable.add_manpage(manpage, _make_raw())
        writable.close()

        kwargs: dict[str, int] = {}
        if max_cache_bytes is not None:
            kwargs["max_cache_bytes"] = max_cache_bytes
        if max_entry_bytes is not None:
            kwargs["max_entry_bytes"] = max_entry_bytes
        if max_entries is not None:
            kwargs["max_entries"] = max_entries

        cached = CachingStore(str(db_path), **kwargs)
        stores.append(cached)
        return cached

    yield make

    for cached in stores:
        cached.close()


def _make_manpage(
    name: str,
    section: str,
    aliases: Sequence[tuple[str, int]] | None = None,
    distro: str = "ubuntu",
    release: str = "26.04",
) -> ParsedManpage:
    source = f"{distro}/{release}/{section}/{name}.{section}.gz"
    if aliases is None:
        aliases = [(name, 10)]
    return ParsedManpage(
        source=source,
        name=name,
        synopsis=f"{name} - do things",
        aliases=list(aliases),
    )


class TestFindManpageCache:
    def test_returns_fresh_list_from_cache(
        self, cached_store_factory: _CachedStoreFactory
    ) -> None:
        mp1 = _make_manpage("printf", "1", aliases=[("printf", 10)])
        mp2 = _make_manpage("printf", "3", aliases=[("printf", 10)])
        store = cached_store_factory([mp1, mp2])

        first = store.find_man_page("printf")
        first.pop(0)
        second = store.find_man_page("printf")

        assert len(second) == 2
        assert {r.section for r in second} == {"1", "3"}

    def test_missing_lookup_is_cached(
        self, cached_store_factory: _CachedStoreFactory
    ) -> None:
        store = cached_store_factory([])
        with pytest.raises(errors.ProgramDoesNotExist):
            store.find_man_page("newtool")
        before = store.manpage_cache_info()

        with pytest.raises(errors.ProgramDoesNotExist):
            store.find_man_page("newtool")
        after = store.manpage_cache_info()

        assert after.hits == before.hits + 1

    def test_oversized_entries_are_not_cached(
        self, cached_store_factory: _CachedStoreFactory
    ) -> None:
        mp = _make_manpage("huge", "1")
        mp.options = [Option(text="x" * 4096)]
        store = cached_store_factory([mp], max_entry_bytes=512)

        assert store.find_man_page("huge")[0].name == "huge"

        info = store.manpage_cache_info()
        assert info.entries == 0
        assert info.size_bytes == 0

    def test_cache_stays_within_total_byte_budget(
        self, cached_store_factory: _CachedStoreFactory
    ) -> None:
        manpages = [_make_manpage(name, "1") for name in ("one", "two", "three")]
        store = cached_store_factory(
            manpages,
            max_cache_bytes=1024,
            max_entry_bytes=4096,
        )
        for name in ("one", "two", "three"):
            store.find_man_page(name)

        info = store.manpage_cache_info()
        assert info.size_bytes <= info.max_bytes

    def test_cache_respects_entry_limit(
        self, cached_store_factory: _CachedStoreFactory
    ) -> None:
        manpages = [_make_manpage(name, "1") for name in ("one", "two", "three")]
        store = cached_store_factory(
            manpages,
            max_cache_bytes=4096,
            max_entry_bytes=4096,
            max_entries=2,
        )
        for name in ("one", "two", "three"):
            store.find_man_page(name)

        assert store.manpage_cache_info().entries == 2

    def test_concurrent_access_is_safe(
        self, cached_store_factory: _CachedStoreFactory
    ) -> None:
        huge = _make_manpage("huge", "1")
        huge.options = [Option(text="x" * 4096)]
        store = cached_store_factory(
            [_make_manpage("alpha", "1"), _make_manpage("beta", "1"), huge],
            max_cache_bytes=4096,
            max_entry_bytes=512,
        )

        def lookup(name: str) -> str:
            try:
                return store.find_man_page(name)[0].name
            except errors.ProgramDoesNotExist:
                return "missing"

        # Warm a positive and negative cache entry, then hammer mixed hits,
        # misses, cold lookups, and oversized uncached entries concurrently.
        assert lookup("alpha") == "alpha"
        assert lookup("missing") == "missing"
        before = store.manpage_cache_info()

        names = ["alpha", "beta", "missing", "huge"] * 50
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lookup, names))

        assert results.count("alpha") == 50
        assert results.count("beta") == 50
        assert results.count("missing") == 50
        assert results.count("huge") == 50

        after = store.manpage_cache_info()
        assert after.hits > before.hits
        assert after.entries <= 3
        assert after.size_bytes <= after.max_bytes

    def test_create_is_not_supported(self) -> None:
        with pytest.raises(TypeError):
            CachingStore.create(":memory:")
