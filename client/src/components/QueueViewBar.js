import React from 'react';
import { SORTS } from '../utils/queueView';

/**
 * A slim bar above the queue list: how this screen sorts it, and, while a filter is on, how much of
 * the queue it is showing, so a narrowed list is never mistaken for the whole queue. Sorting and
 * filtering are this screen's own view; they never change the hospital's order.
 */
const QueueViewBar = ({ sort, onSort, filterLabel, shown, total, onClear, reorderBlocked }) => (
  <div className="px-4 py-2 border-b border-gray-200 text-sm text-gray-700 space-y-1">
    <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
      <label className="flex items-center gap-2">
        <span>Sort</span>
        <select value={sort} onChange={(event) => onSort(event.target.value)}
          className="px-2 py-1 border border-gray-300 rounded bg-white text-sm">
          {Object.entries(SORTS).map(([value, { label }]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </label>
      {filterLabel && (
        <span>
          Showing {shown} of {total} ({filterLabel}) ·{' '}
          <button type="button" onClick={onClear} className="text-blue-700 underline">Clear filter</button>
        </span>
      )}
    </div>
    {sort !== 'queue' && <p className="text-xs text-gray-600">Your view only: the queue order itself is unchanged.</p>}
    {reorderBlocked && <p className="text-xs text-gray-600">Clear the filter and sort to reorder with the arrows.</p>}
  </div>
);

export default QueueViewBar;
