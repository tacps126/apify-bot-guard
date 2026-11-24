"""Fair free-plan allowances for Apify Actors, with a per-Actor pool that keeps
farms of free accounts from draining your data and your budget."""

from .backends import ApifyKvBackend, Backend, MemoryBackend, RedisBackend
from .core import DEFAULTS, Allowance, Config, Entry, Ledger, allowance_of, env_name, is_free_plan, plan_of, user_hash
from .guard import BotGuard, Decision, push_data

__all__ = [
    "DEFAULTS",
    "Allowance",
    "ApifyKvBackend",
    "Backend",
    "BotGuard",
    "Config",
    "Decision",
    "Entry",
    "Ledger",
    "MemoryBackend",
    "RedisBackend",
    "allowance_of",
    "env_name",
    "is_free_plan",
    "plan_of",
    "push_data",
    "user_hash",
]
__version__ = "0.1.1"
