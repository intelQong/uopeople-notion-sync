"""
Notion API client wrapper for the UoPeople sync tool.

Handles creating, updating, and querying the Notion database
where assignment/activity entries are stored.
"""

import logging
from datetime import datetime
from typing import Optional

import httpx
from notion_client import Client as NotionClient
from notion_client.errors import APIResponseError

from models import ActivityType, MoodleActivity, Priority, SyncStatus

_NOTION_VERSION = "2022-06-28"

logger = logging.getLogger(__name__)


class NotionSyncError(Exception):
    """Raised when a Notion sync operation fails."""
    pass


# Mapping of ActivityType to Notion select colors
TYPE_COLORS = {
    ActivityType.ASSIGNMENT: "blue",
    ActivityType.QUIZ: "purple",
    ActivityType.FORUM: "green",
    ActivityType.JOURNAL: "orange",
    ActivityType.OTHER: "gray",
}

# Mapping of Priority to Notion select colors
PRIORITY_COLORS = {
    Priority.URGENT: "red",
    Priority.SOON: "yellow",
    Priority.UPCOMING: "green",
    Priority.OVERDUE: "default",
}


class NotionClientWrapper:
    """Wrapper around the Notion SDK for syncing Moodle activities."""

    def __init__(self, token: str, database_id: str):
        """
        Initialize the Notion client.

        Args:
            token: Notion internal integration token
            database_id: ID of the Notion database to sync to
        """
        self.client = NotionClient(auth=token)
        self.database_id = database_id
        self._existing_entries: dict[str, str] = {}  # unique_key -> page_id
        self._title_prop: str = "Name"  # Default, overridden in validate_database
        self._http = httpx.Client(
            base_url="https://api.notion.com",
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": _NOTION_VERSION,
                "Content-Type": "application/json",
            },
        )

    # ─────────────────────────────────────────────
    # Database Setup & Validation
    # ─────────────────────────────────────────────

    def validate_database(self) -> dict:
        """
        Validate that the database exists and has the required properties.

        Returns:
            Database info dict with title and properties.

        Raises:
            NotionSyncError: If the database is not accessible.
        """
        try:
            db = self.client.databases.retrieve(database_id=self.database_id)
            title = ""
            for title_item in db.get("title", []):
                title += title_item.get("plain_text", "")

            properties = list(db.get("properties", {}).keys())
            logger.info("✅ Connected to Notion database: '%s'", title)
            logger.info("   Properties: %s", ", ".join(properties))

            # Detect the title property name
            for prop_name, prop_def in db.get("properties", {}).items():
                if prop_def.get("type") == "title":
                    self._title_prop = prop_name
                    break

            return {
                "success": True,
                "title": title,
                "properties": properties,
            }
        except APIResponseError as e:
            if e.status == 404:
                raise NotionSyncError(
                    "Database not found. Make sure you've shared the database "
                    "with your integration (click ••• → Connections → your integration)"
                )
            elif e.status == 401:
                raise NotionSyncError(
                    "Invalid Notion token. Check NOTION_TOKEN in config.env"
                )
            raise NotionSyncError(f"Notion API error: {e}")

    def ensure_database_schema(self):
        """
        Ensure the database has all required properties.
        Creates missing properties if needed.
        """
        required_props = {
            "Course": "select",
            "Type": "select",
            "Due Date": "date",
            "Status": "status",
            "Priority": "select",
            "Moodle Link": "url",
            "Description": "rich_text",
            "Synced At": "date",
            "Sync Key": "rich_text",  # Hidden field for duplicate detection
        }

        try:
            db = self.client.databases.retrieve(database_id=self.database_id)
            existing_props = db.get("properties", {})

            updates = {}
            for prop_name, prop_type in required_props.items():
                if prop_name not in existing_props:
                    logger.info("Adding missing property: %s (%s)", prop_name, prop_type)
                    if prop_type == "select":
                        updates[prop_name] = {"select": {"options": []}}
                    elif prop_type == "date":
                        updates[prop_name] = {"date": {}}
                    elif prop_type == "status":
                        updates[prop_name] = {"status": {}}
                    elif prop_type == "url":
                        updates[prop_name] = {"url": {}}
                    elif prop_type == "rich_text":
                        updates[prop_name] = {"rich_text": {}}

            if updates:
                self._http.patch(
                    f"/v1/databases/{self.database_id}",
                    json={"properties": updates},
                )
                logger.info("✅ Updated database schema with %d new properties", len(updates))
            else:
                logger.info("✅ Database schema is up to date")

        except APIResponseError as e:
            logger.warning("Could not update schema (might need manual setup): %s", e)

    # ─────────────────────────────────────────────
    # Entry Management
    # ─────────────────────────────────────────────

    def load_existing_entries(self):
        """
        Load all existing entries from the Notion database to enable
        duplicate detection. Populates self._existing_entries with
        {sync_key: page_id} mapping.
        """
        self._existing_entries = {}
        has_more = True
        start_cursor = None

        while has_more:
            body = {
                "page_size": 100,
            }
            if start_cursor:
                body["start_cursor"] = start_cursor

            resp = self._http.post(
                f"/v1/databases/{self.database_id}/query",
                json=body,
            )
            resp.raise_for_status()
            result = resp.json()

            for page in result.get("results", []):
                sync_key = self._extract_sync_key(page)
                if sync_key:
                    self._existing_entries[sync_key] = page["id"]

            has_more = result.get("has_more", False)
            start_cursor = result.get("next_cursor")

        logger.info("Loaded %d existing entries from Notion", len(self._existing_entries))

    def sync_activity(
        self, activity: MoodleActivity, urgent_hours: int = 24, soon_hours: int = 72
    ) -> SyncStatus:
        """
        Sync a single Moodle activity to Notion.

        Creates a new entry or updates an existing one based on the unique key.

        Args:
            activity: The MoodleActivity to sync.
            urgent_hours: Hours threshold for urgent priority.
            soon_hours: Hours threshold for soon priority.

        Returns:
            SyncStatus indicating what happened.
        """
        priority = activity.calculate_priority(urgent_hours, soon_hours)
        properties = self._build_properties(activity, priority)

        existing_page_id = self._existing_entries.get(activity.unique_key)

        try:
            if existing_page_id:
                # Update existing entry
                if self._needs_update(existing_page_id, activity, priority):
                    self.client.pages.update(
                        page_id=existing_page_id,
                        properties=properties,
                    )
                    logger.debug("🔄 Updated: %s", activity.title)
                    return SyncStatus.UPDATED
                else:
                    logger.debug("⏭️  Unchanged: %s", activity.title)
                    return SyncStatus.UNCHANGED
            else:
                # Create new entry
                self.client.pages.create(
                    parent={"database_id": self.database_id},
                    properties=properties,
                )
                logger.debug("✅ Created: %s", activity.title)
                return SyncStatus.CREATED

        except APIResponseError as e:
            logger.error("❌ Failed to sync '%s': %s", activity.title, e)
            return SyncStatus.FAILED

    def _build_properties(self, activity: MoodleActivity, priority: Priority) -> dict:
        """Build the Notion properties dict for a page create/update."""
        now_iso = datetime.now().astimezone().isoformat()

        properties = {
            self._title_prop: {
                "title": [{"text": {"content": activity.title}}]
            },
            "Course": {
                "select": {"name": activity.course_name}
            },
            "Type": {
                "select": {"name": activity.activity_type.value}
            },
            "Priority": {
                "select": {"name": priority.value}
            },
            "Synced At": {
                "date": {"start": now_iso}
            },
            "Sync Key": {
                "rich_text": [{"text": {"content": activity.unique_key}}]
            },
        }

        # Add due date if available
        if activity.due_date:
            properties["Due Date"] = {
                "date": {"start": activity.due_date.isoformat()}
            }

        # Add Moodle link if available
        if activity.moodle_url:
            properties["Moodle Link"] = {"url": activity.moodle_url}

        # Add description if available
        if activity.description:
            properties["Description"] = {
                "rich_text": [{"text": {"content": activity.description[:2000]}}]
            }

        return properties

    def _needs_update(
        self, page_id: str, activity: MoodleActivity, new_priority: Priority
    ) -> bool:
        """
        Check if an existing Notion page needs to be updated.
        Compares due date and priority to determine if changes are needed.
        """
        try:
            page = self.client.pages.retrieve(page_id=page_id)
            props = page.get("properties", {})

            # Check if due date changed
            existing_due = props.get("Due Date", {}).get("date")
            if existing_due:
                existing_start = existing_due.get("start", "")
                new_start = activity.due_date.isoformat() if activity.due_date else ""
                if existing_start != new_start:
                    return True

            # Check if priority changed (recalculated based on current time)
            existing_priority = props.get("Priority", {}).get("select")
            if existing_priority:
                if existing_priority.get("name") != new_priority.value:
                    return True

            return False
        except Exception:
            # If we can't check, update to be safe
            return True

    def _extract_sync_key(self, page: dict) -> Optional[str]:
        """Extract the sync key from a Notion page."""
        sync_key_prop = page.get("properties", {}).get("Sync Key", {})
        rich_text = sync_key_prop.get("rich_text", [])
        if rich_text:
            return rich_text[0].get("plain_text", "")
        return None

    def cleanup_orphans(self, active_keys: set[str]) -> int:
        """
        Delete pages from Notion whose sync keys are no longer active
        (e.g., items from old courses).
        """
        removed = 0
        has_more = True
        start_cursor = None

        while has_more:
            body = {"page_size": 100}
            if start_cursor:
                body["start_cursor"] = start_cursor

            resp = self._http.post(
                f"/v1/databases/{self.database_id}/query",
                json=body,
            )
            resp.raise_for_status()
            result = resp.json()

            for page in result.get("results", []):
                sync_key = self._extract_sync_key(page)
                if sync_key and sync_key not in active_keys:
                    page_id = page["id"]
                    self.client.pages.update(
                        page_id=page_id,
                        archived=True,
                    )
                    removed += 1
                    logger.info("🗑️  Archived orphan: %s", sync_key)

            has_more = result.get("has_more", False)
            start_cursor = result.get("next_cursor")

        if removed:
            logger.info("Cleaned up %d orphan pages", removed)
        return removed

    # ─────────────────────────────────────────────
    # Testing
    # ─────────────────────────────────────────────

    def test_connection(self) -> dict:
        """
        Test the Notion connection and return database info.
        Useful for verifying setup before running a full sync.
        """
        try:
            info = self.validate_database()
            return {
                "success": True,
                "database_title": info["title"],
                "properties": info["properties"],
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
            }
