"""Strict, process-role-specific startup configuration."""

from .settings import Environment, ProcessRole, StartupConfig, load_startup_config

__all__ = ["Environment", "ProcessRole", "StartupConfig", "load_startup_config"]
