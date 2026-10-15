import React from 'react';

/**
 * Underlined text choices with a count, for narrowing a list (not for moving between pages). One is
 * pressed at a time; pressing the pressed one again goes back to `all`. items: [{ id, label, count }].
 */
const Tabs = ({ label, items, value, onChange }) => (
  <div role="group" aria-label={label} className="flex min-w-0 items-end gap-5 overflow-x-auto">
    {items.map(({ id, label: text, count }) => {
      const pressed = value === id;
      return (
        <button key={id} type="button" aria-pressed={pressed} onClick={() => onChange(pressed ? 'all' : id)}
          className={`-mb-px whitespace-nowrap border-b-2 pb-2.5 pt-3 text-sm transition-colors ${pressed ? 'border-gray-900 font-medium text-gray-900' : 'border-transparent text-gray-600 hover:text-gray-900'}`}>
          {text}
          {count !== undefined && <span className={`ml-1.5 tabular-nums ${pressed ? 'text-gray-700' : 'text-gray-500'}`}>{count}</span>}
        </button>
      );
    })}
  </div>
);

export default Tabs;
