# Visualize YouTube History

How to visualize the user's YouTube history when they give a time range.

## 1. Locate the Data
First, try to find the user's YouTube history file.
1. Use the `find` tool with pattern `*watch-history*.json` or `*watch-history*.html`.
2. Search standard directories (like Downloads, Documents, Desktop, or entire machine).
3. If no file is found, pause and tell the user: "I need your YouTube watch history from Google Takeout. Please go to Google Takeout, export your YouTube history (choose JSON format), extract it, and let me know when it's ready."
4. Stop and wait for them to provide it.

## 2. Parse and Graph the Data (Time Range)
Once you have the path to the `watch-history.json` file:
1. Write a Python script (`analyze_yt.py`) into your workspace using the `write` tool. 
2. The script must:
   - Load the JSON file (`import json`).
   - Filter entries based on the user's requested time range (using the `time` field in ISO 8601 format). Note: The `time` field looks like `2023-01-01T12:00:00.000Z`.
   - Extract channel names from `entry.get('subtitles', [{}])[0].get('name', 'Unknown')`.
   - Use `matplotlib` or `seaborn` to create a visual graph (e.g., Top 10 Channels watched in that period, or Videos watched per day).
   - Save the plot as a PNG file in the workspace (e.g., `yt_graph.png`).
3. Run the script using the `exec` tool. Make sure to handle potential `ModuleNotFoundError` by pip installing `pandas matplotlib` if necessary.

## 3. Display the Graph
Once the python script successfully generates the PNG image:
- Use the `read` tool on the absolute path of the generated `.png` image. This will return the image itself in the chat.
- Add some textual insights (like "Your most watched channel was X...").

## Example Python Snippet
```python
import json
from datetime import datetime
import matplotlib.pyplot as plt
from collections import Counter

# Load data
with open('PATH_TO_JSON', 'r', encoding='utf-8') as f:
    data = json.load(f)

# Filter by time range and extract channels
start_date = datetime.strptime("YYYY-MM-DD", "%Y-%m-%d")
end_date = datetime.strptime("YYYY-MM-DD", "%Y-%m-%d")
channels = []

for item in data:
    if 'time' in item:
        try:
            # handle '2023-01-01T12:00:00.000Z'
            dt = datetime.strptime(item['time'][:19], "%Y-%m-%dT%H:%M:%S")
            if start_date <= dt <= end_date:
                subs = item.get('subtitles', [])
                if subs:
                    channels.append(subs[0].get('name', 'Unknown'))
        except Exception:
            continue

# Count and plot
counts = Counter(channels).most_common(10)
names = [x[0] for x in counts]
freqs = [x[1] for x in counts]

plt.figure(figsize=(10, 6))
plt.barh(names, freqs, color='skyblue')
plt.xlabel('Videos Watched')
plt.title('Top 10 Channels')
plt.gca().invert_yaxis()
plt.tight_layout()
plt.savefig('yt_graph.png')
```