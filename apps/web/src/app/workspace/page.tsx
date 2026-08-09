import { MonitoringMap } from "@/components/monitoring-map";

const stats = [
  ["Monitored sites", "0"],
  ["Due observations", "0"],
  ["Open reviews", "0"],
  ["Monitoring health", "Not connected"]
];

export default function Workspace() {
  return (
    <main className="workspace-page">
      <header className="masthead">
        <div>
          <p className="eyebrow">Private institutional workspace</p>
          <h1>Nigeria Forest Monitor</h1>
          <p>Track predefined forest sites, satellite observations, and reviewed change events.</p>
        </div>
        <span className="environment">Local development</span>
      </header>

      <section className="stats" aria-label="Monitoring summary">
        {stats.map(([label, value]) => (
          <article key={label}>
            <span>{label}</span>
            <strong>{value}</strong>
          </article>
        ))}
      </section>

      <section className="workspace">
        <div className="map-panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">Primary pilot</p>
              <h2>Old Oyo–Kwara–Kainji corridor</h2>
            </div>
            <span className="status">Awaiting database</span>
          </div>
          <MonitoringMap />
        </div>

        <aside className="workflow-panel">
          <p className="eyebrow">Phase 2 shell</p>
          <h2>Operational workflow</h2>
          <ol>
            <li>Register and configure a monitored site</li>
            <li>Discover an eligible observation</li>
            <li>Run the Airflow processing DAG</li>
            <li>Review observable change signals</li>
            <li>Resolve or refer through an authorised workflow</li>
          </ol>
          <p className="notice">
            Detection output is decision-support information. It is not proof of illegal or hostile activity.
          </p>
        </aside>
      </section>
    </main>
  );
}
