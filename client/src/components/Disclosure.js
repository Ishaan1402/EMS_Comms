import React, { useId, useState } from 'react';
import { ChevronRight } from 'lucide-react';

/**
 * A section that folds: a chevron, a title, and (while folded) a one-line summary of what is inside.
 * Replaces the browser's native <details> triangle. The content stays in the page while folded.
 * Controlled with `open` and `onToggle`, or uncontrolled with `defaultOpen`.
 */
const Disclosure = ({ title, summary = null, summaryClassName = 'truncate', open: controlled, defaultOpen = false, onToggle,
  className = '', children }) => {
  const [inner, setInner] = useState(defaultOpen);
  const id = useId();
  const open = controlled ?? inner;
  const toggle = () => {
    if (controlled === undefined) setInner(!open);
    if (onToggle) onToggle(!open);
  };
  return (
    <div className={className}>
      <button type="button" onClick={toggle} aria-expanded={open} aria-controls={id}
        className="flex w-full items-center gap-2 rounded-md py-1 text-left hover:text-gray-900">
        <ChevronRight className={`h-4 w-4 shrink-0 text-gray-500 transition-transform ${open ? 'rotate-90' : ''}`} />
        <span className="text-sm font-semibold text-gray-900">{title}</span>
        {!open && summary && <span className={`min-w-0 text-sm text-gray-600 ${summaryClassName}`}>{summary}</span>}
      </button>
      <div id={id} hidden={!open} className="mt-2 pl-6">{children}</div>
    </div>
  );
};

export default Disclosure;
