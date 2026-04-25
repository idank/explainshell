"""Read-only Store wrapper with a size-aware manpage lookup cache."""

import sqlite3
from threading import RLock, local
from typing import NamedTuple, NoReturn

from cachetools import LRUCache

from explainshell import errors
from explainshell.models import ParsedManpage
from explainshell.store import Store


class _FindManpageMiss(NamedTuple):
    """Cached miss for find_man_page lookups."""

    # Only ProgramDoesNotExist.args (currently a single message string) is
    # preserved — if the exception ever gains structured fields they'll be
    # silently lost on cache hits.
    args: tuple[str, ...]


class ManpageCacheInfo(NamedTuple):
    """Runtime stats for CachingStore's manpage lookup cache."""

    hits: int
    misses: int
    entries: int
    size_bytes: int
    max_bytes: int


# Sized for our current prod instances.
_MANPAGE_CACHE_MAX_BYTES = 32 * 1024 * 1024
_MANPAGE_CACHE_MAX_ENTRY_BYTES = 1024 * 1024
_MANPAGE_CACHE_MAX_ENTRIES = 1024
_CacheKey = tuple[str, str | None, str | None]
_CacheValue = tuple[ParsedManpage, ...] | _FindManpageMiss


def _estimate_text_size(text: str | None) -> int:
    return len(text.encode("utf-8")) if text else 0


def _estimate_value_size(value: object) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        return _estimate_text_size(value)
    if isinstance(value, (bool, int, float)):
        return 8
    if isinstance(value, (list, tuple, set)):
        return 64 + sum(_estimate_value_size(item) for item in value)
    if isinstance(value, dict):
        return 64 + sum(
            _estimate_value_size(key) + _estimate_value_size(val)
            for key, val in value.items()
        )
    return _estimate_text_size(str(value))


def _estimate_manpage_size(manpage: ParsedManpage) -> int:
    total = 256
    total += _estimate_text_size(manpage.source)
    total += _estimate_text_size(manpage.name)
    total += _estimate_text_size(manpage.synopsis)
    total += _estimate_value_size(manpage.aliases)
    total += _estimate_value_size(manpage.subcommands)
    total += _estimate_value_size(manpage.nested_cmd)
    total += _estimate_text_size(manpage.extractor)
    if manpage.extraction_meta is not None:
        total += _estimate_value_size(manpage.extraction_meta.model_dump())

    for option in manpage.options:
        total += 192
        total += _estimate_text_size(option.text)
        total += _estimate_value_size(option.short)
        total += _estimate_value_size(option.long)
        total += _estimate_value_size(option.has_argument)
        total += _estimate_value_size(option.positional)
        total += _estimate_value_size(option.nested_cmd)
        total += _estimate_value_size(option.meta)

    return total


def _estimate_cache_value_size(value: _CacheValue) -> int:
    if isinstance(value, _FindManpageMiss):
        return 64 + _estimate_value_size(value.args)
    return 64 + sum(_estimate_manpage_size(manpage) for manpage in value)


class CachingStore(Store):
    """Read-only Store variant with a size-aware LRU for repeated lookups."""

    def __init__(
        self,
        db_path: str,
        *,
        max_cache_bytes: int = _MANPAGE_CACHE_MAX_BYTES,
        max_entry_bytes: int = _MANPAGE_CACHE_MAX_ENTRY_BYTES,
        max_entries: int = _MANPAGE_CACHE_MAX_ENTRIES,
    ) -> None:
        self._db_path = db_path
        self._local = local()
        self._stores: list[Store] = []
        self._stores_lock = RLock()
        self._closed = False
        self._lock = RLock()
        self._manpage_cache: LRUCache[_CacheKey, _CacheValue] = LRUCache(
            maxsize=max_cache_bytes,
            getsizeof=_estimate_cache_value_size,
        )
        self._manpage_cache_hits = 0
        self._manpage_cache_misses = 0
        self._manpage_cache_max_entry_bytes = max_entry_bytes
        self._manpage_cache_max_entries = max_entries

    @property
    def _conn(self) -> sqlite3.Connection:
        # Inherited read-only Store methods use ``self._conn``. Route them
        # through the current thread's underlying Store connection.
        return self._store()._conn

    def _store(self) -> Store:
        if self._closed:
            raise RuntimeError("CachingStore is closed")

        thread_store = getattr(self._local, "store", None)
        if thread_store is not None:
            return thread_store

        with self._stores_lock:
            if self._closed:
                raise RuntimeError("CachingStore is closed")

            thread_store = Store(self._db_path, read_only=True)
            self._local.store = thread_store
            self._stores.append(thread_store)
            return thread_store

    @classmethod
    def create(cls, db_path: str) -> NoReturn:
        raise TypeError("CachingStore is read-only; use Store.create() to build a DB")

    def close(self) -> None:
        with self._stores_lock:
            self._closed = True
            stores = self._stores
            self._stores = []

        for thread_store in stores:
            thread_store.close()
        if hasattr(self._local, "store"):
            del self._local.store

    def find_man_page(
        self, name: str, distro: str | None = None, release: str | None = None
    ) -> list[ParsedManpage]:
        key = (name, distro, release)
        with self._lock:
            try:
                value = self._manpage_cache[key]
            except KeyError:
                self._manpage_cache_misses += 1
            else:
                self._manpage_cache_hits += 1
                if isinstance(value, _FindManpageMiss):
                    raise errors.ProgramDoesNotExist(*value.args)
                return list(value)

        try:
            value: _CacheValue = tuple(
                self._store().find_man_page(name, distro=distro, release=release)
            )
        except errors.ProgramDoesNotExist as exc:
            value = _FindManpageMiss(exc.args)

        with self._lock:
            self._cache_manpage(key, value)

        if isinstance(value, _FindManpageMiss):
            raise errors.ProgramDoesNotExist(*value.args)
        return list(value)

    def manpage_cache_info(self) -> ManpageCacheInfo:
        with self._lock:
            return ManpageCacheInfo(
                hits=self._manpage_cache_hits,
                misses=self._manpage_cache_misses,
                entries=len(self._manpage_cache),
                size_bytes=self._manpage_cache.currsize,
                max_bytes=self._manpage_cache.maxsize,
            )

    def _cache_manpage(self, key: _CacheKey, value: _CacheValue) -> None:
        entry_size = _estimate_cache_value_size(value)
        if entry_size > self._manpage_cache_max_entry_bytes:
            return

        try:
            self._manpage_cache[key] = value
        except ValueError:
            return

        while len(self._manpage_cache) > self._manpage_cache_max_entries:
            self._manpage_cache.popitem()
