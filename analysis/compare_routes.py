#!/usr/bin/env python3
"""Compare WebFlow Route A, Route B, and Dynamic simulation outputs."""

from __future__ import annotations

import argparse
import csv
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


MODES = ("A", "B", "dynamic")
HTTP_COLUMNS = {
    "timestamp_seconds",
    "route_mode",
    "event",
    "response_bytes",
    "rtt_seconds",
    "status",
}
MONITOR_COLUMNS = {
    "timestamp_seconds",
    "route_label",
    "delay_rtt_ms",
    "packet_loss_rate",
    "queue_occupancy_ratio",
    "router_hops",
    "measurement_validity",
    "weighted_cost",
    "selection_reason",
}
SUMMARY_FIELDS = {
    "successful_responses": r"^Successful responses:\s*(\d+)\s*$",
    "failed_missing_responses": r"^Failed/missing responses:\s*(\d+)\s*$",
    "average_rtt_seconds": r"^Average RTT:\s*([^\s]+)\s+s\s*$",
    "maximum_rtt_seconds": r"^Maximum RTT:\s*([^\s]+)\s+s\s*$",
    "validated_response_body_bytes": r"^Validated response-body bytes:\s*(\d+)\s+bytes\s*$",
    "application_response_body_throughput_bits_per_second": (
        r"^Application response-body throughput:\s*([^\s]+)\s+bit/s"
    ),
    "simulation_duration_seconds": r"^Simulation duration:\s*([^\s]+)\s+s\s*$",
    "measured_application_interval_seconds": (
        r"^Measured application interval:\s*([^\s]+)\s+s\s*$"
    ),
    "expected_responses": r"^Expected requests/responses:\s*\d+/(\d+)\s*$",
    "requests_sent": r"^Total requests sent:\s*(\d+)\s*/\s*\d+\s*$",
    "clients_connected": r"^Clients connected:\s*(\d+)\s*/\s*(\d+)\s*$",
    "failed_connections": r"^Failed connections:\s*(\d+)\s*$",
}


@dataclass
class RunData:
    mode: str
    directory: Path
    http_path: Path
    monitor_path: Path | None
    log_path: Path | None
    http_rows: list[dict[str, str]] = field(default_factory=list)
    monitor_rows: list[dict[str, str]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    validation: str = "missing"
    complete: bool = False
    errors: list[str] = field(default_factory=list)


def number(value: str | None) -> float | None:
    if value is None or value.strip() == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def read_csv(path: Path, required_columns: set[str]) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source, strict=True)
        if reader.fieldnames is None:
            raise ValueError("CSV has no header")
        missing = required_columns.difference(reader.fieldnames)
        if missing:
            raise ValueError("missing required columns: " + ", ".join(sorted(missing)))
        rows = list(reader)
    if not rows:
        raise ValueError("CSV contains a header but no data rows")
    for index, row in enumerate(rows, start=2):
        if None in row or any(value is None for value in row.values()):
            raise ValueError(f"malformed row {index}: column count does not match header")
    return rows


def parse_run_log(path: Path) -> tuple[dict[str, Any], str]:
    summary: dict[str, Any] = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        return summary, f"unable to read console log: {error}"

    for line in text.splitlines():
        for key, pattern in SUMMARY_FIELDS.items():
            match = re.match(pattern, line.strip())
            if not match:
                continue
            if key == "clients_connected":
                summary["clients_connected"] = int(match.group(1))
                summary["configured_clients"] = int(match.group(2))
            elif key in {
                "successful_responses",
                "failed_missing_responses",
                "validated_response_body_bytes",
                "expected_responses",
                "requests_sent",
                "failed_connections",
            }:
                summary[key] = int(match.group(1))
            else:
                parsed = number(match.group(1))
                if parsed is not None:
                    summary[key] = parsed
    pass_seen = re.search(r"^\[TCP validation\]\s+PASS\b", text, re.MULTILINE)
    fail_seen = re.search(r"^\[TCP validation\]\s+FAIL\b", text, re.MULTILINE)
    switch_match = re.search(
        r"DYNAMIC ROUTE SWITCH: A -> B\b.*?timestamp_seconds=([0-9.eE+-]+)", text
    )
    if switch_match:
        summary["dynamic_switch_time_seconds"] = number(switch_match.group(1))
    mode_match = re.search(r"^Route mode:\s*(global|A|B|dynamic)\s*$", text, re.MULTILINE | re.IGNORECASE)
    if mode_match:
        summary["_route_mode"] = mode_match.group(1).lower()
    trace_match = re.search(r"^Trace directory:\s*(.+?)\s*$", text, re.MULTILINE)
    if trace_match:
        summary["_trace_directory"] = trace_match.group(1).strip()
    exit_match = re.search(
        r"^\[analysis runner\]\s+process_exit_code=(-?\d+)\s*$", text, re.MULTILINE
    )
    if exit_match:
        summary["_runner_exit_code"] = int(exit_match.group(1))
    if pass_seen and not fail_seen:
        validation = "PASS"
    elif fail_seen:
        validation = "FAIL"
    else:
        validation = "missing"
    return summary, validation


def paths_match(first: str | Path, second: Path) -> bool:
    try:
        left = os.path.normcase(os.path.abspath(os.path.normpath(str(first))))
        right = os.path.normcase(os.path.abspath(os.path.normpath(str(second))))
        return left == right
    except (OSError, ValueError):
        return False


def discover_runs(results_dir: Path) -> tuple[dict[str, RunData], list[str]]:
    issues: list[str] = []
    candidates: dict[str, list[RunData]] = {mode: [] for mode in MODES}
    if not results_dir.is_dir():
        return {}, [f"Results directory does not exist: {results_dir}"]

    for directory in results_dir.iterdir():
        if not directory.is_dir() or not directory.name.startswith("run-"):
            continue
        http_path = directory / "http-requests.csv"
        if not http_path.exists():
            continue
        try:
            http_rows = read_csv(http_path, HTTP_COLUMNS)
        except (OSError, UnicodeError, csv.Error, ValueError) as error:
            issues.append(f"{directory.name}: malformed or incomplete HTTP CSV: {error}")
            continue

        modes_in_file = {
            row.get("route_mode", "").strip().lower()
            for row in http_rows
            if row.get("route_mode", "").strip()
        }
        folder_mode = directory.name[4:].split("-", 1)[0].lower()
        if len(modes_in_file) > 1:
            issues.append(f"{directory.name}: HTTP CSV contains multiple route modes")
            continue
        detected = next(iter(modes_in_file), folder_mode)
        mode = "dynamic" if detected == "dynamic" else detected.upper()
        if mode not in MODES:
            continue
        if modes_in_file and folder_mode != mode.lower():
            issues.append(
                f"{directory.name}: route mode in filename does not match CSV content"
            )
            continue

        monitor_files = sorted(directory.glob("route-metrics-*.csv"))
        monitor_path = monitor_files[0] if len(monitor_files) == 1 else None
        log_candidates = [
            directory / "simulation.log",
            directory / "run.log",
            directory / "console.log",
        ]
        log_path = next((path for path in log_candidates if path.is_file()), None)
        run = RunData(mode, directory, http_path, monitor_path, log_path, http_rows=http_rows)
        if len(monitor_files) > 1:
            run.errors.append("multiple route-metrics CSV files; monitoring data is ambiguous")
        if monitor_path is not None:
            try:
                run.monitor_rows = read_csv(monitor_path, MONITOR_COLUMNS)
            except (OSError, UnicodeError, csv.Error, ValueError) as error:
                run.errors.append(f"malformed route-metrics CSV: {error}")
                run.monitor_path = None
        else:
            run.errors.append("route-metrics CSV is missing")
        if log_path is not None:
            run.summary, run.validation = parse_run_log(log_path)
            if run.summary.get("_route_mode") != mode.lower():
                run.errors.append(
                    "console log route mode is missing or does not match this run directory"
                )
            logged_directory = run.summary.get("_trace_directory")
            if not logged_directory or not paths_match(logged_directory, directory):
                run.errors.append(
                    "console log trace directory is missing or does not match this run directory"
                )
            if run.validation == "missing":
                run.errors.append("console log has no [TCP validation] PASS or FAIL line")
            if run.summary.get("_runner_exit_code", 0) != 0:
                run.errors.append("console log reports a nonzero simulation process exit code")
            required_summary = (
                "successful_responses",
                "failed_missing_responses",
                "average_rtt_seconds",
                "maximum_rtt_seconds",
                "validated_response_body_bytes",
                "application_response_body_throughput_bits_per_second",
                "simulation_duration_seconds",
                "measured_application_interval_seconds",
            )
            missing_summary = [
                metric for metric in required_summary if run.summary.get(metric) is None
            ]
            if missing_summary:
                run.errors.append(
                    "console log is incomplete; missing summary metrics: "
                    + ", ".join(missing_summary)
                )
            run.complete = (
                not any(
                    error.startswith("console log")
                    for error in run.errors
                )
                and run.validation in {"PASS", "FAIL"}
            )
        else:
            run.errors.append(
                "console log missing; TCP validation and console-only summary metrics "
                "cannot be verified"
            )
        candidates[mode].append(run)

    selected: dict[str, RunData] = {}
    for mode, runs in candidates.items():
        complete_runs = [run for run in runs if run.complete]
        if not complete_runs:
            issues.append(
                f"No complete {mode} run exists with a valid HTTP CSV and matching "
                "console log"
            )
            for run in runs:
                issues.extend(
                    f"{mode} ({run.directory.name}): {error}" for error in run.errors
                )
            continue
        complete_runs.sort(
            key=lambda run: (
                run.http_path.stat().st_mtime,
                run.directory.name,
            ),
            reverse=True,
        )
        selected[mode] = complete_runs[0]
        if len(complete_runs) > 1:
            issues.append(
                f"Found {len(complete_runs)} complete {mode} runs; selected newest "
                f"complete run {complete_runs[0].directory.name}"
            )
        incomplete = [run for run in runs if not run.complete]
        if incomplete:
            issues.append(
                f"Ignored {len(incomplete)} incomplete {mode} run(s); selected only "
                "runs with valid CSV and matching console logs"
            )
    return selected, issues


def add_record(
    records: list[dict[str, str]],
    mode: str,
    metric: str,
    value: Any,
    unit: str,
    status: str,
    source: str,
    scope: str = "whole_run",
    notes: str = "",
) -> None:
    records.append(
        {
            "mode": mode,
            "metric": metric,
            "value": "" if value is None else str(value),
            "unit": unit,
            "status": status,
            "source_file": source,
            "scope": scope,
            "notes": notes,
        }
    )


def summarize_http(run: RunData) -> dict[str, Any]:
    rows = run.http_rows
    completed = [
        row
        for row in rows
        if row.get("event", "").strip() == "request_response"
        and row.get("status", "").strip().lower() == "validated"
    ]
    rtts = [number(row.get("rtt_seconds")) for row in completed]
    valid_rtts = [value for value in rtts if value is not None]
    response_bytes = [
        number(row.get("response_bytes")) for row in completed
    ]
    valid_bytes = [value for value in response_bytes if value is not None]
    statuses = {
        row.get("status", "").strip().lower()
        for row in rows
        if row.get("event", "").strip() == "request_response"
    }
    invalid_status_rows = sum(
        1
        for row in rows
        if row.get("event", "").strip() == "request_response"
        and row.get("status", "").strip().lower() not in {"validated"}
    )
    timestamps = [
        number(row.get("timestamp_seconds"))
        for row in rows
        if row.get("event", "").strip() == "request_response"
    ]
    valid_timestamps = [value for value in timestamps if value is not None]
    return {
        "successful_responses": len(completed),
        "failed_csv_rows": invalid_status_rows,
        "rtt_count": len(valid_rtts),
        "average_rtt_seconds": (
            sum(valid_rtts) / len(valid_rtts) if valid_rtts else None
        ),
        "maximum_rtt_seconds": max(valid_rtts) if valid_rtts else None,
        "validated_response_body_bytes": (
            int(sum(valid_bytes))
            if len(valid_bytes) == len(completed)
            and all(value.is_integer() for value in valid_bytes)
            else (sum(valid_bytes) if len(valid_bytes) == len(completed) else None)
        ),
        "request_csv_time_span_seconds": (
            max(valid_timestamps) - min(valid_timestamps)
            if len(valid_timestamps) >= 2
            else None
        ),
        "statuses": statuses,
        "completed_rows": len(
            [row for row in rows if row.get("event", "").strip() == "request_response"]
        ),
    }


def build_records(
    runs: dict[str, RunData], discovery_issues: list[str]
) -> tuple[list[dict[str, str]], dict[str, dict[str, Any]], list[str]]:
    records: list[dict[str, str]] = []
    run_summaries: dict[str, dict[str, Any]] = {}
    issues = list(discovery_issues)
    for mode in MODES:
        run = runs.get(mode)
        if run is None:
            continue
        http_summary = summarize_http(run)
        run_summaries[mode] = http_summary
        issues.extend(f"{mode} ({run.directory.name}): {item}" for item in run.errors)
        http_source = str(run.http_path)
        log_source = str(run.log_path) if run.log_path else "unavailable"

        for metric, value, unit, notes in [
            (
                "successful_responses",
                http_summary["successful_responses"],
                "responses",
                "Count of request_response CSV rows with status=validated",
            ),
            (
                "average_rtt_seconds",
                http_summary["average_rtt_seconds"],
                "s",
                f"Mean of {http_summary['rtt_count']} validated HTTP CSV RTT samples",
            ),
            (
                "maximum_rtt_seconds",
                http_summary["maximum_rtt_seconds"],
                "s",
                f"Maximum of {http_summary['rtt_count']} validated HTTP CSV RTT samples",
            ),
            (
                "validated_response_body_bytes",
                http_summary["validated_response_body_bytes"],
                "bytes",
                "Sum of response_bytes on validated request_response rows",
            ),
        ]:
            status = "available" if value is not None else "unavailable"
            add_record(records, mode, metric, value, unit, status, http_source, notes=notes)

        summary_metric_units = {
            "successful_responses": "responses",
            "failed_missing_responses": "responses",
            "average_rtt_seconds": "s",
            "maximum_rtt_seconds": "s",
            "validated_response_body_bytes": "bytes",
            "application_response_body_throughput_bits_per_second": "bit/s",
            "simulation_duration_seconds": "s",
            "measured_application_interval_seconds": "s",
        }
        csv_metric_values = {
            "successful_responses": http_summary["successful_responses"],
            "average_rtt_seconds": http_summary["average_rtt_seconds"],
            "maximum_rtt_seconds": http_summary["maximum_rtt_seconds"],
            "validated_response_body_bytes": http_summary[
                "validated_response_body_bytes"
            ],
        }
        for metric, unit in summary_metric_units.items():
            value = run.summary.get(metric)
            if metric in csv_metric_values:
                csv_value = csv_metric_values[metric]
                if csv_value is not None:
                    csv_record = next(
                        row
                        for row in records
                        if row["mode"] == mode
                        and row["metric"] == metric
                        and row["source_file"] == http_source
                        and row["scope"] == "whole_run"
                    )
                    csv_record["notes"] += (
                        "; console summary cross-check="
                        + ("unavailable" if value is None else str(value))
                    )
                    if value is not None and not math.isclose(
                        float(csv_value), float(value), rel_tol=1e-5, abs_tol=1e-6
                    ):
                        issues.append(
                            f"{mode} ({run.directory.name}): console and CSV values "
                            f"disagree for {metric}"
                        )
                    continue
                if value is not None:
                    notes = "CSV metric unavailable; value parsed from console summary"
                    source = log_source
                    status = "available_console_fallback"
                else:
                    notes = "Unavailable in both HTTP CSV and console summary"
                    source = http_source
                    status = "unavailable"
            else:
                notes = "Parsed from simulator console summary"
                source = log_source
                status = "available" if value is not None else "unavailable"
            add_record(
                records,
                mode,
                metric,
                value,
                unit,
                status,
                source,
                notes=notes,
            )
        add_record(
            records,
            mode,
            "tcp_validation",
            run.validation if run.validation != "missing" else None,
            "status",
            run.validation,
            log_source,
            notes="Requires the simulator [TCP validation] PASS/FAIL line",
        )
        if run.validation != "PASS":
            issues.append(
                f"{mode} ({run.directory.name}): TCP validation is {run.validation}"
            )
        for required_metric in (
            "failed_missing_responses",
            "application_response_body_throughput_bits_per_second",
            "simulation_duration_seconds",
            "measured_application_interval_seconds",
        ):
            if run.summary.get(required_metric) is None:
                issues.append(
                    f"{mode} ({run.directory.name}): console summary metric "
                    f"{required_metric} is missing"
                )
        expected = run.summary.get("expected_responses")
        if expected is not None and expected != http_summary["successful_responses"]:
            issues.append(
                f"{mode} ({run.directory.name}): {http_summary['successful_responses']} "
                f"validated CSV responses do not match {expected} expected responses"
            )
        failed_summary = run.summary.get("failed_missing_responses")
        if (
            expected is not None
            and failed_summary is not None
            and expected
            != http_summary["successful_responses"] + failed_summary
        ):
            issues.append(
                f"{mode} ({run.directory.name}): console success/failure counts do not "
                "sum to expected responses"
            )
        if http_summary["rtt_count"] != http_summary["successful_responses"]:
            issues.append(
                f"{mode} ({run.directory.name}): RTT data is missing or malformed for "
                f"{http_summary['successful_responses'] - http_summary['rtt_count']} "
                "validated response rows"
            )
        if http_summary["validated_response_body_bytes"] is None:
            issues.append(
                f"{mode} ({run.directory.name}): validated response-body byte data is "
                "missing or malformed"
            )
        if run.validation == "PASS" and expected is None:
            issues.append(
                f"{mode} ({run.directory.name}): PASS log has no expected-response count"
            )

        if mode == "dynamic":
            dynamic_notes = (
                "Whole-run aggregate only; the dynamic CSV RTT samples span the A-to-B "
                "switch and are not attributable to either route"
            )
        else:
            dynamic_notes = "Whole-run aggregate; route-specific attribution is configured mode"
        if mode == "dynamic":
            for metric in (
                "average_rtt_seconds",
                "maximum_rtt_seconds",
                "validated_response_body_bytes",
            ):
                for record in records:
                    if record["mode"] == mode and record["metric"] == metric:
                        record["notes"] = dynamic_notes + "; " + record["notes"]

        if run.monitor_path is None:
            for route in ("A", "B"):
                for metric, unit in [
                    ("weighted_route_cost", "normalized_cost"),
                    ("monitored_delay_rtt", "ms"),
                    ("monitored_packet_loss_rate", "fraction"),
                    ("queue_occupancy_ratio", "ratio"),
                    ("router_hops", "hops"),
                ]:
                    add_record(
                        records,
                        mode,
                        metric,
                        None,
                        unit,
                        "unavailable",
                        "unavailable",
                        scope=f"route_{route}",
                        notes="Route metrics CSV missing or unreadable",
                    )
        else:
            monitor_source = str(run.monitor_path)
            by_route: dict[str, list[dict[str, str]]] = {"A": [], "B": []}
            for row in run.monitor_rows:
                label = row.get("route_label", "").strip()
                if label in by_route:
                    by_route[label].append(row)
            for route in ("A", "B"):
                route_rows = by_route[route]
                if not route_rows:
                    issues.append(
                        f"{mode} ({run.directory.name}): route metrics missing route {route}"
                    )
                    continue
                route_rows.sort(key=lambda row: number(row.get("timestamp_seconds")) or -1.0)
                latest = route_rows[-1]
                timestamp = latest.get("timestamp_seconds", "")
                prefix = f"Latest monitor sample at t={timestamp} s; "
                for column, metric, unit in [
                    ("weighted_cost", "weighted_route_cost", "normalized_cost"),
                    ("delay_rtt_ms", "monitored_delay_rtt", "ms"),
                    ("packet_loss_rate", "monitored_packet_loss_rate", "fraction"),
                    ("queue_occupancy_ratio", "queue_occupancy_ratio", "ratio"),
                    ("router_hops", "router_hops", "hops"),
                ]:
                    value = number(latest.get(column))
                    valid_column = {
                        "delay_rtt_ms": "delay_valid",
                        "packet_loss_rate": "loss_valid",
                        "queue_occupancy_ratio": "queue_valid",
                        "router_hops": "hop_valid",
                    }.get(column)
                    if value is None:
                        status = "unavailable"
                    elif column == "weighted_cost":
                        status = (
                            "available_complete"
                            if latest.get("measurement_validity", "").lower() == "complete"
                            else "invalid_incomplete_measurements"
                        )
                    elif valid_column and latest.get(valid_column, "").lower() != "true":
                        status = "invalid"
                    else:
                        status = "available"
                    if column == "weighted_cost":
                        notes = prefix + (
                            "complete route-specific measurements"
                            if status == "available_complete"
                            else "cost is invalid for comparison when measurements are partial; "
                            "fallback inputs are not evidence"
                        )
                    else:
                        notes = prefix + (
                            "Route-labelled monitor sample; instantaneous values are not "
                            "whole-run aggregates"
                        )
                    if mode == "dynamic" and column in {
                        "delay_rtt_ms",
                        "packet_loss_rate",
                    }:
                        notes += "; TCP cumulative counters are unavailable for route attribution after switching"
                    add_record(
                        records,
                        mode,
                        metric,
                        value,
                        unit,
                        status,
                        monitor_source,
                        scope=f"route_{route}",
                        notes=notes,
                    )
            switch_time = run.summary.get("dynamic_switch_time_seconds")
            if mode == "dynamic":
                add_record(
                    records,
                    mode,
                    "dynamic_switch_time_seconds",
                    switch_time,
                    "s",
                    "available" if switch_time is not None else "unavailable",
                    log_source,
                    scope="whole_run",
                    notes=(
                        "Parsed from actual DYNAMIC ROUTE SWITCH log"
                        if switch_time is not None
                        else "No actual switch log found"
                    ),
                )
    return records, run_summaries, issues


def metric_lookup(records: list[dict[str, str]], mode: str, metric: str) -> Any:
    for record in records:
        if record["mode"] == mode and record["metric"] == metric:
            if record["status"] not in {
                "available",
                "available_complete",
                "available_console_fallback",
            }:
                return None
            return number(record["value"])
    return None


def write_comparison_csv(path: Path, records: list[dict[str, str]]) -> None:
    fields = [
        "mode",
        "metric",
        "value",
        "unit",
        "status",
        "source_file",
        "scope",
        "notes",
    ]
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)


def best_results(records: list[dict[str, str]]) -> list[str]:
    comparisons = [
        ("successful_responses", "maximum", "more successful responses"),
        ("failed_missing_responses", "minimum", "fewer failed/missing responses"),
        ("average_rtt_seconds", "minimum", "lower average RTT"),
        ("maximum_rtt_seconds", "minimum", "lower maximum RTT"),
        ("validated_response_body_bytes", "maximum", "more validated response bytes"),
        (
            "application_response_body_throughput_bits_per_second",
            "maximum",
            "higher application response-body throughput",
        ),
        ("simulation_duration_seconds", "minimum", "shorter simulation duration"),
        (
            "measured_application_interval_seconds",
            "minimum",
            "shorter measured application interval",
        ),
    ]
    output: list[str] = []
    for metric, direction, description in comparisons:
        values = {
            mode: metric_lookup(records, mode, metric)
            for mode in MODES
        }
        measured = {mode: value for mode, value in values.items() if value is not None}
        if not measured:
            output.append(f"- {metric}: unavailable for all selected runs.")
            continue
        target = (max if direction == "maximum" else min)(measured.values())
        winners = [mode for mode, value in measured.items() if math.isclose(value, target)]
        winner_text = ", ".join(winners)
        output.append(
            f"- {metric}: {winner_text} ({description}; {target:g}). "
            f"Measured modes: {', '.join(measured)}."
        )
    valid_costs = [
        record
        for record in records
        if record["metric"] == "weighted_route_cost"
        and record["status"] == "available_complete"
    ]
    measured_route_costs: dict[str, dict[str, str]] = {}
    for route in ("A", "B"):
        candidates = [
            record for record in valid_costs if record["scope"] == f"route_{route}"
        ]
        if candidates:
            measured_route_costs[route] = min(
                candidates,
                key=lambda record: number(record["value"]) or 0.0,
            )
    if set(measured_route_costs) == {"A", "B"}:
        best = min(
            measured_route_costs.values(),
            key=lambda record: number(record["value"]) or 0.0,
        )
        output.append(
            f"- weighted_route_cost: Route {best['scope'][-1]} in {best['mode']} "
            f"({best['value']} normalized cost; complete route-specific measurements). "
            "Route A and B costs came from their respective fixed-route runs."
        )
    else:
        output.append(
            "- weighted_route_cost: no best route reported; all available costs are based "
            "on incomplete measurements or unavailable."
        )
    output.append(
        "- No single route is declared universally best; compare only the measured metrics "
        "above. Application response-body throughput is not physical-link throughput."
    )
    return output


def create_charts(results_dir: Path, records: list[dict[str, str]]) -> list[str]:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return [
            "Charts not generated: Matplotlib is not installed. Install it with: "
            f'"{sys.executable}" -m pip install matplotlib'
        ]

    chart_specs = [
        (
            "route-comparison-rtt.png",
            "average_rtt_seconds",
            "Average RTT (s)",
            "Average HTTP RTT",
        ),
        (
            "route-comparison-throughput.png",
            "application_response_body_throughput_bits_per_second",
            "Application response-body throughput (bit/s)",
            "Application response-body throughput",
        ),
        (
            "route-comparison-responses.png",
            "successful_responses",
            "Successful HTTP responses",
            "Successful responses",
        ),
    ]
    notices: list[str] = []
    for filename, metric, ylabel, title in chart_specs:
        values = [metric_lookup(records, mode, metric) for mode in MODES]
        if all(value is None for value in values):
            notices.append(f"Chart not generated for {metric}: no measured values.")
            continue
        figure, axis = plt.subplots(figsize=(7.5, 4.5))
        positions = [index for index, value in enumerate(values) if value is not None]
        heights = [values[index] for index in positions]
        axis.bar([MODES[index] for index in positions], heights, color="#3274a1")
        axis.set_title(title)
        axis.set_xlabel("Routing mode")
        axis.set_ylabel(ylabel)
        axis.grid(axis="y", linestyle=":", alpha=0.5)
        figure.tight_layout()
        figure.savefig(results_dir / filename, dpi=160)
        plt.close(figure)
        notices.append(f"Chart generated: {results_dir / filename}")
    return notices


def msys_path(path: Path) -> str:
    resolved = str(path.resolve())
    drive, tail = os.path.splitdrive(resolved)
    if not drive:
        return resolved.replace("\\", "/")
    return "/" + drive[0].lower() + tail.replace("\\", "/")


def find_msys_bash(explicit: Path | None) -> Path:
    candidates = [
        explicit,
        Path(r"C:\msys64\usr\bin\bash.exe"),
        Path(r"C:\msys2\usr\bin\bash.exe"),
    ]
    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            return candidate
    discovered = shutil.which("bash")
    if discovered:
        return Path(discovered)
    raise FileNotFoundError(
        "MSYS2 bash was not found. Pass --bash C:\\msys64\\usr\\bin\\bash.exe."
    )


def run_simulation(
    mode: str,
    results_dir: Path,
    switch_time: float,
    bash_path: Path | None,
) -> int:
    project_root = Path(__file__).resolve().parent.parent
    ns3_root = project_root / "ns3-download"
    ns3_launcher = ns3_root / "ns3"
    if not ns3_launcher.is_file():
        print(f"ERROR: NS-3 launcher not found: {ns3_launcher}", file=sys.stderr)
        return 127
    try:
        bash = find_msys_bash(bash_path)
    except FileNotFoundError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 127

    python_bin = msys_path(Path(sys.executable).parent)
    ns3_msys_path = msys_path(ns3_root)
    arguments = f"--route={mode}"
    if mode == "dynamic":
        arguments += f" --dynamicSwitchTime={switch_time:g}"
    command = (
        f'export PATH="/mingw64/bin:{python_bin}:$PATH"; '
        f"cd {shlex.quote(ns3_msys_path)} && "
        f"./ns3 run scratch/webflow-topology -- {arguments}"
    )
    print(f"\n=== Running Route {mode} ===", flush=True)
    captured: list[str] = []
    try:
        process = subprocess.Popen(
            [str(bash), "-lc", command],
            cwd=str(ns3_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except OSError as error:
        print(f"ERROR: Could not start MSYS2 simulation: {error}", file=sys.stderr)
        return 127

    assert process.stdout is not None
    for line in process.stdout:
        captured.append(line)
        sys.stdout.write(line)
        sys.stdout.flush()
    exit_code = process.wait()
    captured.append(f"\n[analysis runner] process_exit_code={exit_code}\n")

    output = "".join(captured)
    trace_match = re.search(r"^Trace directory:\s*(.+?)\s*$", output, re.MULTILINE)
    if trace_match:
        run_directory = Path(trace_match.group(1).strip())
        if not run_directory.is_absolute():
            run_directory = ns3_root / run_directory
        try:
            run_directory = run_directory.resolve()
            run_directory.relative_to(results_dir.resolve())
        except (OSError, ValueError):
            run_directory = None
    else:
        run_directory = None
    if run_directory is None or not run_directory.is_dir():
        fallback = results_dir / f"unmatched-{mode}-{time.time_ns()}.log"
        fallback.write_text(output, encoding="utf-8")
        print(
            f"WARNING: no matching run directory was reported; captured output saved to {fallback}",
            file=sys.stderr,
        )
        return exit_code or 1

    log_path = run_directory / "simulation.log"
    log_path.write_text(output, encoding="utf-8")
    print(f"Saved complete console log: {log_path}")
    return exit_code


def run_all_simulations(
    results_dir: Path, switch_time: float, bash_path: Path | None
) -> int:
    results_dir.mkdir(parents=True, exist_ok=True)
    exit_codes: list[tuple[str, int]] = []
    for mode in MODES:
        exit_codes.append((mode, run_simulation(mode, results_dir, switch_time, bash_path)))
    for mode, code in exit_codes:
        if code != 0:
            print(f"ERROR: Route {mode} simulation exited with code {code}", file=sys.stderr)
    return next((code for _, code in exit_codes if code != 0), 0)


def write_report(
    path: Path,
    results_dir: Path,
    runs: dict[str, RunData],
    records: list[dict[str, str]],
    run_summaries: dict[str, dict[str, Any]],
    issues: list[str],
    chart_notices: list[str],
) -> None:
    lines = [
        "WebFlow Route Performance Comparison",
        "=====================================",
        f"Results directory: {results_dir}",
        "",
        "Selected runs",
        "-------------",
    ]
    for mode in MODES:
        run = runs.get(mode)
        if run is None:
            lines.append(f"- {mode}: NO COMPLETE RUN")
            continue
        lines.append(
            f"- {mode}: {run.directory.name}; complete={run.complete}; "
            f"TCP validation={run.validation}; "
            f"HTTP CSV={run.http_path.name}; "
            f"route metrics CSV={run.monitor_path.name if run.monitor_path else 'missing'}; "
            f"console log={run.log_path.name if run.log_path else 'missing'}"
        )
    lines.extend(["", "Whole-run comparison", "---------------------"])
    main_metrics = [
        ("successful_responses", "responses"),
        ("failed_missing_responses", "responses"),
        ("average_rtt_seconds", "s"),
        ("maximum_rtt_seconds", "s"),
        ("validated_response_body_bytes", "bytes"),
        ("application_response_body_throughput_bits_per_second", "bit/s"),
        ("simulation_duration_seconds", "s"),
        ("measured_application_interval_seconds", "s"),
    ]
    for metric, unit in main_metrics:
        lines.append(f"\n{metric} ({unit})")
        for mode in MODES:
            matching = [
                row for row in records
                if row["mode"] == mode and row["metric"] == metric
                and row["scope"] == "whole_run"
            ]
            if not matching:
                lines.append(f"  {mode}: unavailable (no selected run)")
                continue
            row = matching[0]
            value = row["value"] if row["value"] else "unavailable"
            lines.append(
                f"  {mode}: {value} [{row['status']}] — {row['source_file']}"
            )
            if row["notes"]:
                lines.append(f"    {row['notes']}")
    lines.extend(["", "Route monitoring", "----------------"])
    for mode in MODES:
        lines.append(f"{mode}:")
        mode_rows = [
            row for row in records
            if row["mode"] == mode and row["scope"].startswith("route_")
        ]
        if not mode_rows:
            lines.append("  Route-level metrics unavailable.")
            continue
        for row in mode_rows:
            lines.append(
                f"  {row['scope']} {row['metric']}: "
                f"{row['value'] or 'unavailable'} {row['unit']} [{row['status']}]"
            )
            lines.append(f"    Source: {row['source_file']}")
            lines.append(f"    {row['notes']}")
    dynamic_switch = next(
        (
            row for row in records
            if row["mode"] == "dynamic" and row["metric"] == "dynamic_switch_time_seconds"
        ),
        None,
    )
    lines.extend(["", "Dynamic-route attribution", "-------------------------"])
    if dynamic_switch and dynamic_switch["value"]:
        lines.append(
            f"Observed A -> B switch at {dynamic_switch['value']} s from "
            f"{dynamic_switch['source_file']}."
        )
    else:
        lines.append("No actual dynamic switch timestamp was found in the console log.")
    lines.append(
        "Dynamic whole-run RTT and response metrics are aggregate measurements across the "
        "switch; they are not assigned to Route A or Route B. Route-specific monitor entries "
        "are reported only as their own samples."
    )
    lines.extend(["", "Best measured results", "---------------------"])
    lines.extend(best_results(records))
    lines.extend(["", "Validation and data-quality findings", "------------------------------------"])
    if issues:
        lines.extend(f"- {issue}" for issue in issues)
    else:
        lines.append("- No missing files, malformed CSVs, or incomplete TCP validation detected.")
    lines.extend(["", "Charts", "------"])
    lines.extend(f"- {notice}" for notice in chart_notices)
    lines.extend(
        [
            "",
            "Metric sources",
            "--------------",
            "HTTP request CSV rows provide validated response counts, RTT samples, and "
            "validated response-body byte totals.",
            "Console logs provide authoritative TCP validation, failed/missing response "
            "counts, throughput, simulation duration, application interval, and switch time.",
            "Use --run-simulations to run all three modes while streaming output and saving "
            "the complete per-run console output as simulation.log. The simulator exit code "
            "is preserved and recorded in that log.",
            "Route metrics CSV files provide route-labelled periodic measurements. Weighted "
            "costs with partial measurements are retained as invalid/informational, not treated "
            "as conclusive comparisons.",
            "Application response-body throughput is an application-layer metric, not "
            "physical-link throughput.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare the newest WebFlow Route A, Route B, and Dynamic runs."
    )
    default_results = (
        Path(__file__).resolve().parent.parent
        / "results"
        / "webflow-route-tests"
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=default_results,
        help="Directory containing run-* folders (default: project results directory)",
    )
    parser.add_argument(
        "--run-simulations",
        action="store_true",
        help="Run Route A, Route B, and Dynamic, capturing complete per-run logs before analysis",
    )
    parser.add_argument(
        "--dynamic-switch-time",
        type=float,
        default=3.0,
        help="Dynamic mode switch time in seconds (default: 3.0)",
    )
    parser.add_argument(
        "--bash",
        type=Path,
        default=None,
        help="MSYS2 bash executable (defaults to C:\\msys64\\usr\\bin\\bash.exe)",
    )
    args = parser.parse_args()
    results_dir = args.results_dir.resolve()
    run_exit_code = 0
    if args.run_simulations:
        if not math.isfinite(args.dynamic_switch_time) or args.dynamic_switch_time <= 0:
            parser.error("--dynamic-switch-time must be a finite positive number")
        run_exit_code = run_all_simulations(
            results_dir,
            args.dynamic_switch_time,
            args.bash,
        )
    runs, discovery_issues = discover_runs(results_dir)
    records, summaries, issues = build_records(runs, discovery_issues)
    csv_path = results_dir / "route-comparison.csv"
    report_path = results_dir / "route-comparison.txt"
    results_dir.mkdir(parents=True, exist_ok=True)
    write_comparison_csv(csv_path, records)
    chart_notices = create_charts(results_dir, records)
    write_report(
        report_path,
        results_dir,
        runs,
        records,
        summaries,
        issues,
        chart_notices,
    )
    print(f"Comparison CSV: {csv_path}")
    print(f"Comparison report: {report_path}")
    for mode in MODES:
        run = runs.get(mode)
        if run is None:
            print(f"{mode}: NO COMPLETE RUN")
        else:
            print(
                f"{mode}: {run.directory.name}; complete={run.complete}; "
                f"TCP validation={run.validation}; "
                f"{summaries[mode]['successful_responses']} validated CSV responses"
            )
    for notice in chart_notices:
        print(notice)
    for issue in issues:
        print(f"WARNING: {issue}", file=sys.stderr)
    selected_runs_pass = all(
        mode in runs and runs[mode].validation == "PASS" for mode in MODES
    )
    return 0 if run_exit_code == 0 and selected_runs_pass else (run_exit_code or 2)


if __name__ == "__main__":
    raise SystemExit(main())
