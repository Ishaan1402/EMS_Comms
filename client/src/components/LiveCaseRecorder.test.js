import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import LiveCaseRecorder from './LiveCaseRecorder';

// Every GET waits until the test answers it, so responses can be delivered in any order.
const mockRequests = [];
let mockHandler = null;

jest.mock('axios', () => ({
  __esModule: true,
  default: {
    get: (url) => new Promise((resolve, reject) => mockRequests.push({ url, resolve, reject })),
    post: () => new Promise(() => {}),
    defaults: { headers: { common: {} } },
  },
}));
jest.mock('../hooks/useCaseEvents', () => ({
  __esModule: true,
  default: (handler) => { mockHandler = handler; return 'live'; },
}));
jest.mock('../hooks/useSegmentRecorder', () => ({
  __esModule: true,
  default: () => ({ isRecording: false, start: jest.fn(), stop: jest.fn() }),
  getMicrophone: jest.fn(),
}));
jest.mock('../hooks/useSegmentUploads', () => ({
  __esModule: true,
  default: () => ({ enqueue: jest.fn(), pending: [], reset: jest.fn(), retry: jest.fn(), waitForIdle: jest.fn(), failedCount: () => 0 }),
}));
jest.mock('./CaseChat', () => ({ caseId }) => <div>messages for case {caseId}</div>);
jest.mock('./CaseUpdateForm', () => ({ caseId }) => <div>updates go to case {caseId}</div>);
jest.mock('react-hot-toast', () => ({ __esModule: true, default: Object.assign(jest.fn(), { success: jest.fn(), error: jest.fn() }) }));

const at = (second) => `2026-10-11T18:00:${String(second).padStart(2, '0')}.000Z`;
const patient = (id, text, updatedSecond, status = 'active') => ({
  id, status, patient_info: text, started_at: at(0), updated_at: at(updatedSecond), info_version: 2,
  latest_update_acknowledged: false, operational_status: status === 'active' ? 'inbound' : 'closed',
});
const segment = (caseId, id, seq, text) => ({
  id, case_id: caseId, seq, text, status: 'completed', recorded_at: at(seq), updated_at: at(seq), info_version: 2,
});

const answer = async (url, data) => {
  const index = mockRequests.findIndex((request) => request.url === url);
  if (index < 0) throw new Error(`No request waiting for ${url}; waiting: ${mockRequests.map((r) => r.url).join(', ')}`);
  const [request] = mockRequests.splice(index, 1);
  await act(async () => { request.resolve({ data }); });
};
const answerAll = async (url, data) => {
  while (mockRequests.some((request) => request.url === url)) await answer(url, data);
};
const send = (type, data = {}) => act(() => { mockHandler(type, data); });

let container;
let root;
beforeEach(() => {
  mockRequests.length = 0;
  container = document.createElement('div');
  root = createRoot(container);
});
afterEach(() => act(() => root.unmount()));

test("a late answer for the previous patient never shows on the next patient's screen", async () => {
  await act(async () => root.render(<LiveCaseRecorder />));
  await answer('/api/hospitals', []);
  await answer('/api/cases?status=active', [patient(1, 'Patient A: fever', 5)]);
  await answer('/api/cases/1/segments', [segment(1, 11, 0, 'A first report')]);
  await answerAll('/api/cases/1/updates', []);
  await answerAll('/api/cases/1/findings', { case_id: 1, as_of: at(1), status: 'none', chief_complaint: null,
    vitals: [], findings: [], treatments: [] });
  expect(container.textContent).toContain('A first report');

  // The stream reconnects: the screen re-fetches A, and those answers are held back.
  await send('ready', {});
  // Meanwhile A is closed and B opened (on another device, or by the replay).
  await send('case.updated', { case: patient(1, 'Patient A: fever', 6, 'closed') });
  await send('case.opened', { case: patient(2, 'Patient B: chest pain', 7) });
  await answer('/api/cases?status=active', [patient(2, 'Patient B: chest pain', 7)]);
  await answer('/api/cases/2/segments', [segment(2, 21, 0, 'B first report')]);

  // Now the held answers for A arrive, A having changed after B opened (a late assessment).
  await answer('/api/cases/1/segments', [segment(1, 11, 0, 'A first report'), segment(1, 12, 1, 'A late report')]);
  await answer('/api/cases/1', patient(1, 'Patient A: fever', 9, 'closed'));

  const text = container.textContent;
  expect(text).toContain('Case #2');
  expect(text).toContain('B first report');
  expect(text).not.toMatch(/A first report|A late report/);
  // Anything typed or said next goes to B.
  expect(text).toContain('updates go to case 2');
  expect(text).toContain('messages for case 2');
});

test('"closed" and "opened" in the same batch still switch to the new patient', async () => {
  await act(async () => root.render(<LiveCaseRecorder />));
  await answer('/api/hospitals', []);
  await answer('/api/cases?status=active', [patient(1, 'Patient A: fever', 5)]);
  await answer('/api/cases/1/segments', [segment(1, 11, 0, 'A first report')]);
  await act(() => {
    mockHandler('case.updated', { case: patient(1, 'Patient A: fever', 6, 'closed') });
    mockHandler('case.opened', { case: patient(2, 'Patient B: chest pain', 7) });
  });
  await answer('/api/cases?status=active', [patient(2, 'Patient B: chest pain', 7)]);
  await answer('/api/cases/2/segments', [segment(2, 21, 0, 'B first report')]);
  expect(container.textContent).toContain('Case #2');
  expect(container.textContent).toContain('B first report');
});

test('only the latest open-case check applies its answer', async () => {
  await act(async () => root.render(<LiveCaseRecorder />));
  await answer('/api/hospitals', []);
  // A second check starts before the first is answered; the first answer arrives last.
  await send('ready', {});
  const [first, second] = mockRequests.filter((request) => request.url === '/api/cases?status=active');
  await act(async () => { second.resolve({ data: [patient(2, 'Patient B: chest pain', 7)] }); });
  await answer('/api/cases/2/segments', [segment(2, 21, 0, 'B first report')]);
  mockRequests.splice(mockRequests.indexOf(first), 1);
  await act(async () => { first.resolve({ data: [patient(1, 'Patient A: fever', 5)] }); });
  await answerAll('/api/cases/1/segments', [segment(1, 11, 0, 'A first report')]);
  expect(container.textContent).toContain('B first report');
  expect(container.textContent).not.toContain('A first report');
});
