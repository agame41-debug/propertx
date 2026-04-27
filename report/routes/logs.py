"""
report/routes/logs.py — Logging viewer routes for Rentero web UI.

Provides:
  - /logs — HTML page to view application logs
  - /api/logs — JSON API to fetch logs
"""

from datetime import datetime
from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
import json


def register(app, state) -> None:
    """Register logging routes."""
    require_auth = state["require_auth"]
    require_admin_or_manager = state["require_admin_or_manager"]
    require_csrf = state["require_csrf"]
    templates = state["templates"]
    
    @app.get("/logs", response_class=HTMLResponse)
    async def logs_page(
        request: Request,
        limit: int = 200,
        _=Depends(require_admin_or_manager),
    ):
        """Display application logs."""
        from report.logging_service import get_recent_logs
        
        logs = get_recent_logs(limit)
        
        # Group logs by level for statistics
        level_counts = {}
        for log in logs:
            level = log.get("level", "INFO")
            level_counts[level] = level_counts.get(level, 0) + 1
        
        return templates.TemplateResponse(
            request,
            "logs.html",
            {
                "logs": logs,
                "level_counts": level_counts,
                "total_logs": len(logs),
            },
        )
    
    @app.get("/api/logs")
    async def api_logs(
        limit: int = 200,
        level: str = "",
        _=Depends(require_admin_or_manager),
    ):
        """JSON API to fetch application logs."""
        from report.logging_service import get_recent_logs
        
        logs = get_recent_logs(limit)
        
        # Filter by level if specified
        if level:
            logs = [log for log in logs if log.get("level") == level.upper()]
        
        return {"logs": logs, "count": len(logs)}
    
    @app.post("/api/logs/clear")
    async def api_clear_logs(
        _=Depends(require_admin_or_manager),
        __=Depends(require_csrf),
    ):
        """Clear log buffer."""
        from report.logging_service import clear_logs
        clear_logs()
        return {"status": "cleared"}
