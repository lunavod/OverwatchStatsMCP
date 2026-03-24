# Client File Upload Guide

Upload files (recordings, metadata, etc.) to the MCP server using the [tus resumable upload protocol](https://tus.io/).

## Install tuspy

```bash
pip install tuspy
```

## Basic upload

```python
from tusclient import client

UPLOAD_URL = "https://overwatch-mcp.example.com/files/"
AUTH_KEY = "your-secret-key-here"

tus = client.TusClient(UPLOAD_URL, headers={"Authorization": f"Bearer {AUTH_KEY}"})

uploader = tus.uploader(
    "match_recording.mp4",
    chunk_size=5 * 1024 * 1024,  # 5 MB chunks
    metadata={
        "match_id": "abc123-...",
        "filename": "match_recording.mp4",
    },
)

uploader.upload()
```

## Resumable upload (survives crashes/disconnects)

tuspy can remember in-progress uploads and resume them:

```python
from tusclient import client
from tusclient.storage import filestorage

UPLOAD_URL = "https://overwatch-mcp.example.com/files/"
AUTH_KEY = "your-secret-key-here"

tus = client.TusClient(UPLOAD_URL, headers={"Authorization": f"Bearer {AUTH_KEY}"})

# Stores upload URLs locally so they can be resumed
storage = filestorage.FileStorage("./upload_cache")

uploader = tus.uploader(
    "match_recording.mp4",
    chunk_size=10 * 1024 * 1024,  # 10 MB chunks
    metadata={
        "match_id": "abc123-...",
        "filename": "match_recording.mp4",
    },
    store_url=True,
    url_storage=storage,
)

# Resumes automatically if a previous upload for this file exists
uploader.upload()
```

## Uploading multiple files for one match

Each match can have multiple files attached. Just use the same `match_id` in metadata:

```python
import os
from pathlib import Path
from tusclient import client
from tusclient.storage import filestorage

UPLOAD_URL = "https://overwatch-mcp.example.com/files/"
AUTH_KEY = "your-secret-key-here"
MATCH_ID = "abc123-..."

tus = client.TusClient(UPLOAD_URL, headers={"Authorization": f"Bearer {AUTH_KEY}"})
storage = filestorage.FileStorage("./upload_cache")

files_to_upload = [
    "match_recording.mp4",
    "keyboard_state.json",
    "overwolf_events.json",
    "timeline.csv",
]

for filepath in files_to_upload:
    uploader = tus.uploader(
        filepath,
        chunk_size=10 * 1024 * 1024,
        metadata={
            "match_id": MATCH_ID,
            "filename": Path(filepath).name,
        },
        store_url=True,
        url_storage=storage,
    )
    uploader.upload()
    print(f"Uploaded {filepath}")
```

## Required metadata

| Key        | Required | Description                                  |
|------------|----------|----------------------------------------------|
| `match_id` | Yes      | UUID of the match to attach the file to      |
| `filename` | No       | Original filename (defaults to "unknown")    |

The `match_id` must refer to an existing match in the database. The server validates this during the pre-create hook and rejects uploads with invalid or missing match IDs.

## Authentication

All uploads require a `Bearer` token in the `Authorization` header. This is the same key configured as `TUSD_AUTH_KEY` in the server's `.env`.

If the key is missing or wrong, the server rejects the upload at the pre-create stage (before any file data is sent).

## Chunk size considerations

| Chunk size | Trade-off                                      |
|------------|-------------------------------------------------|
| 1 MB       | More HTTP requests, finer resume granularity    |
| 5 MB       | Good default for most connections               |
| 10-25 MB   | Better throughput on fast connections            |
| 50+ MB     | Fewer requests, but more data to re-upload on failure |

For ~8 GB recordings over a stable connection, 10-25 MB chunks are a good starting point.

## Querying uploaded files

Use the MCP tools to list files attached to a match:

- `list_match_files(match_id)` — returns all attached files with their IDs, filenames, sizes, and upload timestamps
- `delete_match_file(file_id)` — removes a specific file from DB and disk
