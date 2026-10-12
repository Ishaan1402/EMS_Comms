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
