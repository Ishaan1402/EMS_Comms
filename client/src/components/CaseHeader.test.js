import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import CaseHeader from './CaseHeader';

jest.mock('axios', () => ({ __esModule: true, default: { get: jest.fn() } }));
jest.mock('react-hot-toast', () => ({ __esModule: true, default: Object.assign(jest.fn(), { error: jest.fn() }) }));

let container;
let root;
beforeEach(() => {
  container = document.createElement('div');
  root = createRoot(container);
});
afterEach(() => act(() => root.unmount()));

const liveCase = (overrides = {}) => ({
  id: 7, ems_unit: 'Medic 10', status: 'active', arrived_at: null, closed_at: null,
  patient_info: 'Chest pain', emt_first_name: 'Maria', emt_last_name: 'Garcia',
  started_at: '2026-10-15T15:00:00Z', eta_at: '2026-10-15T15:20:00Z', latest_update_acknowledged: false, ...overrides,
});
const render = (props) => act(() => root.render(
  <CaseHeader liveCase={liveCase()} onAcknowledge={() => {}} onMarkArrived={() => {}} {...props} />,
));
const buttons = () => [...container.querySelectorAll('button')].map((b) => b.textContent.trim());

test('the actions sit in the sticky bar with the case, so they stay in reach while the case scrolls', () => {
  render();
  const bar = container.querySelector('.sticky');
  expect(bar.textContent).toContain('Medic 10');
  expect(bar.textContent).toContain('Chest pain');
  expect([...bar.querySelectorAll('button')].map((b) => b.textContent.trim())).toEqual(['Acknowledge', 'Mark arrived']);
});

test('the sticky bar is a direct child of whatever holds the case: sticky only sticks inside its parent', () => {
  render();
  expect(container.firstElementChild.classList.contains('sticky')).toBe(true);
});

test('Acknowledge only while there is something to acknowledge; no actions for a closed case', () => {
  render({ liveCase: liveCase({ latest_update_acknowledged: true }) });
  expect(buttons()).toEqual(['Mark arrived', 'Export FHIR']);
  render({ liveCase: liveCase({ arrived_at: '2026-10-15T15:10:00Z' }) });
  expect(buttons()).toEqual(['Acknowledge', 'Export FHIR']);
  render({ liveCase: liveCase({ status: 'closed', closed_at: '2026-10-15T15:30:00Z' }) });
  expect(buttons()).toEqual(['Export FHIR']);
  expect(container.textContent).toContain('closed ');
});

test('the buttons call what the panel gave them', () => {
  const onAcknowledge = jest.fn();
  const onMarkArrived = jest.fn();
  render({ onAcknowledge, onMarkArrived });
  act(() => [...container.querySelectorAll('button')].find((b) => b.textContent.includes('Acknowledge')).click());
  act(() => [...container.querySelectorAll('button')].find((b) => b.textContent.includes('Mark arrived')).click());
  expect(onAcknowledge).toHaveBeenCalledTimes(1);
  expect(onMarkArrived).toHaveBeenCalledTimes(1);
});

test('one meta line says who, which case and when, and does not repeat the ETA', () => {
  render();
  const meta = container.querySelector('p').textContent;
  expect(meta).toContain('Case #7 · EMT Maria Garcia · started');
  expect(meta).not.toContain('ETA');
});

test('why the case sits where it does is said in the words of the order in use', () => {
  const inQueue = { queue_reasons: ['AI suggests Prepare now', 'ETA 10 min'], queue_position: 1, suggested_position: 3 };
  render({ inQueue: { ...inQueue, suggested_position: 1 } });
  expect(container.textContent).toContain('Why here: AI suggests Prepare now · ETA 10 min');
  expect(container.textContent).not.toContain('Standard order');
  render({ inQueue, queueMode: 'ai_assisted' });          // the AI moved it: say where Standard would have put it
  expect(container.textContent).toContain('Why here: AI suggests Prepare now · ETA 10 min · Standard order: #3');
  render({ inQueue: { ...inQueue, suggested_position: 1 }, queueMode: 'ai_assisted' });
  expect(container.textContent).not.toContain('Standard order');
  render({ inQueue, queueMode: 'manual' });
  expect(container.textContent).toContain('Placed by your team. Standard order would put it #3: AI suggests Prepare now');
  render({ inQueue: undefined });
  expect(container.textContent).not.toContain('Why here');
});
