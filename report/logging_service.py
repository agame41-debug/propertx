"""
report/logging_service.py — Rentero logging service with file and in-memory buffer.

Provides:
  - Ring buffer of recent log messages (last N lines)
  - API endpoint to fetch logs
  - HTML page to view logs in real-time
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import List, Dict

_LOG_BUFFER_SIZE = 1000  # Keep last 1000 log lines in memory
_LOG_FILE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "cache", "rentero.log"
)

class RingBufferHandler(logging.Handler):
    """In-memory ring buffer for recent log messages."""
    
    def __init__(self, capacity: int = 1000):
        super().__init__()
        self.capacity = capacity
        self.buffer: List[Dict] = []
    
    def emit(self, record: logging.LogRecord) -> None:
        """Add log record to buffer."""
        try:
            log_entry = {
                "timestamp": datetime.fromtimestamp(record.created).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": self.format(record),
            }
            self.buffer.append(log_entry)
            
            # Keep buffer size bounded
            if len(self.buffer) > self.capacity:
                self.buffer.pop(0)
        except Exception:
            self.handleError(record)
    
    def get_logs(self, limit: int | None = None) -> List[Dict]:
        """Get recent log entries."""
        if limit is None:
            return self.buffer[:]
        return self.buffer[-limit:] if self.buffer else []


# Global ring buffer
_buffer_handler = RingBufferHandler(_LOG_BUFFER_SIZE)


def setup_logging() -> None:
    """Initialize logging with file and in-memory buffer."""
    # Ensure cache directory exists
    Path(_LOG_FILE_PATH).parent.mkdir(parents=True, exist_ok=True)
    
    # Root logger
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    
    # File handler
    file_handler = logging.FileHandler(_LOG_FILE_PATH, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    ))
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    ))
    
    # Add handlers
    root.addHandler(file_handler)
    root.addHandler(console_handler)
    root.addHandler(_buffer_handler)


def get_recent_logs(limit: int = 200) -> List[Dict]:
    """Get recent log entries from ring buffer."""
    return _buffer_handler.get_logs(limit)


def get_log_file_path() -> str:
    """Get path to log file."""
    return _LOG_FILE_PATH


def clear_logs() -> None:
    """Clear log buffer."""
    _buffer_handler.buffer.clear()
