import pandas as pd
from openpyxl import load_workbook

file_path = r'C:\Users\googler\.workspace-mcp\attachments\Expenses_History_5b4d3a29.xlsx'

data = [
    ['Date', 'Merchant', 'Amount'],
    ['2026/06/21 19:51:50', 'FAMILYMART', '2,421 JPY'],
    ['2026/06/21 18:47:04', 'OPENAI', '1,774 JPY'],
    ['2026/06/21 18:05:27', 'OPENAI', '1,848 JPY'],
    ['2026/06/21 17:23:54', 'MERCARI', '34,800 JPY']
]

df = pd.DataFrame(data[1:], columns=data[0])

with pd.ExcelWriter(file_path, engine='openpyxl', mode='a', if_sheet_exists='replace') as writer:
    df.to_excel(writer, sheet_name='2026-06-21', index=False)

print("Spreadsheet updated locally.")
