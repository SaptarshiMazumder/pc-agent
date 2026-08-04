1. Always look for `watch-history.json` or `watch-history.html` first using the `find` tool before asking the user for it.
2. Do not attempt to use browser automation to scrape history from My Activity unless explicitly asked, as Takeout is much more reliable for bulk data.
3. Use Python (`pandas` and `matplotlib`/`seaborn`) via the `exec` or equivalent tool to process the data and generate graphs.
4. Save generated graphs as `.png` files in the workspace and use the `read` tool to display them to the user.
