# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import threading
import time
from typing import Any, Dict, Tuple

_CACHE: Dict[str, Tuple[float, Any]] = {}
_LOCK = threading.Lock()


def _make_key(func, args, kwargs):
    payload = {
        "func": getattr(func, "__name__", str(func)),
        "args": args,
        "kwargs": kwargs,
    }
    try:
        return json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        return str(payload)


def get(func, hours, *args, **kwargs):
    kwargs = dict(kwargs)
    kwargs.pop("table", None)
    ttl = float(hours) * 3600.0
    key = _make_key(func, args, kwargs)
    now = time.time()
    with _LOCK:
        if key in _CACHE:
            expires_at, value = _CACHE[key]
            if expires_at > now:
                return value
    value = func(*args, **kwargs)
    with _LOCK:
        _CACHE[key] = (now + ttl, value)
    return value


def clear(_tables=None):
    with _LOCK:
        _CACHE.clear()
