import { FILTERS, SORTS, DEFAULT_VIEW, applyQueueView, isDefaultView, viewCounts } from './queueView';

const NOW = Date.parse('2026-10-15T15:00:00Z');
const at = (minutes) => new Date(NOW + minutes * 60000).toISOString();
const ctx = { serverNow: NOW, arrivalMinutes: 5 };
const c = (id, overrides = {}) => ({
  id, status: 'active', arrived_at: null, queue_position: id, eta_at: at(20), last_update_at: at(-10 - id),
  latest_update_acknowledged: true, processing: { needs_review: false }, crew_alerts: { active: [] },
  preparation_category: 'Can wait', ...overrides,
});
const ids = (cases) => cases.map((x) => x.id);
const view = (filter, sort = 'queue') => ({ filter, sort });

test('each filter keeps exactly the cases its count describes', () => {
  const cases = [
    c(1, { processing: { needs_review: true } }),
    c(2, { latest_update_acknowledged: false }),
    c(3, { crew_alerts: { active: [{ alert: 'stroke' }] } }),
    c(4, { preparation_category: 'Prepare now' }),
    c(5, { eta_at: at(3) }),
    c(6),
  ];
  expect(ids(applyQueueView(cases, view('all'), ctx))).toEqual([1, 2, 3, 4, 5, 6]);
  expect(ids(applyQueueView(cases, view('needsReview'), ctx))).toEqual([1]);
  expect(ids(applyQueueView(cases, view('unacknowledged'), ctx))).toEqual([2]);
  expect(ids(applyQueueView(cases, view('crewAlerts'), ctx))).toEqual([3]);
  expect(ids(applyQueueView(cases, view('prepareNow'), ctx))).toEqual([4]);
  expect(ids(applyQueueView(cases, view('arrivingSoon'), ctx))).toEqual([5]);
});

test('the counts are over the whole queue and use the server\'s definitions (case_queue.counts)', () => {
  // The same cases the backend test uses for its counts: one unacknowledged, one Prepare now, one crew alert.
  const cases = [c(1, { latest_update_acknowledged: false, preparation_category: 'Prepare now' }),
    c(2, { crew_alerts: { active: [{ alert: 'sepsis' }], unacknowledged: true } }), c(3, { processing: { needs_review: true } })];
  expect(viewCounts(cases, ctx)).toEqual({
    inbound: 3, needsReview: 1, unacknowledged: 1, crewAlerts: 1, prepareNow: 1, arrivingSoon: 0 });
  // A cancelled alert that is still unacknowledged is not an active crew alert.
  expect(FILTERS.crewAlerts(c(4, { crew_alerts: { active: [], unacknowledged: true } }))).toBe(false);
});

test('arriving soon follows the arrival highlight: inside the window only, never for arrived or no setting', () => {
  expect(FILTERS.arrivingSoon(c(1, { eta_at: at(5) }), ctx)).toBe(true);
  expect(FILTERS.arrivingSoon(c(1, { eta_at: at(6) }), ctx)).toBe(false);
  expect(FILTERS.arrivingSoon(c(1, { eta_at: at(-1) }), ctx)).toBe(false);   // already past
  expect(FILTERS.arrivingSoon(c(1, { eta_at: at(3), arrived_at: at(0) }), ctx)).toBe(false);
  expect(FILTERS.arrivingSoon(c(1, { eta_at: at(3) }), { serverNow: NOW, arrivalMinutes: 0 })).toBe(false);
  expect(FILTERS.arrivingSoon(c(1, { eta_at: null }), ctx)).toBe(false);
});

test('sorting by soonest arrival puts cases without an ETA last and keeps queue order for ties', () => {
  const cases = [c(1, { eta_at: null }), c(2, { eta_at: at(30) }), c(3, { eta_at: at(10) }), c(4, { eta_at: at(10) })];
  expect(ids(applyQueueView(cases, view('all', 'eta'), ctx))).toEqual([3, 4, 2, 1]);
});

test('sorting by newest update shows the latest information first', () => {
  const cases = [c(1, { last_update_at: at(-30) }), c(2, { last_update_at: at(-2) }), c(3, { last_update_at: at(-10) })];
  expect(ids(applyQueueView(cases, view('all', 'recent'), ctx))).toEqual([2, 3, 1]);
});

test('sorting by waiting puts unacknowledged cases first, the longest-waiting information at the top', () => {
  const cases = [
    c(1, { latest_update_acknowledged: true }),
    c(2, { latest_update_acknowledged: false, last_update_at: at(-1) }),
    c(3, { latest_update_acknowledged: false, last_update_at: at(-15) }),
    c(4, { latest_update_acknowledged: true }),
  ];
  expect(ids(applyQueueView(cases, view('all', 'waiting'), ctx))).toEqual([3, 2, 1, 4]);
});

test('the default view is the server\'s queue order (by position), and applying a view never changes the input', () => {
  const cases = [c(2, { queue_position: 2 }), c(1, { queue_position: 1 })];
  const before = ids(cases);
  expect(ids(applyQueueView(cases, DEFAULT_VIEW, ctx))).toEqual([1, 2]);
  applyQueueView(cases, view('all', 'eta'), ctx);
  applyQueueView(cases, view('prepareNow'), ctx);
  expect(ids(cases)).toEqual(before);
  expect(isDefaultView(DEFAULT_VIEW)).toBe(true);
  expect(isDefaultView(view('crewAlerts'))).toBe(false);
  expect(isDefaultView(view('all', 'eta'))).toBe(false);
  expect(Object.keys(SORTS)).toEqual(['queue', 'eta', 'recent', 'waiting']);
});
