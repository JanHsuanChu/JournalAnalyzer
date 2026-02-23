# app.py
# Journal Analyzer — Shiny for Python
# Two-panel layout: Filter the Journal (left 70%) | Analyze the Journal (right 30%).
# Includes AI Report (analysis date range, trend keywords, Ollama summaries).

# 0. Setup #################################

import html as html_module
import os
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from shiny import App, reactive, render, ui

from report_builder import build_report
from utils import filter_entries, filter_entries_by_date_only, fetch_entries, get_api_base

load_dotenv()

# Days of week for filter (order matching common calendars)
DAY_CHOICES = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]
TIME_CHOICES = ["morning", "afternoon", "evening"]

# 1. Theme and compact layout #################################

app_theme = ui.Theme("shiny").add_defaults(primary="#DD4633", body_bg="#FEECEA")

# Smaller margins so more data fits on screen
compact_css = ui.tags.style(
    """
    .container-fluid { padding-left: 8px; padding-right: 8px; }
    .card { margin-bottom: 8px; }
    .card-header { padding: 6px 12px; }
    h2, h3 { margin-top: 12px; margin-bottom: 6px; }
    .journal-table { width: 100%; table-layout: fixed; }
    .journal-table .col-date { width: 9%; }
    .journal-table .col-dow { width: 18%; white-space: nowrap; min-width: 8em; }
    .journal-table .col-tod { width: 14%; white-space: nowrap; min-width: 7.5em; }
    .journal-table .col-text { width: 59%; }
    .journal-table .text-clamp { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; line-height: 1.3; max-height: 2.6em; }
    """
)

# 2. UI: two panels (70% left, 30% right) #################################

panel_title_style = "color: #DD4633; font-weight: bold; font-size: 1.2rem;"

filters_column = ui.div(
    ui.p("Filter journal entries. Leave filters empty to show all.", class_="small"),
    ui.input_checkbox("use_date_filter", "Filter by date range", value=False),
    ui.panel_conditional(
        "input.use_date_filter === true",
        ui.input_date_range(
            "date_range",
            "Date range",
            start=None,
            end=None,
            format="yyyy-mm-dd",
        ),
    ),
    ui.input_checkbox_group(
        "days",
        "Day of week",
        choices=DAY_CHOICES,
        selected=[],
    ),
    ui.input_checkbox_group(
        "times",
        "Time of day",
        choices=TIME_CHOICES,
        selected=[],
    ),
    ui.input_text(
        "keywords",
        "Keywords (wildcard, comma-separated)",
        placeholder="e.g. OCD, productive",
    ),
)

results_column = ui.div(
    ui.p(ui.output_text("summary_count"), class_="mb-2"),
    ui.output_ui("message_ui"),
    ui.output_ui("entries_table"),
    style="min-height: 360px;",
)

left_panel = ui.card(
    ui.card_header("Filter the Journal", style=panel_title_style),
    ui.layout_columns(
        filters_column,
        results_column,
        col_widths=(4, 8),
        row_heights="auto",
    ),
)

right_panel = ui.card(
    ui.card_header("Analyze the Journal", style=panel_title_style),
    ui.p(
        "Select the date range of entries to analyze and optional trend phrases. "
        "Then generate an HTML report with AI summaries.",
        class_="small text-muted",
    ),
    ui.input_date_range(
        "analysis_date_range",
        "Analysis date range (entries to analyze)",
        start="2024-01-01",
        end="2025-12-31",
        format="yyyy-mm-dd",
    ),
    ui.input_text(
        "trend_keywords",
        "Trend(s) to analyze (comma-separated phrases)",
        placeholder="e.g. OCD, productive, motivated",
    ),
    ui.input_action_button("generate_report", "Generate report", class_="btn-primary"),
    ui.output_ui("report_status_ui"),
    ui.output_ui("report_download_ui"),
)

app_ui = ui.page_fluid(
    compact_css,
    ui.h2("Journal Analyzer", style="color: #DD4633; margin-top: 8px; margin-bottom: 8px;"),
    ui.layout_columns(
        left_panel,
        right_panel,
        col_widths=(8, 4),
        row_heights="auto",
    ),
    title="Journal Analyzer",
    theme=app_theme,
    fillable=True,
)

# 3. Server logic #################################


def server(input, output, session):
    # Hold raw entries from API; None until fetch completes (then None = error, DataFrame = success)
    entries_data = reactive.value(None)
    loaded = reactive.value(False)
    # AI Report state
    report_path = reactive.value(None)
    report_error = reactive.value(None)
    generating = reactive.value(False)

    # Fetch entries once when the app is accessed
    @reactive.Effect
    def _fetch_on_load():
        base_url = get_api_base()
        df = fetch_entries(base_url)
        entries_data.set(df)
        loaded.set(True)

    # Filtered table: depends on raw data and all filter inputs
    @reactive.Calc
    def filtered_table():
        raw = entries_data.get()
        if raw is None:
            return None
        date_from = None
        date_to = None
        if input.use_date_filter():
            date_range = input.date_range()
            if date_range and len(date_range) >= 2:
                date_from = date_range[0]
                date_to = date_range[1]
        return filter_entries(
            raw,
            date_from=date_from,
            date_to=date_to,
            days=list(input.days()) if input.days() else [],
            times=list(input.times()) if input.times() else [],
            keywords=input.keywords() or "",
        )

    # Summary: number of entries matching criteria
    @output
    @render.text
    def summary_count():
        tbl = filtered_table()
        if tbl is None:
            return "Entries matching your criteria: —"
        n = len(tbl)
        return f"Entries matching your criteria: {n}"

    # Error and status messages (no stack traces)
    @output
    @render.ui
    def message_ui():
        if not loaded.get():
            return ui.div(
                ui.p("Loading journal entries…", class_="text-muted"),
                class_="p-3",
            )
        if entries_data.get() is None:
            return ui.div(
                ui.p(
                    "Unable to load journal entries. Please ensure the API is running (e.g. uvicorn api:app) and try again.",
                    class_="text-danger fw-bold",
                ),
                class_="p-3",
            )
        tbl = filtered_table()
        if tbl is not None and len(tbl) == 0:
            return ui.div(
                ui.p(
                    "No journal entries match your criteria. Try loosening the filters.",
                    class_="text-warning",
                ),
                class_="p-3",
            )
        return ui.div()

    # Results table: narrow date/dow/tod columns, text column two lines with full text on hover
    @output
    @render.ui
    def entries_table():
        tbl = filtered_table()
        if tbl is None or len(tbl) == 0:
            return ui.div()
        display = tbl.copy()
        if "date" in display.columns:
            display["date"] = display["date"].dt.strftime("%Y-%m-%d")
        rows = []
        for _, row in display.iterrows():
            date_val = html_module.escape(str(row.get("date", "")))
            dow_val = html_module.escape(str(row.get("day_of_week", "")))
            tod_val = html_module.escape(str(row.get("time_of_day", "")))
            text_val = str(row.get("text", ""))
            text_escaped = html_module.escape(text_val)
            text_title = html_module.escape(text_val).replace('"', "&quot;")
            rows.append(
                f'<tr><td class="col-date">{date_val}</td><td class="col-dow">{dow_val}</td>'
                f'<td class="col-tod">{tod_val}</td><td class="col-text"><span class="text-clamp" title="{text_title}">{text_escaped}</span></td></tr>'
            )
        table_html = (
            '<table class="journal-table table table-sm table-striped">'
            "<thead><tr><th class=\"col-date\">Date</th><th class=\"col-dow\">Day</th><th class=\"col-tod\">Time</th><th class=\"col-text\">Text</th></tr></thead>"
            "<tbody>" + "".join(rows) + "</tbody></table>"
        )
        return ui.HTML(table_html)

    # Disable Generate button while generating
    @reactive.Effect
    def _update_generate_button():
        if generating.get():
            ui.update_action_button("generate_report", label="Generating…", disabled=True)
        else:
            ui.update_action_button("generate_report", label="Generate report", disabled=False)

    # Generate report when button is clicked
    @reactive.Effect
    @reactive.event(input.generate_report)
    def _generate_report():
        report_error.set(None)
        report_path.set(None)
        generating.set(True)
        try:
            raw = entries_data.get()
            if raw is None or raw.empty:
                report_error.set("No journal entries available. Ensure the API is running and try again.")
                return
            dr = input.analysis_date_range()
            if not dr or len(dr) < 2:
                report_error.set("Please select an analysis date range.")
                return
            date_from, date_to = dr[0], dr[1]
            subset = filter_entries_by_date_only(raw, date_from, date_to)
            if subset.empty:
                report_error.set("No journal entries in the selected date range. Adjust the range and try again.")
                return
            keywords_str = input.trend_keywords() or ""
            trend_keywords = [k.strip() for k in keywords_str.split(",") if k.strip()]
            api_key = os.environ.get("OLLAMA_API_KEY")
            path = build_report(subset, trend_keywords, api_key, date_from, date_to)
            report_path.set(path)
        except Exception as e:
            report_error.set(
                "Report generation failed. Check that the API is running and OLLAMA_API_KEY is set in .env."
            )
        finally:
            generating.set(False)

    # Status message: generating, error, or API key hint
    @output
    @render.ui
    def report_status_ui():
        if generating.get():
            return ui.div(
                ui.p("Generating report…", class_="text-muted"),
                class_="p-3",
            )
        err = report_error.get()
        if err:
            return ui.div(
                ui.p(err, class_="text-danger fw-bold"),
                class_="p-3",
            )
        if not os.environ.get("OLLAMA_API_KEY"):
            return ui.div(
                ui.p(
                    "Set OLLAMA_API_KEY in .env to generate AI summaries in the report.",
                    class_="text-muted small",
                ),
                class_="p-2",
            )
        return ui.div()

    # Download button and Open link when report is ready
    @output
    @render.ui
    def report_download_ui():
        path = report_path.get()
        if path is None:
            return ui.div()
        api_base = get_api_base().rstrip("/")
        filename = Path(path).name
        open_url = f"{api_base}/reports/{filename}"
        return ui.div(
            ui.download_button("download_report", "Download report", class_="btn-primary me-2"),
            ui.a("Open report", href=open_url, target="_blank", class_="btn btn-outline-primary"),
            class_="p-3",
        )

    @output
    @render.download(filename=lambda: Path(report_path.get()).name if report_path.get() else "report.html")
    def download_report():
        path = report_path.get()
        if path is None:
            return
        with open(path, "rb") as f:
            yield f.read()


app = App(app_ui, server)
