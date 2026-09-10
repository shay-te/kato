import { forwardRef } from 'react';

// Forwards a ref so a caller can measure or scroll to the header's own DOM
// node — the chat's "jump back to the start of this prompt" button needs the
// element's FLOW position, which only the node itself can give.
const StickyHeader = forwardRef(function StickyHeader({
  as: Tag = 'div',
  className = '',
  children,
  ...props
}, ref) {
  const mergedClassName = ['sticky-section-header', className]
    .filter(Boolean)
    .join(' ');
  return (
    <Tag ref={ref} className={mergedClassName} {...props}>
      {children}
    </Tag>
  );
});

export default StickyHeader;
