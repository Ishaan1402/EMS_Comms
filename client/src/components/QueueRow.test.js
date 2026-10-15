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
  queue_reasons: ['ETA 10 min'], vital_changes: [], preparation_category: 'Can wait',
  operational_status: 'acknowledged', acknowledgment: { user_name: 'John Smith', acknowledged_at: at(-5) },
  processing: { status: 'completed', needs_review: false }, crew_alerts: { active: [], unacknowledged: false },
  ...overrides,
});
const render = (liveCase, props = {}) => {
  act(() => root.render(<ul><QueueRow liveCase={liveCase} serverNow={NOW} onSelect={() => {}} {...props} /></ul>));
  return container.querySelector('li');
};

test('the left stripe follows the most urgent thing on the row: crew alert, then Prepare now or a failure, then a person to look', () => {
  const alert = { active: [{ alert: 'stroke', label: 'Stroke', key_time: null }], unacknowledged: false };
  expect(render(base({ crew_alerts: alert, preparation_category: 'Prepare now' })).className).toContain('border-l-slate-900');
  expect(render(base({ preparation_category: 'Prepare now' })).className).toContain('border-l-red-600');
  expect(render(base({ processing: { status: 'failed', needs_review: true } })).className).toContain('border-l-red-600');
  expect(render(base({ operational_status: 'inbound' })).className).toContain('border-l-amber-500');
  expect(render(base({ preparation_category: 'Cannot assess' })).className).toContain('border-l-amber-500');
  expect(render(base()).className).toContain('border-l-transparent');
  expect(render(base({ status: 'closed', preparation_category: 'Prepare now' })).className).toContain('border-l-transparent');
});

test('a case the hospital has not acknowledged says so; an acknowledged one stays quiet', () => {
  const row = render(base({ operational_status: 'inbound' }));
  expect(row.textContent).toContain('Not acknowledged');
  expect(row.textContent).not.toContain('Inbound');
  const quiet = render(base());
  expect(quiet.textContent).toContain('Acknowledged');
  expect(quiet.textContent).not.toContain('Not acknowledged');
  expect(quiet.querySelector('[title^="Acknowledged by John Smith"]')).not.toBeNull();
});

test('the row keeps the AI label as a suggestion and the ETA, and drops the footer and the reasons line', () => {
  const row = render(base({ preparation_category: 'Prepare now' }));
  expect(row.textContent).toContain('AI suggests:Prepare now');
  expect(row.textContent).toContain('arrives in 10 min');
  expect(row.textContent).not.toContain('Why here');
  expect(row.textContent).not.toContain('EMT');
  expect(row.querySelector('button').title).toBe('Why here: ETA 10 min');
});

test('the Standard order\'s suggestion shows only where it differs from where the case is', () => {
  expect(render(base({ suggested_position: 3 })).textContent).toContain('Standard order would put it #3');
  expect(render(base({ suggested_position: 1 })).textContent).not.toContain('Standard order would put it');
});

test('the idle time is quiet text, and a report in progress shows Reporting… instead of Live', () => {
  const idle = render(base({ last_segment_at: at(-9) }));
  expect(idle.textContent).toContain('last audio 9m ago');
  expect(idle.querySelector('.rounded-full.bg-orange-100')).toBeNull();
  expect(render(base()).textContent).toContain('Live');
  const reporting = render(base({ open_report: { id: 'r1', clips: 1, started_at: at(-1) } }));
  expect(reporting.textContent).toContain('Reporting…');
  expect(reporting.textContent).not.toContain('Live');
});

test('the changed vitals keep the conditions when they differ', () => {
  const row = render(base({ vital_changes: [{ label: 'SpO2', from: 89, from_qualifier: 'on room air', to: 92, to_qualifier: 'on 4 liters oxygen' }] }));
  expect(row.textContent).toContain('SpO2 89 (on room air)→92 (on 4 liters oxygen)');
});
