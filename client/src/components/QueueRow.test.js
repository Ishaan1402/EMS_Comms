import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import QueueRow from './QueueRow';

jest.mock('axios', () => ({ __esModule: true, default: { post: jest.fn() } }));
jest.mock('react-hot-toast', () => ({ __esModule: true, default: Object.assign(jest.fn(), { success: jest.fn(), error: jest.fn() }) }));

let container;
let root;
beforeEach(() => {
  container = document.createElement('div');
  root = createRoot(container);
});
afterEach(() => act(() => root.unmount()));

const NOW = Date.parse('2026-10-15T15:00:00Z');
const at = (minutes) => new Date(NOW + minutes * 60000).toISOString();
const base = (overrides = {}) => ({
  id: 7, ems_unit: 'Medic 10', status: 'active', arrived_at: null, patient_info: 'Chest pain',
  started_at: at(-20), last_segment_at: at(-1), eta_at: at(10), queue_position: 1,
  queue_reasons: ['ETA 10 min'], vital_changes: [{ label: 'HR', from: 100, to: 130 }], preparation_category: 'Can wait',
  operational_status: 'acknowledged', acknowledgment: { user_name: 'John Smith', acknowledged_at: at(-5) },
  processing: { status: 'completed', needs_review: false }, crew_alerts: { active: [], unacknowledged: false },
  ...overrides,
});
const render = (liveCase, props = {}) => {
  act(() => root.render(<ul><QueueRow liveCase={liveCase} serverNow={NOW} onSelect={() => {}} {...props} /></ul>));
  return container.querySelector('li');
};

test('the left edge asks for attention only for a crew alert or a failure', () => {
  const alert = { active: [{ alert: 'stroke', label: 'Stroke', key_time: null }], unacknowledged: false };
  expect(render(base({ crew_alerts: alert, preparation_category: 'Prepare now' })).className).toContain('border-l-slate-900');
  expect(render(base({ processing: { status: 'failed', needs_review: true } })).className).toContain('border-l-red-600');
  // Prepare now, a case nobody has acknowledged and Cannot assess are marked in the row's text, not by colour at the edge.
  ['Prepare now', 'Cannot assess'].forEach((category) => {
    expect(render(base({ preparation_category: category })).className).toContain('border-l-transparent');
  });
  expect(render(base({ operational_status: 'inbound' })).className).toContain('border-l-transparent');
  expect(render(base({ status: 'closed', crew_alerts: alert })).className).toContain('border-l-transparent');
});

test('quiet when normal: an acknowledged case has no acknowledged marker, no pills and no vitals', () => {
  const row = render(base());
  expect(row.textContent).not.toContain('Acknowledged');
  expect(row.textContent).not.toContain('HR');
  expect(row.querySelectorAll('.rounded-full:not([aria-hidden])')).toHaveLength(0);
  expect(row.textContent).toContain('AI: Can wait');
});

test('a case nobody has acknowledged says so, as amber text', () => {
  const row = render(base({ operational_status: 'inbound' }));
  const marker = row.querySelector('span.text-amber-700');
  expect(marker.textContent).toBe('Not acknowledged');
  expect(marker.className).toContain('text-amber-700');
  expect(marker.className).not.toMatch(/\bbg-|border-/);
});

test('the row is who and how soon, the complaint, and the AI label; the reasons are in the tooltip', () => {
  const row = render(base({ preparation_category: 'Prepare now' }));
  expect(row.textContent).toContain('#1');
  expect(row.textContent).toContain('Medic 10');
  expect(row.textContent).toContain('10 min');
  expect(row.textContent).toContain('Chest pain');
  expect(row.textContent).toContain('AI: Prepare now');
  expect(row.textContent).not.toContain('Why here');
  expect(row.querySelector('button').title).toBe('Why here: ETA 10 min');
});

test('the Standard order\'s suggestion is in the tooltip, and only where it differs from where the case is', () => {
  expect(render(base({ suggested_position: 3 })).querySelector('button').title).toContain('Standard order would put it #3');
  expect(render(base({ suggested_position: 1 })).querySelector('button').title).not.toContain('Standard order');
});

test('arriving soon is the arrival time in blue, not another badge', () => {
  const soon = render(base({ eta_at: at(3) }), { arrivalMinutes: 5 });
  expect([...soon.querySelectorAll('span')].find((s) => s.textContent === '3 min').className).toContain('text-blue-600');
  const later = render(base({ eta_at: at(30) }), { arrivalMinutes: 5 });
  expect([...later.querySelectorAll('span')].find((s) => s.textContent === '30 min').className).not.toContain('text-blue-600');
  expect(soon.textContent).not.toContain('Arriving soon');
});

test('the arrival time says when it is now, overdue, or missing, and shows the clock time on hover', () => {
  expect(render(base({ eta_at: at(0) })).textContent).toContain('Arriving now');
  expect(render(base({ eta_at: at(-7) })).textContent).toContain('7 min overdue');
  expect(render(base({ eta_at: null })).textContent).toContain('No ETA');
  expect(render(base()).querySelector('[title^="ETA "]')).not.toBeNull();
});

test('a report in progress says Reporting…; an idle case says how long it has been quiet, only after five minutes', () => {
  expect(render(base({ open_report: { id: 'r1', clips: 1, started_at: at(-1) } })).textContent).toContain('Reporting…');
  expect(render(base({ last_segment_at: at(-9) })).textContent).toContain('No audio 9m');
  expect(render(base({ last_segment_at: at(-3) })).textContent).not.toContain('No audio');
});

test('over cases show where they ended up and nothing else', () => {
  const closed = render(base({ status: 'closed' }));
  expect(closed.textContent).toContain('Closed');
  expect(closed.textContent).not.toContain('AI:');
  expect(render(base({ arrived_at: at(-2) })).textContent).toContain('Arrived');
});

test('a case that moved says where from, up in blue and down in gray', () => {
  expect(render(base(), { move: { from: 3, to: 1 } }).textContent).toContain('from #3');
  expect(render(base(), { move: { from: 3, to: 1 } }).querySelector('.text-blue-600')).not.toBeNull();
  expect(render(base(), { move: { from: 1, to: 3 } }).querySelector('.text-blue-600')).toBeNull();
});

test('new clips the doctor has not opened are a count in text', () => {
  expect(render(base(), { unseen: 3 }).textContent).toContain('3 new');
});
