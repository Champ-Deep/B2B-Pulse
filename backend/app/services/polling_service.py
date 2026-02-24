"""Polling configuration and scheduling logic."""

from dataclasses import dataclass


@dataclass
class PollingConfig:
    """Configuration for page polling behavior."""

    normal_interval_seconds: int = 300  # 5 minutes
    hunt_interval_seconds: int = 60  # 1 minute
    hunt_window_start_hour: int = 9  # 9 AM
    hunt_window_end_hour: int = 11  # 11 AM
    max_posts_per_poll: int = 10
    max_retries: int = 3


# Default polling configuration
DEFAULT_POLLING_CONFIG = PollingConfig()
