# yt-transcript

A simple tool to save YouTube video transcripts to your local knowledge bank.
Built for personal use — paste a URL, get a clean markdown file ready to use as context for AI tools like Claude.

---

## What it does

- Fetches the transcript from any YouTube video
- Saves it as a `.md` file with metadata (title, channel, publish date, language)
- Organises files by date so your knowledge bank stays easy to navigate
- Works with Swedish and English by default

---

## Requirements

- Python 3 — [python.org/downloads](https://python.org/downloads)
- Two small libraries (install once):

```bash
pip3 install youtube-transcript-api pytubefix
```

---

## How to use

Open Terminal, navigate to the folder where `yt_transcript.py` lives, and run:

```bash
python3 yt_transcript.py <youtube_url>
```

**Example:**
```bash
python3 yt_transcript.py https://www.youtube.com/watch?v=ABC123
```

Transcripts are saved in a `transcripts/` folder automatically created next to the script.

---

## Options

| Flag | What it does | Example |
|------|-------------|---------|
| `--date` | Set the publish date manually (recommended — auto-detection is unreliable) | `--date 2026-05-15` |
| `--lang` | Preferred transcript language(s) | `--lang sv,en` |
| `--out` | Change where files are saved | `--out ~/my-notes` |
| `--no-clean` | Keep raw timestamps in the output | `--no-clean` |

**Recommended command:**
```bash
python3 yt_transcript.py https://www.youtube.com/watch?v= ABC123 --date 202X-XX-XX
```

---

## Output

Each transcript is saved as a markdown file named:
```
YYYY-MM-DD_Video-Title.md
```

The file looks like this:
```
# NAME

source: https://www.youtube.com/watch?v=ABC123
channel: NAME
published: 2026-05-15
language: sv
fetched: 2026-05-15

---

## Transcript

Varmt välkomna till ytterligare ett avsnitt av Snacka om AI...
```

---

## Tips

- **Date is important.** AI topics move fast — always use `--date` so you know exactly when the content was published.
- **Title not detected?** YouTube sometimes blocks auto-detection. The file will be saved with the video ID as name — just rename it in Finder.
- **Using transcripts with AI?** Paste the contents of a `.md` file directly into Claude or any other AI tool as context. The metadata header helps the AI understand when and where the content is from.

---

## Folder structure

```
yt-transcript/
├── yt_transcript.py     ← the script
└── transcripts/
    ├── 2026-05-15_Video-title.md
    ├── 2026-05-10_Another-video.md
    └── ...
```

---

## Planned improvements

- Auto-summarisation via Claude API
- Tagging and categories
- Batch mode (multiple URLs at once)
- Search across all saved transcripts
