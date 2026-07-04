#!/usr/bin/env python3
"""
UoPeople → Notion Sync Tool

Fetches assignments, quizzes, forums, and calendar events from
UoPeople's Moodle LMS and syncs them to a Notion database.

Usage:
    python sync.py                  # Run full sync
    python sync.py --dry-run        # Fetch data but don't write to Notion
    python sync.py --test-moodle    # Test Moodle connection only
    python sync.py --test-notion    # Test Notion connection only
    python sync.py --help           # Show help
"""

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from colorama import Fore, Style, init as colorama_init
from dotenv import load_dotenv

from brightspace_client import BrightspaceClient, BrightspaceAuthError, BrightspaceError
from notion_client_wrapper import NotionClientWrapper, NotionSyncError
from models import MoodleActivity, SyncResult, SyncStatus

# ─────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────

# Look for config.env in the script's directory
SCRIPT_DIR = Path(__file__).parent.resolve()
CONFIG_FILE = SCRIPT_DIR / "config.env"

# Log directory
LOG_DIR = Path.home() / ".uopeople-sync"
LOG_FILE = LOG_DIR / "sync.log"


def setup_logging(verbose: bool = False):
    """Configure logging to both console and file."""
    LOG_DIR.mkdir(exist_ok=True)

    # File handler (always detailed)
    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    console_handler.setFormatter(logging.Formatter("%(message)s"))

    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)


def load_config() -> dict:
    """Load configuration from config.env file or environment variables."""
    # Check if env vars are already set (GitHub Actions, etc.)
    if os.getenv("MOODLE_USERNAME") and os.getenv("MOODLE_PASSWORD"):
        logger = logging.getLogger(__name__)
        logger.info("Using environment variables for configuration")
    else:
        # Fall back to config.env file
        if not CONFIG_FILE.exists():
            print(f"{Fore.RED}❌ Config file not found: {CONFIG_FILE}{Style.RESET_ALL}")
            print(f"   Copy config.env.example to config.env and fill in your credentials.")
            print(f"   {Fore.CYAN}cp config.env.example config.env{Style.RESET_ALL}")
            sys.exit(1)
        load_dotenv(CONFIG_FILE)

    required = ["MOODLE_USERNAME", "MOODLE_PASSWORD", "NOTION_TOKEN", "NOTION_DATABASE_ID"]
    config = {}
    missing = []

    for key in required:
        value = os.getenv(key, "").strip()
        if not value or value.startswith("your_"):
            missing.append(key)
        config[key] = value

    if missing:
        print(f"{Fore.RED}❌ Missing or unconfigured values in config.env:{Style.RESET_ALL}")
        for key in missing:
            print(f"   • {key}")
        sys.exit(1)

    # Optional values with defaults
    config["LEARN_URL"] = os.getenv("LEARN_URL", "https://learn.uopeople.edu").strip()
    config["TIMEZONE"] = os.getenv("TIMEZONE", "Asia/Dhaka").strip()
    config["URGENT_HOURS"] = int(os.getenv("URGENT_HOURS", "24"))
    config["SOON_HOURS"] = int(os.getenv("SOON_HOURS", "72"))

    return config


# ─────────────────────────────────────────────
# Display Helpers
# ─────────────────────────────────────────────

def print_banner():
    """Print the tool banner."""
    banner = f"""
{Fore.CYAN}╔══════════════════════════════════════════════════╗
║     📚 UoPeople → Notion Sync Tool               ║
║     Never miss an assignment again!               ║
╚══════════════════════════════════════════════════╝{Style.RESET_ALL}
"""
    print(banner)


def print_activity_table(activities: list[MoodleActivity], config: dict):
    """Print a formatted table of fetched activities."""
    if not activities:
        print(f"  {Fore.YELLOW}No activities found.{Style.RESET_ALL}")
        return

    print(f"\n  {'#':<4} {'Type':<12} {'Course':<30} {'Title':<35} {'Due Date':<20}")
    print(f"  {'─'*4} {'─'*12} {'─'*30} {'─'*35} {'─'*20}")

    for i, act in enumerate(activities, 1):
        priority = act.calculate_priority(config["URGENT_HOURS"], config["SOON_HOURS"])

        # Color based on priority
        if priority.name == "OVERDUE":
            color = Fore.RED + Style.BRIGHT
        elif priority.name == "URGENT":
            color = Fore.RED
        elif priority.name == "SOON":
            color = Fore.YELLOW
        else:
            color = Fore.GREEN

        due_str = act.due_date.strftime("%Y-%m-%d %H:%M") if act.due_date else "No date"
        course_short = act.course_name[:28] + ".." if len(act.course_name) > 30 else act.course_name
        title_short = act.title[:33] + ".." if len(act.title) > 35 else act.title

        print(
            f"  {color}{i:<4} {act.activity_type.value:<12} "
            f"{course_short:<30} {title_short:<35} {due_str:<20}{Style.RESET_ALL}"
        )


def print_sync_summary(result: SyncResult):
    """Print the sync result summary."""
    print(f"\n{Fore.CYAN}{'─' * 50}{Style.RESET_ALL}")
    print(f"  📊 Sync Summary: {result.summary()}")
    print(f"  📝 Total items processed: {result.total}")

    if result.errors:
        print(f"\n  {Fore.RED}Errors:{Style.RESET_ALL}")
        for error in result.errors[:5]:  # Show max 5 errors
            print(f"    • {error}")
        if len(result.errors) > 5:
            print(f"    ... and {len(result.errors) - 5} more")

    print(f"{Fore.CYAN}{'─' * 50}{Style.RESET_ALL}\n")


# ─────────────────────────────────────────────
# Core Sync Logic
# ─────────────────────────────────────────────

def fetch_all_activities(client: BrightspaceClient, config: dict) -> list[MoodleActivity]:
    """Fetch all activities from Brightspace."""
    logger = logging.getLogger(__name__)

    # Step 1: Get enrolled courses
    print(f"\n  {Fore.CYAN}📖 Fetching enrolled courses...{Style.RESET_ALL}")
    courses = client.get_enrolled_courses()
    if not courses:
        print(f"  {Fore.YELLOW}⚠️  No courses found. Are you currently enrolled?{Style.RESET_ALL}")
        return []

    print(f"  Found {len(courses)} course(s):")
    for c in courses:
        print(f"    • {c['fullname']}")

    course_ids = [c["id"] for c in courses]

    # Step 2: Fetch assignments (dropbox)
    print(f"\n  {Fore.CYAN}📝 Fetching assignments...{Style.RESET_ALL}")
    assignments = client.get_assignments(course_ids)
    print(f"  Found {len(assignments)} assignment(s)")

    # Step 3: Fetch quizzes
    print(f"\n  {Fore.CYAN}📝 Fetching quizzes...{Style.RESET_ALL}")
    quizzes = client.get_quizzes(course_ids)
    print(f"  Found {len(quizzes)} quiz(es)")

    # Step 4: Fetch forums (discussions)
    print(f"\n  {Fore.CYAN}💬 Fetching forums...{Style.RESET_ALL}")
    forums = client.get_forums(course_ids)
    if forums:
        print(f"  Found {len(forums)} forum(s)")
    else:
        print(f"  No discussion forums found")

    # Merge and deduplicate
    all_activities = []
    seen_keys = set()

    for activity in assignments + quizzes + forums:
        if activity.unique_key not in seen_keys:
            seen_keys.add(activity.unique_key)
            all_activities.append(activity)

    # Enrich course names from the courses list
    course_map = {c["id"]: c["fullname"] for c in courses}
    for activity in all_activities:
        if activity.course_id in course_map:
            activity.course_name = course_map[activity.course_id]

    # Filter: only include activities with a due date
    # This removes orientation/resource courses that have no deadlines
    filtered = [a for a in all_activities if a.due_date is not None]

    # Sort by due date (soonest first)
    filtered.sort(key=lambda a: a.due_date or datetime.max.replace(tzinfo=None))

    print(f"  (filtered out {len(all_activities) - len(filtered)} items without due dates)")
    return filtered


def sync_to_notion(
    notion: NotionClientWrapper,
    activities: list[MoodleActivity],
    config: dict,
) -> SyncResult:
    """Sync all activities to Notion."""
    result = SyncResult()

    print(f"\n  {Fore.CYAN}🔄 Syncing to Notion...{Style.RESET_ALL}")

    # Load existing entries for duplicate detection
    notion.load_existing_entries()

    for activity in activities:
        try:
            status = notion.sync_activity(
                activity,
                urgent_hours=config["URGENT_HOURS"],
                soon_hours=config["SOON_HOURS"],
            )
            if status == SyncStatus.CREATED:
                result.created += 1
            elif status == SyncStatus.UPDATED:
                result.updated += 1
            elif status == SyncStatus.UNCHANGED:
                result.unchanged += 1
            elif status == SyncStatus.FAILED:
                result.failed += 1
                result.errors.append(f"Failed: {activity.title}")
        except Exception as e:
            result.failed += 1
            result.errors.append(f"{activity.title}: {e}")

    return result


# ─────────────────────────────────────────────
# CLI Commands
# ─────────────────────────────────────────────

def cmd_test_brightspace(config: dict):
    """Test Brightspace connection."""
    print(f"\n  {Fore.CYAN}🔌 Testing Brightspace connection...{Style.RESET_ALL}")
    client = BrightspaceClient(
        base_url=config["LEARN_URL"],
        username=config["MOODLE_USERNAME"],
        password=config["MOODLE_PASSWORD"],
    )
    result = client.test_connection()

    if result["success"]:
        print(f"  {Fore.GREEN}✅ Brightspace connection successful!{Style.RESET_ALL}")
        print(f"     Mode: {result.get('mode', 'unknown')}")
        if result.get("site_name"):
            print(f"     Site: {result['site_name']}")
        print(f"     Courses: {result.get('course_count', 0)}")
    else:
        print(f"  {Fore.RED}❌ Brightspace connection failed!{Style.RESET_ALL}")
        print(f"     Error: {result.get('error', 'Unknown error')}")
        sys.exit(1)


def cmd_test_notion(config: dict):
    """Test Notion connection."""
    print(f"\n  {Fore.CYAN}🔌 Testing Notion connection...{Style.RESET_ALL}")
    notion = NotionClientWrapper(
        token=config["NOTION_TOKEN"],
        database_id=config["NOTION_DATABASE_ID"],
    )
    result = notion.test_connection()

    if result["success"]:
        print(f"  {Fore.GREEN}✅ Notion connection successful!{Style.RESET_ALL}")
        print(f"     Database: {result.get('database_title', 'Unknown')}")
        print(f"     Properties: {', '.join(result.get('properties', []))}")
    else:
        print(f"  {Fore.RED}❌ Notion connection failed!{Style.RESET_ALL}")
        print(f"     Error: {result.get('error', 'Unknown error')}")
        sys.exit(1)


def cmd_full_sync(config: dict, dry_run: bool = False):
    """Run the full sync pipeline."""
    logger = logging.getLogger(__name__)

    # Step 1: Authenticate with Brightspace
    print(f"\n  {Fore.CYAN}🔐 Authenticating with Brightspace...{Style.RESET_ALL}")
    try:
        client = BrightspaceClient(
            base_url=config["LEARN_URL"],
            username=config["MOODLE_USERNAME"],
            password=config["MOODLE_PASSWORD"],
        )
        client.authenticate()
        print(f"  {Fore.GREEN}✅ Brightspace authenticated{Style.RESET_ALL}")
    except BrightspaceAuthError as e:
        print(f"  {Fore.RED}❌ Brightspace auth failed: {e}{Style.RESET_ALL}")
        sys.exit(1)

    # Step 2: Fetch all activities
    activities = fetch_all_activities(client, config)
    print(f"\n  📋 Total unique activities: {len(activities)}")
    print_activity_table(activities, config)

    if not activities:
        print(f"\n  {Fore.YELLOW}Nothing to sync!{Style.RESET_ALL}")
        return

    # Step 3: Sync to Notion (or show dry-run)
    if dry_run:
        print(f"\n  {Fore.YELLOW}🏃 DRY RUN — No changes will be made to Notion{Style.RESET_ALL}")
        print(f"  Would sync {len(activities)} activities to Notion database.")
        return

    try:
        notion = NotionClientWrapper(
            token=config["NOTION_TOKEN"],
            database_id=config["NOTION_DATABASE_ID"],
        )
        notion.validate_database()
        notion.ensure_database_schema()

        result = sync_to_notion(notion, activities, config)
        print_sync_summary(result)

    except NotionSyncError as e:
        print(f"  {Fore.RED}❌ Notion error: {e}{Style.RESET_ALL}")
        sys.exit(1)


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

def main():
    colorama_init(autoreset=True)

    parser = argparse.ArgumentParser(
        description="Sync UoPeople Brightspace assignments to Notion",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python sync.py                  Full sync
  python sync.py --dry-run        Preview without writing to Notion
  python sync.py --test-brightspace  Verify Brightspace credentials
  python sync.py --test-notion    Verify Notion setup
  python sync.py -v               Verbose output
        """,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch data but don't write to Notion",
    )
    parser.add_argument(
        "--test-brightspace",
        action="store_true",
        help="Test Brightspace connection only",
    )
    parser.add_argument(
        "--test-notion",
        action="store_true",
        help="Test Notion connection only",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose/debug output",
    )

    args = parser.parse_args()

    setup_logging(verbose=args.verbose)
    print_banner()

    # Load config
    config = load_config()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"  ⏰ {timestamp}")

    # Route to the right command
    if args.test_brightspace:
        cmd_test_brightspace(config)
    elif args.test_notion:
        cmd_test_notion(config)
    else:
        cmd_full_sync(config, dry_run=args.dry_run)

    print(f"  📄 Log file: {LOG_FILE}\n")


if __name__ == "__main__":
    main()
