#!/usr/bin/env python3
"""
Scheduler System for FreQ
Auto-play songs, ads, and jingles based on time or conditions.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Callable, Optional


class ScheduleAction(Enum):
    PLAY_SONG = "play_song"
    PLAY_ADS = "play_ads"
    PLAY_JINGLE = "play_jingle"
    PAUSE = "pause"
    STOP = "stop"
    SET_VOLUME = "set_volume"


@dataclass
class ScheduleEvent:
    """A single scheduled event."""
    id: str
    action: ScheduleAction
    time: str  # HH:MM format or "now+30m" for relative
    days: list[str] = field(default_factory=lambda: ["mon", "tue", "wed", "thu", "fri", "sat", "sun"])
    enabled: bool = True
    params: dict = field(default_factory=dict)  # e.g., {"song_index": 0, "volume": 80}
    last_run: Optional[str] = None
    repeat: bool = False  # Run once or repeat daily
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "action": self.action.value,
            "time": self.time,
            "days": self.days,
            "enabled": self.enabled,
            "params": self.params,
            "last_run": self.last_run,
            "repeat": self.repeat,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> ScheduleEvent:
        return cls(
            id=data["id"],
            action=ScheduleAction(data["action"]),
            time=data["time"],
            days=data.get("days", ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]),
            enabled=data.get("enabled", True),
            params=data.get("params", {}),
            last_run=data.get("last_run"),
            repeat=data.get("repeat", False),
        )


class Scheduler:
    """Scheduler for timed playback events."""
    
    def __init__(self, save_path: Optional[Path] = None):
        self.events: dict[str, ScheduleEvent] = {}
        self._callbacks: dict[ScheduleAction, Callable] = {}
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._save_path = save_path or Path("scheduler.json")
        self._check_interval = 30  # Check every 30 seconds
        
        # Load saved events
        if self._save_path.exists():
            self.load()
    
    def register_callback(self, action: ScheduleAction, callback: Callable):
        """Register a callback for a schedule action."""
        self._callbacks[action] = callback
    
    def add_event(self, event: ScheduleEvent) -> None:
        """Add a new scheduled event."""
        self.events[event.id] = event
        self.save()
    
    def remove_event(self, event_id: str) -> bool:
        """Remove a scheduled event."""
        if event_id in self.events:
            del self.events[event_id]
            self.save()
            return True
        return False
    
    def update_event(self, event: ScheduleEvent) -> None:
        """Update an existing event."""
        self.events[event.id] = event
        self.save()
    
    def get_event(self, event_id: str) -> Optional[ScheduleEvent]:
        """Get an event by ID."""
        return self.events.get(event_id)
    
    def list_events(self) -> list[ScheduleEvent]:
        """List all events."""
        return list(self.events.values())
    
    def start(self) -> None:
        """Start the scheduler."""
        if self._running:
            return
        
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
    
    def stop(self) -> None:
        """Stop the scheduler."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
    
    def _run_loop(self) -> None:
        """Main scheduler loop."""
        while self._running:
            now = datetime.now()
            self._check_events(now)
            time.sleep(self._check_interval)
    
    def _check_events(self, now: datetime) -> None:
        """Check if any events should trigger."""
        current_time = now.strftime("%H:%M")
        current_day = now.strftime("%a").lower()[:3]  # mon, tue, etc.
        
        for event in self.events.values():
            if not event.enabled:
                continue
            
            # Check if day matches
            if current_day not in event.days:
                continue
            
            # Check if time matches
            if event.time != current_time:
                continue
            
            # Check if already run today
            today = now.strftime("%Y-%m-%d")
            if event.last_run == today and not event.repeat:
                continue
            
            # Trigger event
            self._trigger_event(event, now)
    
    def _trigger_event(self, event: ScheduleEvent, now: datetime) -> None:
        """Trigger a scheduled event."""
        callback = self._callbacks.get(event.action)
        if callback:
            try:
                callback(event.params)
                event.last_run = now.strftime("%Y-%m-%d")
                self.save()
            except Exception as e:
                print(f"Scheduler error: {e}")
    
    def save(self) -> None:
        """Save events to file."""
        data = {eid: e.to_dict() for eid, e in self.events.items()}
        self._save_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    
    def load(self) -> None:
        """Load events from file."""
        try:
            data = json.loads(self._save_path.read_text(encoding="utf-8"))
            self.events = {eid: ScheduleEvent.from_dict(e) for eid, e in data.items()}
        except Exception:
            self.events = {}
    
    def get_next_event(self) -> Optional[ScheduleEvent]:
        """Get the next upcoming event."""
        now = datetime.now()
        current_time = now.strftime("%H:%M")
        current_day = now.strftime("%a").lower()[:3]
        
        upcoming = []
        for event in self.events.values():
            if not event.enabled:
                continue
            if current_day not in event.days:
                continue
            if event.time <= current_time:
                continue
            upcoming.append(event)
        
        # Sort by time
        upcoming.sort(key=lambda e: e.time)
        return upcoming[0] if upcoming else None
    
    def create_quick_event(self, action: ScheduleAction, minutes_from_now: int, 
                          repeat: bool = False, **params) -> ScheduleEvent:
        """Create a quick event (X minutes from now)."""
        now = datetime.now()
        target = now + timedelta(minutes=minutes_from_now)
        
        event = ScheduleEvent(
            id=f"quick_{int(time.time())}",
            action=action,
            time=target.strftime("%H:%M"),
            repeat=repeat,
            params=params,
        )
        self.add_event(event)
        return event
