# 📚 Setup Guide — UoPeople → Notion Sync

Follow these steps to set up the sync tool. Total setup time: ~10 minutes.

---

## Step 1: Install Python Dependencies

Make sure you have Python 3.8+ installed, then run:

```bash
cd uopeople-notion-sync
pip install -r requirements.txt
```

---

## Step 2: Set Up Notion Integration (5 minutes)

### 2.1 Create a Notion Integration

1. Go to [Notion Integrations](https://www.notion.so/profile/integrations/internal)
2. Click **+ New integration**
3. Fill in:
   - **Name**: `UoPeople Sync`
   - **Workspace**: Select your workspace
   - **Capabilities**: Check "Read content", "Insert content", "Update content"
4. Click **Submit**
5. **Copy the Internal Integration Token** — you'll need this for `config.env`

### 2.2 Create the Notion Database

1. Open Notion and go to the page where you want the database
2. Type `/database` and select **Database - Full page**
3. Name it: `📚 UoPeople Assignments`
4. The sync tool will **automatically create all required columns** on first run, so you don't need to set them up manually!

### 2.3 Connect the Integration to the Database

> ⚠️ **This step is critical!** Without it, the API will return 404 errors.

1. Open your new database page in Notion
2. Click the **•••** (three dots) menu in the top-right corner
3. Scroll down to **Connections**
4. Click **Connect to** → find `UoPeople Sync` → click it
5. Confirm the connection

### 2.4 Copy the Database ID

1. Open your database in Notion (as a full page)
2. Look at the URL in your browser:
   ```
   https://www.notion.so/your-workspace/19f00145217c4437afb06cfdbb2ad994?v=...
                                        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                                        This is your Database ID
   ```
3. Copy the 32-character ID (the part between the last `/` and the `?`)

---

## Step 3: Configure Credentials

1. Copy the example config file:
   ```bash
   cp config.env.example config.env
   ```

2. Edit `config.env` with your actual values:
   ```bash
   nano config.env
   ```

3. Fill in:
   ```env
   MOODLE_USERNAME=your_actual_uopeople_username
   MOODLE_PASSWORD=your_actual_uopeople_password
   MOODLE_URL=https://my.uopeople.edu

   NOTION_TOKEN=secret_abc123...     # From Step 2.1
   NOTION_DATABASE_ID=19f00145...    # From Step 2.4

   TIMEZONE=Asia/Dhaka
   URGENT_HOURS=24
   SOON_HOURS=72
   ```

> 🔒 **Your credentials stay on your machine.** The `config.env` file is in `.gitignore` and is never uploaded anywhere.

---

## Step 4: Test the Setup

### Test Moodle Connection
```bash
python3 sync.py --test-moodle
```
You should see: `✅ Moodle connection successful!`

### Test Notion Connection
```bash
python3 sync.py --test-notion
```
You should see: `✅ Notion connection successful!`

### Dry Run (Preview)
```bash
python3 sync.py --dry-run
```
This fetches all your assignments but **doesn't write to Notion** — just shows what would be synced.

---

## Step 5: Run Your First Sync

```bash
python3 sync.py
```

Check your Notion database — you should see all your assignments with:
- ✅ Course names
- ✅ Activity types (Assignment, Quiz, Forum, Journal)
- ✅ Due dates
- ✅ Priority levels (🔴 Urgent, 🟡 Soon, 🟢 Upcoming)
- ✅ Direct Moodle links

---

## Step 6: Set Up Daily Auto-Sync

Run the cron setup script:

```bash
bash cron_setup.sh
```

This schedules the sync to run **every day at 7:00 AM**. To change the time:

```bash
bash cron_setup.sh 8 30    # Run at 8:30 AM
bash cron_setup.sh 22 0    # Run at 10:00 PM
```

---

## Step 7: Set Up Notion Reminders (2 minutes)

Since the API can't set Notion reminders directly, set up a **database automation**:

1. Open your `📚 UoPeople Assignments` database in Notion
2. Click the **⚡ lightning bolt** icon at the top-right of the database (or click **•••** → **Automations**)
3. Click **+ New automation**
4. Set the **trigger**:
   - "When" → **Due Date** → **is** → **1 day before**
5. Set the **action**:
   - "Then" → **Send notification to** → **yourself**
6. Click **Create**

> 💡 You can also add a second automation for "Due Date is today" for a same-day reminder.

Now you'll get Notion notifications when deadlines are approaching! 🎉

---

## Troubleshooting

### "Invalid login" error
- Double-check your UoPeople username and password in `config.env`
- Make sure you can log in at [my.uopeople.edu](https://my.uopeople.edu) with those credentials

### "Database not found" error
- Make sure you connected the integration to the database (Step 2.3)
- Double-check the database ID in `config.env`

### "API error: webservice not available"
- UoPeople may have restricted API access. The tool will automatically fall back to web scraping mode.

### No assignments showing up
- Make sure you're currently enrolled in courses
- Try `python3 sync.py -v` for verbose output to see what's happening

### Cron job not running
- Check if cron is installed: `which crontab`
- View your cron jobs: `crontab -l`
- Check the cron log: `tail -20 ~/.uopeople-sync/cron.log`
