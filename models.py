"""
Data models for UoPeople assignment/activity items.
Provides a unified representation for assignments, quizzes, forums, and other
Moodle activities before they are pushed to Notion.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional


class ActivityType(Enum):
    """Types of UoPeople Moodle activities."""
    ASSIGNMENT = "Assignment"
    QUIZ = "Quiz"
    FORUM = "Forum"
    JOURNAL = "Journal"
    OTHER = "Other"


class Priority(Enum):
    """Priority levels based on deadline proximity."""
    URGENT = "🔴 Urgent"
    SOON = "🟡 Soon"
    UPCOMING = "🟢 Upcoming"
    OVERDUE = "⚫ Overdue"


class SyncStatus(Enum):
    """Status of an item after sync attempt."""
    CREATED = "created"
    UPDATED = "updated"
    UNCHANGED = "unchanged"
    FAILED = "failed"


@dataclass
class MoodleActivity:
    """Represents a single activity/assignment from Moodle."""

    # Identity
    moodle_id: int
    course_id: int
    unique_key: str  # {course_id}_{type}_{moodle_id}

    # Content
    title: str
    course_name: str
    activity_type: ActivityType
    description: str = ""

    # Timing
    due_date: Optional[datetime] = None
    time_open: Optional[datetime] = None

    # Links
    moodle_url: str = ""

    # Status from Moodle
    is_submitted: bool = False

    def calculate_priority(
        self, urgent_hours: int = 24, soon_hours: int = 72
    ) -> Priority:
        """Calculate priority based on how close the deadline is."""
        if self.due_date is None:
            return Priority.UPCOMING

        now = datetime.now(self.due_date.tzinfo)
        time_remaining = self.due_date - now

        if time_remaining < timedelta(0):
            return Priority.OVERDUE
        elif time_remaining <= timedelta(hours=urgent_hours):
            return Priority.URGENT
        elif time_remaining <= timedelta(hours=soon_hours):
            return Priority.SOON
        else:
            return Priority.UPCOMING

    @staticmethod
    def detect_activity_type(name: str, mod_name: str = "") -> ActivityType:
        """Detect the activity type from its name or module type."""
        name_lower = name.lower()
        mod_lower = mod_name.lower()

        if mod_lower == "assign" or "written assignment" in name_lower:
            return ActivityType.ASSIGNMENT
        elif mod_lower == "quiz" or "quiz" in name_lower:
            return ActivityType.QUIZ
        elif mod_lower == "forum" or "discussion" in name_lower or "forum" in name_lower:
            return ActivityType.FORUM
        elif "learning journal" in name_lower or "journal" in name_lower:
            return ActivityType.JOURNAL
        else:
            return ActivityType.OTHER

    def to_dict(self) -> dict:
        """Convert to a plain dictionary for logging/debugging."""
        return {
            "unique_key": self.unique_key,
            "title": self.title,
            "course": self.course_name,
            "type": self.activity_type.value,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "moodle_url": self.moodle_url,
            "is_submitted": self.is_submitted,
        }


@dataclass
class SyncResult:
    """Summary of a sync operation."""
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    failed: int = 0
    errors: list = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.created + self.updated + self.unchanged + self.failed

    def summary(self) -> str:
        parts = []
        if self.created:
            parts.append(f"✅ {self.created} new")
        if self.updated:
            parts.append(f"🔄 {self.updated} updated")
        if self.unchanged:
            parts.append(f"⏭️  {self.unchanged} unchanged")
        if self.failed:
            parts.append(f"❌ {self.failed} failed")
        return " | ".join(parts) if parts else "No items processed"
