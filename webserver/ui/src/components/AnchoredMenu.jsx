import { createPortal } from 'react-dom';
import { cx } from '../utils/cx.js';

// The menu ``useAnchoredMenu`` places: portalled to the body over a backdrop
// that closes it on a click anywhere else. Nothing while closed.
export default function AnchoredMenu({ menu, label, className = '', children }) {
  if (!menu.open) { return null; }
  return createPortal(
    <>
      <div className="anchored-menu-backdrop" onClick={menu.close} aria-hidden="true" />
      <div ref={menu.menuRef} className={cx('anchored-menu', className)} style={menu.style} role="menu"
        aria-label={label}>
        {children}
      </div>
    </>,
    document.body,
  );
}
