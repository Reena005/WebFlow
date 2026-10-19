# WebFlow
HTTP Traffic & Network Performance Analyzer using NS-3

## Project modules
1. Network topology and NS-3 setup
2. HTTP-like traffic over TCP
3. Weighted dynamic routing
4. Network performance monitoring
5. FastAPI backend and React dashboard
6. Integration, testing, and evaluation

## Project stack
- NS-3 and C++
- Python and FastAPI
- React and Vite
- Git and GitHub

## Important
All reported performance metrics must come from actual simulation results.
# WebFlow – HTTP Traffic and Network Performance Analyzer Using NS-3

## 1. Project Overview

WebFlow is a network simulation and performance analysis project developed using NS-3. The project aims to study HTTP-like traffic, evaluate network performance under different traffic conditions, and implement a custom weighted dynamic-routing algorithm to select suitable paths based on network conditions.

The simulation uses a client-server network connected through five intermediate routers. Two alternative paths are configured between the client-side router and the server-side router. The network will later be used to study congestion, route selection, traffic performance, and recovery from network degradation.

**Current development status:** Modules 1–3, scheduled Dynamic Route A-to-B switching, route comparison, the FastAPI API, and the React dashboard are implemented. Dynamic switching changes explicit IPv4 static routes; it is not enabled in `global` mode.

## 2. Objectives

- Build and simulate a multi-router network topology using NS-3.
- Configure IPv4 addressing and point-to-point network links.
- Verify communication between a client and a server.
- Generate packet traces for investigating actual packet forwarding.
- Develop TCP-based HTTP-like traffic and multiple traffic workloads.
- Implement weighted dynamic routing based on network conditions.
- Evaluate network performance using simulation-generated measurements.

The objectives listed above describe the overall project. See the implementation and remaining-work sections for current capabilities and limitations.

## 3. Technologies Used

| Technology | Purpose |
|---|---|
| NS-3 | Network simulation |
| C++ | Simulation development |
| IPv4 | Network addressing |
| UDP Echo Applications | Connectivity regression test |
| TCP sockets | HTTP-like client/server request and response |
| PCAP | Packet capture and traffic inspection |
| MSYS2 MINGW64 | Native Windows build environment |
| CMake and Ninja | Build configuration and compilation |
| Git | Version control |

The current simulation runs on Windows using MSYS2 MINGW64. Ubuntu or WSL is not required for the existing setup.

## 4. Network Topology

The topology contains seven nodes:

- Client
- Router R1
- Router R2
- Router R3
- Router R4
- Router R5
- Server

Two alternative paths are configured between R1 and R5.

### Route A

Client → R1 → R2 → R5 → Server

### Route B

Client → R1 → R3 → R4 → R5 → Server

Both routes share the client-to-R1 and R5-to-server links. Route A uses two intermediate routers between R1 and R5, while Route B uses three.

### Network Configuration

| Parameter | Current configuration |
|---|---|
| Total nodes | 7 |
| Total point-to-point links | 7 |
| Link data rate | 10 Mbps |
| Delay per link | 5 ms |
| IPv4 addressing | Separate subnet for each link |
| Server IPv4 address | 10.1.7.2 |
| UDP server port | 9 |
| UDP packet size | 64 bytes |
| Scheduled echo requests | 5 |
| TCP server port | 8080 |
| TCP clients (low profile default) | 2 separate client applications/connections |
| HTTP-like requests (default) | 3 |
| Request body (default) | 128 bytes |
| Request interval (low profile default) | 2 seconds |
| Response body (default) | 128 bytes |
| Traffic load profile (default) | low |
| Baseline routing | NS-3 global routing |

The current configuration provides a basic network on which further traffic and routing experiments can be developed.

## 5. Implementation Completed

### 5.1 NS-3 Setup

NS-3 was downloaded and configured in the following directory:

`C:\Users\Reena\WebFlow\ns3-download`

The NS-3 build completed successfully, and the built-in Hello Simulator example ran successfully.

### 5.2 Topology Creation

The simulation creates seven nodes and installs the Internet protocol stack. Point-to-point devices and channels are configured with a data rate of 10 Mbps and a channel delay of 5 ms.

Each link receives its own IPv4 subnet. The default `global` mode uses `Ipv4GlobalRoutingHelper::PopulateRoutingTables()`. Explicit `A` and `B` modes install static routes for independent path checks.

### 5.3 Connectivity Testing

A UDP echo server is installed on the server node, and a UDP echo client is installed on the client node.

The client sends five 64-byte packets at one-second intervals. The server receives each request and sends an echo response.

**Observed result:** Five requests were sent, five requests reached the server, and five echo replies reached the client.

The first observed round-trip time was approximately 40.6 ms in the current simulation.

This result confirms successful end-to-end UDP communication under the tested configuration. It is a simulation result, not a measurement of a physical network.

### 5.4 Packet Tracing

PCAP tracing captures both endpoints of every point-to-point link, including UDP and TCP traffic. The program reports the unique trace directory and generated capture-file paths.

Use `analysis/verify_route_pcaps.py` to inspect the classic PPP PCAP files. It reports packet timestamps, endpoint IPs and ports, UDP echo traffic, TCP segments and payload bytes, empty captures, and the observed route. TCP payload counts are segment-level; application message boundaries are validated by the simulation's TCP client.

The existence of a PCAP file alone does not prove that a route carried traffic. The verifier distinguishes header-only captures from actual packets and uses the configured client/server tuple to identify forwarding links.

### 5.5 Configurable HTTP-Like TCP Traffic

The configured number of independent TCP client applications connect to the server at `10.1.7.2:8080` by default. They run on the existing Client node to preserve the seven-node topology, use separate TCP sockets, and start 50 ms apart. Each connection sends the configured number of HTTP-like `POST / HTTP/1.1` requests. Requests carry client and request IDs, a `Content-Length`, and a body of the configured size. The server keeps separate stream buffers and response queues per accepted socket, parses complete messages in order, and returns exactly one correlated response per complete request.

Each client buffers the response byte stream and validates complete header/body pairs, correlating responses by client and request ID. Per-client summaries report connection status, requests sent, validated responses, failed or missing responses, and average RTT. The aggregate summary reports totals and weighted average RTT; any failed connection, missing response, or invalid response makes the simulation exit unsuccessfully. This is a small HTTP-like protocol over TCP, not a full HTTP implementation.

Application events log simulation timestamps, traffic profile, route mode, client/request IDs, event type, body payload bytes, validation status, and RTT when complete. Each run writes one CSV row per expected request with the columns `timestamp_seconds,traffic_load,route_mode,client_id,request_id,event,request_bytes,response_bytes,rtt_seconds,status`. By default `http-requests.csv` is written in the unique run trace directory. Use `--csvOutputDirectory=<path>` to select another directory; its CSV filename includes the unique run ID, and existing files are never overwritten.

The summary reports average and maximum RTT, request and response body-byte totals, measured simulation duration, and application response-body throughput. Throughput is calculated as successfully validated response-body bytes multiplied by 8, divided by the interval from the first request send to the last validated response receive. It is reported in bit/s and is not physical-link throughput. Request/response payload metrics count bodies only, not HTTP-like headers or TCP/IP framing.

Configure deterministic traffic profiles with `--trafficLoad=low|medium|high`. Low uses 2 clients, 3 requests per client, and a 2-second interval; medium uses 5 clients, 10 requests, and a 1-second interval; high uses 10 clients, 20 requests, and a 0.2-second interval. Low is the default.

Profiles set the client count, request count, and interval. Explicit `--httpClientCount`, `--httpRequestCount`, and `--httpRequestInterval` options override those profile values; request and response body sizes remain independently configurable with `--httpRequestSize` and `--httpResponseSize`. The simulation prints the selected profile and resolved totals. The run duration accounts for staggered starts, the request schedule, and transfer allowance while retaining the UDP baseline duration.

## 6. Project Structure

The project contains the following main directories:

```text
WebFlow/
├── analysis/
├── backend/
│   ├── main.py
│   └── requirements.txt
├── docs/
├── frontend/
├── ns3/
│   └── scratch/
├── ns3-download/
│   └── scratch/
│       └── webflow-topology.cc
├── results/
│   └── webflow-route-tests/
├── .gitignore
└── README.md
```

The main simulation currently being developed is:

`ns3-download/scratch/webflow-topology.cc`

Simulation trace files are generated under:

`results/webflow-route-tests/`

The FastAPI backend launches NS-3 simulations and the comparison script as background jobs. The React dashboard uses the API for run controls, saved results, and report downloads.

## 7. Build and Run Instructions

### Prerequisites

- Windows
- MSYS2 with the MINGW64 environment
- A working C++ compiler
- CMake, Ninja, Python, and Git
- The downloaded NS-3 source tree

### Step 1: Open MSYS2 MINGW64

Open the **MSYS2 MinGW 64-bit** terminal.

### Step 2: Navigate to the NS-3 directory

```bash
cd /c/Users/Reena/WebFlow/ns3-download
```

### Step 3: Build NS-3

```bash
./ns3 build webflow-topology
```

### Step 4: Run the WebFlow simulation

```bash
./ns3 run scratch/webflow-topology -- --route=global --tcpPort=8080
./ns3 run scratch/webflow-topology -- --route=A --tcpPort=8080
./ns3 run scratch/webflow-topology -- --route=B --tcpPort=8080
./ns3 run scratch/webflow-topology -- --weightDelay=0.5 --weightLoss=0.2 --weightQueue=0.2 --weightHops=0.1
./ns3 run scratch/webflow-topology -- --testRouteMetrics=true
./ns3 run scratch/webflow-topology -- --monitorInterval=1 --monitorStartTime=0.6
./ns3 run scratch/webflow-topology -- --route=B --monitorInterval=1 --monitorStartTime=0.6
./ns3 run scratch/webflow-topology -- --trafficLoad=low
./ns3 run scratch/webflow-topology -- --trafficLoad=medium
./ns3 run scratch/webflow-topology -- --trafficLoad=high
./ns3 run scratch/webflow-topology -- --trafficLoad=medium --route=A --csvOutputDirectory=../results/csv
./ns3 run scratch/webflow-topology -- --trafficLoad=low --csvOutputDirectory=../results/csv
./ns3 run scratch/webflow-topology -- --httpClientCount=2 --httpRequestCount=2
./ns3 run scratch/webflow-topology -- --httpRequestSize=64 --httpRequestInterval=0.25 --httpRequestCount=2 --httpResponseSize=32
./ns3 run scratch/webflow-topology -- --route=dynamic --dynamicSwitchTime=3.0
```

The simulation prints its configuration, server address, trace locations, UDP echo activity, and TCP application logs. Each run creates a new directory under `results/webflow-route-tests`.

To inspect a run's captures from the NS-3 root, use the MINGW64 Python installation (substitute the actual run directory):

```bash
/mingw64/bin/python.exe ../analysis/verify_route_pcaps.py ../results/webflow-route-tests/run-global-<run-id>
```

If the simulation file or target name changes, use the actual target name configured in the current NS-3 source tree.

### Start the Dashboard on Windows

Keep the backend and frontend running in separate PowerShell terminals. The backend uses the project's virtual environment and the MSYS2 installation at `C:\msys64`.

**Terminal 1 — FastAPI backend:**

```powershell
cd C:\Users\Reena\WebFlow\backend
.\.venv\Scripts\python.exe -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

If the backend virtual environment does not exist yet, create it and install the listed backend requirements before starting Uvicorn:

```powershell
cd C:\Users\Reena\WebFlow\backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

**Terminal 2 — Vite frontend:**

```powershell
cd C:\Users\Reena\WebFlow\frontend
npm run dev -- --host 127.0.0.1
```

Open the local Vite URL shown in the terminal (normally `http://localhost:5173`). The dashboard talks to FastAPI at `http://127.0.0.1:8000`. Use **HTTP Traffic** and **Route Analysis** to configure and launch a simulation; **Reports** can run the saved Route A, Route B, and Dynamic comparison workflow. Only one simulation or comparison job can run at a time. Dynamic switch time must fall inside the configured HTTP traffic window.

### User-Controlled NS-3 Node Creation

The **Network Topology** page submits the requested count to FastAPI, which launches the NS-3 topology-only workflow. The page updates its node and link inventory only after NS-3 exits successfully and the backend verifies the generated topology CSVs. Supported counts are 7–254.

The original seven nodes remain the routing core (Client, R1–R5, Server). Each requested node after index 6 is created in the NS-3 `NodeContainer`, installed with IPv4, and connected as a leaf host to R1 using a unique `/24` subnet. These auxiliary nodes are not transit routers and do not participate in Route A, Route B, or HTTP traffic. HTTP endpoints are currently fixed to Client node 0 and Server node 6; selecting other endpoints is rejected rather than silently redirected.

Each topology-creation or simulation run stores its own topology CSVs, settings, stdout/stderr logs, and completion record under `results/webflow-route-tests/run-<mode>-<id>`. The dashboard's **Performance Charts** page reads saved, TCP-validated runs and only charts available measurements. Dynamic RTT/throughput values remain whole-run aggregates; they are not split into route-specific values. Node-count charts appear only after successful runs at multiple counts are available.

To test node creation from the NS-3 source directory in an MSYS2 MINGW64 terminal:

```bash
cd /c/Users/Reena/WebFlow/ns3-download
./ns3 build webflow-topology
./ns3 run scratch/webflow-topology -- --route=A --nodeCount=7 --topologyOnly=true
./ns3 run scratch/webflow-topology -- --route=A --nodeCount=8 --topologyOnly=true
```

Use the Network Topology page to launch these topology-only operations from the dashboard. To test a regular HTTP simulation at the selected node count, create it first in the dashboard, then start the simulation from **HTTP Traffic** or **Route Analysis**. The current simulation source still uses the original 7-node HTTP endpoint pair.

## 8. Current Routing Approach

The default `global` mode uses NS-3 global routing as a baseline. `--route=A` and `--route=B` use explicit static routes to independently exercise each path.

In the equal-link topology, global routing selected Route A in the inspected captures. The static route modes provide evidence for Route B independently; they are test configurations, not weighted dynamic routing.

Route selection and PCAP tracing are preserved when the TCP application is enabled. TCP traffic follows the selected route mode.

The current global-routing baseline is not the explicit Dynamic routing mode. Dynamic mode installs Route A initially, then updates the IPv4 static routes for forward and return traffic to Route B at `--dynamicSwitchTime` (default 3.0 seconds). It is a scheduled switch; route costs and monitoring recommendations do not trigger the switch. The global mode continues to use NS-3 global shortest-path routing.

### Module 3 Phase 1: Weighted Cost Calculation

The simulation reports a separate advisory weighted cost for Route A and Route B; this calculation does not drive the scheduled Dynamic switch. Route A is modeled as four links with three router hops; Route B is five links with four router hops. Their 20 ms and 25 ms delay values are topology-derived propagation estimates from the configured 5 ms link delay, not live end-to-end measurements. Packet-loss rate and queue occupancy are unavailable because this phase adds no live loss/queue measurement, and are explicitly reported as unavailable.

Normalization references are 50 ms delay, 0.10 packet-loss fraction, 1.0 queue-occupancy ratio, and 5 router hops. Available values use `clamp(value / reference, 0, 1)`. Unavailable or unusable values use the neutral normalized score 0.5. Effective weights are the configured nonnegative weights divided by their sum. The cost is the weighted sum of the four normalized scores, bounded to `[0,1]`. A cost difference within `1e-9` is reported as a tie; the existing forwarding mode is retained.

Set weights with `--weightDelay`, `--weightLoss`, `--weightQueue`, and `--weightHops`. Defaults are `0.4`, `0.3`, `0.2`, and `0.1`. Negative, non-finite, or all-zero weights are rejected. Run `--testRouteMetrics=true` for the normalization and cost-bound self-test. Costs are advisory only; no forwarding-table updates are performed.

### Module 3 Phase 2: Network Metric Monitoring

Periodic sampling is configured with `--monitorInterval` (default 1 second) and `--monitorStartTime` (default 0.6 seconds, after initialization and before the default traffic workload completes). Invalid intervals and start times outside the run/expected traffic window are rejected.

For the route actually carrying application traffic, delay is the cumulative mean RTT of completed HTTP-like requests matched by client/request ID; it is not one-way delay. The reported request-delivery loss fraction is cumulative `(requests sent - unique request IDs parsed by the server) / requests sent`. This is an application-request delivery metric, not TCP segment loss, since TCP retransmits dropped segments. With no requests yet, delay and loss are unavailable.

Queue occupancy is sampled from each point-to-point device's actual `GetQueue()` current/max sizes. The route value sums both directions' TX queues along that candidate path, reports the queue unit (packets or bytes), and divides summed current occupancy by summed configured capacity. This is an instantaneous snapshot and may miss short bursts. Router hop counts remain 3 for Route A and 4 for Route B.

Monitoring records are printed for both candidates and written to a unique `route-metrics-<run-id>.csv` in the configured CSV output directory. When one forwarding mode is selected, only that route has observed request RTT/loss; the other route's traffic-dependent metrics are explicitly unavailable, while its queues and known hop count are still sampled. In this topology, global routing uses the shorter Route A path. Phase 3 recalculates both costs at every sample and reports the lower route only when its cost is valid; partial fallback costs remain labeled partial and are not used to select a route. Selection is advisory and does not install routes or change forwarding. To measure RTT/loss for both candidates, run separate `--route=A` and `--route=B` simulations; an inactive path is not reported as having measured traffic metrics. The monitoring CSV includes the advisory selected route and reason.

## 9. Planned Development

### Module 1 — Network Topology and NS-3 Setup

- Completed: verify Route A and Route B independently, preserve PCAP traces and route reports.

### Module 2 — HTTP/TCP Traffic Generation

- Phase 1 completed: TCP client/server HTTP-like request and response with stream buffering and client validation.
- Phase 2 completed: configurable request count, request body size, request interval, and response body size, with per-response RTT and summary validation.
- Phase 3 completed: multiple staggered TCP client connections with per-client and aggregate response/RTT summaries.
- Phase 4 completed: reproducible low, medium, and high traffic profiles with explicit parameter override precedence.
- Phase 5 completed: timestamped application event logs, unique per-run CSV records, response validation, payload/RTT summaries, and accurately defined application throughput.
- Future phases: event integration and analysis beyond the per-request CSV output.

### Module 3 — Route Analysis and Dynamic Switching

- Phase 1 completed: bounded normalization and weighted advisory cost calculation, using topology-derived delay/hops and explicitly unavailable loss/queue metrics.
- Phase 2 implemented: periodic RTT/request-delivery/queue snapshots and per-run route metrics CSV.
- Phase 3 implemented: per-sample comparison of valid weighted costs with an advisory selected route and reason; actual forwarding remains unchanged.
- Phase 4 implemented: scheduled Dynamic mode updates IPv4 static forwarding routes in both directions from Route A to Route B and logs the switch time. This is time-based, not cost-triggered.
- Measurement limitation: only the selected path carries HTTP-like traffic per simulation. Run both explicit route modes to measure both candidates independently; inactive-path traffic metrics remain unavailable.
- Dynamic-run RTT and response summaries cover the whole run and are not attributed to only one route. Per-route monitor samples are kept separate and incomplete measurements are not conclusive cost comparisons.
- Further work: experiment with congestion/failure-driven switching, hysteresis, and route recovery.

### Remaining Project Work

Further planned work includes controlled congestion/failure experiments, broader route comparisons, and additional evaluation metrics.

## 10. Expected Evaluation Metrics

The following metrics are planned for future experiments:

- Throughput
- End-to-end delay
- Packet delivery ratio
- Packet loss
- TCP retransmissions
- Queue occupancy and packet drops
- Link utilization
- Routing overhead
- Route changes and recovery time

Metrics are shown only when supported by the generated run CSVs and console logs. Application response-body throughput is an application-layer metric, not physical-link throughput.

## 11. Conclusion

WebFlow has a seven-node NS-3 topology with two alternative paths, verified UDP echo traffic, PCAP route analysis, configurable multi-client HTTP-like TCP traffic, route monitoring and scheduled Dynamic switching. A FastAPI backend connects the simulation and comparison workflow to a page-specific React dashboard. Dynamic results remain whole-run aggregates unless route-specific monitoring is independently available.
