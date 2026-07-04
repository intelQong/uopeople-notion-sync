# 📚 UoPeople → Notion Sync Tool

Automatically sync your UoPeople (Moodle) assignments, quizzes, discussion forums, and learning journal deadlines to a Notion database — with priority labels and daily scheduling.

## Features

- **🔄 Auto-sync** — Pulls assignments, quizzes, forums, and calendar events from Moodle
- **📊 Priority labels** — Automatically tags items as 🔴 Urgent, 🟡 Soon, or 🟢 Upcoming
- **🔗 Direct links** — Each entry includes a clickable link back to Moodle
- **🚫 No duplicates** — Smart sync key prevents duplicate entries
- **⏰ Daily schedule** — Runs automatically via cron job every morning
- **🔒 Secure** — Credentials stay on your machine, never uploaded
- **📲 Notion reminders** — Get notified when deadlines approach (via Notion automation)

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure credentials
cp config.env.example config.env
nano config.env  # Fill in your details

# 3. Test connections
python3 sync.py --test-moodle
python3 sync.py --test-notion

# 4. Run first sync
python3 sync.py

# 5. Set up daily auto-sync
bash cron_setup.sh
```

📖 **Full setup instructions**: See [setup_guide.md](setup_guide.md)

## Usage

```bash
python3 sync.py                  # Full sync
python3 sync.py --dry-run        # Preview without writing to Notion
python3 sync.py --test-moodle    # Verify Moodle credentials
python3 sync.py --test-notion    # Verify Notion setup
python3 sync.py -v               # Verbose output
```

## How It Works

```
┌─────────────────┐         ┌──────────────┐         ┌─────────────────┐
│   UoPeople      │         │              │         │     Notion      │
│   Moodle LMS    │───API──▶│   sync.py    │───API──▶│    Database     │
│                 │         │              │         │                 │
│ • Assignments   │         │ • Fetch      │         │ • Tasks         │
│ • Quizzes       │         │ • Normalize  │         │ • Due Dates     │
│ • Forums        │         │ • Dedup      │         │ • Priorities    │
│ • Calendar      │         │ • Sync       │         │ • Status        │
└─────────────────┘         └──────────────┘         └─────────────────┘
                                                            │
                                                            ▼
                                                    ┌─────────────────┐
                                                    │  📲 Notion      │
                                                    │  Notifications  │
                                                    │  (Automation)   │
                                                    └─────────────────┘
```

## Notion Database Columns

| Column | Type | Auto-filled |
|--------|------|-------------|
| Task | Title | ✅ Activity name |
| Course | Select | ✅ Course name |
| Type | Select | ✅ Assignment/Quiz/Forum/Journal |
| Due Date | Date | ✅ Deadline |
| Status | Status | Default: Not Started |
| Priority | Select | ✅ 🔴/🟡/🟢 based on deadline |
| Moodle Link | URL | ✅ Direct link |
| Description | Text | ✅ Truncated description |

## File Structure

```
uopeople-notion-sync/
├── sync.py                   # Main sync script (run this)
├── moodle_client.py          # Moodle API + scraping client
├── notion_client_wrapper.py  # Notion API wrapper
├── models.py                 # Data models
├── config.env.example        # Credential template
├── config.env                # Your credentials (gitignored)
├── requirements.txt          # Python dependencies
├── cron_setup.sh             # Cron job installer
├── setup_guide.md            # Detailed setup instructions
└── README.md                 # This file
```

## Requirements

- Python 3.8+
- A UoPeople student account
- A Notion account (free tier works)

## License

MIT — Built for UoPeople students who grind and study. 💪
