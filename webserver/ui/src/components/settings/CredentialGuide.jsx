// The "why kato needs this key, and where you get it" card that sits above
// every credential form (first-run wizard + the Settings credentials
// panels). Content comes from utils/credentialGuides.js.
//
// Shape: the WHY line is always visible (it is the part a first-comer reads
// before deciding to hand kato a token), the step-by-step is a native
// disclosure — open by default in the wizard, collapsed in Settings where
// the operator has already been through it once — and the provider links
// stay outside the disclosure so they are one click away either way.
//
// External links go through plain <a target="_blank">; utils/tauriLinks.js
// installs the delegated handler that routes them to the system browser in
// the desktop app.
export default function CredentialGuide({ guide, defaultOpen = false, settingsFilePath = '' }) {
  if (!guide) { return null; }
  const steps = guide.steps || [];
  const path = settingsFilePath || '~/.kato/settings.json';
  return (
    <section className="credential-guide">
      <p className="credential-guide__why">
        <span className="credential-guide__why-label">Why kato needs this</span>
        {guide.why}
      </p>
      {guide.storesSecret !== false && (
        <p className="credential-guide__privacy">
          Stored on this machine in <code>{path}</code> and sent only to
          {' '}{guide.provider}.
        </p>
      )}
      {steps.length > 0 && (
        <details className="credential-guide__how" open={defaultOpen}>
          <summary className="credential-guide__summary">
            Where do I get a {guide.provider} {guide.credential}?
          </summary>
          {guide.location && (
            <p className="credential-guide__location">{guide.location}</p>
          )}
          <ol className="credential-guide__steps">
            {steps.map((step) => (
              <li key={step}>{step}</li>
            ))}
          </ol>
          {guide.note && (
            <p className="credential-guide__note">{guide.note}</p>
          )}
        </details>
      )}
      <div className="credential-guide__links">
        {guide.createUrl && (
          <a
            className="credential-guide__link"
            href={guide.createUrl}
            target="_blank"
            rel="noopener noreferrer"
          >
            {guide.createLabel || 'Create the key'} →
          </a>
        )}
        {guide.docsUrl && (
          <a
            className="credential-guide__link"
            href={guide.docsUrl}
            target="_blank"
            rel="noopener noreferrer"
          >
            {guide.docsLabel || `${guide.provider} documentation`} →
          </a>
        )}
      </div>
    </section>
  );
}
