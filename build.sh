#!/bin/sh

# Exit on errors
set -e

# This script is executed by Netlify during the build process.
# It replaces the placeholder strings in our static HTML files
# with the actual Supabase project URL and anon (publishable) key
# provided as Netlify environment variables.  Without this step
# the client-side scripts would not be able to connect to the
# Supabase backend.

if [ -z "$SUPABASE_URL" ] || [ -z "$SUPABASE_ANON_KEY" ]; then
  echo "Error: SUPABASE_URL and SUPABASE_ANON_KEY environment variables must be set." >&2
  exit 1
fi

# Replace placeholders in all HTML files within the public directory
for file in $(find public -type f -name '*.html'); do
  echo "Processing $file"
  # Use sed to perform in-place substitution
  # Use | as the delimiter to avoid escaping slashes in the URL
  sed -i "s|{{ SUPABASE_URL }}|$SUPABASE_URL|g" "$file"
  sed -i "s|{{ SUPABASE_ANON_KEY }}|$SUPABASE_ANON_KEY|g" "$file"
done

echo "Build complete: placeholders replaced with environment values."