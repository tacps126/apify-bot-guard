"""Monthly free-plan allowances for Apify Actors, with a per-Actor pool that
keeps farms of free accounts from draining your data and your proxy budget."""

from .backends import ApifyKvBackend, Backend, MemoryBackend, RedisBackend
from .core import Allowance, Config, Entry, Ledger, allowance_of, is_free_plan
from .guard import FreeGuard, push_data

__all__ = [
    "Allowance",
    "ApifyKvBackend",
    "Backend",
    "Config",
    "Entry",
    "FreeGuard",
    "Ledger",
    "MemoryBackend",
    "RedisBackend",
    "allowance_of",
    "is_free_plan",
    "push_data",
]
__version__ = "0.1.0"
