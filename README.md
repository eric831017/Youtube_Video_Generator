# AI Video Generation Telegram Bot

A single-user Telegram bot that turns text (or text + image) prompts into AI-generated videos. It optimises the prompt with GPT-4o, generates the video through fal.ai (text-to-video or image-to-video), uploads it to Google Drive, and optionally publishes it to YouTube. Monthly spending is tracked against a configurable budget.

## Features

- Text-to-video and image-to-video via 11 fal.ai models: Kling 2.6 Pro, Kling 2.1 Pro/Master, Seedance 2.0 / 2.0 Fast / 1.5 Pro, Veo 3.1 / 3.1 Fast, Wan 2.6, MiniMax Hailuo 02, PixVerse 4.5
- GPT-4o prompt optimisation with vision awareness when an image is provided
- Inline confirmation with cost estimate before every generation
- Automatic upload to a Google Drive folder with a public share link
- Optional one-tap upload to YouTube with configurable privacy and category
- Monthly budget enforcement and persistent cost log
- `/settings` command to manage every default parameter without redeploying

## Requirements

- Python 3.10+
- A Telegram bot token
- An OpenAI API key with access to `gpt-4o`
- A fal.ai API key
- A Google Cloud project with Drive API and YouTube Data API v3 enabled
- Linux host (Ubuntu recommended) if you want to run it as a `systemd` service

## 1. Clone and install

```bash
git clone https://github.com/eric831017/youtube_video_generator.git
cd youtube_video_generator

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## 2. Create the Telegram bot

1. Open Telegram and talk to [@BotFather](https://t.me/BotFather).
2. Send `/newbot`, follow the prompts, and copy the bot token.
3. Get your Telegram user ID from [@userinfobot](https://t.me/userinfobot) — this is the only user that will be allowed to talk to the bot.

## 3. Get API keys

### OpenAI
1. Go to <https://platform.openai.com/api-keys>.
2. Create a secret key and copy it.

### fal.ai
1. Go to <https://fal.ai/dashboard/keys>.
2. Create a key and copy it.

## 4. Set up Google OAuth (Drive + YouTube)

The bot uses a single OAuth client for both Drive upload and YouTube upload.

1. Open the [Google Cloud Console](https://console.cloud.google.com/) and create (or pick) a project.
2. Enable these APIs from **APIs & Services → Library**:
   - **Google Drive API**
   - **YouTube Data API v3**
3. Go to **APIs & Services → OAuth consent screen**:
   - User type: **External**
   - Fill in app name and your email
   - Add your Google account as a **Test user** (required while the app is in testing)
   - Add these scopes:
     - `https://www.googleapis.com/auth/drive.file`
     - `https://www.googleapis.com/auth/youtube.upload`
4. Go to **APIs & Services → Credentials → Create Credentials → OAuth client ID**:
   - Application type: **Desktop app**
   - Copy the generated **Client ID** and **Client Secret**

You have two ways to provide the client secret to the bot — pick one:

- **Option A (recommended):** put `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` into `.env` (see next step).
- **Option B:** download the JSON from the Credentials page and save it as `data/client_secrets.json` in the project root.

## 5. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env`:

```ini
TELEGRAM_BOT_TOKEN=123456:ABCDEF...
TELEGRAM_ALLOWED_USER_ID=123456789
OPENAI_API_KEY=sk-...
FAL_API_KEY=...
GOOGLE_CLIENT_ID=...apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-...
GOOGLE_DRIVE_FOLDER_NAME=AI Videos
```

`GOOGLE_DRIVE_FOLDER_NAME` is the folder the bot creates (or reuses) in your Drive for generated videos.

## 6. First run and OAuth authorisation

```bash
source venv/bin/activate
python main.py
```

The bot will start polling. On Telegram:

1. Send `/start` to confirm the bot replies.
2. Send `/auth`. The bot replies with a Google authorisation URL.
3. Open the URL in a browser and sign in with the Google account that owns the target Drive/YouTube. After granting access, the browser redirects to a `localhost` page that fails to load — **this is normal**.
4. Copy the full URL from the browser address bar (it looks like `http://localhost/?code=4/0Adxxxx&scope=...`), or just the `code=` value after it.
5. Back in Telegram send either:
   - `/auth http://localhost/?code=4/0Adxxxxxx&scope=...` (paste the full URL), or
   - `/auth 4/0Adxxxxxx` (paste only the code value)
6. The bot replies `✅ Google 授權完成` and stores the refresh token in `data/token.json`. You won't need to repeat this step.

## 7. Daily usage

- **Text only:** send a text message → confirm → wait for the video.
- **Image + text:** send a photo (or an image document), tap **🎬 起始幀** (image-to-video) or **🎨 風格參考** (text + image reference), then send the text prompt.
- **`/cancel`** aborts the current flow and clears any cached image.
- **`/settings`** opens an inline menu to change default model, aspect ratio, duration, resolution, monthly budget, YouTube privacy, and YouTube category.
- **`/budget`** shows how much of the monthly budget has been used.
- **`/history`** shows the last 5 generations with cost and YouTube URL (if uploaded).

Cached images auto-expire after 10 minutes; the bot will notify you and reset the flow.

## 8. Run as a systemd service (optional)

For a VPS deployment, copy the project to `/home/ubuntu/video_bot` (or edit the paths in `video_bot.service`), then:

```bash
sudo cp video_bot.service /etc/systemd/system/video_bot.service
sudo systemctl daemon-reload
sudo systemctl enable --now video_bot
sudo systemctl status video_bot
journalctl -u video_bot -f
```

The unit file expects:
- Working directory: `/home/ubuntu/video_bot`
- Virtualenv: `/home/ubuntu/video_bot/venv`
- `.env` file at `/home/ubuntu/video_bot/.env`

Adjust them if your layout differs.

## Available models

Select a model via `/settings → 修改模型`. Prices are per second of generated video (verify current pricing at [fal.ai](https://fal.ai)).

| Key | Label | t2v endpoint | i2v endpoint | $/sec | Max dur |
|---|---|---|---|---|---|
| `kling-2.6-pro` *(default)* | Kling 2.6 Pro | `fal-ai/kling-video/v2.6/pro/text-to-video` | `…/image-to-video` | $0.035 | 10 s |
| `kling-2.1-pro` | Kling 2.1 Pro | `fal-ai/kling-video/v2.1/pro/text-to-video` | `…/image-to-video` | $0.014 | 10 s |
| `kling-2.1-master` | Kling 2.1 Master | `fal-ai/kling-video/v2.1/master/text-to-video` | `…/image-to-video` | $0.028 | 10 s |
| `seedance-2.0` | Seedance 2.0 | `bytedance/seedance-2.0/text-to-video` | `…/image-to-video` | $0.062 | 10 s |
| `seedance-2.0-fast` | Seedance 2.0 Fast | `bytedance/seedance-2.0/fast/text-to-video` | `…/image-to-video` | $0.018 | 10 s |
| `seedance-1.5` | Seedance 1.5 Pro | `fal-ai/bytedance/seedance/v1.5/pro/text-to-video` | `…/image-to-video` | $0.014 | 10 s |
| `veo-3.1` | Veo 3.1 | `fal-ai/veo3.1` | `fal-ai/veo3.1/image-to-video` | $0.050 | 8 s |
| `veo-3.1-fast` | Veo 3.1 Fast | `fal-ai/veo3.1/fast` | `fal-ai/veo3.1/fast/image-to-video` | $0.025 | 8 s |
| `wan-2.6` | Wan 2.6 | `wan/v2.6/text-to-video` | `wan/v2.6/image-to-video` | $0.010 | 10 s |
| `minimax-hailuo-02` | MiniMax Hailuo 02 | `fal-ai/minimax/hailuo-02/standard/text-to-video` | `…/image-to-video` | $0.045 | 10 s |
| `pixverse-4.5` | PixVerse 4.5 | `fal-ai/pixverse/v4.5/text-to-video` | `…/image-to-video` | $0.020 | 8 s |

> **Note:** Kling 2.1 *Standard* (`fal-ai/kling-video/v2.1/standard/…`) is image-to-video only and is intentionally excluded — using it for text-to-video causes a 404.

## Project layout

```
.
├── main.py                   # bot entry point, command wiring, TTL loop
├── config.py                 # env loading, settings persistence, model table
├── handlers/
│   ├── prompt.py             # image & text intake, prompt optimisation
│   ├── generate.py           # fal.ai submission and polling
│   ├── upload.py             # Google Drive + YouTube upload
│   ├── settings.py           # /settings /budget /history /auth
│   └── common.py             # auth check, MarkdownV2 helpers
├── services/
│   ├── fal_client.py
│   ├── optimizer.py          # GPT-4o prompt optimiser
│   ├── drive_client.py
│   ├── youtube_client.py
│   └── google_auth.py
├── models/
│   ├── session.py            # user session dataclass + state machine
│   └── cost_tracker.py
├── data/
│   ├── settings.json         # auto-created on first run
│   ├── cost_tracker.json     # auto-created on first run
│   ├── token.json            # created by /auth, DO NOT commit
│   └── client_secrets.json   # optional, DO NOT commit
├── requirements.txt
├── .env.example
└── video_bot.service
```

## Troubleshooting

- **`Missing required environment variables`** — check `.env`; `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USER_ID`, `OPENAI_API_KEY`, and `FAL_API_KEY` are all mandatory.
- **`🚫 Sorry, this bot is restricted...`** — the Telegram user ID talking to the bot doesn't match `TELEGRAM_ALLOWED_USER_ID`.
- **`Google credentials missing or invalid`** — run `/auth` again; if it keeps failing, delete `data/token.json` and redo the OAuth flow.
- **`⚠️ 本月預算已達上限`** — raise the budget in `/settings` or wait for the next month.
- **fal.ai 429 errors** — the client retries once after 60 seconds; if it still fails, wait and try again.
- **YouTube upload fails** — the Drive link is still valid; verify the YouTube API is enabled and your OAuth scopes include `youtube.upload`.

## Security notes

- Never commit `.env`, `data/token.json`, or `data/client_secrets.json` (the provided `.gitignore` already excludes them).
- The bot checks `TELEGRAM_ALLOWED_USER_ID` on every handler; only that user can trigger generations or spend money.
- Images are held in memory as base64 and are purged automatically after 10 minutes or on `/cancel`.
