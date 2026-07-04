"""
Brightspace (D2L) client for UoPeople's online campus.

Authenticates via form-based login, then scrapes course data:
assignments, quizzes, and discussions from Brightspace pages.
"""

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests
from bs4 import BeautifulSoup

from models import ActivityType, MoodleActivity

logger = logging.getLogger(__name__)


class BrightspaceAuthError(Exception):
    """Raised when authentication fails."""
    pass


class BrightspaceError(Exception):
    """Raised when a data fetch fails."""
    pass


class BrightspaceClient:
    """Client for interacting with UoPeople's Brightspace (D2L) LMS."""

    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (X11; Linux aarch64; rv:120.0) Gecko/20100101 Firefox/120.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        })
        self._authenticated = False
        self._tz = timezone(timedelta(hours=6))

    # ─────────────────────────────────────────────
    # Authentication
    # ─────────────────────────────────────────────

    def authenticate(self):
        """Log in to Brightspace via form-based authentication."""
        logger.info("Authenticating with Brightspace at %s...", self.base_url)

        login_data = {
            "loginPath": "/d2l/login",
            "userName": self.username,
            "password": self.password,
        }

        resp = self.session.post(
            f"{self.base_url}/d2l/lp/auth/login/login.d2l",
            data=login_data,
            timeout=30,
        )

        if resp.status_code != 200:
            raise BrightspaceAuthError(
                f"Login failed with status {resp.status_code}"
            )

        # Check we're not still on the login page
        if "/d2l/login" in resp.url or "login" in resp.url.lower():
            raise BrightspaceAuthError(
                "Invalid username or password. Check credentials in config.env"
            )

        self._authenticated = True
        logger.info("Authenticated with Brightspace (user: %s)", self.username)

    # ─────────────────────────────────────────────
    # Courses
    # ─────────────────────────────────────────────

    def get_enrolled_courses(self) -> list[dict]:
        """Fetch all courses via the internal mycourses API."""
        if not self._authenticated:
            raise BrightspaceError("Not authenticated. Call authenticate() first.")

        resp = self.session.get(
            f"{self.base_url}/d2l/le/manageCourses/api/mycourses",
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        courses = []
        for c in data.get("Courses", []):
            ou_id = c.get("OrgUnitId", "")
            courses.append({
                "id": int(ou_id) if ou_id else 0,
                "fullname": c.get("Name", "Unknown Course"),
                "code": c.get("Code", ""),
            })

        logger.info("Found %d enrolled courses", len(courses))
        return courses

    # ─────────────────────────────────────────────
    # Assignments (Dropbox)
    # ─────────────────────────────────────────────

    def get_assignments(self, course_ids: list[int]) -> list[MoodleActivity]:
        """Scrape assignments from Brightspace dropbox pages."""
        activities = []
        for cid in course_ids:
            try:
                acts = self._get_assignments_for_course(cid)
                activities.extend(acts)
            except Exception as e:
                logger.warning("Failed to fetch assignments for course %d: %s", cid, e)
        logger.info("Found %d assignments total", len(activities))
        return activities

    def _get_assignments_for_course(self, course_id: int) -> list[MoodleActivity]:
        """Scrape the dropbox folder list page for a single course."""
        url = f"{self.base_url}/d2l/lms/dropbox/user/folders_list.d2l?ou={course_id}"
        resp = self.session.get(url, timeout=30)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        activities = []

        # Extract course name from the page
        course_name = self._extract_course_name_from_page(soup, course_id)

        # Find the dropbox table
        table = soup.find("table", class_="d2l-grid")
        if not table:
            return activities

        rows = table.find_all("tr")
        for row in rows:
            th = row.find("th", class_="d_ich")
            if not th:
                continue

            # Assignment name
            link = th.find("a", class_="d2l-link")
            if not link:
                continue
            name = link.get_text(strip=True)
            href = link.get("href", "")

            # Extract folder ID (db=)
            db_match = re.search(r"db=(\d+)", href)
            folder_id = int(db_match.group(1)) if db_match else 0

            # Due date
            due_date = None
            dates_label = th.find("label")
            if dates_label and dates_label.find("strong"):
                due_text = dates_label.find("strong").get_text(strip=True)
                due_text = due_text.replace("Due on ", "").replace("Due ", "")
                due_date = self._parse_brightspace_date(due_text)

            # Status
            status_cells = row.find_all("td", class_="d_gt")
            is_submitted = False
            if status_cells:
                status_text = status_cells[0].get_text(strip=True).lower()
                is_submitted = "submitted" in status_text

            # Build submit URL
            submit_url = f"{self.base_url}{href}" if href.startswith("/") else href

            act = MoodleActivity(
                moodle_id=folder_id,
                course_id=course_id,
                unique_key=f"{course_id}_assignment_{folder_id}",
                title=name,
                course_name=course_name,
                activity_type=ActivityType.ASSIGNMENT,
                due_date=due_date,
                moodle_url=submit_url,
                is_submitted=is_submitted,
            )
            activities.append(act)

        logger.info("Course %d: found %d assignments", course_id, len(activities))
        return activities

    # ─────────────────────────────────────────────
    # Quizzes
    # ─────────────────────────────────────────────

    def get_quizzes(self, course_ids: list[int]) -> list[MoodleActivity]:
        """Scrape quizzes from Brightspace quiz list pages."""
        activities = []
        for cid in course_ids:
            try:
                acts = self._get_quizzes_for_course(cid)
                activities.extend(acts)
            except Exception as e:
                logger.warning("Failed to fetch quizzes for course %d: %s", cid, e)
        logger.info("Found %d quizzes total", len(activities))
        return activities

    def _get_quizzes_for_course(self, course_id: int) -> list[MoodleActivity]:
        """Scrape the quiz list page for a single course."""
        url = f"{self.base_url}/d2l/lms/quizzing/user/quizzes_list.d2l?ou={course_id}"
        resp = self.session.get(url, timeout=30)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        activities = []

        course_name = self._extract_course_name_from_page(soup, course_id)

        table = soup.find("table", class_="d2l-grid")
        if not table:
            return activities

        # Find all quiz links directly
        for link in table.find_all("a", onclick=re.compile(r"GoToQuiz")):
            name = link.get_text(strip=True)
            if not name:
                continue

            # Extract quiz ID from onclick
            onclick = link.get("onclick", "")
            qi_match = re.search(r"GoToQuiz\((\d+)", onclick)
            quiz_id = int(qi_match.group(1)) if qi_match else 0

            # Walk up to find the row
            row = link.find_parent("tr")
            if not row:
                continue

            # Due date from the span
            date_span = link.find_next("span")
            due_date = None
            if date_span:
                date_text = date_span.get_text(strip=True)
                due_match = re.search(r"Due (?:on\s+)?(.+?)(?:\s*Available|$)", date_text)
                if due_match:
                    due_str = due_match.group(1).strip()
                    due_date = self._parse_brightspace_date(due_str)

            # Check status from the row
            is_submitted = False
            effort_link = row.find("a", href=re.compile(r"quiz_submissions"))
            if effort_link:
                status_text = effort_link.get_text(strip=True).lower()
                if "attempt" in status_text:
                    is_submitted = True

            q_url = f"{self.base_url}/d2l/lms/quizzing/user/quiz_summary.d2l?qi={quiz_id}"
            act = MoodleActivity(
                moodle_id=quiz_id,
                course_id=course_id,
                unique_key=f"{course_id}_quiz_{quiz_id}",
                title=name,
                course_name=course_name,
                activity_type=ActivityType.QUIZ,
                due_date=due_date,
                moodle_url=q_url,
                is_submitted=is_submitted,
            )
            activities.append(act)

        logger.info("Course %d: found %d quizzes", course_id, len(activities))
        return activities

    # ─────────────────────────────────────────────
    # Forums / Discussions
    # ─────────────────────────────────────────────

    def get_forums(self, course_ids: list[int]) -> list[MoodleActivity]:
        """Try to scrape discussions/forums. Many courses won't have them."""
        activities = []
        for cid in course_ids:
            try:
                acts = self._get_forums_for_course(cid)
                activities.extend(acts)
            except Exception:
                pass
        return activities

    def _get_forums_for_course(self, course_id: int) -> list[MoodleActivity]:
        """Try to fetch discussions for a course."""
        url = f"{self.base_url}/d2l/lms/discussions/forum/list.d2l?ou={course_id}"
        resp = self.session.get(url, timeout=30)

        if resp.status_code != 200:
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        activities = []
        course_name = self._extract_course_name_from_page(soup, course_id)

        for link in soup.find_all("a", href=re.compile(r"forumId=")):
            name = link.get_text(strip=True)
            if not name:
                continue
            href = link.get("href", "")
            forum_match = re.search(r"forumId=(\d+)", href)
            forum_id = int(forum_match.group(1)) if forum_match else 0

            act = MoodleActivity(
                moodle_id=forum_id,
                course_id=course_id,
                unique_key=f"{course_id}_forum_{forum_id}",
                title=name,
                course_name=course_name,
                activity_type=ActivityType.FORUM,
                moodle_url=f"{self.base_url}/d2l/lms/discussions/forum/list.d2l?ou={course_id}&forumId={forum_id}",
            )
            activities.append(act)

        return activities

    # ─────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────

    @staticmethod
    def _parse_brightspace_date(date_str: str) -> Optional[datetime]:
        """Parse Brightspace date strings like 'Jun 25, 2026 10:55 AM'."""
        if not date_str:
            return None
        date_str = date_str.strip()
        for fmt in [
            "%b %d, %Y %I:%M %p",
            "%b %d, %Y",
            "%Y-%m-%d",
        ]:
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue
        logger.debug("Could not parse date: '%s'", date_str)
        return None

    @staticmethod
    def _extract_course_name_from_page(soup: BeautifulSoup, fallback_id: int) -> str:
        """Extract course name from the page title or header."""
        title_tag = soup.find("title")
        if title_tag:
            title = title_tag.get_text(strip=True)
            # Title format: "Assignments - COURSE NAME"
            parts = title.split(" - ", 1)
            if len(parts) > 1:
                return parts[1]
        return f"Course {fallback_id}"

    # ─────────────────────────────────────────────
    # Testing
    # ─────────────────────────────────────────────

    def test_connection(self) -> dict:
        """Test the Brightspace connection."""
        try:
            self.authenticate()
            courses = self.get_enrolled_courses()
            return {
                "success": True,
                "mode": "brightspace",
                "site_name": "UoPeople Brightspace",
                "username": self.username,
                "course_count": len(courses),
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
            }
