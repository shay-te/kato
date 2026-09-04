// A titled SUBSECTION inside a settings panel.
//
// Distinct from <SettingsPanelHead>, which titles the whole panel with an
// <h3>. Panels that reused SettingsPanelHead for an inner group drew a
// subsection at the same size and weight as the panel's own title, so the
// two read as siblings; and panels that titled a group with nothing at all
// left the operator to infer where one group ended and the next began —
// reported as "3 sections here, and the title for each section looks like
// plain text".
//
// The heading is an <h4> so the document outline matches what is drawn: a
// level below the panel's <h3>. ``children`` is optional description copy,
// same contract as SettingsPanelHead.
export default function SettingsSectionHead({ title, children }) {
  return (
    <div className="settings-drawer-section-head">
      <h4 className="settings-section-title">{title}</h4>
      {children}
    </div>
  );
}
