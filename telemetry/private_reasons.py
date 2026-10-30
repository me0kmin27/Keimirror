"""Private numeric diagnostics. Never carries source strings or identities."""
from contextvars import ContextVar
from enum import IntEnum
from functools import partial
import math
from typing import Any, Callable


class Reason(IntEnum):
    TCP_OWNERLESS = 1
    TCP_OWNERLESS_ESTABLISHED = 2
    TCP_OWNERLESS_OTHER_STATE = 3
    TCP_OWNERLESS_ROOT = 4
    TCP_OWNERLESS_NONROOT = 5
    TCP_OWNERLESS_UID_UNKNOWN = 6
    TCP_PROC_STAT_ENOENT = 10
    TCP_PROC_CMDLINE_ENOENT = 11
    TCP_RETRY_EXHAUSTED = 12
    TCP_PROCESS_TABLE_ENOENT = 13
    TCP_SS_ENOENT = 14
    TCP_DIAG_ENOENT = 15
    TCP_OTHER_ENOENT = 16
    TCP_HOLDER_DIR_ENOENT = 17
    TCP_HOLDER_LINK_ENOENT = 18
    TCP_DEADLINE = 19
    TCP_OBSERVATION_FAILED = 20
    TCP_BASELINE = 21
    TCP_PAIR_WINDOW = 22
    TCP_SOCKET_SET_CHANGED = 23
    TCP_COUNTER_OR_IDENTITY = 24
    TCP_LOST_ATTRIBUTED_COOKIE = 25
    TCP_RPC_UNAVAILABLE = 26
    TCP_NAMESPACE_CHANGED = 27
    TCP_OWNERSHIP_CHANGED = 28
    TCP_UNCLASSIFIED_TRANSPORT = 29
    TCP_RPC_FIRST_HOLDERS_ENOENT = 30
    TCP_RPC_SECOND_HOLDERS_ENOENT = 31
    TCP_ATTEMPT = 32
    TCP_NAMESPACE_ENOENT = 33
    TCP_OWNER_FD_ENOENT = 34
    TCP_HOLDER_VERIFY_ENOENT = 35
    TCP_HOLDER_RECOVERED_PROJECT = 36
    TCP_HOLDER_RECOVERED_UNRELATED = 37
    HTTP_UNCERTAIN_CHANGED = 100
    HTTP_ACTIVE_UNCERTAIN = 101
    HTTP_COUNTER_RESET = 102
    HTTP_EPOCH_CHANGED = 103
    HTTP_PAIR_WINDOW = 104
    HTTP_BASELINE = 105
    HTTP_WORKER_COVERAGE = 106
    HTTP_IDENTITY_CHANGED = 107
    HTTP_WORKERS_FAILED = 108
    HTTP_READ_FAILED = 109
    HTTP_DECODE_FAILED = 110
    COMBINED_TCP_NULL = 200
    COMBINED_HTTP_NULL = 201


_callback: ContextVar[Callable | None] = ContextVar('private_reason_callback', default=None)


class CollectionDeadline(Exception):
    """Existing whole-tick deadline, shared so observers never swallow it."""


def emit(code, repository=None, count=1):
    callback = _callback.get()
    if callback is not None:
        try:
            callback(code, repository, count)
        except CollectionDeadline:
            raise
        except Exception:
            # An observer cannot alter measurement return values/exceptions.
            pass


class Recorder:
    """One tick, fixed vocabulary, saturating counts; never read old diagnostics."""
    def __init__(self, repositories):
        self.repositories = frozenset(repositories)
        self.sources: dict[str, dict[str, Any]] = {
            source: dict(counts={}, repositories={}) for source in ('tcp', 'http', 'combined')}

    def sink(self, source):
        return partial(self.add, source)

    def add(self, source, code, repository=None, count=1):
        if (source not in self.sources or not isinstance(code, Reason)
                or type(count) is not int or count < 1
                or repository is not None and repository not in self.repositories):
            return
        row = self.sources[source]
        counts = row['counts'] if repository is None else row['repositories'].setdefault(repository, {})
        key = str(int(code))
        counts[key] = min(2**31 - 1, counts.get(key, 0) + count)

    def snapshot(self, now, tcp, http):
        def stamp(value):
            return value if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 2**53-1 else None
        for name, state in (('tcp', tcp), ('http', http)):
            state = state if isinstance(state, dict) else {}
            self.sources[name].update(state_present=bool(state),
                                      monotonic=stamp(state.get('monotonic')),
                                      observed_at=stamp(state.get('at')))
        return dict(schema_version=1, collected_at=stamp(now), sources=self.sources)
