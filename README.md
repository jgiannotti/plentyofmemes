# Plenty of Memes

Plenty of Memes is a fast, minimal, endlessly fresh meme feed.  This repository
contains all of the code required to run the public website, the admin portal,
and the ingestion pipeline.  Everything runs on free tiers—no credit card
required.  Once configured the system operates completely unattended, pulling
fresh content from the internet, filtering out duplicates and NSFW content,
and allowing an administrator to approve or reject posts with a simple
interface.

## Project overview

```
plentyofmemes/
├── public/               # Static assets served to the browser
│   ├── index.html        # Public feed with infinite scroll
│   ├── admin.html        # Admin dashboard (login, approve/reject)
│   └── style.css         # Shared styles
├── scripts/
│   └── ingest_memes.py   # Scheduled ingestion script (runs in GitHub Actions)
├── .github/workflows/
│   └── ingest.yml        # GitHub Actions workflow to run ingestion daily
└── README.md             # This file
```

The frontend is written in plain HTML and JavaScript to avoid the need for a
build step.  Tailwind CSS is pulled from a CDN so you get a clean, modern
design out of the box.  The admin portal uses the Supabase JavaScript client
from a CDN for authentication and database access.

## Setup instructions

Because this repo uses third‑party services, you must perform a few one‑off
configuration steps before the site will be live.  None of these steps
require payment, but some will ask you to verify your email address.  When
these steps refer to **Supabase** or **Vercel**, you can substitute any
equivalent free hosting or database provider—only the environment variables
and policies need to be adjusted.

1. **Create a Supabase project.**
   - Sign up at [supabase.com](https://supabase.com) using your own email.  The
     free tier is sufficient and does not require a credit card.  If you
     cannot or prefer not to use Supabase, you can host PostgreSQL anywhere
     and expose it through [PostgREST](https://postgrest.org).  Adjust the
     environment variables accordingly.
   - Create a new project and note the _Project URL_ and _Anon key_.  You
     will need these values later.  Set a strong database password.
   - In the **SQL Editor**, create a table called `memes` with the schema
     below:

     ```sql
     create table if not exists public.memes (
       id uuid primary key default gen_random_uuid(),
       title text,
       image_url text,
       source_url text,
       author text,
       score integer,
       nsfw_score numeric,
       md5 text,
       phash text,
       duplicate_of uuid references memes(id),
       status text check (status in ('pending','approved','rejected')) default 'pending',
       published_at timestamptz,
       created_at timestamptz default now()
     );
     ```

     Add indexes on `status`, `md5`, and `phash` to speed up queries:

     ```sql
     create index if not exists idx_memes_status on memes (status);
     create index if not exists idx_memes_md5 on memes (md5);
     create index if not exists idx_memes_phash on memes (phash);
     ```

   - Enable **Row Level Security** on the table and add the following
     policies:

     ```sql
     alter table memes enable row level security;

     -- Public users may read only approved posts
     create policy "Public read approved" on memes
       for select using (status = 'approved');

     -- Authenticated admin can read everything and modify anything
     create policy "Admin full access" on memes
       for all using (auth.role() = 'authenticated' and auth.uid() = auth.jwt() ->> 'sub');
     ```

   - Create an admin user via the **Authentication** tab.  Use an email and
     password of your choosing.  Once the user is confirmed, you can log in
     to the admin portal.  No other users are required.

2. **Host the frontend.**
   - This repository does not require a build step.  You can host the
     `public/` folder on any static hosting platform.  Popular options include
     [Vercel](https://vercel.com), [Netlify](https://www.netlify.com),
     [Cloudflare Pages](https://pages.cloudflare.com) or even Supabase
     Storage.  All of these services have a free tier that allows custom
     domains.
   - Upload the contents of the `public/` directory and set `index.html` as
     the root page.  If your hosting platform supports environment
     variables, set the following variables:

     - `SUPABASE_URL`: the Project URL from step 1.
     - `SUPABASE_ANON_KEY`: the anon key from step 1.

     If your host does not support environment variables for static sites,
     edit `public/index.html` and `public/admin.html` and replace the
     placeholders `{{ SUPABASE_URL }}` and `{{ SUPABASE_ANON_KEY }}` with your
     actual values.

3. **Configure the ingestion workflow.**
   - Fork this repository into your Git hosting provider (GitHub, GitLab, etc.).
   - In your repository settings, add the following repository secrets:

     - `SUPABASE_URL`
     - `SUPABASE_SERVICE_ROLE_KEY`: obtain this from your Supabase project
       settings under “API”.  The service role key has elevated privileges
       and must be kept secret.
     - `REDDIT_USER_AGENT`: a string like `plentyofmemes/1.0 (by u/<your user>)`

     The GitHub Actions workflow defined in `.github/workflows/ingest.yml`
     uses these secrets to run the ingestion script every six hours.  It
     fetches the top posts from a handful of meme subreddits, downloads the
     images, filters out NSFW and near‑duplicate content, and inserts new
     pending records into the `memes` table.

4. **Connect your domain.**
   - Once your site is deployed on a static host, configure a CNAME record
     for `plentyofmemes.com` pointing at the host’s domain.  For example,
     Vercel assigns a domain like `plentyofmemes.vercel.app`.  In GoDaddy,
     create a CNAME record for the root (using `@`) pointing at that
     subdomain.  DNS changes can take a few minutes to propagate.

## Admin workflow

1. Navigate to `/admin.html` on your deployed site.  Enter your admin
   credentials.  If authentication succeeds you will see three panels:
   - **Pending:** memes waiting for approval.  You can review the image,
     caption, source, popularity score, NSFW score, and duplicate warnings.
   - **Approved:** currently published memes.  You can unpublish or edit
     captions.
   - **Rejected:** rejected posts are kept for reference and duplicate
     detection but are hidden from the public feed.

2. Clicking **Approve** on a pending meme sets its `status` to
   `approved` and schedules it for immediate publication.  You can also
   specify a future `published_at` timestamp to drip‑feed content.  Clicking
   **Reject** marks the meme as rejected.

3. Approved memes appear immediately on the public homepage.  Visitors can
   scroll infinitely; new content is loaded in pages of 20 posts.

## Instagram automation

Automatic posting to Instagram requires access to the Meta Graph API, which
is only available for Business accounts that have been reviewed by Meta.  No
free, unauthenticated API exists at this time.  However the ingestion
workflow produces an RSS feed (`/rss.xml`) that contains the latest
approved memes.  You can connect this feed to a service like Buffer,
IFTTT or Zapier (all of which offer free plans) to schedule posts to
Instagram.  Follow the documentation of your chosen service to import the
RSS feed and map the image and caption fields.

## Duplicate detection

The ingestion script implements two forms of duplicate detection:

1. **Exact matches** are detected by computing the MD5 checksum of the
   downloaded image.  If a pending meme’s MD5 matches an existing meme in
   the database it is automatically marked as a duplicate.
2. **Near‑duplicates** use a perceptual hash (pHash) computed with
   [ImageHash](https://github.com/JohannesBuchner/imagehash).  If the
   Hamming distance between two pHashes is less than 5 the new meme is
   considered a near duplicate.  In this case the `duplicate_of` field
   references the original meme and the admin dashboard displays a
   similarity warning.

Both scores are stored in the database so you have full transparency into
why a meme was flagged.

## NSFW filtering

To keep the public feed safe the ingestion script uses the
[nsfw-detector](https://github.com/GantMan/nsfw_detector) library to
classify images.  Images with a NSFW probability greater than `0.4` are
automatically rejected and not inserted into the database.  The NSFW
probability is stored in the `nsfw_score` column for reference.

## Contributing

Feel free to fork this repository and adjust it to your needs.  Pull
requests are welcome.  Please respect the ethos of the project—keep it
clean, fast and fun.