import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import LiveCasesPanel from './LiveCasesPanel';

// Every GET waits until the test answers it, so responses can be delivered in any order.
const mockRequests = [];
let mockHandler = null;
const mockPut = jest.fn(() => Promise.resolve({ data: {} }));

jest.mock('axios', () => ({
  __esModule: true,
  default: {
    get: (url) => new Promise((resolve) => mockRequests.push({ url, resolve })),
    post: () => new Promise(() => {}),
    put: (...args) => mockPut(...args),
  },
}));
jest.mock('../hooks/useCaseEvents', () => ({
  __esModule: true,
  default: (handler) => { mockHandler = handler; return 'live'; },
}));
jest.mock('./CaseChat', () => () => null);
jest.mock('react-hot-toast', () => ({ __esModule: true, default: Object.assign(jest.fn(), { error: jest.fn() }) }));

const at = (second) => `2026-10-11T18:00:${String(second).padStart(2, '0')}.000Z`;
const inbound = (id, unit) => ({
  id, ems_unit: unit, status: 'active', patient_info: `${unit} patient`, started_at: at(0), updated_at: at(1),
  eta_at: '2026-10-11T18:30:00.000Z', info_version: 2, latest_update_acknowledged: true,
  acknowledgment: { info_version: 2, user_name: 'John Smith', acknowledged_at: at(1) },
  operational_status: 'acknowledged', preparation_category: 'Can wait', emt_first_name: 'E', emt_last_name: 'T',
  processing: { status: 'completed', needs_review: false, reasons: [] },
});
const queue = (second, units) => ({
  as_of: at(second), rule_version: 'draft-3', rule: 'r', counts: { inbound: 2, needs_review: 0, unacknowledged: 0, prepare_now: 0 },
  cases: units.map(([id, unit], index) => ({ ...inbound(id, unit), queue_position: index + 1, queue_reasons: ['Can wait'], vital_changes: [] })),
});

const pendingFor = (url) => mockRequests.filter((request) => request.url === url);
const resolve = async (request, data) => {
  mockRequests.splice(mockRequests.indexOf(request), 1);
  await act(async () => { request.resolve({ data }); });
};
const rowOrder = (container) => {
  const text = container.querySelector('ul').textContent;
  return ['Medic 1', 'Medic 2'].sort((a, b) => text.indexOf(a) - text.indexOf(b));
};

const chip = (label) => [...container.querySelectorAll('[aria-pressed]')].find((b) => b.textContent.startsWith(label));
const press = (node) => act(() => node.click());
const orderButton = () => [...container.querySelectorAll('button')].find((b) => b.textContent.startsWith('Order:'));

let container;
let root;
beforeEach(() => {
  mockRequests.length = 0;
  container = document.createElement('div');
  root = createRoot(container);
});
afterEach(() => act(() => root.unmount()));

test('an older queue answer arriving last does not undo a newer order', async () => {
  await act(async () => root.render(<LiveCasesPanel />));
  await resolve(pendingFor('/api/hospitals/mine')[0], { id: 1, name: 'General' });
  await resolve(pendingFor('/api/cases')[0], [inbound(1, 'Medic 1'), inbound(2, 'Medic 2')]);
  await resolve(pendingFor('/api/cases/queue')[0], queue(1, [[1, 'Medic 1'], [2, 'Medic 2']]));
  expect(rowOrder(container)).toEqual(['Medic 1', 'Medic 2']);

  // Two refreshes overlap (e.g. reconnects); the newer one is answered first.
  await act(() => { mockHandler('ready', { server_time: at(2) }); });
  await act(() => { mockHandler('ready', { server_time: at(3) }); });
  const [older, newer] = pendingFor('/api/cases/queue');
  await resolve(newer, queue(5, [[2, 'Medic 2'], [1, 'Medic 1']]));
  await resolve(older, queue(4, [[1, 'Medic 1'], [2, 'Medic 2']]));

  expect(rowOrder(container)).toEqual(['Medic 2', 'Medic 1']);
});

test('a delayed hospital fetch cannot undo a newer setting received by the stream', async () => {
  // The arrival highlight decides which cases count as "arriving soon": 15 minutes (newer, from the stream)
  // includes a case 10 minutes out; the older fetch's 5 minutes would not.
  await act(async () => root.render(<LiveCasesPanel />));
  await act(() => mockHandler('hospital.updated', { hospital: {
    id: 1, name: 'General', arrival_highlight_minutes: 15, settings_version: 2,
  } }));
  await resolve(pendingFor('/api/hospitals/mine')[0], {
    id: 1, name: 'General', arrival_highlight_minutes: 5, settings_version: 1,
  });
  const soon = { ...inbound(1, 'Medic 1'), eta_at: new Date(Date.now() + 10 * 60000).toISOString() };
  await resolve(pendingFor('/api/cases')[0], [soon]);
  await resolve(pendingFor('/api/cases/queue')[0], { ...queue(1, [[1, 'Medic 1']]), cases: [{ ...soon, queue_position: 1, queue_reasons: [] }] });
  expect(chip('Arriving soon')).toBeDefined();
  expect(chip('Arriving soon').textContent).toBe('Arriving soon1');
});

test('queue rows label the AI category as the AI\'s, and the order menu carries the options and the disclosure', async () => {
  await act(async () => root.render(<LiveCasesPanel />));
  await resolve(pendingFor('/api/hospitals/mine')[0], { id: 1, name: 'General' });
  await resolve(pendingFor('/api/cases')[0], [inbound(1, 'Medic 1')]);
  const body = queue(1, [[1, 'Medic 1']]);
  await resolve(pendingFor('/api/cases/queue')[0], { ...body, queue_mode: 'standard', counts: { ...body.counts, crew_alerts: 0 } });
  expect(container.querySelector('ul').textContent).toContain('Can wait');
  expect(container.querySelector('ul').textContent).not.toContain('AI:');   // the case says where it comes from
  expect(container.querySelector('[role="listbox"]')).toBeNull();            // closed: nothing in the way
  expect(orderButton().textContent).toBe('Order: Standard');
  press(orderButton());
  const options = [...container.querySelectorAll('[role="option"]')];
  expect(options.map((o) => o.querySelector('span span').textContent)).toEqual(['Standard', 'AI-assisted', 'Manual']);
  expect(options[0].getAttribute('aria-selected')).toBe('true');
  expect(container.textContent).toContain('not validated on real EMS audio');
  expect(container.textContent).not.toContain('draft-');
});

test('in Manual the order says so and the rows get drag handles; Standard has none', async () => {
  await loadTwoCases({ queue_mode: 'manual', auto_order: false });
  expect(orderButton().textContent).toBe('Order: Manual');
  expect(container.querySelector('ul').querySelector('[aria-label^="Move "]')).not.toBeNull();
  await act(() => { mockHandler('queue.reordered', {}); });
  await new Promise((done) => setTimeout(done, 350));    // the panel re-fetches the queue shortly after
  await resolve(pendingFor('/api/cases/queue')[0], { ...queue(9, [[1, 'Medic 1'], [2, 'Medic 2']]), queue_mode: 'standard', auto_order: true });
  expect(orderButton().textContent).toBe('Order: Standard');
  expect(container.querySelector('ul').querySelector('[aria-label^="Move "]')).toBeNull();
});

test('a pushed clip row from a finished report clears its "reporting…" label', async () => {
  await act(async () => root.render(<LiveCasesPanel />));
  await resolve(pendingFor('/api/hospitals/mine')[0], { id: 1, name: 'General' });
  const initial = { ...inbound(1, 'Medic 1'), info_version: 1 };
  await resolve(pendingFor('/api/cases')[0], [initial]);
  await resolve(pendingFor('/api/cases/queue')[0], { ...queue(1, [[1, 'Medic 1']]), cases: [initial] });
  const clip = { id: 1, case_id: 1, seq: 0, text: 'BP 100 over 64', status: 'completed',
    recorded_at: at(0), updated_at: at(2), report_id: 'r1', report_complete: 0, info_version: null };
  await resolve(pendingFor('/api/cases/1/segments')[0], [clip]);
  expect(container.textContent).toContain('reporting…');

  await act(() => mockHandler('segment.updated', {
    case_id: 1, segment: { ...clip, report_complete: 1, info_version: 2, updated_at: at(5) },
  }));
  expect(container.textContent).not.toContain('reporting…');
});

test('the panel clips its corners without becoming a scroll container, so the case header can stick to the window', async () => {
  await act(async () => root.render(<LiveCasesPanel />));
  const panel = container.firstChild;
  expect(panel.className).toContain('overflow-clip');
  expect(panel.className).not.toContain('overflow-hidden');
});

// ---- this screen's own filter and sort: never the hospital's order ------------------------------

const WAITING = { ...inbound(2, 'Medic 2'), latest_update_acknowledged: false, operational_status: 'inbound' };
const loadTwoCases = async (extra = {}) => {
  await act(async () => root.render(<LiveCasesPanel />));
  await resolve(pendingFor('/api/hospitals/mine')[0], { id: 1, name: 'General', arrival_highlight_minutes: 5 });
  const closed = { ...inbound(3, 'Medic 3'), status: 'closed', closed_at: at(5) };
  await resolve(pendingFor('/api/cases')[0], [inbound(1, 'Medic 1'), WAITING, closed]);
  const body = queue(1, [[1, 'Medic 1'], [2, 'Medic 2']]);
  body.cases[1] = { ...body.cases[1], latest_update_acknowledged: false, operational_status: 'inbound' };
  await resolve(pendingFor('/api/cases/queue')[0], { ...body, queue_mode: 'standard', ...extra });
};
const rowUnits = () => [...container.querySelectorAll('ul > li')].map((li) => li.textContent).join('|');
const chooseSort = (label) => {
  press([...container.querySelectorAll('button')].find((b) => b.textContent.startsWith('Sort:')));
  press([...container.querySelectorAll('[role="option"]')].find((o) => o.textContent.startsWith(label)));
};

test('a filter narrows the list and hides the closed cases, and the counts stay those of the whole queue', async () => {
  await loadTwoCases();
  expect(rowUnits()).toContain('Medic 1');
  expect(rowUnits()).toContain('Medic 2');
  expect(container.textContent).toContain('Arrived and closed');
  expect(chip('All').textContent).toBe('All2');

  press(chip('Not acknowledged'));
  expect(rowUnits()).toContain('Medic 2');
  expect(rowUnits()).not.toContain('Medic 1');
  expect(container.textContent).not.toContain('Arrived and closed');
  expect(chip('Not acknowledged').getAttribute('aria-pressed')).toBe('true');
  expect(chip('All').textContent).toBe('All2');                   // the counts stay hospital-wide

  press(chip('Not acknowledged'));                                // pressing it again shows everyone
  expect(rowUnits()).toContain('Medic 1');
  expect(container.textContent).toContain('Arrived and closed');
});

test('a filter with nothing in it is hidden until it is on; one that empties while on says so and can be cleared', async () => {
  await loadTwoCases();
  expect(chip('Crew alerts')).toBeUndefined();                    // zero: not offered
  expect(chip('Needs review')).toBeUndefined();
  press(chip('Not acknowledged'));
  // Another doctor acknowledges the case while this screen has the filter on.
  await act(() => mockHandler('case.updated', { case: {
    ...WAITING, latest_update_acknowledged: true, operational_status: 'acknowledged', updated_at: at(30) } }));
  expect(container.textContent).toContain('No cases match this filter.');
  expect(chip('Not acknowledged')).toBeDefined();                  // still there, pressed, so it can be turned off
  press([...container.querySelectorAll('button')].find((b) => b.textContent === 'Show all'));
  expect(container.textContent).not.toContain('No cases match');
  expect(rowUnits()).toContain('Medic 1');
});

test('sorting is this screen\'s view: the rows reorder and keep their queue numbers', async () => {
  await loadTwoCases();
  expect(rowUnits().indexOf('Medic 1')).toBeLessThan(rowUnits().indexOf('Medic 2'));
  chooseSort('Waiting');                                          // Medic 2's information is unacknowledged
  expect(rowUnits().indexOf('Medic 2')).toBeLessThan(rowUnits().indexOf('Medic 1'));
  expect(container.querySelector('ul').textContent).toContain('#2');   // still its queue position
  expect([...container.querySelectorAll('button')].find((b) => b.textContent.startsWith('Sort:')).textContent).toBe('Sort: Waiting');
  chooseSort('Queue');
  expect(rowUnits().indexOf('Medic 1')).toBeLessThan(rowUnits().indexOf('Medic 2'));
});

test('in Manual, moving a row with the keyboard shows the new order at once and saves it for the hospital', async () => {
  mockPut.mockClear();
  await loadTwoCases({ queue_mode: 'manual', auto_order: false });
  const handle = container.querySelector('ul [aria-label^="Move Medic 1"]');
  await act(async () => handle.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true })));
  expect(rowUnits().indexOf('Medic 2')).toBeLessThan(rowUnits().indexOf('Medic 1'));
  expect(mockPut).toHaveBeenCalledWith('/api/hospitals/mine/doctor-order', { case_ids: [2, 1] });
  expect(rowUnits()).not.toContain('from #');          // no "moved" cue for this doctor's own move
});

test('in Manual, a move that fails to save puts back the order the server last confirmed', async () => {
  mockPut.mockClear();
  mockPut.mockImplementationOnce(() => Promise.reject(new Error('503')));
  await loadTwoCases({ queue_mode: 'manual', auto_order: false });
  const handle = container.querySelector('ul [aria-label^="Move Medic 1"]');
  await act(async () => handle.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true })));
  // The recovery GET never answers here (as in an outage); the screen must not keep the unsaved order.
  expect(pendingFor('/api/cases/queue')).toHaveLength(1);
  expect(rowUnits().indexOf('Medic 1')).toBeLessThan(rowUnits().indexOf('Medic 2'));
});

test('the drag handles only show with the plain queue view, and the screen says why they are gone', async () => {
  await loadTwoCases({ queue_mode: 'manual', auto_order: false });
  const arrows = () => container.querySelectorAll('ul [aria-label^="Move "]').length;
  expect(arrows()).toBe(2);
  chooseSort('ETA');
  expect(arrows()).toBe(0);
  expect(container.textContent).toContain('Show all cases in queue order to drag them into a new order.');
  chooseSort('Queue');
  press(chip('Not acknowledged'));
  expect(arrows()).toBe(0);
  press(chip('Not acknowledged'));
  expect(arrows()).toBe(2);
});

const loadAiAssisted = async (pinned = false) => {
  mockPut.mockClear();
  await act(async () => root.render(<LiveCasesPanel />));
  await resolve(pendingFor('/api/hospitals/mine')[0], { id: 1, name: 'General' });
  await resolve(pendingFor('/api/cases')[0], [inbound(1, 'Medic 1'), inbound(2, 'Medic 2')]);
  const body = queue(1, [[1, 'Medic 1'], [2, 'Medic 2']]);
  body.cases[0] = { ...body.cases[0], pinned };
  await resolve(pendingFor('/api/cases/queue')[0], { ...body, queue_mode: 'ai_assisted', auto_order: true });
};

test('in AI-assisted a doctor can move a row, and the move pins it for the hospital', async () => {
  await loadAiAssisted();
  expect(container.textContent).not.toContain('Back to AI order');
  const handle = container.querySelector('ul [aria-label^="Move Medic 1"]');
  await act(async () => handle.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true })));
  expect(rowOrder(container)).toEqual(['Medic 2', 'Medic 1']);
  expect(mockPut).toHaveBeenCalledWith('/api/hospitals/mine/ai-pins', { case_ids: [2, 1], moved: 1 });
});

test('in AI-assisted a pinned row says so and the team can go back to the AI order', async () => {
  await loadAiAssisted(true);
  expect(container.querySelector('ul').textContent).toContain('Moved by your team');
  expect(container.textContent).toContain('Back to AI order');
});

test('in AI-assisted a case that needs a person now has no handle: it stays on top', async () => {
  await loadTwoCases({ queue_mode: 'ai_assisted', auto_order: true });
  expect(container.querySelector('ul [aria-label^="Move Medic 1"]')).not.toBeNull();
  expect(container.querySelector('ul [aria-label^="Move Medic 2"]')).toBeNull();   // not acknowledged
});
