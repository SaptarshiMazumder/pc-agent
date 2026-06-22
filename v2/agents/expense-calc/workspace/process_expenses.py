import pandas as pd
import re
import matplotlib.pyplot as plt
import os

with open('emails_raw.txt', 'r', encoding='utf-8') as f:
    text = f.read()

pattern = re.compile(r'利用日時\s+([\d/:\s]+)\n利用店舗\s+(.+)\n利用金額\s+([0-9,]+)円\n利用通貨\s+([A-Z]+)')
matches = pattern.findall(text)

def categorize(merchant):
    m = merchant.lower()
    if any(x in m for x in ['seven-eleven', 'familymart', 'deiri-yamazaki', 'lawson', 'mcdonald', 'kfc']):
        return 'Food & Convenience'
    elif any(x in m for x in ['openai', 'apple.com', 'openart', 'cursor', 'google', 'rakuten kobo']):
        return 'Subscriptions & Digital'
    elif any(x in m for x in ['amazon', 'mercari', 'tectkitijyouzitenn']):
        return 'Shopping & Amenities'
    elif 'softbank' in m:
        return 'Utilities & Phone'
    else:
        return 'Other'

data = []
for m in matches:
    dt, store, amt_str, curr = m
    date_only = dt.split()[0].replace('/', '-')
    amt = int(amt_str.replace(',', ''))
    cat = categorize(store.strip())
    data.append({'Date': date_only, 'Time': dt.split()[1], 'Merchant': store.strip(), 'Category': cat, 'Amount_JPY': amt})

if data:
    new_df = pd.DataFrame(data)
    if os.path.exists('expenses.csv'):
        old_df = pd.read_csv('expenses.csv')
        df = pd.concat([old_df, new_df]).drop_duplicates(subset=['Date', 'Time', 'Merchant', 'Amount_JPY'])
    else:
        df = new_df
else:
    df = pd.read_csv('expenses.csv')

df['Date'] = pd.to_datetime(df['Date'])
df = df.sort_values(by=['Date', 'Time'])
df.to_csv('expenses.csv', index=False)

# Write to Excel with specific tab order
with pd.ExcelWriter('Expenses_History.xlsx', engine='openpyxl') as writer:
    # 1st tab: Comparison (all data)
    df.to_excel(writer, sheet_name='Comparison', index=False)
    
    # Subsequent tabs: Daily (most recent first)
    dates = df['Date'].dt.strftime('%Y-%m-%d').unique()
    dates = sorted(dates, reverse=True)
    
    for d in dates:
        day_df = df[df['Date'].dt.strftime('%Y-%m-%d') == d]
        day_df.to_excel(writer, sheet_name=d, index=False)

# Plotting
daily = df.groupby('Date')['Amount_JPY'].sum()
min_date, max_date = df['Date'].min(), df['Date'].max()
idx = pd.date_range(min_date, max_date)
daily = daily.reindex(idx, fill_value=0)

weekly_cat = df.groupby([pd.Grouper(key='Date', freq='W-MON'), 'Category'])['Amount_JPY'].sum().unstack(fill_value=0)
weekly_cat.index = weekly_cat.index.strftime('Week of %Y-%m-%d')

fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 12))

ax1.plot(daily.index, daily.values, marker='o', linestyle='-', color='royalblue', linewidth=2)
ax1.set_title('Daily Expenses Trend', fontsize=14)
ax1.set_xlabel('Date')
ax1.set_ylabel('Amount (JPY)')
ax1.grid(True, linestyle='--', alpha=0.6)
ax1.tick_params(axis='x', rotation=45)

if not weekly_cat.empty:
    weekly_cat.plot(kind='bar', stacked=True, ax=ax2, colormap='Set2', edgecolor='black')
    ax2.set_title('Weekly Spending by Category', fontsize=14)
    ax2.set_xlabel('Week Ending (Monday)')
    ax2.set_ylabel('Amount (JPY)')
    ax2.legend(title='Category', bbox_to_anchor=(1.05, 1), loc='upper left')
    ax2.grid(axis='y', linestyle='--', alpha=0.6)
    ax2.tick_params(axis='x', rotation=0)

plt.tight_layout()
plt.savefig('expenses_graph.png', bbox_inches='tight')

import openpyxl
from openpyxl.drawing.image import Image as XLImage
wb = openpyxl.load_workbook('Expenses_History.xlsx')
ws = wb['Comparison']
img = XLImage('expenses_graph.png')
ws.add_image(img, 'H2') # Insert the image starting at cell H2
wb.save('Expenses_History.xlsx')

import shutil
shutil.copy('Expenses_History.xlsx', r'C:\Users\googler\.workspace-mcp\attachments\Expenses_History_temp.xlsx')

print("Excel and graph generated successfully.")
