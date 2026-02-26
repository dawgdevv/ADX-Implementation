import io

import numpy as np
import pandas as pd
from django.http import HttpResponse
from django.shortcuts import redirect, render

# Required OHLC columns from the assignment input format.
REQUIRED_COLUMNS = ["Open", "High", "Low", "Close"]
# Common ADX window used in trading.
ADX_PERIOD = 14


def index(request):
	# Initial page where user uploads a CSV file.
	return render(request, "app/index.html")


def _calculate_adx(dataframe: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    df = dataframe.copy().reset_index(drop=True)

    # True Range
    prev_close = df["Close"].shift(1)
    df["TR"] = pd.concat(
        [
            df["High"] - df["Low"],
            (df["High"] - prev_close).abs(),
            (df["Low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

	# First row special case
    df.loc[0, "TR"] = df.loc[0, "High"] - df.loc[0, "Low"]

    # Directional Movement
    up_move = df["High"].diff()
    down_move = df["Low"].shift(1) - df["Low"]

    df["+DM1"] = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    df["-DM1"] = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    # Wilder smoothing
    df["TR14"] = np.nan
    df["+DM14"] = np.nan
    df["-DM14"] = np.nan

    # First smoothed value at index = period
    first_tr14 = df["TR"].iloc[1 : period + 1].sum()
    first_pdm14 = df["+DM1"].iloc[1 : period + 1].sum()
    first_mdm14 = df["-DM1"].iloc[1 : period + 1].sum()

    df.loc[period, "TR14"] = first_tr14
    df.loc[period, "+DM14"] = first_pdm14
    df.loc[period, "-DM14"] = first_mdm14

    # Recursive smoothing
    for i in range(period + 1, len(df)):
        df.loc[i, "TR14"] = (
            df.loc[i - 1, "TR14"] - (df.loc[i - 1, "TR14"] / period) + df.loc[i, "TR"]
        )
        df.loc[i, "+DM14"] = (
            df.loc[i - 1, "+DM14"] - (df.loc[i - 1, "+DM14"] / period) + df.loc[i, "+DM1"]
        )
        df.loc[i, "-DM14"] = (
            df.loc[i - 1, "-DM14"] - (df.loc[i - 1, "-DM14"] / period) + df.loc[i, "-DM1"]
        )

    # DI calculation (safe division)
    df["+DI14"] = np.where(df["TR14"] == 0, 0, 100 * (df["+DM14"] / df["TR14"]))
    df["-DI14"] = np.where(df["TR14"] == 0, 0, 100 * (df["-DM14"] / df["TR14"]))

    df["DI14 Diff"] = (df["+DI14"] - df["-DI14"]).abs()
    df["DI14 Sum"] = df["+DI14"] + df["-DI14"]

    df["DX"] = np.where(
        df["DI14 Sum"] == 0,
        0,
        100 * (df["DI14 Diff"] / df["DI14 Sum"]),
    )

    # ADX calculation
    df["ADX"] = np.nan

    adx_start = period * 2

    if len(df) > adx_start:
        first_adx = df["DX"].iloc[period : adx_start].mean()
        df.loc[adx_start, "ADX"] = first_adx

        for i in range(adx_start + 1, len(df)):
            df.loc[i, "ADX"] = (
                (df.loc[i - 1, "ADX"] * (period - 1)) + df.loc[i, "DX"]
            ) / period

    # Aliases
    df["+DI"] = df["+DI14"]
    df["-DI"] = df["-DI14"]

    return df



def result(request):
	# Keep this endpoint POST-only because it depends on uploaded file content.
	if request.method != "POST":
		return redirect("index")

	uploaded_file = request.FILES.get("csv_file")
	if uploaded_file is None:
		return render(request, "app/index.html", {"error_message": "Please upload a CSV file."})

	try:
		# Read CSV from uploaded bytes.
		decoded = uploaded_file.read().decode("utf-8")
		df = pd.read_csv(io.StringIO(decoded))

		# Validate required columns.
		missing_columns = [column for column in REQUIRED_COLUMNS if column not in df.columns]
		if missing_columns:
			message = f"Missing required columns: {', '.join(missing_columns)}"
			return render(request, "app/index.html", {"error_message": message})

		# Ensure OHLC values are numeric before calculations.
		for column in REQUIRED_COLUMNS:
			df[column] = pd.to_numeric(df[column], errors="coerce")

		if df[REQUIRED_COLUMNS].isnull().any().any():
			return render(
				request,
				"app/index.html",
				{"error_message": "CSV has invalid numeric values in Open/High/Low/Close."},
			)

		# Calculate ADX output columns.
		adx_df = _calculate_adx(df, period=ADX_PERIOD)
		output_columns = [
			"Open",
			"High",
			"Low",
			"Close",
			"TR",
			"+DM1",
			"-DM1",
			"TR14",
			"+DM14",
			"-DM14",
			"+DI14",
			"-DI14",
			"DI14 Diff",
			"DI14 Sum",
			"DX",
			"ADX",
		]
		final_output_df = adx_df[output_columns]

		# Keep rows where chart series are available.
		chart_df = adx_df[["ADX", "+DI", "-DI"]].dropna().reset_index(drop=True)
		if chart_df.empty:
			return render(
				request,
				"app/index.html",
				{"error_message": "Not enough rows to calculate ADX. Please upload more data."},
			)

		# Save generated output in session for one-click XLSX download.
		request.session["output_json"] = final_output_df.to_json(orient="split")

		# Build chart context as plain lists for JSON-safe rendering in template.
		context = {
			"labels": (chart_df.index + 1).astype(str).tolist(),
			"adx_values": chart_df["ADX"].round(4).tolist(),
			"plus_di_values": chart_df["+DI"].round(4).tolist(),
			"minus_di_values": chart_df["-DI"].round(4).tolist(),
			# Latest values for stat cards shown above the chart.
			"latest_adx": round(float(chart_df["ADX"].iloc[-1]), 2),
			"latest_plus_di": round(float(chart_df["+DI"].iloc[-1]), 2),
			"latest_minus_di": round(float(chart_df["-DI"].iloc[-1]), 2),
			"total_rows": len(chart_df),
		}
		return render(request, "app/result.html", context)
	except Exception:
		# Minimal user-facing fallback for malformed files or parse errors.
		return render(
			request,
			"app/index.html",
			{"error_message": "Unable to process this file. Please upload a valid CSV."},
		)


def download_output(request):
	# Serve the last computed output as XLSX from the current session.
	output_json = request.session.get("output_json")
	if not output_json:
		return render(
			request,
			"app/index.html",
			{"error_message": "No output available yet. Please upload a CSV first."},
		)

	output_df = pd.read_json(output_json, orient="split")
	excel_buffer = io.BytesIO()
	with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
		output_df.to_excel(writer, index=False, sheet_name="ADX Output")
	excel_buffer.seek(0)

	response = HttpResponse(
		excel_buffer.getvalue(),
		content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
	)
	response["Content-Disposition"] = 'attachment; filename="adx_output.xlsx"'
	return response
