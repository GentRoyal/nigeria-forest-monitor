import Image from "next/image";
import Link from "next/link";

const observationSteps = [
  {
    number: "01",
    title: "Observe",
    text: "Bring recurring satellite observations into one site history, even where routine field access is difficult."
  },
  {
    number: "02",
    title: "Compare",
    text: "Measure what changed across a defined grid and preserve the source, date, method, and result."
  },
  {
    number: "03",
    title: "Review",
    text: "Give analysts evidence to assess remotely before an authorised institution decides what happens next."
  }
];

export default function Home() {
  return (
    <div className="landing-page">
      <header className="site-header">
        <Link className="brand" href="/" aria-label="Nigeria Forest Monitor home">
          <span className="flag-mark" aria-hidden="true">
            <i />
            <i />
            <i />
          </span>
          <span>
            Nigeria Forest Monitor
            <small>Forest observation &amp; evidence system</small>
          </span>
        </Link>

        <nav aria-label="Primary navigation">
          <a href="#overview">Overview</a>
          <a href="#purpose">Mission</a>
          <a href="#method">How it works</a>
          <a href="#coverage">Pilot area</a>
        </nav>

        <Link className="header-action" href="/workspace">
          Enter workspace <span aria-hidden="true">↗</span>
        </Link>
      </header>

      <main className="landing-main">
        <section className="hero" id="overview" aria-labelledby="hero-title">
          <Image
            className="hero-image"
            src="/images/nigeria-watch-hero.png"
            alt="A documentary composition of Nigeria connecting forests, a deserted road, civilians, and satellite observation lines"
            fill
            priority
            sizes="100vw"
          />
          <div className="hero-shade" />
          <div className="hero-grid" aria-hidden="true" />

          <div className="hero-copy">
            <p className="kicker">Satellite observation. Accountable review.</p>
            <h1 id="hero-title">
              Monitor forest change across <em>Nigeria.</em>
            </h1>
            <p className="hero-intro">
              Detect unusual land-cover change, preserve the evidence behind every signal, and help authorised
              analysts decide what deserves attention.
            </p>
            <div className="hero-actions">
              <Link className="primary-action" href="/workspace">
                Explore the workspace <span aria-hidden="true">→</span>
              </Link>
              <a className="text-action" href="#purpose">
                Why this exists
              </a>
            </div>
          </div>

          <div className="product-preview" aria-label="Illustrative monitoring workspace preview">
            <div className="preview-topbar">
              <div>
                <span className="preview-symbol" aria-hidden="true">⌁</span>
                <strong>Nigeria Forest Monitor</strong>
              </div>
              <span className="demo-badge">Illustrative interface</span>
            </div>
            <div className="preview-body">
              <div className="preview-sidebar" aria-hidden="true">
                <span className="active">Overview</span>
                <span>Sites</span>
                <span>Observations</span>
                <span>Review queue</span>
                <span>Reports</span>
              </div>
              <div className="preview-content">
                <div className="preview-title-row">
                  <div>
                    <small>Primary pilot</small>
                    <strong>Old Oyo—Kwara—Kainji</strong>
                  </div>
                  <span>Sample view</span>
                </div>
                <div className="preview-grid">
                  <div className="preview-map">
                    <span className="preview-map-label">Monitoring grid</span>
                    <i className="signal signal-one" />
                    <i className="signal signal-two" />
                    <i className="signal signal-three" />
                    <div className="map-legend">
                      <span><i /> No finding</span>
                      <span><i /> Review</span>
                      <span><i /> Priority</span>
                    </div>
                  </div>
                  <div className="preview-sidecards">
                    <article>
                      <small>Site status</small>
                      <strong>Awaiting first run</strong>
                      <span>Cadence is set by the user</span>
                    </article>
                    <article>
                      <small>Review principle</small>
                      <strong>Human decision required</strong>
                      <span>No automated accusation</span>
                    </article>
                  </div>
                  <div className="comparison-card">
                    <small>Observation comparison</small>
                    <div>
                      <span className="sar-frame before">Baseline</span>
                      <b aria-hidden="true">→</b>
                      <span className="sar-frame after">Current</span>
                    </div>
                  </div>
                  <div className="queue-card">
                    <small>Review queue</small>
                    <p><i /> New observation <span>Unreviewed</span></p>
                    <p><i /> Coverage check <span>Ready</span></p>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <div className="capability-strip" aria-label="Core capabilities">
            <div><span>◫</span><p><strong>SAR + optical ready</strong>Sensor-independent records</p></div>
            <div><span>⌖</span><p><strong>Recurring observation</strong>User-defined monitoring cadence</p></div>
            <div><span>◇</span><p><strong>Human review</strong>Evidence before escalation</p></div>
          </div>

          <div className="hero-footnote" aria-label="Pilot record">
            <span>NGA / PILOT 01</span>
            <span>Old Oyo—Kwara—Kainji</span>
            <span>Remote observation · Human judgement</span>
          </div>
        </section>

        <section className="statement" id="purpose" aria-labelledby="purpose-title">
          <p className="section-index">01 / Purpose</p>
          <div>
            <h2 id="purpose-title">A forest can disappear quietly. The consequences do not.</h2>
            <div className="statement-copy">
              <p>
                Nigeria’s forests hold biodiversity, livelihoods, water systems, and memory. Yet the places most in
                need of observation can be the hardest—and sometimes the least safe—to reach.
              </p>
              <p>
                This platform connects recurring imagery, defined monitoring grids, and an accountable review
                process. It helps people see what deserves attention without pretending that a satellite image is a
                verdict.
              </p>
            </div>
          </div>
        </section>

        <section className="field-notes" aria-label="Connected monitoring context">
          <article className="field-note forest-note">
            <span>Forest</span>
            <strong>The living baseline</strong>
          </article>
          <article className="field-note road-note">
            <span>Access</span>
            <strong>Distance cannot mean invisibility</strong>
          </article>
          <article className="field-note people-note">
            <span>People</span>
            <strong>Safety before field verification</strong>
          </article>
        </section>

        <section className="method" id="method" aria-labelledby="method-title">
          <div className="method-heading">
            <p className="section-index">02 / Method</p>
            <h2 id="method-title">From a signal in the canopy to a decision with a record.</h2>
          </div>
          <div className="method-steps">
            {observationSteps.map((step) => (
              <article key={step.number}>
                <span>{step.number}</span>
                <h3>{step.title}</h3>
                <p>{step.text}</p>
              </article>
            ))}
          </div>
        </section>

        <section className="coverage" id="coverage" aria-labelledby="coverage-title">
          <div className="coverage-map" aria-hidden="true">
            <span className="map-ring ring-one" />
            <span className="map-ring ring-two" />
            <span className="map-point" />
            <span className="map-line" />
            <b>NGA / PILOT 01</b>
          </div>
          <div className="coverage-copy">
            <p className="section-index">03 / First ground</p>
            <h2 id="coverage-title">Old Oyo—Kwara—Kainji</h2>
            <p>
              The first monitoring corridor crosses ecological and administrative boundaries. It gives the system a
              demanding, meaningful place to prove that site history, recurrence, and careful review work together.
            </p>
            <dl>
              <div>
                <dt>Mode</dt>
                <dd>Private institutional workspace</dd>
              </div>
              <div>
                <dt>Cadence</dt>
                <dd>User-defined + manual trigger</dd>
              </div>
              <div>
                <dt>Finding</dt>
                <dd>Observable change, never automatic guilt</dd>
              </div>
            </dl>
          </div>
        </section>

        <section className="responsibility" aria-labelledby="responsibility-title">
          <p className="section-index">The line we will not cross</p>
          <h2 id="responsibility-title">Technology can direct attention. It cannot replace evidence or judgement.</h2>
          <p>
            Detections remain decision-support indicators. Remote corroboration comes first. Field activity belongs
            only inside an authorised government workflow with explicit responsibility for people’s safety.
          </p>
        </section>
      </main>

      <footer className="site-footer">
        <div className="footer-flag" aria-hidden="true" />
        <p>Nigeria Forest Monitor</p>
        <p className="footer-tagline">See change. Preserve evidence.</p>
        <Link href="/workspace">Private workspace →</Link>
      </footer>
    </div>
  );
}
