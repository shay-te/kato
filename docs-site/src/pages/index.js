import clsx from 'clsx';
import Link from '@docusaurus/Link';
import useBaseUrl from '@docusaurus/useBaseUrl';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import Layout from '@theme/Layout';
import styles from './index.module.css';

// Marketing landing — sells Kato as a product, then funnels to the docs.
const VALUE_PROPS = [
  {
    icon: '🗂️',
    title: 'One cockpit for every task',
    body:
      'Every ticket the agent is working — across YouTrack, Jira, GitHub, ' +
      'GitLab, and Bitbucket — lives in one tabbed UI. No hopping between ' +
      'terminals, IDEs, and browser tabs.',
  },
  {
    icon: '🔔',
    title: 'One approval popup',
    body:
      'Every permission request, even from a backgrounded task, surfaces in ' +
      'one modal showing the exact command. Allow, Allow-always, or Deny — ' +
      'never window-hop for a prompt again.',
  },
  {
    icon: '🛡️',
    title: 'Security you can see',
    body:
      'Action Guard blocks credential reads, network exfiltration, and ' +
      'sandbox escapes, and asks before dual-use actions. Tune it per ' +
      'category; a no-legit-use floor can never be loosened.',
  },
  {
    icon: '🚦',
    title: 'Nothing ships without you',
    body:
      'No auto-commit, no auto-push, no auto-resolve. Kato writes the code, ' +
      'runs your tests, and stops — you review the diff and click ' +
      'Done — Push when it is ready.',
  },
  {
    icon: '🔌',
    title: 'Run on the agent you like',
    body:
      'Claude, Codex, or OpenHands behind the same UI. Pick the model and ' +
      'effort per task; the rest of the workflow stays identical.',
  },
  {
    icon: '👀',
    title: 'See and steer the work',
    body:
      'A live diff viewer with inline comments — drop a note on any line and ' +
      'Kato treats it as a new instruction and re-runs. It even handles PR ' +
      'review comments for you.',
  },
];

function Hero() {
  const { siteConfig } = useDocusaurusContext();
  return (
    <header className={clsx('hero', 'hero--kato')}>
      <div className="container">
        <img className={styles.heroLogo} src={useBaseUrl('/img/kato.png')} alt="Kato" />
        <h1 className="hero__title">{siteConfig.title}</h1>
        <p className="hero__subtitle">
          Ship more tickets with an AI agent you can actually trust — from one
          screen, with one approval popup, behind security you control.
        </p>
        <div className={styles.buttons}>
          <Link className="button button--primary button--lg" to="/docs/getting-started">
            Get started in 5 minutes
          </Link>
          <Link className="button button--secondary button--lg" to="/docs/why-kato">
            Why Kato →
          </Link>
        </div>
      </div>
    </header>
  );
}

function ValueProps() {
  return (
    <section className={styles.props}>
      <div className="container">
        <h2 className={styles.propsHeading}>Why teams put Kato on their backlog</h2>
        <div className={styles.grid}>
          {VALUE_PROPS.map((p) => (
            <div key={p.title} className={styles.card}>
              <div className={styles.cardIcon} aria-hidden="true">{p.icon}</div>
              <h3 className={styles.cardTitle}>{p.title}</h3>
              <p className={styles.cardBody}>{p.body}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

export default function Home() {
  return (
    <Layout
      title="Kato — the AI coding agent you can govern"
      description="Kato puts an autonomous coding agent on your tickets — one cockpit, one approval flow, security you can see."
    >
      <Hero />
      <main>
        <ValueProps />
      </main>
    </Layout>
  );
}
