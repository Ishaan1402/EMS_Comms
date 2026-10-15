import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import LiveCasesPanel from './LiveCasesPanel';

// Every GET waits until the test answers it, so responses can be delivered in any order.
const mockRequests = [];
let mockHandler = null;

jest.mock('axios', () => ({
  __esModule: true,
  default: {
    get: (url) => new Promise((resolve) => mockRequests.push({ url, resolve })),
    post: () => new Promise(() => {}),
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
  await act(async () => root.render(<LiveCasesPanel />));
  await act(() => mockHandler('hospital.updated', { hospital: {
    id: 1, name: 'General', arrival_highlight_minutes: 15, settings_version: 2,
  } }));
  await resolve(pendingFor('/api/hospitals/mine')[0], {
    id: 1, name: 'General', arrival_highlight_minutes: 5, settings_version: 1,
  });
  expect(container.textContent).toContain('Arrival highlight · 15 min');
});

test('queue rows label the AI category as a suggestion, and the order control carries the rule and the disclosure', async () => {
  await act(async () => root.render(<LiveCasesPanel />));
  await resolve(pendingFor('/api/hospitals/mine')[0], { id: 1, name: 'General' });
  await resolve(pendingFor('/api/cases')[0], [inbound(1, 'Medic 1')]);
  const body = queue(1, [[1, 'Medic 1']]);
  await resolve(pendingFor('/api/cases/queue')[0], { ...body, queue_mode: 'standard', counts: { ...body.counts, crew_alerts: 0 },
    rules: { standard: 'Arrival order rule.', smart: 'Smart rule.' }, rule_versions: { standard: 'draft-4', smart: 'smart-1' } });
  const row = container.querySelector('ul').textContent;
  expect(row).toContain('AI suggests:Can wait');
  const help = container.querySelector('#queue-order-help');
  expect(help.hidden).toBe(true);                       // closed, but in the page
  expect(help.textContent).toContain('Arrival order rule.');
  expect(help.textContent).toContain('Smart rule.');
  expect(help.textContent).toContain('not validated on real EMS audio');
  expect(container.textContent).toContain('0 crew alerts');
  expect(container.textContent).not.toContain('draft-3');
  expect(container.querySelector('[aria-checked="true"]').textContent).toBe('Standard');
});

test('in Manual the control says so, and only then do the rows get arrows', async () => {
  await act(async () => root.render(<LiveCasesPanel />));
  await resolve(pendingFor('/api/hospitals/mine')[0], { id: 1, name: 'General' });
  await resolve(pendingFor('/api/cases')[0], [inbound(1, 'Medic 1')]);
  const body = queue(1, [[1, 'Medic 1']]);
  const manual = { ...body, queue_mode: 'manual', auto_order: false, counts: { ...body.counts, crew_alerts: 0 } };
  await resolve(pendingFor('/api/cases/queue')[0], manual);
  expect(container.querySelector('[aria-checked="true"]').textContent).toBe('Manual');
  expect(container.querySelector('ul').querySelector('[aria-label="Move up"]')).not.toBeNull();
  await act(() => { mockHandler('queue.reordered', {}); });
  await new Promise((done) => setTimeout(done, 350));    // the panel re-fetches the queue shortly after
  await resolve(pendingFor('/api/cases/queue')[0], { ...manual, as_of: at(9), queue_mode: 'smart', auto_order: true });
  expect(container.querySelector('[aria-checked="true"]').textContent).toBe('Smart (AI)');
  expect(container.querySelector('ul').querySelector('[aria-label="Move up"]')).toBeNull();
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

const loadTwoCases = async (extra = {}) => {
  await act(async () => root.render(<LiveCasesPanel />));
  await resolve(pendingFor('/api/hospitals/mine')[0], { id: 1, name: 'General', arrival_highlight_minutes: 5 });
  const waiting = { ...inbound(2, 'Medic 2'), latest_update_acknowledged: false, operational_status: 'inbound' };
  const closed = { ...inbound(3, 'Medic 3'), status: 'closed', closed_at: at(5) };
  await resolve(pendingFor('/api/cases')[0], [inbound(1, 'Medic 1'), waiting, closed]);
  const body = queue(1, [[1, 'Medic 1'], [2, 'Medic 2']]);
  body.cases[1] = { ...body.cases[1], latest_update_acknowledged: false, operational_status: 'inbound' };
  await resolve(pendingFor('/api/cases/queue')[0], { ...body, queue_mode: 'standard', ...extra });
};
const chip = (label) => [...container.querySelectorAll('[aria-pressed]')].find((b) => b.textContent.endsWith(label));
const press = (node) => act(() => node.click());
const rowUnits = () => [...container.querySelectorAll('ul > li')].map((li) => li.textContent).join('|');
const chooseSort = (value) => act(() => {
  const select = [...container.querySelectorAll('label')].find((l) => l.textContent.startsWith('Sort')).querySelector('select');
  Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set.call(select, value);
  select.dispatchEvent(new Event('change', { bubbles: true }));
});

test('a filter narrows the list, says how much it shows, hides the closed cases, and never changes the counts', async () => {
  await loadTwoCases();
  expect(rowUnits()).toContain('Medic 1');
  expect(rowUnits()).toContain('Medic 2');
  expect(container.textContent).toContain('Arrived & closed');
  expect(container.textContent).toContain('2 inbound');

  press(chip('not acknowledged'));
  expect(rowUnits()).toContain('Medic 2');
  expect(rowUnits()).not.toContain('Medic 1');
  expect(container.textContent).not.toContain('Arrived & closed');
  expect(container.textContent).toContain('Showing 1 of 2 (not acknowledged)');
  expect(container.textContent).toContain('2 inbound');          // the counts stay hospital-wide
  expect(chip('not acknowledged').textContent).toBe('1 not acknowledged');

  press([...container.querySelectorAll('button')].find((b) => b.textContent === 'Clear filter'));
  expect(rowUnits()).toContain('Medic 1');
  expect(container.textContent).toContain('Arrived & closed');
  expect(container.textContent).not.toContain('Showing');
});

test('a filter that matches nothing says so and can be cleared from the count that is pressed', async () => {
  await loadTwoCases();
  press(chip('crew alerts'));
  expect(container.textContent).toContain('No cases match this filter.');
  expect(container.textContent).toContain('Showing 0 of 2 (crew alerts)');
  press(chip('crew alerts'));
  expect(container.textContent).not.toContain('No cases match');
});

test('sorting is this screen\'s view: the rows reorder, keep their queue numbers, and say so', async () => {
  await loadTwoCases();
  expect(rowUnits().indexOf('Medic 1')).toBeLessThan(rowUnits().indexOf('Medic 2'));
  chooseSort('waiting');                                              // Medic 2's information is unacknowledged
  expect(rowUnits().indexOf('Medic 2')).toBeLessThan(rowUnits().indexOf('Medic 1'));
  expect(container.textContent).toContain('Your view only: the queue order itself is unchanged.');
  expect(container.querySelector('ul').textContent).toContain('#2');   // still its queue position
  chooseSort('queue');
  expect(rowUnits().indexOf('Medic 1')).toBeLessThan(rowUnits().indexOf('Medic 2'));
});

test('the arrows only show in Manual with the plain queue view, and the screen says why they are gone', async () => {
  await loadTwoCases({ queue_mode: 'manual', auto_order: false });
  const arrows = () => container.querySelectorAll('ul [aria-label="Move up"]').length;
  expect(arrows()).toBe(2);
  chooseSort('eta');
  expect(arrows()).toBe(0);
  expect(container.textContent).toContain('Clear the filter and sort to reorder with the arrows.');
  chooseSort('queue');
  press(chip('not acknowledged'));
  expect(arrows()).toBe(0);
  press(chip('not acknowledged'));
  expect(arrows()).toBe(2);
});
