import csv
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = (PROJECT_ROOT / "results" / "webflow-route-tests").resolve()
NS3_ROOT = PROJECT_ROOT / "ns3-download"
NS3_LAUNCHER = NS3_ROOT / "ns3"
SIMULATION_FILE = NS3_ROOT / "scratch" / "webflow-topology.cc"
ANALYSIS_FILE = PROJECT_ROOT / "analysis" / "compare_routes.py"
MSYS_BASH = Path(r"C:\msys64\usr\bin\bash.exe")
JOB_TIMEOUT_SECONDS = 900
RUN_PATTERN = re.compile(r"^run-(A|B|dynamic|global)-(\d+)$")
SAFE_RESULT_FILE = re.compile(
    r"^(?:http-requests(?:-\d+)?\.csv|route-metrics-\d+\.csv|"
    r"simulation(?:\.stderr)?\.log|topology-(?:nodes|links)\.csv|"
    r"settings\.json|run-status\.json|route-verification\.txt|[a-z0-9-]+\.pcap)$"
)
SUMMARY_PATTERNS = {
    "successful_responses": re.compile(r"^Successful responses:\s*(\d+)\s*$"),
    "failed_missing_responses": re.compile(r"^Failed/missing responses:\s*(\d+)\s*$"),
    "average_rtt_seconds": re.compile(r"^Average RTT:\s*([^\s]+)\s+s\s*$"),
    "maximum_rtt_seconds": re.compile(r"^Maximum RTT:\s*([^\s]+)\s+s\s*$"),
    "validated_response_body_bytes": re.compile(
        r"^Validated response-body bytes:\s*(\d+)\s+bytes\s*$"
    ),
    "application_response_body_throughput_bits_per_second": re.compile(
        r"^Application response-body throughput:\s*([^\s]+)\s+bit/s"
    ),
    "simulation_duration_seconds": re.compile(
        r"^Simulation duration:\s*([^\s]+)\s+s\s*$"
    ),
    "measured_application_interval_seconds": re.compile(
        r"^Measured application interval:\s*([^\s]+)\s+s\s*$"
    ),
    "configured_clients": re.compile(r"^Configured clients:\s*(\d+)\s*$"),
    "requests_per_client": re.compile(r"^Requests per client:\s*(\d+)\s*$"),
    "requested_node_count": re.compile(r"^Requested node count:\s*(\d+)\s*$"),
    "actual_node_count": re.compile(r"^Actual nodes created:\s*(\d+)\s*$"),
}

app = FastAPI(
    title="WebFlow API",
    description="HTTP Traffic and Network Performance Analyzer",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

_job_lock = threading.Lock()
_current_job: dict | None = None


class SimulationSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route: Literal["A", "B", "dynamic"]
    node_count: int = Field(default=7, ge=7, le=254)
    source_node_index: int = Field(default=0, ge=0, le=253)
    destination_node_index: int = Field(default=6, ge=0, le=253)
    traffic_load: Literal["low", "medium", "high"] = "low"
    http_client_count: int = Field(default=2, ge=1, le=20)
    http_request_count: int = Field(default=3, ge=1, le=50)
    http_request_interval: float = Field(default=2.0, ge=0.05, le=30)
    http_request_size: int = Field(default=128, ge=1, le=1_000_000)
    http_response_size: int = Field(default=128, ge=1, le=1_000_000)
    dynamic_switch_time: float = Field(default=3.0, gt=0, le=120)
    weight_delay: float = Field(default=0.4, ge=0, le=1000)
    weight_loss: float = Field(default=0.3, ge=0, le=1000)
    weight_queue: float = Field(default=0.2, ge=0, le=1000)
    weight_hops: float = Field(default=0.1, ge=0, le=1000)
    monitor_interval: float = Field(default=1.0, ge=0.1, le=60)

    @model_validator(mode="after")
    def validate_route_settings(self):
        if self.source_node_index != 0 or self.destination_node_index != 6:
            raise ValueError(
                "HTTP routing currently supports only Client node 0 as source and "
                "Server node 6 as destination."
            )
        for name in (
            "http_request_interval",
            "dynamic_switch_time",
            "weight_delay",
            "weight_loss",
            "weight_queue",
            "weight_hops",
            "monitor_interval",
        ):
            if not math.isfinite(getattr(self, name)):
                raise ValueError(f"{name} must be a finite number.")
        if not any(
            (self.weight_delay, self.weight_loss, self.weight_queue, self.weight_hops)
        ):
            raise ValueError("At least one route-cost weight must be greater than zero.")
        if self.route == "dynamic":
            last_client_start = 0.75 + (self.http_client_count - 1) * 0.05
            transfer_bytes = (
                self.http_client_count
                * self.http_request_count
                * (self.http_request_size + self.http_response_size + 512)
            )
            expected_traffic_end = (
                last_client_start
                + self.http_request_interval * (self.http_request_count - 1)
                + transfer_bytes * 8 / 10_000_000
                + 0.1
            )
            if not 0.75 < self.dynamic_switch_time < expected_traffic_end:
                raise ValueError(
                    "Dynamic switch time must fall inside the expected HTTP traffic "
                    f"window (0.75 to {expected_traffic_end:.3f} seconds) for these settings."
                )
        return self


class TopologySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_count: int = Field(ge=7, le=254)
    source_node_index: int = Field(default=0, ge=0, le=253)
    destination_node_index: int = Field(default=6, ge=0, le=253)

    @model_validator(mode="after")
    def validate_endpoints(self):
        if self.source_node_index != 0 or self.destination_node_index != 6:
            raise ValueError(
                "The available traffic path supports Client node 0 to Server node 6 only."
            )
        return self


class ComparisonSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dynamic_switch_time: float = Field(default=3.0, gt=0, le=120)

    @model_validator(mode="after")
    def validate_switch_time(self):
        if not math.isfinite(self.dynamic_switch_time):
            raise ValueError("Dynamic switch time must be a finite number.")
        expected_end = 0.8 + 2 * 2 + 6 * (128 + 128 + 512) * 8 / 10_000_000 + 0.1
        if not 0.75 < self.dynamic_switch_time < expected_end:
            raise ValueError(
                "Dynamic switch time for the comparison defaults must fall inside "
                f"the expected HTTP traffic window (0.75 to {expected_end:.3f} seconds)."
            )
        return self


def _inside_results(path: Path) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(RESULTS_DIR)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Result file not found.") from error
    return resolved


def _number(value: str | int | float | None):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _parse_log(path: Path) -> dict:
    data: dict = {
        "tcp_validation": "missing",
        "dynamic_switch_time_seconds": None,
    }
    try:
        path = _inside_results(path)
    except HTTPException:
        return data
    if not path.is_file():
        return data
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return data
    for line in text.splitlines():
        stripped = line.strip()
        for name, pattern in SUMMARY_PATTERNS.items():
            match = pattern.match(stripped)
            if match:
                value = match.group(1)
                data[name] = int(value) if name in {
                    "successful_responses",
                    "failed_missing_responses",
                    "validated_response_body_bytes",
                    "configured_clients",
                    "requests_per_client",
                    "requested_node_count",
                    "actual_node_count",
                } else _number(value)
    if re.search(r"^\[TCP validation\]\s+FAIL\b", text, re.MULTILINE):
        data["tcp_validation"] = "FAIL"
    elif re.search(r"^\[TCP validation\]\s+PASS\b", text, re.MULTILINE):
        data["tcp_validation"] = "PASS"
    switch = re.search(
        r"DYNAMIC ROUTE SWITCH: A -> B\b.*?timestamp_seconds=([0-9.eE+-]+)",
        text,
    )
    if switch:
        data["dynamic_switch_time_seconds"] = _number(switch.group(1))
    mode = re.search(
        r"^Route mode:\s*(global|A|B|dynamic)\s*$",
        text,
        re.MULTILINE | re.IGNORECASE,
    )
    trace = re.search(r"^Trace directory:\s*(.+?)\s*$", text, re.MULTILINE)
    exit_code = re.search(
        r"^\[analysis runner\]\s+process_exit_code=(-?\d+)\s*$",
        text,
        re.MULTILINE,
    )
    if mode:
        data["_route_mode"] = mode.group(1).lower()
    if trace:
        data["_trace_directory"] = trace.group(1).strip()
    if exit_code:
        data["_runner_exit_code"] = int(exit_code.group(1))
    data["_topology_only"] = bool(re.search(
        r"^Topology creation complete; simulation applications were not started\.\s*$",
        text,
        re.MULTILINE,
    ))
    return data


def _read_csv(path: Path) -> list[dict]:
    path = _inside_results(path)
    with path.open("r", newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source, strict=True)
        if reader.fieldnames is None:
            raise ValueError("CSV has no header")
        rows = list(reader)
        if any(None in row or any(value is None for value in row.values()) for row in rows):
            raise ValueError("CSV contains a malformed row")
    return rows


def _latest_monitor(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    try:
        rows = _read_csv(path)
    except (OSError, csv.Error, ValueError, HTTPException):
        return []
    latest: dict[str, dict] = {}
    for row in rows:
        route = row.get("route_label")
        timestamp = _number(row.get("timestamp_seconds"))
        if route not in ("A", "B") or timestamp is None:
            continue
        if route not in latest or timestamp >= latest[route]["timestamp_seconds"]:
            latest[route] = {
                "route": route,
                "timestamp_seconds": timestamp,
                "weighted_cost": _number(row.get("weighted_cost")),
                "measurement_validity": row.get("measurement_validity") or "unknown",
                "delay_rtt_ms": _number(row.get("delay_rtt_ms")),
                "packet_loss_rate": _number(row.get("packet_loss_rate")),
                "queue_occupancy_ratio": _number(row.get("queue_occupancy_ratio")),
                "router_hops": _number(row.get("router_hops")),
            }
    return list(latest.values())


def _monitor_series(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    try:
        rows = _read_csv(path)
    except (OSError, csv.Error, ValueError, HTTPException):
        return []
    series = []
    for row in rows:
        timestamp = _number(row.get("timestamp_seconds"))
        route = row.get("route_label")
        if timestamp is None or route not in ("A", "B"):
            continue
        series.append({
            "timestamp_seconds": timestamp,
            "route": route,
            "weighted_cost": _number(row.get("weighted_cost")),
            "measurement_validity": row.get("measurement_validity") or "unknown",
        })
    return series[-1000:]


def _read_topology(directory: Path) -> dict | None:
    _inside_results(directory)
    node_path = _inside_results(directory / "topology-nodes.csv")
    link_path = _inside_results(directory / "topology-links.csv")
    if not node_path.is_file() or not link_path.is_file():
        return None
    try:
        nodes = _read_csv(node_path)
        links = _read_csv(link_path)
    except (OSError, csv.Error, ValueError, HTTPException):
        return None
    node_records = []
    for node in nodes:
        try:
            node_records.append({
                "index": int(node["node_index"]),
                "ns3_id": int(node["ns3_node_id"]),
                "role": node["role"],
                "supported_source": node["supported_source"].lower() == "true",
                "supported_destination": node["supported_destination"].lower() == "true",
                "ip_addresses": [],
            })
        except (KeyError, TypeError, ValueError):
            return None
    if len(node_records) < 7:
        return None
    nodes_by_role = {node["role"].casefold(): node for node in node_records}
    for link in links:
        endpoint_a = nodes_by_role.get((link.get("endpoint_a") or "").casefold())
        endpoint_b = nodes_by_role.get((link.get("endpoint_b") or "").casefold())
        if endpoint_a is None or endpoint_b is None:
            return None
        if link.get("ipv4_a"):
            endpoint_a["ip_addresses"].append(link["ipv4_a"])
        if link.get("ipv4_b"):
            endpoint_b["ip_addresses"].append(link["ipv4_b"])
        link["bandwidth"] = link.get("bandwidth") or None
        link["delay_seconds"] = _number(link.get("delay_seconds"))
    for node in node_records:
        node["ip_address"] = node["ip_addresses"][0] if node["ip_addresses"] else None
    return {
        "node_count": len(node_records),
        "nodes": node_records,
        "links": links,
        "source_node": next(
            (node for node in node_records if node["supported_source"]), None
        ),
        "destination_node": next(
            (node for node in node_records if node["supported_destination"]), None
        ),
        "routing_limitations": (
            "HTTP traffic currently supports only Client node 0 to Server node 6. "
            "Additional nodes are IPv4-connected leaf hosts on R1 and are not transit "
            "members of Route A or Route B."
        ),
    }


def _run_summary(directory: Path) -> dict:
    _inside_results(directory)
    match = RUN_PATTERN.fullmatch(directory.name)
    if not match:
        raise ValueError("Unexpected run directory name")
    mode = match.group(1)
    http_path = directory / "http-requests.csv"
    if not http_path.is_file():
        alternates = sorted(directory.glob("http-requests-*.csv"))
        http_path = alternates[-1] if alternates else http_path
    log_path = directory / "simulation.log"
    log = _parse_log(log_path)
    csv_metrics = {
        "successful_responses": None,
        "average_rtt_seconds": None,
        "maximum_rtt_seconds": None,
        "validated_response_body_bytes": None,
    }
    csv_valid = False
    csv_error = None
    if http_path.is_file():
        try:
            rows = _read_csv(http_path)
            required = {
                "event",
                "response_bytes",
                "rtt_seconds",
                "status",
            }
            if not rows or not required.issubset(rows[0]):
                raise ValueError("CSV is empty or missing required columns")
            validated = [
                row for row in rows
                if row.get("event") == "request_response"
                and row.get("status") == "validated"
            ]
            rtts = [
                value for row in validated
                if (value := _number(row.get("rtt_seconds"))) is not None
            ]
            bytes_received = [
                value for row in validated
                if (value := _number(row.get("response_bytes"))) is not None
            ]
            csv_metrics = {
                "successful_responses": len(validated),
                "average_rtt_seconds": sum(rtts) / len(rtts) if rtts else None,
                "maximum_rtt_seconds": max(rtts) if rtts else None,
                "validated_response_body_bytes": sum(bytes_received),
            }
            csv_valid = True
        except (OSError, csv.Error, ValueError, HTTPException) as error:
            csv_error = str(error)
    trace_matches = False
    if log.get("_trace_directory"):
        try:
            trace_matches = (
                Path(log["_trace_directory"]).resolve()
                == directory.resolve()
            )
        except OSError:
            trace_matches = False
    topology = _read_topology(directory)
    topology_only = log.get("_topology_only", False)
    status_path = _inside_results(directory / "run-status.json")
    try:
        status_record = json.loads(status_path.read_text(encoding="utf-8"))
        run_status = status_record.get("status")
    except (OSError, json.JSONDecodeError, AttributeError):
        run_status = None
    settings_path = _inside_results(directory / "settings.json")
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        if not isinstance(settings, dict):
            settings = None
    except (OSError, json.JSONDecodeError):
        settings = None
    complete = bool(
        (
            topology_only
            and topology is not None
            and log.get("actual_node_count") == topology["node_count"]
        )
        or (
            log.get("_route_mode") == mode.lower()
            and trace_matches
            and log.get("tcp_validation") in ("PASS", "FAIL")
            and csv_valid
        )
    )
    files = []
    for candidate in directory.iterdir():
        if candidate.is_file() and SAFE_RESULT_FILE.fullmatch(candidate.name):
            try:
                candidate = _inside_results(candidate)
                files.append({"name": candidate.name, "size_bytes": candidate.stat().st_size})
            except HTTPException:
                continue
    return {
        "id": directory.name,
        "mode": mode,
        "complete": complete,
        "topology_only": topology_only,
        "status": run_status or (
            "completed" if complete and log.get("tcp_validation") == "PASS" else "failed"
        ),
        "node_count": log.get("actual_node_count") or log.get("requested_node_count"),
        "settings": settings,
        "tcp_validation": log.get("tcp_validation", "missing"),
        "http_csv": http_path.name if http_path.is_file() else None,
        "csv_error": csv_error,
        "metrics": {
            **csv_metrics,
            "failed_missing_responses": log.get("failed_missing_responses"),
            "application_response_body_throughput_bits_per_second": log.get(
                "application_response_body_throughput_bits_per_second"
            ),
            "simulation_duration_seconds": log.get("simulation_duration_seconds"),
            "measured_application_interval_seconds": log.get(
                "measured_application_interval_seconds"
            ),
            "dynamic_switch_time_seconds": log.get("dynamic_switch_time_seconds"),
        },
        "monitoring": _latest_monitor(
            next(directory.glob("route-metrics-*.csv"), directory / "missing.csv")
        ),
        "monitor_series": _monitor_series(
            next(directory.glob("route-metrics-*.csv"), directory / "missing.csv")
        ),
        "topology": topology,
        "files": sorted(files, key=lambda item: item["name"]),
        "updated_at": datetime.fromtimestamp(
            directory.stat().st_mtime, timezone.utc
        ).isoformat(),
    }


def _simulation_events(job: dict, output: str) -> list[dict]:
    events = []
    trace_directory = None
    if job.get("run_directory"):
        trace_directory = RESULTS_DIR / job["run_directory"]
    else:
        match = re.search(
            r"^Trace directory:\s*(.+?)\s*$",
            output,
            re.MULTILINE,
        )
        if match:
            trace_directory = Path(match.group(1).strip())

    if trace_directory is not None:
        try:
            trace_directory = _inside_results(trace_directory)
            event_path = _inside_results(trace_directory / "simulation-events.jsonl")
            if event_path.is_file():
                with event_path.open("rb") as source:
                    offset = max(0, event_path.stat().st_size - 262144)
                    source.seek(offset)
                    text = source.read().decode("utf-8", errors="replace")
                if offset:
                    text = text.partition("\n")[2]
                for line in text.splitlines()[-250:]:
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(event, dict) and event.get("event_type") == "packet":
                        event["event_id"] = (
                            f"packet-{event.get('timestamp_seconds')}-"
                            f"{event.get('packet_id')}-{event.get('current_node')}-"
                            f"{event.get('next_hop')}"
                        )
                        events.append(event)
        except (OSError, HTTPException, ValueError):
            events = []

    switch_match = re.search(
        r"DYNAMIC ROUTE SWITCH: A -> B\b.*?timestamp_seconds=([0-9.eE+-]+)",
        output,
    )
    switch_time = _number(switch_match.group(1)) if switch_match else None
    mode = (job.get("mode") or "").lower()
    for line in output.splitlines():
        if not line.startswith("[HTTP] "):
            continue
        fields = dict(re.findall(r"([a-z_]+)=([^\s]+)", line))
        timestamp = _number(fields.get("timestamp_seconds"))
        event = fields.get("event", "")
        if timestamp is None or not event:
            continue
        is_response = "response" in event
        is_server = event.startswith("server_")
        current_node = "Server" if is_server else "Client"
        source, destination = (
            ("Server", "Client") if is_response else ("Client", "Server")
        )
        active_route = mode.upper() if mode in ("a", "b") else (
            "B" if switch_time is not None and timestamp >= switch_time else "A"
        )
        events.append({
            "event_id": (
                f"http-{timestamp}-{fields.get('client_id')}-"
                f"{fields.get('request_id')}-{event}"
            ),
            "timestamp_seconds": timestamp,
            "event_type": "http_response" if is_response else "http_request",
            "stage": event,
            "source": source,
            "destination": destination,
            "current_node": current_node,
            "next_hop": None,
            "route": active_route,
            "client_id": _number(fields.get("client_id")),
            "request_id": _number(fields.get("request_id")),
            "validation": fields.get("validation"),
        })

    events.sort(key=lambda event: event["timestamp_seconds"])
    return events[-300:]


def _latest_runs() -> dict[str, dict | None]:
    found: dict[str, list[tuple[float, Path]]] = {
        "A": [],
        "B": [],
        "dynamic": [],
        "global": [],
    }
    if not RESULTS_DIR.is_dir():
        return {mode: None for mode in found}
    for directory in RESULTS_DIR.iterdir():
        match = RUN_PATTERN.fullmatch(directory.name)
        if match and directory.is_dir():
            try:
                _inside_results(directory)
                found[match.group(1)].append((directory.stat().st_mtime, directory))
            except (OSError, HTTPException):
                continue
    latest: dict[str, dict | None] = {}
    for mode, directories in found.items():
        directories.sort(key=lambda item: (item[0], item[1].name), reverse=True)
        latest[mode] = None
        for _, directory in directories:
            try:
                candidate = _run_summary(directory)
            except (OSError, ValueError):
                continue
            if candidate["topology_only"]:
                continue
            if latest[mode] is None or candidate["complete"]:
                latest[mode] = candidate
            if candidate["complete"]:
                break
    return latest


def _latest_topology() -> dict | None:
    if not RESULTS_DIR.is_dir():
        return None
    candidates = []
    for directory in RESULTS_DIR.iterdir():
        if not directory.is_dir() or not RUN_PATTERN.fullmatch(directory.name):
            continue
        if (directory / "topology-nodes.csv").is_file():
            candidates.append(directory)
    candidates.sort(key=lambda directory: directory.stat().st_mtime, reverse=True)
    for directory in candidates:
        topology = _read_topology(directory)
        if topology is not None:
            return {
                "run_id": directory.name,
                **topology,
            }
    return None


def _run_history(limit: int = 100) -> list[dict]:
    if not RESULTS_DIR.is_dir():
        return []
    directories = [
        directory for directory in RESULTS_DIR.iterdir()
        if directory.is_dir() and RUN_PATTERN.fullmatch(directory.name)
    ]
    directories.sort(key=lambda directory: directory.stat().st_mtime, reverse=True)
    history = []
    for directory in directories:
        try:
            summary = _run_summary(directory)
        except (OSError, ValueError, HTTPException):
            continue
        if summary["tcp_validation"] != "missing":
            history.append(summary)
        if len(history) >= limit:
            break
    return history


def _comparison_rows() -> list[dict]:
    path = RESULTS_DIR / "route-comparison.csv"
    if not path.is_file():
        return []
    try:
        return _read_csv(path)
    except (OSError, csv.Error, ValueError, HTTPException):
        return []


def _comparison_report() -> dict:
    csv_path = RESULTS_DIR / "route-comparison.csv"
    report_path = RESULTS_DIR / "route-comparison.txt"
    try:
        report = (
            _inside_results(report_path).read_text(encoding="utf-8", errors="replace")
            if report_path.is_file()
            else None
        )
    except (OSError, HTTPException):
        report = None
    return {
        "csv_available": csv_path.is_file(),
        "report_available": report_path.is_file(),
        "report": report,
        "comparison": _comparison_rows(),
        "runs": _latest_runs(),
        "run_history": _run_history(),
        "latest_report": _latest_traffic_run(),
        "results_directory": str(RESULTS_DIR),
    }


def _latest_traffic_run() -> dict | None:
    completed = [
        run for run in _latest_runs().values()
        if run and run["complete"] and not run["topology_only"]
        and run["status"] == "completed"
    ]
    return max(completed, key=lambda run: run["updated_at"], default=None)


def _latest_report_text(run: dict) -> str:
    settings = run.get("settings") or {}
    metrics = run["metrics"]
    topology = run.get("topology") or {}
    route_name = "Dynamic" if run["mode"].lower() == "dynamic" else f"Route {run['mode']}"
    lines = [
        "Simulation Report",
        "=================",
        "",
        f"Simulation status: {run['status']}",
        f"TCP validation: {run['tcp_validation']}",
        f"Route mode: {route_name}",
        f"Configured node count: {run['node_count'] or 'Not available'}",
        f"Run identifier: {run['id']}",
        "",
        "Traffic configuration",
        "---------------------",
    ]
    traffic_settings = (
        ("Traffic profile", "traffic_load"),
        ("HTTP clients", "http_client_count"),
        ("Requests per client", "http_request_count"),
        ("Request interval (s)", "http_request_interval"),
        ("Request body size (bytes)", "http_request_size"),
        ("Response body size (bytes)", "http_response_size"),
    )
    for label, key in traffic_settings:
        lines.append(f"{label}: {settings.get(key, 'Not available')}")
    if run["mode"].lower() == "dynamic":
        lines.append(
            "Route switch (A to B, s): "
            f"{metrics.get('dynamic_switch_time_seconds') or settings.get('dynamic_switch_time') or 'Not available'}"
        )
    lines.extend([
        "",
        "Node and link summary",
        "---------------------",
        f"Nodes: {len(topology.get('nodes', [])) or run['node_count'] or 'Not available'}",
        f"Links: {len(topology.get('links', []))}",
    ])
    for link in topology.get("links", []):
        delay = _number(link.get("delay_seconds"))
        delay_text = f"{delay * 1000:g} ms" if delay is not None else "Not available"
        lines.append(
            f"{link.get('endpoint_a', 'Unknown')} ({link.get('ipv4_a') or 'IP unavailable'})"
            f" -- {link.get('endpoint_b', 'Unknown')} ({link.get('ipv4_b') or 'IP unavailable'}); "
            f"bandwidth {link.get('bandwidth') or 'Not available'}; delay {delay_text}"
        )
    lines.extend([
        "",
        "Performance metrics",
        "--------------------",
        f"Average latency: {_format_report_metric(metrics.get('average_rtt_seconds'), 's')}",
        f"Maximum latency: {_format_report_metric(metrics.get('maximum_rtt_seconds'), 's')}",
        "Throughput: "
        f"{_format_report_metric(metrics.get('application_response_body_throughput_bits_per_second'), 'bit/s')}",
        f"Successful HTTP responses: {metrics.get('successful_responses') if metrics.get('successful_responses') is not None else 'Not available'}",
        f"Failed or missing HTTP responses: {metrics.get('failed_missing_responses') if metrics.get('failed_missing_responses') is not None else 'Not available'}",
    ])
    successes = metrics.get("successful_responses")
    failures = metrics.get("failed_missing_responses")
    if successes is not None and failures is not None:
        lines.extend(["", "Result summary", "--------------"])
        lines.append(
            f"{successes} successful and {failures} failed or missing responses "
            f"were recorded in {route_name}."
        )
    return "\n".join(lines) + "\n"


def _format_report_metric(value, unit: str) -> str:
    return f"{value} {unit}" if value is not None else "Not available"


def _latest_report_csv(run: dict) -> str:
    settings = run.get("settings") or {}
    metrics = run["metrics"]
    topology = run.get("topology") or {}
    fields = [
        "run_id", "status", "route_mode", "node_count", "tcp_validation",
        "traffic_load", "http_client_count", "http_request_count",
        "http_request_interval", "http_request_size", "http_response_size",
        "dynamic_switch_time_seconds", "node_count_from_topology", "link_count",
        *METRIC_CSV_FIELDS,
    ]
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    writer.writerow({
        "run_id": run["id"],
        "status": run["status"],
        "route_mode": run["mode"],
        "node_count": run["node_count"],
        "tcp_validation": run["tcp_validation"],
        **{key: settings.get(key) for key in (
            "traffic_load", "http_client_count", "http_request_count",
            "http_request_interval", "http_request_size", "http_response_size",
        )},
        "dynamic_switch_time_seconds": metrics.get("dynamic_switch_time_seconds"),
        "node_count_from_topology": len(topology.get("nodes", [])) or None,
        "link_count": len(topology.get("links", [])),
        **{key: metrics.get(key) for key in METRIC_CSV_FIELDS},
    })
    return output.getvalue()


METRIC_CSV_FIELDS = (
    "successful_responses",
    "failed_missing_responses",
    "average_rtt_seconds",
    "maximum_rtt_seconds",
    "application_response_body_throughput_bits_per_second",
    "simulation_duration_seconds",
    "measured_application_interval_seconds",
)


def _job_snapshot() -> dict:
    with _job_lock:
        if _current_job is None:
            return {"status": "idle", "running": False}
        job = dict(_current_job)
    log_path = Path(job.pop("_log_path"))
    tail = ""
    if log_path.is_file():
        try:
            with log_path.open("rb") as source:
                source.seek(max(0, log_path.stat().st_size - 12_000))
                tail = source.read().decode("utf-8", errors="replace")
        except OSError:
            tail = ""
    if not tail:
        tail = job.pop("_output_tail", "")
    stderr_tail = ""
    stderr_path_value = job.get("_stderr_path")
    if stderr_path_value:
        stderr_path = Path(stderr_path_value)
        if stderr_path.is_file():
            try:
                with stderr_path.open("rb") as source:
                    source.seek(max(0, stderr_path.stat().st_size - 6000))
                    stderr_tail = source.read().decode("utf-8", errors="replace")
            except OSError:
                pass
    if not stderr_tail:
        stderr_tail = job.pop("_stderr_tail", "")
    job.pop("_stderr_path", None)
    job["output_tail"] = tail
    job["stderr_tail"] = stderr_tail
    job["running"] = job["status"] == "running"
    event_output = tail
    event_log_path = (
        RESULTS_DIR / job["run_directory"] / "simulation.log"
        if job.get("run_directory")
        else log_path
    )
    try:
        event_output = event_log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    job["events"] = (
        _simulation_events(job, event_output)
        if job.get("kind") == "simulation"
        else []
    )
    if job.get("run_directory"):
        try:
            result = _run_summary(RESULTS_DIR / job["run_directory"])
            job["tcp_validation"] = result["tcp_validation"]
            job["metrics"] = result["metrics"]
            job["result_complete"] = result["complete"]
            job["topology"] = result["topology"]
        except (OSError, ValueError, HTTPException):
            job["result_complete"] = False
    return job


def _set_job(job_id: str, **updates):
    with _job_lock:
        if _current_job and _current_job["job_id"] == job_id:
            _current_job.update(updates)


def _msys_path(path: Path) -> str:
    absolute = str(path.resolve())
    drive, remainder = os.path.splitdrive(absolute)
    if not drive:
        raise RuntimeError(f"Cannot map path for MSYS2: {absolute}")
    return "/" + drive[0].lower() + remainder.replace("\\", "/")


def _simulation_arguments(settings: SimulationSettings) -> list[str]:
    values = [
        ("route", settings.route),
        ("nodeCount", settings.node_count),
        ("sourceNodeIndex", settings.source_node_index),
        ("destinationNodeIndex", settings.destination_node_index),
        ("trafficLoad", settings.traffic_load),
        ("httpClientCount", settings.http_client_count),
        ("httpRequestCount", settings.http_request_count),
        ("httpRequestInterval", settings.http_request_interval),
        ("httpRequestSize", settings.http_request_size),
        ("httpResponseSize", settings.http_response_size),
        ("weightDelay", settings.weight_delay),
        ("weightLoss", settings.weight_loss),
        ("weightQueue", settings.weight_queue),
        ("weightHops", settings.weight_hops),
        ("monitorInterval", settings.monitor_interval),
    ]
    if settings.route == "dynamic":
        values.append(("dynamicSwitchTime", settings.dynamic_switch_time))
    return [f"--{name}={value}" for name, value in values]


def _start_job(
    kind: str,
    command: list[str],
    cwd: Path,
    route: str | None = None,
    settings: dict | None = None,
):
    global _current_job
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    job_id = uuid.uuid4().hex
    log_path = RESULTS_DIR / f".webflow-job-{job_id}.log"
    stderr_path = RESULTS_DIR / f".webflow-job-{job_id}.stderr.log"
    now = datetime.now(timezone.utc).isoformat()
    with _job_lock:
        if _current_job and _current_job["status"] == "running":
            raise HTTPException(
                status_code=409,
                detail="A simulation or comparison is already running.",
            )
        _current_job = {
            "job_id": job_id,
            "kind": kind,
            "mode": route,
            "status": "running",
            "started_at": now,
            "finished_at": None,
            "exit_code": None,
            "error": None,
            "run_directory": None,
            "_log_path": str(log_path),
            "_stderr_path": str(stderr_path),
            "settings": settings or {},
        }
    worker = threading.Thread(
        target=_run_job,
        args=(job_id, command, cwd, log_path, stderr_path, kind, route, settings),
        daemon=True,
        name=f"webflow-{kind}-{job_id[:8]}",
    )
    worker.start()
    return _job_snapshot()


def _run_job(
    job_id: str,
    command: list[str],
    cwd: Path,
    log_path: Path,
    stderr_path: Path,
    kind: str,
    route: str | None,
    settings: dict | None,
):
    exit_code = None
    error = None
    status = "failed"
    run_directory = None
    timed_out = False
    before_runs = {
        path.name for path in RESULTS_DIR.iterdir()
        if path.is_dir() and RUN_PATTERN.fullmatch(path.name)
    }
    try:
        with log_path.open("w", encoding="utf-8", errors="replace") as output, \
                stderr_path.open("w", encoding="utf-8", errors="replace") as errors:
            process = subprocess.Popen(
                command,
                cwd=str(cwd),
                stdout=output,
                stderr=errors,
                shell=False,
            )
            _set_job(job_id, process_id=process.pid)
            try:
                exit_code = process.wait(timeout=JOB_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                timed_out = True
                process.terminate()
                try:
                    exit_code = process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    exit_code = process.wait()
        if kind in ("simulation", "topology"):
            output = log_path.read_text(encoding="utf-8", errors="replace")
            trace_match = re.search(
                r"^Trace directory:\s*(.+?)\s*$", output, re.MULTILINE
            )
            mode_match = re.search(
                r"^Route mode:\s*(A|B|dynamic|global)\s*$",
                output,
                re.MULTILINE | re.IGNORECASE,
            )
            if trace_match and mode_match and mode_match.group(1).lower() == route:
                candidate = Path(trace_match.group(1).strip()).resolve()
                try:
                    candidate.relative_to(RESULTS_DIR)
                    expected = RUN_PATTERN.fullmatch(candidate.name)
                    if candidate.is_dir() and expected and expected.group(1).lower() == route:
                        run_directory = candidate
                        shutil.copyfile(log_path, candidate / "simulation.log")
                        shutil.copyfile(stderr_path, candidate / "simulation.stderr.log")
                        if kind == "simulation":
                            result = _run_summary(candidate)
                            if not result["complete"] or result["tcp_validation"] != "PASS":
                                error = (
                                    "Simulation output or TCP validation is incomplete; "
                                    f"TCP validation={result['tcp_validation']}."
                                )
                        else:
                            result = _run_summary(candidate)
                            if (
                                not result["complete"]
                                or result["node_count"] != settings["node_count"]
                            ):
                                error = (
                                    "NS-3 did not create the requested number of nodes "
                                    "or topology files are invalid."
                                )
                        with (candidate / "settings.json").open("w", encoding="utf-8") as config:
                            json.dump(settings or {}, config, indent=2)
                            config.write("\n")
                    else:
                        error = "Simulator reported an unexpected result directory."
                except (OSError, ValueError):
                    error = "Simulator result directory is outside the allowed results folder."
            else:
                error = "Simulator output did not identify its route mode and run directory."
        elif kind == "comparison":
            output = log_path.read_text(encoding="utf-8", errors="replace")
            for directory in RESULTS_DIR.iterdir():
                if (
                    not directory.is_dir()
                    or directory.name in before_runs
                    or not RUN_PATTERN.fullmatch(directory.name)
                ):
                    continue
                mode = RUN_PATTERN.fullmatch(directory.name).group(1).lower()
                run_settings = {
                    **(settings or {}),
                    "route": mode,
                    "node_count": 7,
                }
                if mode == "dynamic":
                    run_settings["dynamic_switch_time"] = (
                        (settings or {}).get("dynamic_switch_time", 3.0)
                    )
                with (directory / "settings.json").open("w", encoding="utf-8") as config:
                    json.dump(run_settings, config, indent=2)
                    config.write("\n")
        if timed_out:
            status = "timed_out"
            error = f"Process exceeded the {JOB_TIMEOUT_SECONDS}-second time limit."
        elif exit_code == 0 and error is None:
            status = "completed"
        elif error is None:
            error = f"Process exited with code {exit_code}."
    except (OSError, RuntimeError) as exception:
        error = str(exception)
    finally:
        finished_at = datetime.now(timezone.utc).isoformat()
        try:
            with log_path.open("rb") as source:
                source.seek(max(0, log_path.stat().st_size - 12_000))
                output_tail = source.read().decode("utf-8", errors="replace")
        except OSError:
            output_tail = ""
        try:
            with stderr_path.open("rb") as source:
                source.seek(max(0, stderr_path.stat().st_size - 6000))
                stderr_tail = source.read().decode("utf-8", errors="replace")
        except OSError:
            stderr_tail = ""
        if run_directory:
            status_record = {
                "job_id": job_id,
                "kind": kind,
                "status": status,
                "exit_code": exit_code,
                "error": error,
                "started_at": _current_job.get("started_at") if _current_job else None,
                "finished_at": finished_at,
            }
            with (run_directory / "run-status.json").open("w", encoding="utf-8") as result_status:
                json.dump(status_record, result_status, indent=2)
                result_status.write("\n")
        _set_job(
            job_id,
            status=status,
            exit_code=exit_code,
            error=error,
            run_directory=run_directory.name if run_directory else None,
            finished_at=finished_at,
            _output_tail=output_tail,
            _stderr_tail=stderr_tail,
        )
        try:
            log_path.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            stderr_path.unlink(missing_ok=True)
        except OSError:
            pass


@app.get("/")
def root():
    return {
        "project": "WebFlow",
        "message": "HTTP Traffic and Network Performance Analyzer API",
        "docs": "/docs",
    }


@app.get("/health")
def health():
    snapshot = _job_snapshot()
    return {
        "project": "WebFlow",
        "status": "running",
        "simulation": snapshot["status"],
    }


@app.get("/api/status")
def project_status():
    report_csv = RESULTS_DIR / "route-comparison.csv"
    report_txt = RESULTS_DIR / "route-comparison.txt"
    return {
        "project": "WebFlow",
        "modules": {
            "module_1_topology": "completed",
            "module_2_http_traffic": "completed",
            "module_3_route_analysis": "completed",
        },
        "files": {
            "simulation_source_exists": SIMULATION_FILE.is_file(),
            "analysis_script_exists": ANALYSIS_FILE.is_file(),
            "comparison_csv_exists": report_csv.is_file(),
            "comparison_report_exists": report_txt.is_file(),
        },
        "results_directory": str(RESULTS_DIR),
        "latest_runs": _latest_runs(),
        "latest_topology": _latest_topology(),
        "simulation": _job_snapshot(),
    }


@app.get("/api/results")
def comparison_results():
    return _comparison_report()


@app.post("/api/simulations")
def start_simulation(settings: SimulationSettings):
    if not NS3_LAUNCHER.is_file():
        raise HTTPException(status_code=503, detail=f"NS-3 launcher not found: {NS3_LAUNCHER}")
    if not SIMULATION_FILE.is_file():
        raise HTTPException(status_code=503, detail="NS-3 simulation source is missing.")
    if not MSYS_BASH.is_file():
        raise HTTPException(
            status_code=503,
            detail=f"MSYS2 bash.exe not found at the configured location: {MSYS_BASH}",
        )
    python_directory = Path(sys.executable).parent
    if not (python_directory / "python3.exe").is_file():
        python_directory = Path(sys.base_prefix).parent / "bin"
    if not (python_directory / "python3.exe").is_file():
        raise HTTPException(
            status_code=503,
            detail="The configured MSYS2 PATH does not contain python3.exe required by NS-3.",
        )
    python_path = _msys_path(python_directory)
    arguments = _simulation_arguments(settings)
    return _start_job(
        "simulation",
        [
            str(MSYS_BASH),
            "-c",
            f'export PATH="/mingw64/bin:{python_path}:$PATH"; exec "$@"',
            "webflow",
            "./ns3",
            "run",
            "scratch/webflow-topology",
            "--",
            *arguments,
        ],
        NS3_ROOT,
        settings.route.lower(),
        settings.model_dump(),
    )


@app.post("/api/topologies")
def create_topology(settings: TopologySettings):
    if not NS3_LAUNCHER.is_file() or not SIMULATION_FILE.is_file():
        raise HTTPException(status_code=503, detail="The NS-3 launcher or simulation source is missing.")
    if not MSYS_BASH.is_file():
        raise HTTPException(
            status_code=503,
            detail=f"MSYS2 bash.exe not found at the configured location: {MSYS_BASH}",
        )
    python_directory = Path(sys.executable).parent
    if not (python_directory / "python3.exe").is_file():
        python_directory = Path(sys.base_prefix).parent / "bin"
    if not (python_directory / "python3.exe").is_file():
        raise HTTPException(
            status_code=503,
            detail="The configured MSYS2 PATH does not contain python3.exe required by NS-3.",
        )
    python_path = _msys_path(python_directory)
    return _start_job(
        "topology",
        [
            str(MSYS_BASH),
            "-c",
            f'export PATH="/mingw64/bin:{python_path}:$PATH"; exec "$@"',
            "webflow",
            "./ns3",
            "run",
            "scratch/webflow-topology",
            "--",
            "--route=A",
            f"--nodeCount={settings.node_count}",
            f"--sourceNodeIndex={settings.source_node_index}",
            f"--destinationNodeIndex={settings.destination_node_index}",
            "--topologyOnly=true",
        ],
        NS3_ROOT,
        "a",
        settings.model_dump(),
    )


@app.post("/api/comparisons")
def start_comparison(settings: ComparisonSettings):
    if not ANALYSIS_FILE.is_file():
        raise HTTPException(status_code=503, detail="Route comparison script is missing.")
    if not MSYS_BASH.is_file():
        raise HTTPException(
            status_code=503,
            detail=f"MSYS2 bash.exe not found at the configured location: {MSYS_BASH}",
        )
    command = [
        sys.executable,
        str(ANALYSIS_FILE),
        "--run-simulations",
        "--dynamic-switch-time",
        str(settings.dynamic_switch_time),
        "--bash",
        str(MSYS_BASH),
    ]
    return _start_job("comparison", command, PROJECT_ROOT)


@app.get("/api/simulations/status")
def simulation_status():
    return _job_snapshot()


@app.get("/api/simulations/{job_id}")
def simulation_status_by_id(job_id: str):
    snapshot = _job_snapshot()
    if snapshot.get("job_id") != job_id:
        raise HTTPException(status_code=404, detail="Simulation job not found.")
    return snapshot


@app.get("/api/simulations/{job_id}/logs")
def simulation_logs(job_id: str):
    snapshot = _job_snapshot()
    if snapshot.get("job_id") != job_id:
        raise HTTPException(status_code=404, detail="Simulation job not found.")
    return {
        "job_id": job_id,
        "status": snapshot["status"],
        "stdout": snapshot.get("output_tail", ""),
        "stderr": snapshot.get("stderr_tail", ""),
        "exit_code": snapshot.get("exit_code"),
        "error": snapshot.get("error"),
    }


@app.get("/api/topologies/latest")
def latest_topology():
    topology = _latest_topology()
    if topology is None:
        raise HTTPException(status_code=404, detail="No NS-3 topology has been created yet.")
    return topology


@app.get("/api/topologies/{job_id}")
def topology_creation_status(job_id: str):
    snapshot = _job_snapshot()
    if snapshot.get("job_id") != job_id or snapshot.get("kind") != "topology":
        raise HTTPException(status_code=404, detail="Topology creation job not found.")
    if snapshot["running"]:
        raise HTTPException(status_code=409, detail="Topology is still being created.")
    if snapshot.get("status") != "completed" or not snapshot.get("topology"):
        raise HTTPException(
            status_code=500,
            detail=snapshot.get("error") or "NS-3 topology creation did not complete.",
        )
    return {
        "job_id": job_id,
        "run_id": snapshot["run_directory"],
        **snapshot["topology"],
    }


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    if not RUN_PATTERN.fullmatch(run_id):
        raise HTTPException(status_code=404, detail="Simulation run not found.")
    directory = _inside_results(RESULTS_DIR / run_id)
    if not directory.is_dir():
        raise HTTPException(status_code=404, detail="Simulation run not found.")
    try:
        summary = _run_summary(directory)
    except (OSError, ValueError) as error:
        raise HTTPException(status_code=500, detail=f"Unable to read simulation run: {error}") from error
    return summary


@app.get("/api/reports/latest.txt")
def download_latest_report_text():
    run = _latest_traffic_run()
    if run is None:
        raise HTTPException(status_code=404, detail="No completed traffic simulation report is available.")
    return Response(
        _latest_report_text(run),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="simulation-report.txt"'},
    )


@app.get("/api/reports/latest.csv")
def download_latest_report_csv():
    run = _latest_traffic_run()
    if run is None:
        raise HTTPException(status_code=404, detail="No completed traffic simulation metrics are available.")
    return Response(
        _latest_report_csv(run),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="simulation-metrics.csv"'},
    )


@app.get("/api/reports/{report_name}")
def download_report(report_name: str):
    reports = {
        "route-comparison.csv": ("text/csv", "route-comparison.csv"),
        "route-comparison.txt": ("text/plain; charset=utf-8", "route-comparison.txt"),
    }
    selected = reports.get(report_name)
    if selected is None:
        raise HTTPException(status_code=404, detail="Report not found.")
    media_type, filename = selected
    path = _inside_results(RESULTS_DIR / filename)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Report is not available yet.")
    return FileResponse(path, media_type=media_type, filename=filename)


@app.get("/api/results/files/{run_id}/{filename}")
def download_result_file(run_id: str, filename: str):
    if not RUN_PATTERN.fullmatch(run_id) or not SAFE_RESULT_FILE.fullmatch(filename):
        raise HTTPException(status_code=404, detail="Result file not found.")
    run_directory = _inside_results(RESULTS_DIR / run_id)
    if not run_directory.is_dir():
        raise HTTPException(status_code=404, detail="Result file not found.")
    path = _inside_results(run_directory / filename)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Result file not found.")
    media_type = (
        "text/csv"
        if filename.endswith(".csv")
        else "text/plain; charset=utf-8"
        if filename.endswith(".txt") or filename.endswith(".log")
        else "application/vnd.tcpdump.pcap"
    )
    return FileResponse(path, media_type=media_type, filename=filename)
