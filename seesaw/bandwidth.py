'''Global bandwidth-limit registry.

Holds the operator-configured aggregate download/upload caps (KiB/s) and the
live concurrency divisors, and computes the per-process limit each spawned
wget/rsync/curl worker should receive. Populated by the warrior (or the
standalone runner); read by the external-process tasks when they realize their
argument lists.

When a total is unset or ``0`` every accessor returns ``None`` so process
arguments are left unchanged -- this keeps plain ``run-pipeline`` behaviour
identical for anyone who does not set a limit.

A cap is an aggregate *ceiling*: ``per_process = max(1, total // divisor)``.
It is conservative (it under-shoots when fewer than ``divisor`` workers are
actually transferring) and never exceeds the configured total.
'''

# Module-global registry. Same idiom as ``_all_procs`` in externalprocess and
# ``ConfigValue.collector`` in config.
_limits = {
    "download": None,
    "upload": None,
    "download_divisor": None,
    "upload_divisor": None,
}


def set_limits(download=None, upload=None,
               download_divisor=None, upload_divisor=None):
    '''Populate the registry. Each value may be a plain int, a callable
    returning an int, or any object with a ``realize(item)`` method (e.g. a
    ``ConfigValue``), so live UI/config changes are tracked.'''
    _limits["download"] = download
    _limits["upload"] = upload
    _limits["download_divisor"] = download_divisor
    _limits["upload_divisor"] = upload_divisor


def reset():
    '''Clear the registry (no limits). Used by the test suite and any caller
    that wants to disable limiting.'''
    set_limits()


def _resolve(value, item):
    if value is None:
        return None
    if hasattr(value, "realize"):
        return value.realize(item)
    if callable(value):
        return value()
    return value


def _per_process(total_key, divisor_key, item):
    total = _resolve(_limits[total_key], item)
    if total is None:
        return None
    total = int(total)
    if total <= 0:
        return None
    divisor = _resolve(_limits[divisor_key], item)
    divisor = int(divisor) if divisor is not None else 1
    if divisor < 1:
        divisor = 1
    return max(1, total // divisor)


def download_kib(item=None):
    return _per_process("download", "download_divisor", item)


def upload_kib(item=None):
    return _per_process("upload", "upload_divisor", item)


def wget_limit_rate(item=None):
    '''wget ``--limit-rate`` value: per-process KiB with a ``k`` suffix
    (wget's ``k`` == 1024 bytes), or ``None`` for unlimited.'''
    kib = download_kib(item)
    return "%dk" % kib if kib is not None else None


def rsync_bwlimit(item=None):
    '''rsync ``--bwlimit`` value: a bare integer (rsync's native unit is KiB/s),
    or ``None`` for unlimited. Bare integer keeps compatibility with rsync
    < 3.1 (no suffix support).'''
    kib = upload_kib(item)
    return str(kib) if kib is not None else None


def curl_limit_rate(item=None):
    '''curl ``--limit-rate`` value: per-process KiB with a ``k`` suffix,
    or ``None`` for unlimited. curl's suffixes are 1024-based (``k`` == ``K``
    == 1024 bytes), matching wget, so the same KiB value is consistent across
    both tools.'''
    kib = upload_kib(item)
    return "%dk" % kib if kib is not None else None
