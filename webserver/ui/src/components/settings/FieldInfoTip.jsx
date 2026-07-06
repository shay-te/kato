import { useCallback, useState } from 'react';
import { createPortal } from 'react-dom';

// The ⓘ affordance every setup/settings field carries. Hover or focus shows
// a portal tooltip with the field's explanation; the environment-variable
// name lives HERE (last line of the text) instead of being printed next to
// every label. Extracted from SchemaSettingsPanel so the wizard and the
// provider/repositories panels share the exact same behavior and styling.
export default function FieldInfoTip({ text }) {
  const [pos, setPos] = useState(null);
  const show = useCallback((e) => {
    const r = e.currentTarget.getBoundingClientRect();
    setPos({ x: r.left + r.width / 2, y: r.top });
  }, []);
  const hide = useCallback(() => setPos(null), []);
  if (!text) { return null; }
  return (
    <>
      <span
        className="settings-drawer-field-info"
        tabIndex={0}
        role="img"
        aria-label="Field info"
        onMouseEnter={show}
        onMouseLeave={hide}
        onFocus={show}
        onBlur={hide}
      >
        ⓘ
      </span>
      {pos && createPortal(
        <div
          className="settings-field-tooltip"
          style={{ left: pos.x, top: pos.y }}
        >
          {text}
        </div>,
        document.body,
      )}
    </>
  );
}
