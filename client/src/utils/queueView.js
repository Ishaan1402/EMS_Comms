// A doctor's own view of the queue: which cases to show and in what order. It never changes the
// hospital's ranking (that is the server's, GET /api/cases/queue); it only narrows or re-sorts what
// this screen shows, and a reload resets it. The filter definitions are the ones case_queue.counts
// uses, so a count and the list it filters always agree.

const unknownTime = '9999';

// Same rule as the "Arriving soon" badge (ArrivalHighlight in HospitalWorkflowSettings.js).
const arrivesSoon = (c, { serverNow, arrivalMinutes }) => {
  if (c.status !== 'active' || c.arrived_at || !arrivalMinutes) return false;
  const remaining = Date.parse(c.eta_at) - serverNow;
  return Number.isFinite(remaining) && remaining >= 0 && remaining <= arrivalMinutes * 60000;
};

export const FILTERS = {
  all: () => true,
  needsReview: (c) => Boolean(c.processing?.needs_review),
  unacknowledged: (c) => !c.latest_update_acknowledged,
  crewAlerts: (c) => (c.crew_alerts?.active?.length ?? 0) > 0,
  prepareNow: (c) => c.preparation_category === 'Prepare now',
  arrivingSoon: arrivesSoon,
};

// When the latest information about a case arrived (it falls back for cases that have no version yet).
const lastUpdate = (c) => c.last_update_at || c.updated_at || c.started_at || unknownTime;
const byPosition = (a, b) => (a.queue_position ?? 0) - (b.queue_position ?? 0);

export const SORTS = {
  queue: { label: 'Queue order', compare: byPosition },
  eta: {
    label: 'Soonest arrival',
    compare: (a, b) => (Number(!a.eta_at) - Number(!b.eta_at)) || (a.eta_at || '').localeCompare(b.eta_at || '') || byPosition(a, b),
  },
  recent: {
    label: 'Newest update',
    compare: (a, b) => lastUpdate(b).localeCompare(lastUpdate(a)) || byPosition(a, b),
  },
  waiting: {
    label: 'Waiting longest to be acknowledged',
    // Cases whose latest information nobody has acknowledged, the one that has waited longest first.
    compare: (a, b) => (Number(Boolean(a.latest_update_acknowledged)) - Number(Boolean(b.latest_update_acknowledged)))
      || (a.latest_update_acknowledged ? 0 : lastUpdate(a).localeCompare(lastUpdate(b))) || byPosition(a, b),
  },
};

export const DEFAULT_VIEW = { filter: 'all', sort: 'queue' };
export const isDefaultView = (view) => view.filter === DEFAULT_VIEW.filter && view.sort === DEFAULT_VIEW.sort;

/** The cases to show, filtered and sorted. Never changes its input. */
export const applyQueueView = (cases, { filter, sort }, context) => (
  cases.filter((c) => FILTERS[filter](c, context)).sort(SORTS[sort].compare)
);

/** How many of the hospital's inbound cases each filter matches (always over the whole queue). */
export const viewCounts = (cases, context) => Object.fromEntries(
  Object.entries({
    inbound: 'all', needsReview: 'needsReview', unacknowledged: 'unacknowledged', crewAlerts: 'crewAlerts',
    prepareNow: 'prepareNow', arrivingSoon: 'arrivingSoon',
  }).map(([name, filter]) => [name, cases.filter((c) => FILTERS[filter](c, context)).length]),
);
