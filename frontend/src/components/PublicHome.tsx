export function PublicHome() {
  return <main className="public-home">
    <header><a href="/" className="brand">NIFTY RADAR<span>Personal trading research</span></a><a className="owner-link" href="/owner">Owner access</a></header>
    <section className="home-intro"><p className="eyebrow">NSE cash · Intraday · Nifty 100</p>
      <h1>Observe the setup.<br />Understand the decision.</h1>
      <p>A personal research project connecting a market-wide radar with a rule-based trading desk. Built around a clear sequence: spike, pullback, continuation and VWAP qualification.</p>
      <a className="owner-link" href="/owner">Login to the private workspace</a>
    </section>
    <section className="home-grid">
      <article><p className="eyebrow">The project</p><h2>One connected workflow</h2><p>Morning preparation, sector observation, trade review and configuration in a single owner workspace.</p></article>
      <article><p className="eyebrow">Current direction</p><h2>Clarity before automation</h2><p>Making decisions explainable, keeping the original setup visible and testing execution and recovery in simulation.</p></article>
      <article><p className="eyebrow">Future ideas</p><h2>Learn from each session</h2><p>Separate strategy outcomes from engineering quality, improve review tools and evaluate changes against recorded evidence.</p></article>
    </section>
    <footer>Independent personal project. Not investment advice or a promise of returns. Operational information is private.</footer>
  </main>
}
