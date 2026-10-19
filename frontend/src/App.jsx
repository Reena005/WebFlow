import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import './App.css'

const API = 'http://127.0.0.1:8000'
const TABS = ['Overview', 'Network Topology', 'Run Simulation', 'Analytics', 'Reports']
const NAV_ICONS = {
  Overview: <><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></>,
  'Network Topology': <><circle cx="6" cy="6" r="2.5" /><circle cx="18" cy="7" r="2.5" /><circle cx="12" cy="18" r="2.5" /><path d="m8.2 7 7.5-.1M7.5 8l3.2 7.7m5.8-6.8-3.4 6.4" /></>,
  'Run Simulation': <><path d="M5 3.8a1 1 0 0 1 1.5-.86l12 7.06a2.3 2.3 0 0 1 0 4l-12 7.06A1 1 0 0 1 5 20.2z" /></>,
  Analytics: <><path d="M4 19V5m0 14h17" /><path d="m7 15 4-4 3 2 6-7" /></>,
  Reports: <><path d="M6 3.5h8l4 4v13H6z" /><path d="M14 3.5v4h4M9 12h6m-6 3.5h6" /></>,
}
const ROUTE_PATHS = {
  A: ['Client', 'R1', 'R2', 'R5', 'Server'],
  B: ['Client', 'R1', 'R3', 'R4', 'R5', 'Server'],
}
const PROFILE_DEFAULTS = {
  low: { clients: 2, requests: 3, interval: 2 },
  medium: { clients: 5, requests: 10, interval: 1 },
  high: { clients: 10, requests: 20, interval: 0.2 },
}

const INITIAL_SETTINGS = {
  route: 'A',
  node_count: 7,
  source_node_index: 0,
  destination_node_index: 6,
  traffic_load: 'low',
  http_client_count: 2,
  http_request_count: 3,
  http_request_interval: 2,
  http_request_size: 128,
  http_response_size: 128,
  dynamic_switch_time: 3,
  weight_delay: 0.4,
  weight_loss: 0.3,
  weight_queue: 0.2,
  weight_hops: 0.1,
  monitor_interval: 1,
}

const METRICS = [
  ['successful_responses', 'Successful responses', 'responses'],
  ['failed_missing_responses', 'Failed or missing responses', 'responses'],
  ['average_rtt_seconds', 'Average round-trip time', 's'],
  ['maximum_rtt_seconds', 'Maximum round-trip time', 's'],
  ['application_response_body_throughput_bits_per_second', 'Application response-body throughput', 'bit/s'],
  ['simulation_duration_seconds', 'Simulation duration', 's'],
  ['measured_application_interval_seconds', 'Measured application interval', 's'],
]

async function request(path, options = {}) {
  const response = await fetch(`${API}${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...options.headers },
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new Error(data.detail || `Request failed (${response.status}).`)
  }
  return data
}

async function fetchDashboardData() {
  const [health, status, results, simulation] = await Promise.all([
    request('/health'),
    request('/api/status'),
    request('/api/results'),
    request('/api/simulations/status'),
  ])
  return { health, status, results, simulation }
}

function displayMetric(value, unit = '') {
  if (value === null || value === undefined || value === '') return 'Not available'
  const rendered = typeof value === 'number'
    ? new Intl.NumberFormat(undefined, { maximumFractionDigits: 4 }).format(value)
    : value
  return unit ? `${rendered} ${unit}` : rendered
}

function MetricCard({ label, value, note }) {
  return (
    <article className="metric-card">
      <div className="metric-card-heading">
        <span className="metric-label">{label}</span>
        <span className="metric-accent" aria-hidden="true" />
      </div>
      <strong>{value}</strong>
      {note && <span className="metric-note">{note}</span>}
    </article>
  )
}

function Panel({ title, description, action, children, className = '' }) {
  return (
    <section className={`panel ${className}`}>
      <div className="panel-heading">
        <div>
          <h2>{title}</h2>
          {description && <p>{description}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  )
}

function App() {
  const [health, setHealth] = useState(null)
  const [status, setStatus] = useState(null)
  const [results, setResults] = useState(null)
  const [createdTopology, setCreatedTopology] = useState(null)
  const [requestedNodeCount, setRequestedNodeCount] = useState(7)
  const [job, setJob] = useState(null)
  const [settings, setSettings] = useState(INITIAL_SETTINGS)
  const [activeTab, setActiveTab] = useState('Overview')
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const loadDashboard = useCallback(async () => {
    try {
      const data = await fetchDashboardData()
      setHealth(data.health)
      setStatus(data.status)
      setResults(data.results)
      setJob(data.simulation.status === 'idle' ? null : data.simulation)
      setCreatedTopology(data.status.latest_topology || null)
      if (data.status.latest_topology) {
        setRequestedNodeCount(data.status.latest_topology.node_count)
        setSettings((current) => ({
          ...current,
          node_count: data.status.latest_topology.node_count,
        }))
      }
      setError('')
    } catch (loadError) {
      setError(`Cannot load dashboard data: ${loadError.message}`)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    let active = true
    fetchDashboardData()
      .then((data) => {
        if (!active) return
        setHealth(data.health)
        setStatus(data.status)
        setResults(data.results)
        setJob(data.simulation.status === 'idle' ? null : data.simulation)
        setCreatedTopology(data.status.latest_topology || null)
        if (data.status.latest_topology) {
          setRequestedNodeCount(data.status.latest_topology.node_count)
          setSettings((current) => ({
            ...current,
            node_count: data.status.latest_topology.node_count,
          }))
        }
        setError('')
      })
      .catch((loadError) => {
        if (active) setError(`Cannot load dashboard data: ${loadError.message}`)
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => { active = false }
  }, [])

  useEffect(() => {
    if (!job?.running) return undefined
    const timer = window.setInterval(async () => {
      try {
        const nextJob = await request('/api/simulations/status')
        setJob(nextJob)
        if (!nextJob.running) {
          if (nextJob.kind === 'topology' && nextJob.status === 'completed') {
            const topology = await request(`/api/topologies/${nextJob.job_id}`)
            setCreatedTopology(topology)
            setRequestedNodeCount(topology.node_count)
            setSettings((current) => ({ ...current, node_count: topology.node_count }))
            setNotice(`NS-3 confirmed creation of ${topology.node_count} nodes.`)
          }
          await loadDashboard()
        }
      } catch (pollError) {
        setError(`Unable to refresh simulation status: ${pollError.message}`)
      }
    }, 350)
    return () => window.clearInterval(timer)
  }, [job?.running, loadDashboard])

  async function createNodes(event) {
    event.preventDefault()
    setError('')
    setNotice('')
    setSubmitting(true)
    try {
      const nextJob = await request('/api/topologies', {
        method: 'POST',
        body: JSON.stringify({
          node_count: Number(requestedNodeCount),
          source_node_index: Number(settings.source_node_index),
          destination_node_index: Number(settings.destination_node_index),
        }),
      })
      setJob(nextJob)
      setNotice('NS-3 is creating the requested topology. Nodes will update when creation is confirmed.')
    } catch (createError) {
      setError(createError.message)
    } finally {
      setSubmitting(false)
    }
  }

  function updateSetting(event) {
    const { name, value, type } = event.target
    const nextValue = type === 'number' ? Number(value) : value
    setSettings((current) => ({ ...current, [name]: nextValue }))
  }

  function updateProfile(event) {
    const profile = event.target.value
    const defaults = PROFILE_DEFAULTS[profile]
    setSettings((current) => ({
      ...current,
      traffic_load: profile,
      http_client_count: defaults.clients,
      http_request_count: defaults.requests,
      http_request_interval: defaults.interval,
    }))
  }

  async function startSimulation(event) {
    event.preventDefault()
    setError('')
    setNotice('')
    setSubmitting(true)
    try {
      const nextJob = await request('/api/simulations', {
        method: 'POST',
        body: JSON.stringify(settings),
      })
      setJob(nextJob)
      setNotice('Simulation started. Its status and output will update here.')
    } catch (startError) {
      setError(startError.message)
    } finally {
      setSubmitting(false)
    }
  }

  const latestRuns = results?.runs || status?.latest_runs || {}
  const runHistory = results?.run_history || []
  const savedRuns = ['A', 'B', 'dynamic'].map((mode) => {
    const latest = latestRuns[mode]
    if (latest && !latest.topology_only && latest.tcp_validation !== 'missing') return latest
    return runHistory
      .filter((run) => run.mode.toLowerCase() === mode
        && !run.topology_only
        && run.tcp_validation !== 'missing')
      .sort((first, second) => (second.updated_at || '').localeCompare(first.updated_at || ''))[0] || null
  }).filter(Boolean)
  const latestSaved = [...savedRuns].sort((first, second) => (
    (second.updated_at || '').localeCompare(first.updated_at || '')
  ))[0]
  const latestReport = results?.latest_report || null
  const jobRunning = Boolean(job?.running)
  const actionDisabled = jobRunning || submitting

  function simulationAction() {
    return (
      <button
        className="primary-button"
        type="submit"
        form="simulation-settings"
        disabled={actionDisabled}
      >
        {actionDisabled ? 'Simulation running…' : 'Start simulation'}
      </button>
    )
  }

  function renderOverview() {
    const aRun = latestRuns.A
    const bRun = latestRuns.B
    const dynamicRun = latestRuns.dynamic
    const successful = [aRun, bRun, dynamicRun]
      .map((run) => run?.metrics?.successful_responses)
      .filter((value) => value !== null && value !== undefined)
    const responsesTotal = successful.length
      ? successful.reduce((sum, value) => sum + value, 0)
      : null
    return (
      <>
        <section className="welcome">
          <div>
            <div className="eyebrow">NETWORK PERFORMANCE WORKSPACE</div>
            <h2>Understand your network.<br />Measure every route.</h2>
            <p>
              Create a supported NS-3 topology, run HTTP-like TCP traffic,
              and compare saved route results using measured data.
            </p>
          </div>
          <div className="welcome-graphic" aria-hidden="true">
            <div className="network-line line-one" />
            <div className="network-line line-two" />
            <div className="network-line line-three" />
            <div className="network-node node-one">C</div>
            <div className="network-node node-two">R1</div>
            <div className="network-node node-three">R2</div>
            <div className="network-node node-four">R3</div>
            <div className="network-node node-five">S</div>
          </div>
        </section>
        <section className="metrics-grid">
          <MetricCard label="Project modules" value="3" note="Topology, HTTP traffic, route analysis" />
          <MetricCard label="Latest A/B/Dynamic responses" value={displayMetric(responsesTotal)} note="Sum of available saved-run results" />
          <MetricCard
            label="Latest saved run"
            value={latestSaved?.mode || 'Not available'}
            note={latestSaved?.id || 'No saved simulation run found'}
          />
          <MetricCard
            label="Backend API"
            value={health?.status === 'running' ? 'Online' : 'Offline'}
            note={health?.status === 'running' ? 'FastAPI · Port 8000' : 'Backend connection unavailable'}
          />
        </section>
        <Panel title="Project modules" description="Implementation status from the backend">
          <div className="module-grid">
            {[
              ['01', 'Network Topology', status?.modules?.module_1_topology],
              ['02', 'HTTP Traffic', status?.modules?.module_2_http_traffic],
              ['03', 'Route Analysis', status?.modules?.module_3_route_analysis],
            ].map(([number, title, moduleStatus]) => (
              <article className="module-card" key={number}>
                <div className="module-card-top">
                  <span className="module-number">{number}</span>
                  <span className={`state-label ${moduleStatus === 'completed' ? 'good' : ''}`}>
                    {moduleStatus || 'Unknown'}
                  </span>
                </div>
                <h3>{title}</h3>
                <p>{moduleStatus === 'completed' ? 'Implementation reported complete by FastAPI.' : 'Status unavailable.'}</p>
                <button className="text-button" onClick={() => setActiveTab(title === 'Network Topology' ? title : 'Run Simulation')}>Open section →</button>
              </article>
            ))}
          </div>
        </Panel>
        <Panel title="Saved run summary" description="Latest generated result per routing mode">
          <RunSummary runs={savedRuns} />
        </Panel>
      </>
    )
  }

  function renderTopology() {
    return (
      <>
        <Panel title="User-controlled NS-3 node creation" description="Nodes are created by the NS-3 simulation. Nodes 7 and above are IPv4-connected leaf hosts on R1 and do not change the Route A/B paths.">
          <form className="topology-create-form" onSubmit={createNodes}>
            <NumberField
              name="requested_node_count"
              label="Number of nodes (7–254)"
              min="7"
              max="254"
              value={requestedNodeCount}
              onChange={(event) => setRequestedNodeCount(event.target.value)}
            />
            <Field label="HTTP source node">
              <select
                value={settings.source_node_index}
                onChange={(event) => setSettings((current) => ({ ...current, source_node_index: Number(event.target.value) }))}
              >
                {(createdTopology?.nodes || [{ index: 0, role: 'Client', supported_source: true }])
                  .filter((node) => node.supported_source)
                  .map((node) => <option key={node.index} value={node.index}>{node.role} · node {node.index}</option>)}
              </select>
            </Field>
            <Field label="HTTP destination node">
              <select
                value={settings.destination_node_index}
                onChange={(event) => setSettings((current) => ({ ...current, destination_node_index: Number(event.target.value) }))}
              >
                {(createdTopology?.nodes || [{ index: 6, role: 'Server', supported_destination: true }])
                  .filter((node) => node.supported_destination)
                  .map((node) => <option key={node.index} value={node.index}>{node.role} · node {node.index}</option>)}
              </select>
            </Field>
            <button className="primary-button" type="submit" disabled={actionDisabled}>
              {jobRunning && job?.kind === 'topology' ? 'Creating nodes…' : 'Create Nodes'}
            </button>
          </form>
          <p className="form-help">
            Supported HTTP endpoints are Client (node 0) and Server (node 6). Other endpoint selections are rejected until the network routes and applications support them.
          </p>
          {createdTopology ? (
            <>
              <div className="created-topology-heading">
                <strong>{createdTopology.node_count} NS-3 nodes confirmed</strong>
                <span>Source: node {createdTopology.source_node?.index} · Destination: node {createdTopology.destination_node?.index} · Run {createdTopology.run_id}</span>
              </div>
              <TopologyGraph
                key={createdTopology.run_id}
                topology={createdTopology}
                routeMode={job?.kind === 'simulation' ? job.mode : latestReport?.mode}
                simulationSettings={job?.kind === 'simulation' ? job.settings : latestReport?.settings}
                simulationMetrics={job?.kind === 'simulation' ? job.metrics : latestReport?.metrics}
                simulationJob={job?.kind === 'simulation' ? job : null}
              />
            </>
          ) : loading ? (
            <div className="topology-state" role="status">Loading the latest NS-3 topology inventory…</div>
          ) : error ? (
            <div className="topology-state topology-state-error" role="alert">
              Topology inventory could not be loaded. {error}
            </div>
          ) : job?.kind === 'topology' && !job.running && job.status !== 'completed' ? (
            <div className="topology-state topology-state-error" role="alert">
              Topology creation failed. {job.error || `Job status: ${job.status}`}
            </div>
          ) : (
            <div className="topology-state" role="status">
              No NS-3 topology inventory is available yet. Set a node count and choose “Create Nodes”; the graph will appear after NS-3 confirms the created nodes and links.
            </div>
          )}
        </Panel>
        <Panel title="Configured route paths" description="Route overlays are matched against the actual NS-3 link inventory; a missing configured hop is not drawn.">
          <div className="route-cards">
            {Object.entries(ROUTE_PATHS).map(([route, path]) => (
              <article className={`route-card route-${route.toLowerCase()}`} key={route}>
                <span>ROUTE {route} · {path.length - 2} ROUTER HOPS</span>
                <strong>{path.join(' → ')}</strong>
              </article>
            ))}
            <article className="route-card route-dynamic">
              <span>DYNAMIC ROUTING</span>
              <strong>Route A initially → Route B after the recorded switch event</strong>
            </article>
          </div>
        </Panel>
        <Panel title="Configured network" description="Configuration read from the existing simulation source.">
          <div className="detail-grid">
            <Detail label="Actual nodes created" value={createdTopology ? createdTopology.node_count : 'Not created'} />
            <Detail label="Point-to-point links" value={createdTopology ? `${createdTopology.links.length} links` : 'Not created'} />
            <Detail label="Data rate" value={createdTopology?.links?.[0]?.bandwidth || 'Not available'} />
            <Detail label="Link delay" value={createdTopology?.links?.[0]?.delay_seconds != null ? displayMetric(createdTopology.links[0].delay_seconds * 1000, 'ms') : 'Not available'} />
            <Detail label="Addressing" value="IPv4, one /24 subnet per link" />
            <Detail label="Routing modes" value="Route A, Route B, Dynamic, Global" />
          </div>
        </Panel>
      </>
    )
  }

  function renderSimulation() {
    const simulationGraphJob = job?.kind === 'simulation'
      && (job.running || job.mode?.toLowerCase() === settings.route.toLowerCase())
      ? job
      : null
    const simulationTopology = createdTopology || status?.latest_topology || simulationGraphJob?.topology
    return (
      <>
        <Panel
          title="Simulation settings"
          description="Configure routing and HTTP-like TCP traffic for the next NS-3 run."
          action={simulationAction()}
        >
          <form id="simulation-settings" onSubmit={startSimulation}>
            <section className="settings-group" aria-labelledby="routing-settings-title">
              <div className="settings-group-heading">
                <h3 id="routing-settings-title">Routing strategy</h3>
                <p>Choose a fixed path or schedule a dynamic switch.</p>
              </div>
              <div className="form-grid">
                <Field label="Routing mode">
                  <select name="route" value={settings.route} onChange={updateSetting}>
                    <option value="A">Route A</option>
                    <option value="B">Route B</option>
                    <option value="dynamic">Dynamic (A → B)</option>
                  </select>
                </Field>
                <NumberField
                  name="dynamic_switch_time"
                  label="Dynamic switch time (s)"
                  min="0.01"
                  max="120"
                  step="0.1"
                  value={settings.dynamic_switch_time}
                  onChange={updateSetting}
                  disabled={settings.route !== 'dynamic'}
                />
                <NumberField name="weight_delay" label="Delay weight" min="0" max="1000" step="0.05" value={settings.weight_delay} onChange={updateSetting} />
                <NumberField name="weight_loss" label="Packet-loss weight" min="0" max="1000" step="0.05" value={settings.weight_loss} onChange={updateSetting} />
                <NumberField name="weight_queue" label="Queue-occupancy weight" min="0" max="1000" step="0.05" value={settings.weight_queue} onChange={updateSetting} />
                <NumberField name="weight_hops" label="Router-hop weight" min="0" max="1000" step="0.05" value={settings.weight_hops} onChange={updateSetting} />
                <NumberField name="monitor_interval" label="Monitor interval (s)" min="0.1" max="60" step="0.1" value={settings.monitor_interval} onChange={updateSetting} />
              </div>
              <p className="form-help">
                At least one route-cost weight must be positive. Dynamic switch time is validated against the configured traffic window.
              </p>
            </section>
            <section className="settings-group" aria-labelledby="traffic-settings-title">
              <div className="settings-group-heading">
                <h3 id="traffic-settings-title">Traffic workload</h3>
                <p>Traffic profiles set initial values that you can customize.</p>
              </div>
              <div className="form-grid">
                <Field label="Traffic profile">
                  <select name="traffic_load" value={settings.traffic_load} onChange={updateProfile}>
                    <option value="low">Low</option>
                    <option value="medium">Medium</option>
                    <option value="high">High</option>
                  </select>
                </Field>
                <NumberField name="http_client_count" label="HTTP clients" min="1" max="20" value={settings.http_client_count} onChange={updateSetting} />
                <NumberField name="http_request_count" label="Requests per client" min="1" max="50" value={settings.http_request_count} onChange={updateSetting} />
                <NumberField name="http_request_interval" label="Request interval (s)" min="0.05" max="30" step="0.05" value={settings.http_request_interval} onChange={updateSetting} />
                <NumberField name="http_request_size" label="Request body size (bytes)" min="1" max="1000000" value={settings.http_request_size} onChange={updateSetting} />
                <NumberField name="http_response_size" label="Response body size (bytes)" min="1" max="1000000" value={settings.http_response_size} onChange={updateSetting} />
              </div>
            </section>
            <div className="form-actions simulation-form-actions">
              <span>Ready to run: <strong>{settings.route === 'dynamic' ? 'Dynamic' : `Route ${settings.route}`}</strong> · {settings.traffic_load} traffic profile</span>
              <button className="primary-button" type="submit" disabled={actionDisabled}>
                {actionDisabled ? 'Simulation running…' : 'Run simulation'}
              </button>
            </div>
          </form>
        </Panel>
        <Panel
          title="HTTP-like TCP packet traversal"
          description="Moving envelopes replay observed TCP transmit events hop by hop on their actual NS-3 links. Packet IDs are not correlated with HTTP request IDs, and a transmission does not confirm delivery."
        >
          {simulationTopology ? (
            <>
              <div className={`simulation-graph-state ${simulationGraphJob?.running ? 'is-live' : ''}`}>
                <span className="simulation-graph-status-dot" />
                {simulationGraphJob?.running
                  ? 'Live NS-3 packet traversal · transmit events only'
                  : simulationGraphJob?.status === 'completed'
                    ? 'Completed run · replaying recorded TCP transmissions'
                    : `Route ${settings.route === 'dynamic' ? 'A → B (Dynamic)' : settings.route} preview · run to see observed TCP packets`}
              </div>
              <TopologyGraph
                key={`simulation-${simulationTopology.run_id || simulationTopology.job_id || 'latest'}`}
                topology={simulationTopology}
                routeMode={simulationGraphJob?.running ? simulationGraphJob.mode : settings.route}
                simulationSettings={simulationGraphJob?.running ? simulationGraphJob.settings : settings}
                simulationMetrics={simulationGraphJob?.metrics}
                simulationJob={simulationGraphJob}
              />
            </>
          ) : loading ? (
            <div className="topology-state" role="status">Loading the latest NS-3 node and link inventory…</div>
          ) : error ? (
            <div className="topology-state topology-state-error" role="alert">
              Network inventory is unavailable. {error}
            </div>
          ) : (
            <div className="topology-state">
              No NS-3 topology is available. Create a topology in Network Topology before running traffic.
            </div>
          )}
        </Panel>
        <Panel title="Latest saved traffic results" description="Values come from the matching run CSV and console log; unavailable values are not replaced with zero.">
          <RunSummary runs={savedRuns} trafficOnly />
        </Panel>
        <Panel title="Latest route monitoring" description="Latest sample per route from each saved run; partial measurements remain unavailable.">
          <RouteMonitoring runs={savedRuns} />
        </Panel>
      </>
    )
  }

  function renderPerformanceCharts() {
    return <PerformanceCharts runs={runHistory.length ? runHistory : savedRuns} loading={loading} />
  }

  function renderReports() {
    const reportRun = latestReport
    const reportMetrics = reportRun?.metrics || {}
    const reportTopology = reportRun?.topology
    const reportSettings = reportRun?.settings || {}
    const routeName = reportRun
      ? reportRun.mode === 'dynamic' ? 'Dynamic' : `Route ${reportRun.mode}`
      : 'Not available'
    const responseSummary = reportMetrics.successful_responses != null
      && reportMetrics.failed_missing_responses != null
      ? `${reportMetrics.successful_responses} successful responses and ${reportMetrics.failed_missing_responses} failed or missing responses were recorded for ${routeName}.`
      : 'Response summary is unavailable because the run did not include complete response counts.'
    return (
      <Panel title="Simulation Report" description="This report and its downloads always reflect the latest completed traffic simulation. Topology-only jobs are excluded.">
        {!reportRun ? (
          <EmptyState>Run a traffic simulation to create a report. Creating a topology alone does not produce performance results.</EmptyState>
        ) : (
          <>
            <div className="report-summary-cards">
              <MetricCard label="Simulation status" value={reportRun.status === 'completed' ? 'Completed' : reportRun.status} note={`TCP validation ${reportRun.tcp_validation}`} />
              <MetricCard label="Route mode" value={routeName} note={`${reportRun.node_count ?? 'Unknown'} configured nodes`} />
              <MetricCard label="Average latency" value={displayMetric(reportMetrics.average_rtt_seconds == null ? null : reportMetrics.average_rtt_seconds * 1000, 'ms')} />
              <MetricCard label="Application throughput" value={displayMetric(reportMetrics.application_response_body_throughput_bits_per_second, 'bit/s')} />
              <MetricCard label="Successful responses" value={displayMetric(reportMetrics.successful_responses)} />
              <MetricCard label="Failed or missing responses" value={displayMetric(reportMetrics.failed_missing_responses)} />
            </div>
            <div className="report-downloads">
              <a className="report-download" href={`${API}/api/reports/latest.txt`} download>
                <span className="file-badge">TXT</span><span>Download TXT Report<small>Latest completed traffic simulation</small></span>
              </a>
              <a className="report-download" href={`${API}/api/reports/latest.csv`} download>
                <span className="file-badge">CSV</span><span>Download CSV Metrics<small>Measurements from {reportRun.id}</small></span>
              </a>
            </div>
            <section className="report-detail-section">
              <h3>Simulation configuration</h3>
              <div className="table-scroll">
                <table>
                  <tbody>
                    <tr><th>Route mode</th><td>{routeName}</td></tr>
                    <tr><th>Configured node count</th><td>{reportRun.node_count ?? 'Not available'}</td></tr>
                    <tr><th>Traffic profile</th><td>{reportSettings.traffic_load || 'Not available'}</td></tr>
                    <tr><th>HTTP clients</th><td>{displayMetric(reportSettings.http_client_count)}</td></tr>
                    <tr><th>Requests per client</th><td>{displayMetric(reportSettings.http_request_count)}</td></tr>
                    <tr><th>Request interval</th><td>{displayMetric(reportSettings.http_request_interval, 's')}</td></tr>
                    <tr><th>Request / response body size</th><td>{displayMetric(reportSettings.http_request_size, 'bytes')} / {displayMetric(reportSettings.http_response_size, 'bytes')}</td></tr>
                    {reportRun.mode === 'dynamic' && <tr><th>Route switch (A → B)</th><td>{displayMetric(reportMetrics.dynamic_switch_time_seconds ?? reportSettings.dynamic_switch_time, 's')}</td></tr>}
                  </tbody>
                </table>
              </div>
            </section>
            <section className="report-detail-section">
              <h3>Node and link summary</h3>
              <p>{reportTopology?.nodes?.length ?? reportRun.node_count ?? 'Not available'} nodes · {reportTopology?.links?.length ?? 0} links</p>
              {reportTopology?.nodes?.length > 0 && (
                <div className="table-scroll report-inventory">
                  <table>
                    <thead><tr><th>Node</th><th>Role</th><th>IP addresses</th></tr></thead>
                    <tbody>{reportTopology.nodes.map((node) => <tr key={node.index}><td>{node.index}</td><td>{node.role}</td><td>{node.ip_addresses?.join(', ') || 'Not available'}</td></tr>)}</tbody>
                  </table>
                </div>
              )}
              {reportTopology?.links?.length > 0 && (
                <div className="table-scroll report-inventory">
                  <table>
                    <thead><tr><th>Link</th><th>Endpoints</th><th>Bandwidth</th><th>Delay</th></tr></thead>
                    <tbody>{reportTopology.links.map((link) => <tr key={link.link}><td>{link.link}</td><td>{link.endpoint_a} — {link.endpoint_b}</td><td>{link.bandwidth || 'Not available'}</td><td>{link.delay_seconds == null ? 'Not available' : displayMetric(link.delay_seconds * 1000, 'ms')}</td></tr>)}</tbody>
                  </table>
                </div>
              )}
            </section>
            <section className="report-detail-section">
              <h3>Performance measurements</h3>
              <div className="table-scroll">
                <table>
                  <tbody>
                    <tr><th>Average latency</th><td>{displayMetric(reportMetrics.average_rtt_seconds, 's')}</td></tr>
                    <tr><th>Maximum latency</th><td>{displayMetric(reportMetrics.maximum_rtt_seconds, 's')}</td></tr>
                    <tr><th>Application response-body throughput</th><td>{displayMetric(reportMetrics.application_response_body_throughput_bits_per_second, 'bit/s')}</td></tr>
                    <tr><th>Successful HTTP responses</th><td>{displayMetric(reportMetrics.successful_responses, 'responses')}</td></tr>
                    <tr><th>Failed or missing HTTP responses</th><td>{displayMetric(reportMetrics.failed_missing_responses, 'responses')}</td></tr>
                  </tbody>
                </table>
              </div>
            </section>
            <div className="report-result-summary"><strong>Result summary</strong><p>{responseSummary}</p></div>
          </>
        )}
      </Panel>
    )
  }

  const pageContent = {
    Overview: renderOverview,
    'Network Topology': renderTopology,
    'Run Simulation': renderSimulation,
    Analytics: renderPerformanceCharts,
    Reports: renderReports,
  }[activeTab]()

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">W</div>
          <div><strong>WebFlow</strong><span>Network Analyzer</span></div>
        </div>
        <div className="nav-label">WORKSPACE</div>
        {TABS.map((item) => (
          <button
            key={item}
            className={`nav-item ${activeTab === item ? 'active' : ''}`}
            onClick={() => setActiveTab(item)}
            aria-current={activeTab === item ? 'page' : undefined}
          >
            <svg className="nav-icon" viewBox="0 0 24 24" aria-hidden="true">{NAV_ICONS[item]}</svg>
            {item}
          </button>
        ))}
        <div className="sidebar-bottom">
          <span className={`status-dot ${health?.status === 'running' ? 'online' : ''}`} />
          <div>
            <strong>API connection</strong>
            <span>{health?.status === 'running' ? 'Connected' : loading ? 'Checking connection' : 'Unavailable'}</span>
          </div>
        </div>
      </aside>

      <main className="main-content">
        <header className="topbar">
          <div className="page-heading">
            <div className="breadcrumb">WebFlow / Workspace</div>
            <h1>{activeTab}</h1>
          </div>
          <div className="topbar-actions">
            <div className="header-statuses" aria-label="Service status">
              <span className={`header-status ${health?.status === 'running' ? 'online' : ''}`}>
                <i className="status-dot" />
                Backend {health?.status === 'running' ? 'online' : loading ? 'checking' : 'offline'}
              </span>
              <span className={`header-status ${job?.running ? 'running' : job?.status === 'completed' ? 'complete' : job?.status && job.status !== 'idle' ? 'failed' : ''}`}>
                <i className="status-dot" />
                {job?.running
                  ? `Simulation running${job.mode ? ` · ${job.mode}` : ''}`
                  : job?.status === 'completed'
                    ? 'Simulation completed'
                    : job?.status && job.status !== 'idle'
                      ? `Simulation ${job.status.replaceAll('_', ' ')}`
                      : health?.simulation === 'running'
                        ? 'Simulation running'
                        : health?.status === 'running' ? 'Simulation idle' : 'Simulation unavailable'}
              </span>
            </div>
            <button className="refresh-button" onClick={loadDashboard} disabled={loading}>
              {loading ? 'Refreshing…' : 'Refresh data'}
            </button>
          </div>
        </header>

        {error && <div className="error-banner" role="alert">{error}</div>}
        {notice && <div className="success-banner" role="status">{notice}</div>}
        {job && (
          <section className={`job-banner ${job.running ? 'job-running' : job.status === 'completed' ? 'job-complete' : 'job-failed'}`}>
            <div className="job-header">
              <div>
                <strong>{job.running ? 'Work in progress' : job.status === 'completed' ? 'Process completed' : 'Process did not complete successfully'}</strong>
                <span>{job.kind === 'comparison'
                  ? 'Route A / Route B / Dynamic workflow'
                  : job.kind === 'topology'
                    ? 'NS-3 topology creation'
                    : `Route ${job.mode || ''} simulation`}</span>
              </div>
              <span>{job.running ? 'Running' : job.exit_code === null ? job.status : `Exit code ${job.exit_code}`}</span>
            </div>
            {job.error && <p className="job-error">{job.error}</p>}
            {job.run_directory && <p>Result directory: <code>{job.run_directory}</code></p>}
            {!job.running && job.kind === 'simulation' && job.metrics && (
              <div className="current-result">
                <strong>Current simulation result · TCP validation {job.tcp_validation || 'Not available'}</strong>
                <span>Successful responses: {displayMetric(job.metrics.successful_responses)}</span>
                <span>Failed/missing responses: {displayMetric(job.metrics.failed_missing_responses)}</span>
                <span>Average RTT: {displayMetric(job.metrics.average_rtt_seconds, 's')}</span>
                <span>Maximum RTT: {displayMetric(job.metrics.maximum_rtt_seconds, 's')}</span>
                <span>Application response-body throughput: {displayMetric(job.metrics.application_response_body_throughput_bits_per_second, 'bit/s')}</span>
                <span>Simulation duration: {displayMetric(job.metrics.simulation_duration_seconds, 's')}</span>
                <span>Measured application interval: {displayMetric(job.metrics.measured_application_interval_seconds, 's')}</span>
                {job.mode === 'dynamic' && <small>Dynamic metrics are whole-run measurements and are not attributable to one route.</small>}
              </div>
            )}
            {job.output_tail && (
              <details className="job-output">
                <summary>NS-3 terminal stdout</summary>
                <pre>{job.output_tail}</pre>
              </details>
            )}
            {job.stderr_tail && (
              <details className="job-output">
                <summary>Process stderr</summary>
                <pre>{job.stderr_tail}</pre>
              </details>
            )}
            {!job.running && job.status === 'completed' && job.kind === 'simulation' && (
              <p>Process exited successfully. Check TCP validation in the matching saved run below.</p>
            )}
          </section>
        )}

        {pageContent}

        <footer>
          <span>WebFlow · HTTP Traffic &amp; Network Performance Analyzer</span>
          <span>{results?.results_directory || 'FastAPI · Port 8000'}</span>
        </footer>
      </main>
    </div>
  )
}

function Field({ label, children }) {
  return <label className="form-field"><span>{label}</span>{children}</label>
}

function NumberField({ name, label, value, onChange, min, max, step = '1', disabled = false }) {
  return (
    <Field label={label}>
      <input
        type="number"
        name={name}
        value={value}
        onChange={onChange}
        min={min}
        max={max}
        step={step}
        disabled={disabled}
        required
      />
    </Field>
  )
}

function Detail({ label, value }) {
  return <div className="detail-item"><span>{label}</span><strong>{value}</strong></div>
}

function EmptyState({ children }) {
  return <div className="empty-state">{children}</div>
}

function TopologyGraph({ topology, routeMode, simulationSettings, simulationMetrics, simulationJob }) {
  const svgRef = useRef(null)
  const scrollRef = useRef(null)
  const panRef = useRef(null)
  const [viewBox, setViewBox] = useState(null)
  const [selection, setSelection] = useState(null)
  const [animatedPacketEvents, setAnimatedPacketEvents] = useState([])
  const [playbackKey, setPlaybackKey] = useState(0)
  const [animationNow, setAnimationNow] = useState(() => performance.now())
  const seenPacketEventsRef = useRef(new Set())
  const animationRunKeyRef = useRef(null)
  const [reduceMotion, setReduceMotion] = useState(() => (
    typeof window !== 'undefined'
    && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  ))
  useEffect(() => {
    const preference = window.matchMedia?.('(prefers-reduced-motion: reduce)')
    if (!preference) return undefined
    const updatePreference = (event) => setReduceMotion(event.matches)
    preference.addEventListener('change', updatePreference)
    return () => preference.removeEventListener('change', updatePreference)
  }, [])
  const nodes = topology.nodes || []
  const isCoreRole = (role) => /^r\d+$/i.test(role)
  const auxiliaryNodes = nodes.filter((node) => (
    !node.supported_source
    && !node.supported_destination
    && !isCoreRole(node.role)
  ))
  const graphWidth = 1240
  const auxiliaryRows = Math.ceil(auxiliaryNodes.length / 8)
  const graphHeight = auxiliaryRows ? 565 + (auxiliaryRows - 1) * 82 : 500
  const positions = new Map()
  const corePositions = {
    client: [90, 250],
    r1: [280, 250],
    r2: [485, 125],
    r3: [485, 375],
    r4: [700, 375],
    r5: [910, 250],
    server: [1115, 250],
  }
  nodes.forEach((node) => {
    const role = node.role.toLowerCase()
    const position = corePositions[role]
    if (position) {
      positions.set(node.index, position)
    } else {
      const auxiliaryIndex = auxiliaryNodes.findIndex((item) => item.index === node.index)
      positions.set(node.index, [80 + (auxiliaryIndex % 8) * 145, 510 + Math.floor(auxiliaryIndex / 8) * 82])
    }
  })
  const nodesByRole = new Map(nodes.map((node) => [node.role.toLowerCase(), node]))
  const pairKey = (first, second) => [first.toLowerCase(), second.toLowerCase()].sort().join('|')
  const routeLinks = Object.fromEntries(Object.entries(ROUTE_PATHS).map(([route, path]) => {
    const configuredHops = new Set(path.slice(1).map((role, index) => pairKey(path[index], role)))
    return [route, new Set((topology.links || [])
      .filter((link) => configuredHops.has(pairKey(link.endpoint_a || '', link.endpoint_b || '')))
      .map((link) => pairKey(link.endpoint_a || '', link.endpoint_b || '')))]
  }))
  const normalizedMode = routeMode?.toLowerCase()
  const dynamicMode = normalizedMode === 'dynamic'
  const events = simulationJob?.events || []
  const clientNode = nodes.find((node) => node.supported_source)
    || nodes.find((node) => node.role.toLowerCase() === 'client')
  const serverNode = nodes.find((node) => node.supported_destination)
    || nodes.find((node) => node.role.toLowerCase() === 'server')
  const clientIp = clientNode?.ip_address || clientNode?.ip_addresses?.[0]
  const serverIp = serverNode?.ip_address || serverNode?.ip_addresses?.[0]
  const packetEvents = events.filter((event) => (
    event.event_type === 'packet'
    && event.packet_id != null
    && event.current_node
    && event.next_hop
    && topology.links?.some((link) => (
      pairKey(link.endpoint_a || '', link.endpoint_b || '')
      === pairKey(event.current_node, event.next_hop)
    ))
    && ((event.source === clientIp && event.destination === serverIp)
      || (event.source === serverIp && event.destination === clientIp))
  )).sort((first, second) => first.timestamp_seconds - second.timestamp_seconds)
  const latestPacketEvent = packetEvents[packetEvents.length - 1]
  const animationRunKey = simulationJob?.job_id || simulationJob?.run_directory || null
  function packetPlaybackEvents(sourceEvents, playbackStartedAt, stagger = true) {
    const replayEvents = sourceEvents.slice(-20)
    const firstTimestamp = replayEvents[0]?.timestamp_seconds
    const timestampSpan = replayEvents.length > 1
      ? replayEvents[replayEvents.length - 1].timestamp_seconds - firstTimestamp
      : 0
    const replayScale = timestampSpan > 0 ? 2400 / timestampSpan : 0
    return replayEvents.map((event) => ({
      ...event,
      event_id: event.event_id
        || `${event.timestamp_seconds}-${event.packet_id}-${event.current_node}-${event.next_hop}`,
      animation_started_at: playbackStartedAt + (stagger
        ? Math.round((event.timestamp_seconds - firstTimestamp) * replayScale)
        : 0),
    }))
  }
  useEffect(() => {
    if (animationRunKeyRef.current !== animationRunKey) {
      animationRunKeyRef.current = animationRunKey
      seenPacketEventsRef.current.clear()
      setAnimatedPacketEvents([])
    }
    if (!animationRunKey) return

    const newEvents = packetEvents.filter((event) => {
      const eventId = event.event_id
        || `${event.timestamp_seconds}-${event.packet_id}-${event.current_node}-${event.next_hop}`
      if (seenPacketEventsRef.current.has(eventId)) return false
      seenPacketEventsRef.current.add(eventId)
      return true
    })
    if (!newEvents.length) return

    window.requestAnimationFrame((playbackStartedAt) => {
      if (animationRunKeyRef.current !== animationRunKey) return
      const additions = packetPlaybackEvents(newEvents, playbackStartedAt, !simulationJob?.running)
      setAnimatedPacketEvents((current) => [...current, ...additions].slice(-20))
    })
  }, [animationRunKey, packetEvents, simulationJob?.running])
  useEffect(() => {
    if (reduceMotion || animatedPacketEvents.length === 0) return undefined
    const timer = window.setInterval(() => {
      const now = performance.now()
      setAnimationNow(now)
      setAnimatedPacketEvents((current) => {
        const active = current.filter((event) => now < event.animation_started_at + 700)
        return active.length === current.length ? current : active
      })
    }, 32)
    return () => window.clearInterval(timer)
  }, [animatedPacketEvents.length, reduceMotion])
  const recentHttpEvents = events
    .filter((event) => event.event_type === 'http_request' || event.event_type === 'http_response')
    .slice(-8)
    .reverse()
  const logSwitch = simulationJob?.output_tail?.match(
    /DYNAMIC ROUTE SWITCH: A -> B\b.*?timestamp_seconds=([0-9.eE+-]+)/,
  )
  const reportedSwitchTime = simulationMetrics?.dynamic_switch_time_seconds
  const actualSwitchTime = typeof reportedSwitchTime === 'number' && Number.isFinite(reportedSwitchTime)
    ? reportedSwitchTime
    : logSwitch ? Number(logSwitch[1]) : null
  const configuredSwitchTime = simulationSettings?.dynamic_switch_time
  const observedRouteBEvent = events.find((event) => (
    event.event_type === 'packet' && event.route === 'B'
  ))
  const switchConfirmed = actualSwitchTime !== null && Number.isFinite(actualSwitchTime)
    || Boolean(observedRouteBEvent)
  const activeRoute = dynamicMode
    ? latestPacketEvent?.route === 'B' || switchConfirmed ? 'B' : 'A'
    : normalizedMode === 'a' || normalizedMode === 'b'
      ? normalizedMode.toUpperCase()
      : null
  const dynamicRouteState = switchConfirmed
    ? actualSwitchTime !== null && Number.isFinite(actualSwitchTime)
      ? `Route A → Route B transition recorded at ${displayMetric(actualSwitchTime, 's')}; Route B active`
      : `Route B first observed in TCP packet events at ${displayMetric(observedRouteBEvent.timestamp_seconds, 's')}`
    : simulationJob?.running
      ? `Route A active · scheduled switch at ${displayMetric(configuredSwitchTime, 's')} · waiting for an observed switch event`
      : `Route A preview · configured switch time ${displayMetric(configuredSwitchTime, 's')}; no run event is available`
  const currentViewBox = viewBox || { x: 0, y: 0, width: graphWidth, height: graphHeight }
  const selectedNode = selection?.type === 'node'
    ? nodes.find((node) => node.index === selection.id)
    : null
  const selectedLink = selection?.type === 'link'
    ? (topology.links || []).find((link) => link.link === selection.id)
    : null

  function zoom(factor) {
    setViewBox((current) => {
      const box = current || { x: 0, y: 0, width: graphWidth, height: graphHeight }
      const width = Math.max(420, Math.min(graphWidth * 2, box.width / factor))
      const height = Math.max(300, Math.min(graphHeight * 2, box.height / factor))
      return {
        x: box.x + (box.width - width) / 2,
        y: box.y + (box.height - height) / 2,
        width,
        height,
      }
    })
  }

  function fitToScreen() {
    if (!nodes.length) return
    const coordinates = [...positions.values()]
    const padding = 70
    const minX = Math.min(...coordinates.map(([x]) => x)) - padding
    const maxX = Math.max(...coordinates.map(([x]) => x)) + padding
    const minY = Math.min(...coordinates.map(([, y]) => y)) - padding
    const maxY = Math.max(...coordinates.map(([, y]) => y)) + padding
    const boundsWidth = maxX - minX
    const boundsHeight = maxY - minY
    const viewportWidth = scrollRef.current?.clientWidth || graphWidth
    const viewportHeight = scrollRef.current?.clientHeight || Math.min(graphHeight, 600)
    const aspectRatio = viewportWidth / viewportHeight
    const fittedWidth = Math.max(boundsWidth, boundsHeight * aspectRatio)
    const fittedHeight = Math.max(boundsHeight, boundsWidth / aspectRatio)
    const centerX = (minX + maxX) / 2
    const centerY = (minY + maxY) / 2
    setViewBox({
      x: centerX - fittedWidth / 2,
      y: centerY - fittedHeight / 2,
      width: fittedWidth,
      height: fittedHeight,
    })
    if (scrollRef.current) {
      scrollRef.current.scrollTo({ left: 0, top: 0 })
    }
  }

  function resetView() {
    setViewBox(null)
    if (scrollRef.current) {
      scrollRef.current.scrollTo({ left: 0, top: 0 })
    }
  }

  function replayRecordedPackets() {
    window.requestAnimationFrame((playbackStartedAt) => {
      setAnimatedPacketEvents(packetPlaybackEvents(packetEvents, playbackStartedAt))
    })
    setPlaybackKey((current) => current + 1)
  }

  function startPan(event) {
    if (event.target !== svgRef.current && !event.target.classList.contains('topology-background')) return
    panRef.current = {
      x: event.clientX,
      y: event.clientY,
      viewBox: currentViewBox,
    }
    event.currentTarget.setPointerCapture(event.pointerId)
  }

  function movePan(event) {
    if (!panRef.current || !svgRef.current) return
    const bounds = svgRef.current.getBoundingClientRect()
    const start = panRef.current
    setViewBox({
      ...start.viewBox,
      x: start.viewBox.x - ((event.clientX - start.x) / bounds.width) * start.viewBox.width,
      y: start.viewBox.y - ((event.clientY - start.y) / bounds.height) * start.viewBox.height,
    })
  }

  function activateWithKeyboard(event, action) {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      action()
    }
  }

  return (
    <div className="topology-graph">
      <div className="topology-graph-toolbar">
        <div className="topology-legend" aria-label="Topology legend">
          <span><i className="legend-client" />Client</span>
          <span><i className="legend-server" />Server</span>
          <span><i className="legend-router" />Core router</span>
          <span><i className="legend-auxiliary" />Auxiliary host</span>
          <span><i className="legend-route-a" />Route A path</span>
          <span><i className="legend-route-b" />Route B path</span>
          <span><i className="legend-request-message" />Client-to-server packet</span>
          <span><i className="legend-response-message" />Server-to-client packet</span>
        </div>
        <div className="graph-controls" aria-label="Graph view controls">
          <button type="button" onClick={() => zoom(1.2)} aria-label="Zoom in">+</button>
          <button type="button" onClick={() => zoom(1 / 1.2)} aria-label="Zoom out">−</button>
          <button type="button" onClick={fitToScreen}>Fit to screen</button>
          <button type="button" onClick={resetView}>Reset view</button>
          {packetEvents.length > 0 && !simulationJob?.running && (
            <button type="button" onClick={replayRecordedPackets}>Replay observed packets</button>
          )}
        </div>
      </div>
      <div className="topology-graph-scroll" ref={scrollRef}>
        <svg
          ref={svgRef}
          className="topology-graph-svg"
          viewBox={`${currentViewBox.x} ${currentViewBox.y} ${currentViewBox.width} ${currentViewBox.height}`}
          role="img"
          aria-label={`${nodes.length}-node NS-3 network topology with ${topology.links?.length || 0} links`}
          style={{ minWidth: viewBox ? 0 : undefined }}
          onPointerDown={startPan}
          onPointerMove={movePan}
          onPointerUp={() => { panRef.current = null }}
          onPointerCancel={() => { panRef.current = null }}
        >
          <rect className="topology-background" x="-10000" y="-10000" width="30000" height="30000" fill="#fbfcfd" />
          {(topology.links || []).map((link) => {
            const source = nodesByRole.get((link.endpoint_a || '').toLowerCase())
            const target = nodesByRole.get((link.endpoint_b || '').toLowerCase())
            if (!source || !target) return null
            const [x1, y1] = positions.get(source.index) || [0, 0]
            const [x2, y2] = positions.get(target.index) || [0, 0]
            const actualPair = pairKey(link.endpoint_a || '', link.endpoint_b || '')
            const onRouteA = routeLinks.A.has(actualPair)
            const onRouteB = routeLinks.B.has(actualPair)
            const routeColor = onRouteA && onRouteB
              ? activeRoute === 'B' ? '#c3974f' : activeRoute === 'A' ? '#579476' : '#6481a3'
              : onRouteA
                ? '#579476'
                : onRouteB
                  ? '#c3974f'
                  : '#b7c0cc'
            const isActiveRouteLink = activeRoute === 'A' ? onRouteA : activeRoute === 'B' ? onRouteB : false
            const selected = selection?.type === 'link' && selection.id === link.link
            const delay = link.delay_seconds == null ? 'delay unavailable' : `${link.delay_seconds * 1000} ms`
            const bandwidth = link.bandwidth || 'bandwidth unavailable'
            const details = `${link.endpoint_a} to ${link.endpoint_b}; ${bandwidth}; ${delay}`
            const middleX = (x1 + x2) / 2
            const middleY = (y1 + y2) / 2
            return (
              <g
                key={link.link}
                className={`topology-graph-link ${onRouteA ? 'route-a-link' : ''} ${onRouteB ? 'route-b-link' : ''} ${isActiveRouteLink ? 'active-route-link' : ''}`}
                role="button"
                tabIndex={0}
                aria-label={`Link ${link.endpoint_a} to ${link.endpoint_b}, ${details}. Click for link details.`}
                onClick={() => setSelection({ type: 'link', id: link.link })}
                onKeyDown={(event) => activateWithKeyboard(event, () => setSelection({ type: 'link', id: link.link }))}
              >
                <title>{details}</title>
                <line x1={x1} y1={y1} x2={x2} y2={y2} stroke="transparent" strokeWidth="18" />
                <line
                  x1={x1}
                  y1={y1}
                  x2={x2}
                  y2={y2}
                  stroke={routeColor}
                  strokeWidth={selected ? 4 : onRouteA || onRouteB ? 2.5 : 1.5}
                />
                {!auxiliaryNodes.includes(source) && !auxiliaryNodes.includes(target) && (
                  <text x={middleX} y={middleY - 8} className="topology-edge-label">
                    {link.bandwidth || 'Bandwidth n/a'} · {link.delay_seconds == null ? 'Delay n/a' : `${link.delay_seconds * 1000} ms`}
                  </text>
                )}
              </g>
            )
          })}
          {animatedPacketEvents.map((packetEvent) => {
            const from = nodesByRole.get((packetEvent.current_node || '').toLowerCase())
            const to = nodesByRole.get((packetEvent.next_hop || '').toLowerCase())
            if (!from || !to) return null
            const [fromX, fromY] = positions.get(from.index) || [0, 0]
            const [toX, toY] = positions.get(to.index) || [0, 0]
            const midpointX = (fromX + toX) / 2
            const midpointY = (fromY + toY) / 2
            const progress = reduceMotion
              ? 0.5
              : Math.max(0, Math.min(1, (animationNow - packetEvent.animation_started_at) / 700))
            const messageX = fromX + (toX - fromX) * progress
            const messageY = fromY + (toY - fromY) * progress
            const isRequestDirection = packetEvent.source === clientIp
              && packetEvent.destination === serverIp
            const messageDirection = isRequestDirection ? 'request-direction' : 'response-direction'
            const directionLabel = isRequestDirection ? 'Client to Server' : 'Server to Client'
            return (
              <g
                key={`${animationRunKey}-${playbackKey}-${packetEvent.event_id}`}
                className={`observed-message-packet ${messageDirection}`}
                aria-label={`Observed TCP packet ${packetEvent.packet_id}, ${directionLabel}`}
              >
                <title>
                  TCP packet {packetEvent.packet_id} · observed transmit {packetEvent.current_node} to {packetEvent.next_hop} · {directionLabel} · Route {packetEvent.route} · {displayMetric(packetEvent.timestamp_seconds, 's')}. Transmission only; delivery is not confirmed.
                </title>
                <g
                  transform={`translate(${reduceMotion ? midpointX : messageX} ${reduceMotion ? midpointY : messageY})`}
                  opacity={reduceMotion || animationNow >= packetEvent.animation_started_at ? 1 : 0}
                >
                  <rect x="-20" y="-15" width="40" height="30" rx="6" />
                  <path className="message-envelope-fold" d="m-17 -12 17 14 17-14" />
                  <text x="25" y="5" className="packet-event-label">TCP #{packetEvent.packet_id}</text>
                </g>
              </g>
            )
          })}
          {nodes.map((node) => {
            const [x, y] = positions.get(node.index) || [0, 0]
            const role = node.role.toLowerCase()
            const isClient = Boolean(node.supported_source) || role === 'client'
            const isServer = Boolean(node.supported_destination) || role === 'server'
            const isRouter = isCoreRole(node.role)
            const isAuxiliary = !isClient && !isServer && !isRouter
            const selected = selectedNode?.index === node.index
            const nodeClass = isClient
              ? 'node-client'
              : isServer
                ? 'node-server'
                : isRouter
                  ? 'node-router'
                  : 'node-auxiliary'
            const shortRole = isAuxiliary ? 'Aux' : node.role
            const nodeColor = isClient ? '#e8f0fa' : isServer ? '#f0eafa' : isRouter ? '#eaf3ed' : '#f2eee6'
            return (
              <g
                key={node.index}
                className={`topology-graph-node ${nodeClass} ${selected ? 'selected' : ''}`}
                role="button"
                tabIndex={0}
                aria-label={`Node ${node.index}, ${node.role}. Click for node details.`}
                onClick={() => setSelection({ type: 'node', id: node.index })}
                onKeyDown={(event) => activateWithKeyboard(event, () => setSelection({ type: 'node', id: node.index }))}
              >
                <title>Node {node.index} · NS-3 ID {node.ns3_id ?? 'not available'} · {node.role}</title>
                {isAuxiliary || isServer
                  ? <rect x={x - 33} y={y - 23} width="66" height="46" rx={isAuxiliary ? 9 : 16} fill={nodeColor} />
                  : <circle cx={x} cy={y} r="27" fill={nodeColor} />}
                <text x={x} y={y + 4} className={`topology-node-role ${isAuxiliary ? 'auxiliary' : ''}`}>{shortRole}</text>
                <text x={x} y={y + 43} className="topology-node-id">Node {node.index}</text>
              </g>
            )
          })}
        </svg>
      </div>
      <div className="topology-route-status" aria-live="polite">
        <span className="topology-route-note">
          Route A: {routeLinks.A.size === ROUTE_PATHS.A.length - 1 ? 'all configured links present' : `inventory is missing ${ROUTE_PATHS.A.length - 1 - routeLinks.A.size} configured link(s)`}
        </span>
        <span className="topology-route-note">
          Route B: {routeLinks.B.size === ROUTE_PATHS.B.length - 1 ? 'all configured links present' : `inventory is missing ${ROUTE_PATHS.B.length - 1 - routeLinks.B.size} configured link(s)`}
        </span>
        {dynamicMode ? (
          <span className="topology-route-note">
            Dynamic simulation: {dynamicRouteState}. {simulationJob?.running ? 'Current route' : simulationJob?.status === 'completed' ? 'Last recorded route' : 'Preview route'}: Route {activeRoute}. Both configured paths are shown.
          </span>
        ) : normalizedMode === 'a' || normalizedMode === 'b' ? (
          <span className="topology-route-note">
            {simulationJob?.running ? 'Running simulation' : simulationJob ? 'Latest recorded run' : 'Route preview'}: Route {normalizedMode.toUpperCase()}.
          </span>
        ) : (
          <span className="topology-route-note">No routing mode is selected.</span>
        )}
        {latestPacketEvent ? (
          <span className="topology-route-note">
            Latest observed packet hop: {latestPacketEvent.source === clientIp ? 'Client → Server' : 'Server → Client'} · {latestPacketEvent.current_node} → {latestPacketEvent.next_hop} · Route {latestPacketEvent.route} · {displayMetric(latestPacketEvent.timestamp_seconds, 's')}.
          </span>
        ) : (
          <span className="topology-route-note">
            {simulationJob?.running ? 'Waiting for the first NS-3 TCP transmit event.' : 'No packet movement is shown until an NS-3 transmit event is available.'}
          </span>
        )}
      </div>
      <div className="http-event-feed" aria-live="polite">
        <div className="http-event-feed-heading">
          <strong>HTTP request/response lifecycle</strong>
          <span>Application-level events · not mapped to individual packet hops</span>
        </div>
        {recentHttpEvents.length ? (
          <ol className="http-event-list">
            {recentHttpEvents.map((event) => {
              const isResponse = event.event_type === 'http_response'
              const label = `${isResponse ? 'Response' : 'Request'} #${event.request_id ?? 'unknown'}`
              const stage = (event.stage || 'event').replaceAll('_', ' ')
              const validation = event.validation && event.validation !== 'PENDING'
                ? ` · ${event.validation}`
                : ''
              return (
                <li key={event.event_id} className={isResponse ? 'http-response-event' : 'http-request-event'}>
                  <span className="http-event-icon" aria-hidden="true">{isResponse ? '↩' : '✉'}</span>
                  <span className="http-event-copy">
                    <strong>{label}</strong>
                    <span>Client {event.client_id ?? 'unknown'} · {stage}{validation}</span>
                  </span>
                  <time>{displayMetric(event.timestamp_seconds, 's')}</time>
                </li>
              )
            })}
          </ol>
        ) : (
          <p className="http-event-empty">
            {simulationJob?.running ? 'Waiting for an observed HTTP lifecycle event.' : 'No HTTP lifecycle events are available for this run.'}
          </p>
        )}
      </div>
      <div className="topology-selection" aria-live="polite">
        {selectedNode ? (
          <>
            <strong>Node {selectedNode.index} · {selectedNode.role}</strong>
            <span>NS-3 ID: {selectedNode.ns3_id ?? 'Not available'}</span>
            <span>IP address{selectedNode.ip_addresses?.length === 1 ? '' : 'es'}: {selectedNode.ip_addresses?.join(', ') || 'Not available'}</span>
            <span>Connected neighbors: {(topology.links || [])
              .filter((link) => link.endpoint_a?.toLowerCase() === selectedNode.role?.toLowerCase()
                || link.endpoint_b?.toLowerCase() === selectedNode.role?.toLowerCase())
              .map((link) => link.endpoint_a?.toLowerCase() === selectedNode.role?.toLowerCase() ? link.endpoint_b : link.endpoint_a)
              .join(', ') || 'None'}</span>
          </>
        ) : selectedLink ? (
          <>
            <strong>Link · {selectedLink.link}</strong>
            <span>Source: {selectedLink.endpoint_a} ({selectedLink.ipv4_a || 'IP unavailable'})</span>
            <span>Destination: {selectedLink.endpoint_b} ({selectedLink.ipv4_b || 'IP unavailable'})</span>
            <span>Bandwidth: {selectedLink.bandwidth || 'Not available'} · Delay: {selectedLink.delay_seconds == null ? 'Not available' : displayMetric(selectedLink.delay_seconds * 1000, 'ms')}</span>
          </>
        ) : <span>Select a node or link to inspect its actual NS-3 details.</span>}
      </div>
    </div>
  )
}

function RunSummary({ runs, trafficOnly = false }) {
  if (!runs.length) return <EmptyState>No saved simulation result directories are available.</EmptyState>
  const metrics = trafficOnly ? METRICS : [
    METRICS[0],
    METRICS[1],
    METRICS[2],
    METRICS[4],
  ]
  return (
    <div className="table-scroll">
      <table>
        <thead><tr><th>Run mode</th><th>TCP validation</th>{metrics.map(([, label]) => <th key={label}>{label}</th>)}<th>Run ID</th></tr></thead>
        <tbody>
          {runs.map((run) => (
            <tr key={run.id}>
              <td>{run.mode === 'dynamic' ? 'Dynamic' : `Route ${run.mode}`}</td>
              <td><span className={`state-label ${run.tcp_validation === 'PASS' ? 'good' : 'warn'}`}>{run.tcp_validation}</span></td>
              {metrics.map(([key, , unit]) => <td key={key}>{displayMetric(run.metrics[key], unit)}</td>)}
              <td><code>{run.id}</code>{!run.complete && <small className="table-note">{run.csv_error || 'Incomplete run data'}</small>}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="table-footnote">These are saved-run values, not a live simulation feed. Dynamic RTT and response measurements cover the whole run.</p>
    </div>
  )
}

function RouteMonitoring({ runs }) {
  if (!runs.length) return <EmptyState>No route-monitor CSV data is available.</EmptyState>
  return (
    <div className="monitor-grid">
      {runs.map((run) => (
        <article className="monitor-card" key={run.id}>
          <h3>{run.mode === 'dynamic' ? 'Dynamic' : `Route ${run.mode}`}</h3>
          <small>{run.id}</small>
          {run.monitoring?.length ? run.monitoring.map((sample) => (
            <div className="monitor-sample" key={sample.route}>
              <strong>Route {sample.route}</strong>
              <span>Sample time: {displayMetric(sample.timestamp_seconds, 's')}</span>
              <span>
                Weighted cost: {sample.measurement_validity === 'complete'
                  ? displayMetric(sample.weighted_cost)
                  : `Not conclusive${sample.weighted_cost === null ? '' : ` (${displayMetric(sample.weighted_cost)} reported)`}`}
              </span>
              <span>Measurement validity: {sample.measurement_validity}</span>
              <span>Delay RTT: {displayMetric(sample.delay_rtt_ms, 'ms')}</span>
              <span>Packet loss: {displayMetric(sample.packet_loss_rate)}</span>
              <span>Queue occupancy: {displayMetric(sample.queue_occupancy_ratio)}</span>
              <span>Router hops: {displayMetric(sample.router_hops)}</span>
            </div>
          )) : <p className="empty-inline">Route-monitor measurements are not available.</p>}
        </article>
      ))}
    </div>
  )
}

function PerformanceCharts({ runs, loading }) {
  const completeRuns = runs
    .filter((run) => run.complete && run.tcp_validation === 'PASS')
    .sort((first, second) => (first.updated_at || '').localeCompare(second.updated_at || ''))
    .slice(-12)
    .map((run, index) => ({ ...run, shortLabel: `Run ${index + 1}` }))
  const averageRtt = completeRuns
    .filter((run) => Number.isFinite(run.metrics?.average_rtt_seconds))
    .map((run) => ({ ...run, value: run.metrics.average_rtt_seconds * 1000 }))
  const maximumRtt = completeRuns
    .filter((run) => Number.isFinite(run.metrics?.maximum_rtt_seconds))
    .map((run) => ({ ...run, value: run.metrics.maximum_rtt_seconds * 1000 }))
  const throughput = completeRuns
    .filter((run) => Number.isFinite(run.metrics?.application_response_body_throughput_bits_per_second))
    .map((run) => ({ ...run, value: run.metrics.application_response_body_throughput_bits_per_second }))
  const responseCounts = completeRuns
    .filter((run) => Number.isFinite(run.metrics?.successful_responses) || Number.isFinite(run.metrics?.failed_missing_responses))
    .map((run) => ({
      ...run,
      successful_responses: run.metrics.successful_responses ?? null,
      failed_missing_responses: run.metrics.failed_missing_responses ?? null,
    }))
  const monitorGroups = completeRuns.flatMap((run) => ['A', 'B'].map((route) => ({
    label: `${run.shortLabel} · ${route}`,
    samples: (run.monitor_series || [])
      .filter((sample) => sample.route === route
        && sample.measurement_validity === 'complete'
        && Number.isFinite(sample.weighted_cost))
        .sort((first, second) => first.timestamp_seconds - second.timestamp_seconds)
      .map((sample) => ({
        ...sample,
        id: run.id,
        mode: run.mode,
        node_count: run.node_count,
        settings: run.settings,
      })),
  })).filter((group) => group.samples.length))
  const nodeResults = completeRuns
    .filter((run) => run.settings
      && Number.isFinite(run.node_count)
      && Number.isFinite(run.metrics?.average_rtt_seconds))
    .map((run) => ({
      node_count: run.node_count,
      average_rtt_ms: run.metrics.average_rtt_seconds * 1000,
      mode: run.mode,
      id: run.id,
      settings_signature: JSON.stringify(Object.entries(run.settings)
        .filter(([key]) => key !== 'node_count')
        .sort(([first], [second]) => first.localeCompare(second))),
    }))
  const nodeComparisonGroups = new Map()
  nodeResults.forEach((run) => {
    const group = nodeComparisonGroups.get(run.settings_signature) || []
    group.push(run)
    nodeComparisonGroups.set(run.settings_signature, group)
  })
  const comparableNodeResults = [...nodeComparisonGroups.values()]
    .sort((first, second) => second.length - first.length)
    .find((group) => new Set(group.map((run) => run.node_count)).size > 1) || []
  const hasMultipleNodeCounts = comparableNodeResults.length > 0
  const tooltipStyle = { border: '1px solid #e5eaf2', borderRadius: 8, fontSize: 12 }

  if (loading) return <Panel title="Performance charts"><EmptyState>Loading saved simulation metrics…</EmptyState></Panel>
  if (!completeRuns.length) {
    return <Panel title="Performance charts" description="Charts include completed runs with TCP validation PASS."><EmptyState>No validated saved-run measurements are available yet.</EmptyState></Panel>
  }

  return (
    <>
      <Panel title="Performance charts" description={`Showing the ${completeRuns.length === 12 ? '12 most recent' : 'available'} completed, TCP-validated traffic runs. Run labels are short; hover a point or bar for the full run ID, route, node count, and traffic settings. Runs may use different settings, so comparisons are descriptive rather than controlled experiments.`}>
        <div className="performance-chart-grid">
          <ChartPanel title="Average round-trip time" unit="ms" empty={!averageRtt.length}>
            <ResponsiveContainer width="100%" height={280}>
              <BarChart data={averageRtt} margin={{ top: 12, right: 16, bottom: 12, left: 12}}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="shortLabel" interval="preserveStartEnd" tick={{ fontSize: 10 }} />
                <YAxis unit=" ms" width={72} tick={{ fontSize: 10 }} />
                <Tooltip content={<RunTooltip />} contentStyle={tooltipStyle} formatter={(value) => [`${Number(value).toFixed(3)} ms`, 'Average RTT']} />
                <Bar dataKey="value" name="Average RTT" fill="#6885c9" />
              </BarChart>
            </ResponsiveContainer>
          </ChartPanel>
          <ChartPanel title="Maximum round-trip time" unit="ms" empty={!maximumRtt.length}>
            <ResponsiveContainer width="100%" height={280}>
              <BarChart data={maximumRtt} margin={{ top: 12, right: 16, bottom: 12, left: 12 }}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="shortLabel" interval="preserveStartEnd" tick={{ fontSize: 10 }} />
                <YAxis unit=" ms" width={72} tick={{ fontSize: 10 }} />
                <Tooltip content={<RunTooltip />} contentStyle={tooltipStyle} formatter={(value) => [`${Number(value).toFixed(3)} ms`, 'Maximum RTT']} />
                <Bar dataKey="value" name="Maximum RTT" fill="#c18c5b" />
              </BarChart>
            </ResponsiveContainer>
          </ChartPanel>
          <ChartPanel title="Application response-body throughput" unit="bit/s" empty={!throughput.length}>
            <ResponsiveContainer width="100%" height={280}>
              <BarChart data={throughput} margin={{ top: 12, right: 16, bottom: 12, left: 12 }}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="shortLabel" interval="preserveStartEnd" tick={{ fontSize: 10 }} />
                <YAxis unit=" bit/s" width={88} tick={{ fontSize: 10 }} />
                <Tooltip content={<RunTooltip />} contentStyle={tooltipStyle} formatter={(value) => [`${Number(value).toFixed(2)} bit/s`, 'Application throughput']} />
                <Bar dataKey="value" name="Application response-body throughput" fill="#54977b" />
              </BarChart>
            </ResponsiveContainer>
            <small className="chart-footnote">Application payload throughput, not physical-link throughput.</small>
          </ChartPanel>
          <ChartPanel title="Successful and failed HTTP responses" unit="responses" empty={!responseCounts.length}>
            <ResponsiveContainer width="100%" height={280}>
              <BarChart data={responseCounts} margin={{ top: 12, right: 16, bottom: 12, left: 12 }}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="shortLabel" interval="preserveStartEnd" tick={{ fontSize: 10 }} />
                <YAxis width={70} tickFormatter={(value) => `${value} resp.`} tick={{ fontSize: 10 }} />
                <Tooltip content={<RunTooltip />} contentStyle={tooltipStyle} />
                <Legend />
                <Bar dataKey="successful_responses" name="Successful" stackId="responses" fill="#54977b" />
                <Bar dataKey="failed_missing_responses" name="Failed/missing" stackId="responses" fill="#c97869" />
              </BarChart>
            </ResponsiveContainer>
          </ChartPanel>
          <ChartPanel title="Measured route cost over time" unit="weighted cost" empty={!monitorGroups.length}>
            <ResponsiveContainer width="100%" height={280}>
              <LineChart margin={{ top: 12, right: 20, bottom: 18, left: 10 }}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="timestamp_seconds" type="number" domain={['dataMin', 'dataMax']} tickFormatter={(value) => `${value}s`} tick={{ fontSize: 10 }} />
                <YAxis width={68} tick={{ fontSize: 10 }} />
                <Tooltip content={<RunTooltip />} contentStyle={tooltipStyle} labelFormatter={(value) => `Simulation time: ${value}s`} formatter={(value) => [Number(value).toFixed(4), 'Weighted cost']} />
                <Legend />
                {monitorGroups.map((group, index) => (
                  <Line
                    key={group.label}
                    data={group.samples}
                    type="monotone"
                    dataKey="weighted_cost"
                    name={group.label}
                    stroke={['#6885c9', '#54977b', '#c18c5b', '#8976ad', '#548c9b', '#c97869'][index % 6]}
                    dot={false}
                  />
                ))}
              </LineChart>
            </ResponsiveContainer>
            <small className="chart-footnote">Only samples marked complete by the route monitor are charted.</small>
          </ChartPanel>
          <ChartPanel title="Node count versus average RTT" unit="nodes · ms" empty={!hasMultipleNodeCounts}>
            {hasMultipleNodeCounts ? (
              <ResponsiveContainer width="100%" height={280}>
                <ScatterChart data={comparableNodeResults} margin={{ top: 10, right: 20, bottom: 20, left: 10 }}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="node_count" name="Node count" type="number" unit=" nodes" />
                  <YAxis dataKey="average_rtt_ms" name="Average RTT" unit=" ms" />
                  <Tooltip content={<RunTooltip />} contentStyle={tooltipStyle} formatter={(value, name) => [`${Number(value).toFixed(3)} ${name === 'Average RTT' ? 'ms' : 'nodes'}`, name]} />
                  <Scatter name="Matched-settings runs" data={comparableNodeResults} fill="#6885c9" />
                </ScatterChart>
              </ResponsiveContainer>
            ) : <EmptyState>Comparison requires validated runs at two or more node counts with matching settings apart from node count.</EmptyState>}
          </ChartPanel>
        </div>
      </Panel>
    </>
  )
}

function RunTooltip({ active, payload, label }) {
  const run = payload?.[0]?.payload
  if (!active || !run) return null
  return (
    <div className="chart-tooltip">
      <strong>{run.mode === 'dynamic' ? 'Dynamic' : `Route ${run.mode}`} · {label || run.shortLabel}</strong>
      <span>Run ID: {run.id || 'Not available'}</span>
      <span>Nodes: {run.node_count ?? 'Not available'}</span>
      <span>
        Traffic: {run.settings?.traffic_load || 'Not available'} · {run.settings?.http_client_count ?? '?'} clients · {run.settings?.http_request_count ?? '?'} requests/client
      </span>
    </div>
  )
}


function ChartPanel({ title, unit, empty, children }) {
  return (
    <article className="performance-chart">
      <h3>{title}<small>{unit}</small></h3>
      {empty ? <EmptyState>No validated measurements for this chart.</EmptyState> : children}
    </article>
  )
}

export default App
