"""
Moodle API client for UoPeople's online campus.

Authenticates via username/password to get a web service token, then uses
Moodle's REST API to fetch courses, assignments, calendar events, and forums.

Falls back to web scraping if the API is restricted.
"""

import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from models import ActivityType, MoodleActivity

logger = logging.getLogger(__name__)


class MoodleAuthError(Exception):
    """Raised when Moodle authentication fails."""
    pass


class MoodleAPIError(Exception):
    """Raised when a Moodle API call fails."""
    pass


class MoodleClient:
    """Client for interacting with UoPeople's Moodle LMS."""

    # Moodle mobile web service name (used by Open LMS app)
    SERVICE_NAME = "moodle_mobile_app"

    def __init__(self, base_url: str, username: str, password: str, tz_offset_hours: int = 6):
        """
        Initialize the Moodle client.

        Args:
            base_url: The Moodle site URL (e.g., https://my.uopeople.edu)
            username: UoPeople username
            password: UoPeople password
            tz_offset_hours: Timezone offset from UTC (default: 6 for Asia/Dhaka)
        """
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.tz = timezone(timedelta(hours=tz_offset_hours))
        self.token: Optional[str] = None
        self.user_id: Optional[int] = None
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "MoodleMobile 4.3.0 (UoPeople Sync Tool)"
        })
        self._api_available = True

    # ─────────────────────────────────────────────
    # Authentication
    # ─────────────────────────────────────────────

    def authenticate(self) -> str:
        """
        Authenticate with Moodle and get a web service token.
        Falls back to session-based login if the API token endpoint fails.

        Returns:
            The web service token string.

        Raises:
            MoodleAuthError: If both authentication methods fail.
        """
        logger.info("Authenticating with Moodle at %s...", self.base_url)

        # Try API token first
        try:
            self.token = self._get_api_token()
            self.user_id = self._get_user_id()
            logger.info("✅ Authenticated via API (user ID: %s)", self.user_id)
            return self.token
        except MoodleAuthError:
            raise
        except Exception as e:
            logger.warning("API token auth failed (%s), trying session login...", e)
            self._api_available = False

        # Fallback: session-based login
        try:
            self._session_login()
            logger.info("✅ Authenticated via session login")
            return "session"
        except Exception as e:
            raise MoodleAuthError(f"All authentication methods failed: {e}")

    def _get_api_token(self) -> str:
        """Get a web service token via the token endpoint."""
        url = f"{self.base_url}/login/token.php"
        data = {
            "username": self.username,
            "password": self.password,
            "service": self.SERVICE_NAME,
        }

        resp = self.session.post(url, data=data, timeout=30)
        resp.raise_for_status()
        result = resp.json()

        if "token" in result:
            return result["token"]
        elif "error" in result:
            error_msg = result.get("error", "Unknown error")
            if "invalidlogin" in error_msg.lower() or "invalid login" in error_msg.lower():
                raise MoodleAuthError(
                    "Invalid username or password. Please check your credentials in config.env"
                )
            raise MoodleAPIError(f"Token error: {error_msg}")
        else:
            raise MoodleAPIError(f"Unexpected response: {result}")

    def _get_user_id(self) -> int:
        """Get the current user's Moodle ID."""
        result = self._api_call("core_webservice_get_site_info")
        return result.get("userid", 0)

    def _session_login(self):
        """Login via browser session (fallback method)."""
        # Get the login page to extract the login token
        login_url = f"{self.base_url}/login/index.php"
        resp = self.session.get(login_url, timeout=30)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        login_token_input = soup.find("input", {"name": "logintoken"})
        login_token = login_token_input["value"] if login_token_input else ""

        # Submit login form
        data = {
            "username": self.username,
            "password": self.password,
            "logintoken": login_token,
            "anchor": "",
        }
        resp = self.session.post(login_url, data=data, timeout=30, allow_redirects=True)
        resp.raise_for_status()

        # Check if login was successful by looking for the logout link
        if "logout" not in resp.text.lower() and "loggedin" not in resp.text.lower():
            raise MoodleAuthError(
                "Session login failed. Check your credentials in config.env"
            )

    # ─────────────────────────────────────────────
    # API Calls
    # ─────────────────────────────────────────────

    def _api_call(self, function: str, **params) -> dict:
        """
        Make a Moodle REST API call.

        Args:
            function: The Moodle web service function name
            **params: Additional parameters for the function

        Returns:
            The JSON response as a dictionary.
        """
        if not self.token:
            raise MoodleAPIError("Not authenticated. Call authenticate() first.")

        url = f"{self.base_url}/webservice/rest/server.php"
        data = {
            "wstoken": self.token,
            "wsfunction": function,
            "moodlewsrestformat": "json",
            **params,
        }

        resp = self.session.post(url, data=data, timeout=30)
        resp.raise_for_status()
        result = resp.json()

        # Check for API-level errors
        if isinstance(result, dict) and "exception" in result:
            error_msg = result.get("message", "Unknown API error")
            error_code = result.get("errorcode", "")
            raise MoodleAPIError(f"API error [{error_code}]: {error_msg}")

        return result

    # ─────────────────────────────────────────────
    # Data Fetching — API Mode
    # ─────────────────────────────────────────────

    def get_enrolled_courses(self) -> list[dict]:
        """Fetch all courses the user is enrolled in."""
        if self._api_available:
            return self._get_courses_api()
        else:
            return self._get_courses_scrape()

    def _get_courses_api(self) -> list[dict]:
        """Get courses via the API."""
        result = self._api_call(
            "core_enrol_get_users_courses",
            userid=self.user_id,
        )
        # Filter to only active/visible courses
        courses = []
        for course in result:
            if course.get("visible", 1) == 1:
                courses.append({
                    "id": course["id"],
                    "fullname": course.get("fullname", "Unknown Course"),
                    "shortname": course.get("shortname", ""),
                })
        logger.info("Found %d enrolled courses", len(courses))
        return courses

    def get_assignments(self, course_ids: list[int]) -> list[MoodleActivity]:
        """Fetch assignments for the given course IDs."""
        if self._api_available:
            return self._get_assignments_api(course_ids)
        else:
            return self._get_assignments_scrape(course_ids)

    def _get_assignments_api(self, course_ids: list[int]) -> list[MoodleActivity]:
        """Get assignments via the API."""
        # Build the courseids[] parameters
        params = {}
        for i, cid in enumerate(course_ids):
            params[f"courseids[{i}]"] = cid

        result = self._api_call("mod_assign_get_assignments", **params)
        activities = []

        for course_data in result.get("courses", []):
            course_id = course_data.get("id", 0)
            course_name = course_data.get("fullname", "Unknown Course")

            for assign in course_data.get("assignments", []):
                due_ts = assign.get("duedate", 0)
                due_date = (
                    datetime.fromtimestamp(due_ts, tz=self.tz)
                    if due_ts > 0
                    else None
                )

                # Clean HTML from description
                desc = self._strip_html(assign.get("intro", ""))

                activity = MoodleActivity(
                    moodle_id=assign.get("id", 0),
                    course_id=course_id,
                    unique_key=f"{course_id}_assign_{assign.get('id', 0)}",
                    title=assign.get("name", "Untitled Assignment"),
                    course_name=course_name,
                    activity_type=MoodleActivity.detect_activity_type(
                        assign.get("name", ""), "assign"
                    ),
                    description=desc[:500],  # Truncate long descriptions
                    due_date=due_date,
                    moodle_url=f"{self.base_url}/mod/assign/view.php?id={assign.get('cmid', assign.get('id', 0))}",
                )
                activities.append(activity)

        logger.info("Found %d assignments via API", len(activities))
        return activities

    def get_calendar_events(self, course_ids: list[int], days_ahead: int = 30) -> list[MoodleActivity]:
        """Fetch upcoming calendar events (quizzes, forums, etc.)."""
        if self._api_available:
            return self._get_calendar_events_api(course_ids, days_ahead)
        else:
            return self._get_calendar_events_scrape(days_ahead)

    def _get_calendar_events_api(
        self, course_ids: list[int], days_ahead: int = 30
    ) -> list[MoodleActivity]:
        """Get calendar events via the API."""
        now = datetime.now(self.tz)
        time_start = int(now.timestamp())
        time_end = int((now + timedelta(days=days_ahead)).timestamp())

        params = {
            "options[timestart]": time_start,
            "options[timeend]": time_end,
            "options[userevents]": 1,
            "options[siteevents]": 1,
        }
        # Add course IDs to the events filter
        for i, cid in enumerate(course_ids):
            params[f"events[courseids][{i}]"] = cid

        result = self._api_call("core_calendar_get_calendar_events", **params)
        activities = []
        seen_keys = set()

        for event in result.get("events", []):
            event_id = event.get("id", 0)
            course_id = event.get("courseid", 0)
            mod_name = event.get("modulename", "")
            name = event.get("name", "Untitled Event")

            # Skip duplicates (assignments already fetched separately)
            unique_key = f"{course_id}_{mod_name}_{event.get('instance', event_id)}"
            if unique_key in seen_keys:
                continue
            seen_keys.add(unique_key)

            due_ts = event.get("timestart", 0)
            due_date = (
                datetime.fromtimestamp(due_ts, tz=self.tz)
                if due_ts > 0
                else None
            )

            desc = self._strip_html(event.get("description", ""))
            course_name = event.get("coursename", "") or f"Course {course_id}"

            activity = MoodleActivity(
                moodle_id=event_id,
                course_id=course_id,
                unique_key=unique_key,
                title=name,
                course_name=course_name,
                activity_type=MoodleActivity.detect_activity_type(name, mod_name),
                description=desc[:500],
                due_date=due_date,
                moodle_url=(
                    f"{self.base_url}/mod/{mod_name}/view.php?id={event.get('cmid', event_id)}"
                    if mod_name
                    else f"{self.base_url}/calendar/view.php?id={event_id}"
                ),
            )
            activities.append(activity)

        logger.info("Found %d calendar events via API", len(activities))
        return activities

    def get_forums(self, course_ids: list[int]) -> list[MoodleActivity]:
        """Fetch forum activities for courses."""
        if not self._api_available:
            return []  # Forums are captured via calendar events in scrape mode

        activities = []
        params = {}
        for i, cid in enumerate(course_ids):
            params[f"courseids[{i}]"] = cid

        try:
            result = self._api_call("mod_forum_get_forums_by_courses", **params)
            for forum in result:
                course_id = forum.get("course", 0)
                activity = MoodleActivity(
                    moodle_id=forum.get("id", 0),
                    course_id=course_id,
                    unique_key=f"{course_id}_forum_{forum.get('id', 0)}",
                    title=forum.get("name", "Discussion Forum"),
                    course_name=f"Course {course_id}",
                    activity_type=ActivityType.FORUM,
                    description=self._strip_html(forum.get("intro", ""))[:500],
                    moodle_url=f"{self.base_url}/mod/forum/view.php?id={forum.get('cmid', forum.get('id', 0))}",
                )
                activities.append(activity)
            logger.info("Found %d forums via API", len(activities))
        except MoodleAPIError as e:
            logger.warning("Could not fetch forums: %s", e)

        return activities

    # ─────────────────────────────────────────────
    # Data Fetching — Scraping Mode (Fallback)
    # ─────────────────────────────────────────────

    def _get_courses_scrape(self) -> list[dict]:
        """Get courses by scraping the Moodle dashboard."""
        url = f"{self.base_url}/my/"
        resp = self.session.get(url, timeout=30)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        courses = []

        # Look for course links in the dashboard
        for link in soup.find_all("a", href=re.compile(r"/course/view\.php\?id=\d+")):
            href = link.get("href", "")
            match = re.search(r"id=(\d+)", href)
            if match:
                course_id = int(match.group(1))
                course_name = link.get_text(strip=True)
                if course_name and course_id not in [c["id"] for c in courses]:
                    courses.append({
                        "id": course_id,
                        "fullname": course_name,
                        "shortname": "",
                    })

        logger.info("Found %d courses via scraping", len(courses))
        return courses

    def _get_assignments_scrape(self, course_ids: list[int]) -> list[MoodleActivity]:
        """Get assignments by scraping course pages."""
        activities = []
        for course_id in course_ids:
            try:
                url = f"{self.base_url}/course/view.php?id={course_id}"
                resp = self.session.get(url, timeout=30)
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, "html.parser")

                # Extract course name
                course_name_tag = soup.find("h1")
                course_name = course_name_tag.get_text(strip=True) if course_name_tag else f"Course {course_id}"

                # Find activity links
                for link in soup.find_all("a", href=re.compile(r"/mod/(assign|quiz|forum)/view\.php")):
                    href = link.get("href", "")
                    mod_match = re.search(r"/mod/(\w+)/view\.php\?id=(\d+)", href)
                    if mod_match:
                        mod_name = mod_match.group(1)
                        mod_id = int(mod_match.group(2))
                        name = link.get_text(strip=True)
                        if name:
                            activity = MoodleActivity(
                                moodle_id=mod_id,
                                course_id=course_id,
                                unique_key=f"{course_id}_{mod_name}_{mod_id}",
                                title=name,
                                course_name=course_name,
                                activity_type=MoodleActivity.detect_activity_type(name, mod_name),
                                moodle_url=urljoin(self.base_url, href),
                            )
                            activities.append(activity)
            except Exception as e:
                logger.warning("Failed to scrape course %d: %s", course_id, e)

        logger.info("Found %d activities via scraping", len(activities))
        return activities

    def _get_calendar_events_scrape(self, days_ahead: int = 30) -> list[MoodleActivity]:
        """Get events by scraping the Moodle calendar."""
        activities = []
        try:
            url = f"{self.base_url}/calendar/view.php?view=upcoming"
            resp = self.session.get(url, timeout=30)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            for event_div in soup.find_all("div", class_="event"):
                title_tag = event_div.find("a")
                if title_tag:
                    name = title_tag.get_text(strip=True)
                    href = title_tag.get("href", "")
                    mod_match = re.search(r"/mod/(\w+)/view\.php\?id=(\d+)", href)

                    activity = MoodleActivity(
                        moodle_id=hash(name) % 100000,
                        course_id=0,
                        unique_key=f"cal_{hash(name + href) % 100000}",
                        title=name,
                        course_name="Calendar Event",
                        activity_type=MoodleActivity.detect_activity_type(
                            name, mod_match.group(1) if mod_match else ""
                        ),
                        moodle_url=urljoin(self.base_url, href) if href else "",
                    )
                    activities.append(activity)
        except Exception as e:
            logger.warning("Failed to scrape calendar: %s", e)

        logger.info("Found %d calendar events via scraping", len(activities))
        return activities

    # ─────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────

    @staticmethod
    def _strip_html(html: str) -> str:
        """Remove HTML tags and clean up whitespace."""
        if not html:
            return ""
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(separator=" ", strip=True)
        # Collapse multiple spaces
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def test_connection(self) -> dict:
        """
        Test the Moodle connection and return site info.
        Useful for verifying credentials before running a full sync.
        """
        try:
            self.authenticate()
            if self._api_available:
                info = self._api_call("core_webservice_get_site_info")
                return {
                    "success": True,
                    "mode": "api",
                    "site_name": info.get("sitename", "Unknown"),
                    "username": info.get("username", self.username),
                    "user_id": info.get("userid", 0),
                    "fullname": info.get("fullname", ""),
                }
            else:
                return {
                    "success": True,
                    "mode": "scraping",
                    "username": self.username,
                }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
            }
