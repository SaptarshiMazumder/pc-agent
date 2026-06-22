import pandas as pd
import sys

file_path = sys.argv[1]

data = [
    ["2026/06/21 19:51:50", "FAMILYMART", 2421, "Food & Convenience"],
    ["2026/06/21 18:47:04", "OPENAI", 1774, "Subscriptions & Digital"],
    ["2026/06/21 18:05:27", "OPENAI", 1848, "Subscriptions & Digital"],
    ["2026/06/21 17:23:54", "MERCARI", 34800, "Shopping & Amenities"]
]
df = pd.DataFrame(data, columns=["Date", "Merchant", "Amount", "Category"])

with pd.ExcelWriter(file_path, engine='openpyxl', mode='a', if_sheet_exists='replace') as writer:
    df.to_excel(writer, sheet_name='2026-06-21', index=False)

print("Update complete")