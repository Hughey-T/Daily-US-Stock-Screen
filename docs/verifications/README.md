# Verification records

Verification CSV files are stored separately from predictions and join to them
with `prediction_id`. Never append future returns to a prediction CSV.

All stock, SPY-relative, and sector-relative returns use split-adjusted closing
prices and exclude dividends, matching the screening pipeline. One verification
row is allowed for each `prediction_id`; its horizon must match the prediction.
