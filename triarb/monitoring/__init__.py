"""Structured logging, metrics, and the terminal dashboard."""

from triarb.monitoring.jsonlog import log_event, setup_logging
from triarb.monitoring.metrics import Histogram, Metrics

__all__ = ["Histogram", "Metrics", "log_event", "setup_logging"]
