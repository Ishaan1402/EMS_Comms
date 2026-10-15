import React from 'react';
import Menu from './Menu';
import QueueOrderMenu from './QueueOrderMenu';
import Tabs from './Tabs';
import { SORTS } from '../utils/queueView';

const FILTERS = [
  { id: 'all', label: 'All', count: 'inbound' },
  { id: 'unacknowledged', label: 'Not acknowledged', count: 'unacknowledged' },
  { id: 'crewAlerts', label: 'Crew alerts', count: 'crewAlerts' },
  { id: 'needsReview', label: 'Needs review', count: 'needsReview' },
  { id: 'prepareNow', label: 'AI: Prepare now', count: 'prepareNow' },
  { id: 'arrivingSoon', label: 'Arriving soon', count: 'arrivingSoon' },
];

const SORT_OPTIONS = Object.entries(SORTS).map(([value, { label, description }]) => ({ value, label, description }));

/**
 * The one row above the queue: the filters (counts of the whole queue; a filter with nothing in it is
 * hidden unless it is on), then how the queue is ordered for the hospital and how this screen sorts it.
 * Filtering and sorting are this screen's own view; they never change the hospital's order.
 */
const QueueToolbar = ({ counts, filter, onFilter, sort, onSort, queueMode, onModeChange, hasHospital }) => {
  const items = counts ? FILTERS
    .filter(({ id, count }) => id === 'all' || counts[count] > 0 || filter === id)
    .map(({ id, label, count }) => ({ id, label, count: counts[count] })) : [];
  return (
    <div className="flex flex-wrap items-end justify-between gap-x-4 border-b border-gray-200 px-5">
      {counts ? <Tabs label="Filter the queue" items={items} value={filter} onChange={onFilter} /> : <span />}
      <div className="flex items-center gap-1 py-1.5">
        {hasHospital && <QueueOrderMenu mode={queueMode} onChange={onModeChange} />}
        <Menu label="Sort" value={sort} options={SORT_OPTIONS} onChange={onSort} />
      </div>
    </div>
  );
};

export default QueueToolbar;
