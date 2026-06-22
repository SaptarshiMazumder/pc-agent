import pandas as pd
from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference

# Create a Pandas Excel writer using openpyxl as the engine
file_path = "yucho_expenses.xlsx"
writer = pd.ExcelWriter(file_path, engine='openpyxl')

# Data for the last 4 days (June 18-21)
data_18 = pd.DataFrame([["2026/06/18 22:57:04", "AMAZON CO JP", 1141]], columns=["Date", "Description", "Amount"])
data_19 = pd.DataFrame(columns=["Date", "Description", "Amount"])
data_20 = pd.DataFrame(columns=["Date", "Description", "Amount"])
data_21 = pd.DataFrame([
    ["2026/06/21 19:51:50", "FAMILYMART", 2421],
    ["2026/06/21 18:47:04", "OPENAI", 1774],
    ["2026/06/21 18:05:27", "OPENAI", 1848],
    ["2026/06/21 17:23:54", "MERCARI", 34800],
], columns=["Date", "Description", "Amount"])

# Write each day to a separate tab
data_18.to_excel(writer, sheet_name="2026-06-18", index=False)
data_19.to_excel(writer, sheet_name="2026-06-19", index=False)
data_20.to_excel(writer, sheet_name="2026-06-20", index=False)
data_21.to_excel(writer, sheet_name="2026-06-21", index=False)

# Comparison Data
comparison_data = pd.DataFrame([
    ["2026-06-18", 1141],
    ["2026-06-19", 0],
    ["2026-06-20", 0],
    ["2026-06-21", 40843]
], columns=["Date", "Total Amount"])
comparison_data.to_excel(writer, sheet_name="comparison tab", index=False)

# Get the workbook and the comparison worksheet to add a chart
workbook = writer.book
worksheet = workbook["comparison tab"]

chart = BarChart()
chart.title = "Weekly Expenses Comparison"
chart.x_axis.title = "Date"
chart.y_axis.title = "Amount (JPY)"

# Data for chart
data = Reference(worksheet, min_col=2, min_row=1, max_row=5, max_col=2)
categories = Reference(worksheet, min_col=1, min_row=2, max_row=5)
chart.add_data(data, titles_from_data=True)
chart.set_categories(categories)

worksheet.add_chart(chart, "D2")

writer.close()
