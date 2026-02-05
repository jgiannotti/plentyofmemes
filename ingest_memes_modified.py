"""
ingest_memes.py
=================

This script fetches the latest memes from a handful of subreddits, filters
them for NSFW content and duplicates, and inserts the survivors into your
Supabase database as pending memes.  It is designed to run in an automated
environment such as a GitHub Actions workflow and relies on environment
variables for configuration.

Environment variables expected:

```
SUPABASE_URL               – Your Supabase project URL
SUPABASE_SERVICE_ROLE_KEY  – A service‑role API key with insert privileges
REDDIT_USER_AGENT          – A descriptive user agent for Reddit requests
```

The script uses only open source libraries available on PyPI.  When running
in GitHub Actions you should install the dependencies listed in the
accompanying workflow file.  See README.md for details.
"""

import os
import sys
import json
import hashlib
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict

import requests
from PIL import Image
from io import BytesIO
import imagehash

try:
    # nsfw_detector pulls in tensorflow; import lazily to avoid heavy
    # startup cost if the module is missing.  The GitHub Actions workflow
    # installs this dependency.
    from nsfw_detector import predict
except ImportError:
    predict = None  # type: ignore

try:
    # supabase-py client; if not installed the script will exit with
    # instructions.
    from supabase import create_client, Client
except ImportError:
    create_client = None  # type: ignore
    Client = None  # type: ignore


def error(message: str) -> None:
    """Prints an error message and exits."""
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def get_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        error(f"Missing required environment variable {name}")
    return value


@dataclass
class MemeCandidate:
    """Represents a meme fetched from Reddit."""

    title: str
    image_url: str
    source_url: str
    author: Optional[str]
    score: int
    md5: str = field(init=False)
    phash: Optional[str] = field(init=False)
    nsfw_score: float = field(init=False)
    duplicate_of: Optional[str] = field(init=False)

    def download_and_process(self, nsfw_model) -> bool:
        """
        Downloads the image, computes MD5 and perceptual hash, and runs
        NSFW detection.  Returns True if the image should be kept, False if
        it should be rejected due to NSFW content.
        """
        try:
            resp = requests.get(self.image_url, timeout=15)
            resp.raise_for_status()
            img_data = resp.content
        except Exception as e:
            print(f"Failed to download {self.image_url}: {e}")
            return False
        # Compute MD5
        self.md5 = hashlib.md5(img_data).hexdigest()
        # Compute pHash
        try:
            image = Image.open(BytesIO(img_data)).convert('RGB')
            ph = imagehash.phash(image)
            self.phash = ph.__str__()  # returns hex string
        except Exception as e:
            print(f"Failed to compute pHash for {self.image_url}: {e}")
            self.phash = None
        # NSFW detection
        if nsfw_model:
            try:
                # nsfw_detector expects a dict mapping filepaths to arrays; we
                # can use the in-memory image by converting to numpy.
                # Save to a temporary in‑memory file via BytesIO.
                temp = BytesIO(img_data)
                temp.name = 'image.jpg'
                nsfw_preds: Dict[str, Dict[str, float]] = predict.classify(nsfw_model, temp)  # type: ignore
                scores = list(nsfw_preds.values())[0]
                # Use the maximum of porn categories as the NSFW probability
                self.nsfw_score = float(
                    scores.get('porn', 0) + scores.get('hentai', 0) + scores.get('sexy', 0)
                )
            except Exception as e:
                print(f"Failed to run NSFW detector on {self.image_url}: {e}")
                self.nsfw_score = 0.0
        else:
            # If NSFW model not available treat everything as safe
            self.nsfw_score = 0.0
        # Filter out obviously NSFW
        return self.nsfw_score < 0.4


def fetch_reddit_posts(subreddit: str, user_agent: str, limit: int = 20) -> List[MemeCandidate]:
    """
    Fetches top posts from a subreddit and returns a list of MemeCandidate
    objects for posts that include images.
    """
    url = f"https://www.reddit.com/r/{subreddit}/top.json?t=day&limit={limit}"
    try:
        resp = requests.get(url, headers={"User-Agent": user_agent}, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"Failed to fetch posts from r/{subreddit}: {e}")
        return []
    candidates: List[MemeCandidate] = []
    for child in data.get('data', {}).get('children', []):
        post = child.get('data', {})
        # Skip stickied posts and ads
        if post.get('stickied') or post.get('pinned'):
            continue
        # Skip if marked NSFW by Reddit
        if post.get('over_18'):
            continue
        # Accept only image posts; look for preview or url_overridden_by_dest
        image_url = post.get('url_overridden_by_dest') or post.get('url')
        if not image_url:
            continue
        # Filter out non‑image files (.gifv, .mp4, .webm, etc.)
        if any(image_url.lower().endswith(ext) for ext in ['.gifv', '.mp4', '.webm']):
            continue
        # Accept JPEG/PNG/GIF; we allow .gif as static but will treat as image
        if not any(image_url.lower().endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.gif']):
            continue
        title = post.get('title', '')
        score = int(post.get('ups', 0))
        author = post.get('author')
        permalink = post.get('permalink')
        source_url = f"https://www.reddit.com{permalink}" if permalink else ''
        candidate = MemeCandidate(
            title=title.strip(),
            image_url=image_url,
            source_url=source_url,
            author=author,
            score=score,
        )
        candidates.append(candidate)
    return candidates


def load_existing_hashes(supabase: 'Client') -> Tuple[Dict[str, str], List[Tuple[str, str]]]:
    """
    Fetches all existing MD5 and pHash values from the database.  Returns a
    mapping of MD5 → meme ID for exact match detection and a list of
    (pHash, id) tuples for near duplicate detection.  Entries with null
    pHash are excluded from the list.
    """
    existing_md5: Dict[str, str] = {}
    existing_phash: List[Tuple[str, str]] = []
    try:
        response = supabase.table('memes').select('id, md5, phash').execute()
        for row in response.data:
            md5 = row.get('md5')
            if md5:
                existing_md5[md5] = row['id']
            ph = row.get('phash')
            if ph:
                existing_phash.append((ph, row['id']))
    except Exception as e:
        error(f"Failed to fetch existing hashes: {e}")
    return existing_md5, existing_phash


def find_duplicate(candidate: MemeCandidate, existing_md5: Dict[str, str], existing_phash: List[Tuple[str, str]]) -> Optional[str]:
    """
    Checks if the candidate duplicates an existing meme.  Returns the ID of the
    meme it duplicates or None if unique.  Exact duplicates (matching MD5)
    supersede near duplicates.  Near duplicates are detected when the
    Hamming distance between pHashes is less than 5.
    """
    if candidate.md5 in existing_md5:
        return existing_md5[candidate.md5]
    if candidate.phash:
        try:
            cand_hash = imagehash.hex_to_hash(candidate.phash)
            for ph, mid in existing_phash:
                try:
                    h = imagehash.hex_to_hash(ph)
                    # The subtraction operator on ImageHash returns the Hamming distance
                    dist = cand_hash - h
                    if dist < 5:
                        return mid
                except Exception:
                    continue
        except Exception:
            pass
    return None


def insert_pending(supabase: 'Client', candidates: List[MemeCandidate]) -> None:
    """
    Inserts a batch of new pending memes into the database.  Skips those that
    duplicate existing memes or are NSFW.
    """
    existing_md5, existing_phash = load_existing_hashes(supabase)
    to_insert = []
    for cand in candidates:
        # Download image, compute MD5, pHash and NSFW score (we compute the NSFW score but do not filter out NSFW content)
        # Calling download_and_process will set md5, phash and nsfw_score on the candidate.
        cand.download_and_process(NSFW_MODEL)
        dup_id = find_duplicate(cand, existing_md5, existing_phash)
        cand.duplicate_of = dup_id
        # Only insert if not exact duplicate; near duplicates are allowed but flagged
        if dup_id and cand.md5 in existing_md5:
            # Skip exact duplicates entirely
            continue
        to_insert.append(cand)
    if not to_insert:
        print("No new memes to insert.")
        return
    # Prepare rows for insertion
    rows = []
    for cand in to_insert:
        rows.append({
            'title': cand.title,
            'image_url': cand.image_url,
            'source_url': cand.source_url,
            'author': cand.author,
            'score': cand.score,
            'md5': cand.md5,
            'phash': cand.phash,
            'nsfw_score': cand.nsfw_score,
            'duplicate_of': cand.duplicate_of,
            # Auto‑approve all memes so they appear in the public feed immediately
            'status': 'approved'
        })
    try:
        res = supabase.table('memes').insert(rows).execute()
        print(f"Inserted {len(res.data)} new memes.")
    except Exception as e:
        error(f"Failed to insert memes: {e}")


def main() -> None:
    # Ensure dependencies are available
    global NSFW_MODEL
    if create_client is None:
        error("The supabase-py library is not installed.  Please run `pip install supabase`.")
    # Read environment variables
    supabase_url = get_env('SUPABASE_URL')
    supabase_key = get_env('SUPABASE_SERVICE_ROLE_KEY')
    reddit_user_agent = os.getenv('REDDIT_USER_AGENT', 'plentyofmemes/1.0 (+https://plentyofmemes.com)')
    # Create Supabase client
    supabase: Client = create_client(supabase_url, supabase_key)
    # Load NSFW model if available
    NSFW_MODEL = None
    if predict:
        try:
            NSFW_MODEL = predict.load_model()
        except Exception as e:
            print(f"Failed to load NSFW model: {e}")
            NSFW_MODEL = None
    # Define target subreddits
    subreddits = [
        'memes',
        'dankmemes',
        'funny',
        'wholesomememes',
        'AdviceAnimals'
    ]
    all_candidates: List[MemeCandidate] = []
    for sub in subreddits:
        print(f"Fetching top posts from r/{sub}…")
        posts = fetch_reddit_posts(sub, reddit_user_agent, limit=25)
        print(f"  Retrieved {len(posts)} candidates from r/{sub}.")
        all_candidates.extend(posts)
    if not all_candidates:
        print("No candidates found.")
        return
    print(f"Processing {len(all_candidates)} total candidates…")
    insert_pending(supabase, all_candidates)


if __name__ == '__main__':
    main()