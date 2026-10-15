import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import LiveCaseRecorder from './LiveCaseRecorder';
import { getMicrophone } from '../hooks/useSegmentRecorder';

// Every GET waits until the test answers it, so responses can be delivered in any order.
const mockRequests = [];
const mockPosts = [];
let mockRecording = false;
let mockHandler = null;
let mockOnUploaded;
let mockOnSegment;
const mockStart = jest.fn();
const mockStop = jest.fn();
const mockEnqueue = jest.fn();
let mockPending = [];
let mockFailedCount = 0;

jest.mock('axios', () => ({
  __esModule: true,
  default: {
    get: (url) => new Promise((resolve, reject) => mockRequests.push({ url, resolve, reject })),
    post: (url, body) => { mockPosts.push({ url, body }); return new Promise((resolve, reject) => mockRequests.push({ url, body, resolve, reject })); },
    defaults: { headers: { common: {} } },
  },
}));
jest.mock('../hooks/useCaseEvents', () => ({
  __esModule: true,
  default: (handler) => { mockHandler = handler; return 'live'; },
}));
jest.mock('../hooks/useSegmentRecorder', () => ({
  __esModule: true,
  default: ({ onSegment }) => {
    mockOnSegment = onSegment;
    return { isRecording: mockRecording, start: mockStart, stop: mockStop };
  },
  getMicrophone: jest.fn(),
}));
jest.mock('../hooks/useSegmentUploads', () => ({
  __esModule: true,
  default: (onUploaded) => {
    mockOnUploaded = onUploaded;
    return { enqueue: mockEnqueue, pending: mockPending, reset: jest.fn(), retry: jest.fn(), waitForIdle: jest.fn(), failedCount: () => mockFailedCount };
  },
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
  mockPosts.length = 0;
  mockRecording = false;
  mockStart.mockReset();
  mockStop.mockReset().mockResolvedValue();
  mockEnqueue.mockReset();
  mockPending = [];
  mockFailedCount = 0;
  getMicrophone.mockReset();
  container = document.createElement('div');
  root = createRoot(container);
});
afterEach(() => act(() => root.unmount()));

const openPatient = async (id) => {
  await answer('/api/cases?status=active', [patient(id, `Patient ${id}`, 7)]);
  await answer(`/api/cases/${id}/segments`, [segment(id, id * 10, 0, `Report for patient ${id}`)]);
};
const switchPatient = async () => {
  await send('case.updated', { case: patient(1, 'Patient 1', 8, 'closed') });
  await send('case.opened', { case: patient(2, 'Patient 2', 9) });
  await openPatient(2);
};

test('a local case start supersedes its pending SSE lookup so the next case is not locked', async () => {
  await act(async () => root.render(<LiveCaseRecorder />));
  await answer('/api/hospitals', [{ id: 1, name: 'General' }]);
  await answer('/api/cases?status=active', []);
  act(() => {
    const hospital = container.querySelector('select');
    hospital.value = '1';
    hospital.dispatchEvent(new Event('change', { bubbles: true }));
  });
  act(() => [...container.querySelectorAll('button')]
    .find((button) => button.textContent === 'Start Case Without Audio').click());
  // The server broadcasts the case before the POST response reaches its creator.
  await send('case.opened', { case: patient(1, 'Patient 1', 7) });
  await answer('/api/cases?status=active', [patient(1, 'Patient 1', 7)]);
  await answer('/api/cases', patient(1, 'Patient 1', 7));
  await answer('/api/cases/1/segments', []);
  await act(async () => [...container.querySelectorAll('button')]
    .find((button) => button.textContent === 'End Case').click());
  await answer('/api/cases/1/close', patient(1, 'Patient 1', 8, 'closed'));
  expect(container.textContent).not.toContain('Checking for an open case');
  expect([...container.querySelectorAll('button')]
    .find((button) => button.textContent === 'Start Case Without Audio').disabled).toBe(false);
});

test('an upload response for the old patient never enters the next transcript', async () => {
  await act(async () => root.render(<LiveCaseRecorder />));
  await openPatient(1);
  await switchPatient();
  act(() => mockOnUploaded(segment(1, 12, 1, 'Old patient upload finished late')));
  expect(container.textContent).toContain('Report for patient 2');
  expect(container.textContent).not.toContain('Old patient upload finished late');
});

test('microphone permission arriving after the patient changes cannot start recording', async () => {
  let permit;
  getMicrophone.mockImplementation(() => new Promise((resolve) => { permit = resolve; }));
  const stop = jest.fn();
  const stream = { getTracks: () => [{ stop }] };
  await act(async () => root.render(<LiveCaseRecorder />));
  await openPatient(1);
  act(() => [...container.querySelectorAll('button')]
    .find((button) => button.textContent === 'Record Report').click());
  await switchPatient();
  await act(async () => permit(stream));
  expect(mockStart).not.toHaveBeenCalled();
  expect(stop).toHaveBeenCalledTimes(1);
});

test('a remote case closure stops the microphone and keeps its final clip with the original patient', async () => {
  const stream = { getTracks: () => [{ stop: jest.fn() }] };
  getMicrophone.mockResolvedValue(stream);
  mockStart.mockReturnValue(true);
  mockStop.mockImplementation(async () => {
    mockOnSegment({ blob: new Blob(['last words']), recordedAt: at(10), durationMs: 1000 });
  });
  await act(async () => root.render(<LiveCaseRecorder />));
  await openPatient(1);
  await act(async () => [...container.querySelectorAll('button')]
    .find((button) => button.textContent === 'Record Report').click());
  await switchPatient();
  expect(mockStop).toHaveBeenCalledTimes(1);
  expect(mockEnqueue).toHaveBeenCalledWith(expect.objectContaining({ caseId: 1, seq: 1 }));
  expect(mockEnqueue.mock.calls.every(([item]) => item.caseId === 1)).toBe(true);
});

test('unsent audio from a remotely closed case stays visible and blocks adopting another patient', async () => {
  await act(async () => root.render(<LiveCaseRecorder />));
  await openPatient(1);
  mockPending = [{ caseId: 1, seq: 1, status: 'upload_failed', recorded_at: at(1), duration_ms: 1000 }];
  mockFailedCount = 1;
  await switchPatient();
  expect(container.textContent).toContain('Transcript — Case #1 (closed)');
  expect(container.textContent).not.toContain('Report for patient 2');
  expect(container.textContent).toContain('Discard unsent audio and continue');
  expect([...container.querySelectorAll('button')]
    .find((button) => button.textContent === 'Start Case & Record Report').disabled).toBe(true);
  const leaving = new Event('beforeunload', { cancelable: true });
  window.dispatchEvent(leaving);
  expect(leaving.defaultPrevented).toBe(true);
});

test('permission arriving after logout releases the stream without starting a recorder', async () => {
  let permit;
  getMicrophone.mockImplementation(() => new Promise((resolve) => { permit = resolve; }));
  const stop = jest.fn();
  await act(async () => root.render(<LiveCaseRecorder />));
  await openPatient(1);
  act(() => [...container.querySelectorAll('button')]
    .find((button) => button.textContent === 'Record Report').click());
  act(() => root.render(<div>Logged out</div>));
  await act(async () => permit({ getTracks: () => [{ stop }] }));
  expect(mockStart).not.toHaveBeenCalled();
  expect(stop).toHaveBeenCalledTimes(1);
});

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

test('after a reload mid-report, the open report is sent with the clips the server has', async () => {
  await act(async () => root.render(<LiveCaseRecorder />));
  await answer('/api/hospitals', []);
  const open = { ...patient(1, 'Patient A: fever', 5), open_report: { id: 'r1', clips: 2, started_at: at(1) } };
  await answer('/api/cases?status=active', [open]);
  await answer('/api/cases/1/segments', [segment(1, 11, 0, 'BP 92 over'), segment(1, 12, 1, '58')]);
  expect(mockPosts).toEqual([{ url: '/api/cases/1/reports/r1/end', body: { clips: 2 } }]);
});

test("a reconnect doesn't send a report another device may still be recording", async () => {
  await act(async () => root.render(<LiveCaseRecorder />));
  await answer('/api/hospitals', []);
  await answer('/api/cases?status=active', []);
  await send('case.opened', { case: patient(2, 'Patient B: chest pain', 7) });
  const open = { ...patient(2, 'Patient B: chest pain', 7), open_report: { id: 'r9', clips: 1, started_at: at(7) } };
  await answer('/api/cases?status=active', [open]);
  await answer('/api/cases/2/segments', [segment(2, 21, 0, 'B first words')]);
  expect(mockPosts).toEqual([]);
});


const click = async (label) => act(async () => {
  const button = [...container.querySelectorAll('button')].find((b) => b.textContent === label);
  if (!button) throw new Error(`Button ${label} missing: ${container.textContent}`);
  button.click();
});
const startMicrophoneReport = async () => {
  getMicrophone.mockResolvedValue({ getTracks: () => [{ stop: jest.fn() }] });
  mockStart.mockImplementation(() => { mockRecording = true; return true; });
  mockStop.mockImplementation(async () => {
    mockRecording = false;
    mockOnSegment({ blob: new Blob(['whole report']), recordedAt: at(10), durationMs: 15000 });
  });
  await act(async () => root.render(<LiveCaseRecorder />));
  await openPatient(1);
  await click('Record Report');
};

test('a failed Send remains visible and retries the same report and final clip count', async () => {
  await startMicrophoneReport();
  await click('Send Report');
  const request = mockRequests.find((r) => r.url.includes('/reports/'));
  expect(request.body).toEqual({ clips: 1 });
  await act(async () => request.reject(new Error('Response lost')));
  mockRequests.splice(mockRequests.indexOf(request), 1);
  expect(container.textContent).toContain('Report not sent');
  expect(container.textContent).toContain('Retry Send Report');
  const leaving = new Event('beforeunload', { cancelable: true });
  window.dispatchEvent(leaving);
  expect(leaving.defaultPrevented).toBe(true);
  await click('Retry Send Report');
  const retry = mockRequests.find((r) => r.url.includes('/reports/'));
  expect({ url: retry.url, body: retry.body }).toEqual({ url: request.url, body: request.body });
  expect(mockStop).toHaveBeenCalledTimes(1);
  await answer(retry.url, { id: retry.url.split('/')[5], clips: 1 });
  expect(container.textContent).not.toContain('Retry Send Report');
  expect(container.textContent).not.toContain('Report not sent');
});

test('Send waits for uploads and keeps its report when an upload fails', async () => {
  await startMicrophoneReport();
  mockFailedCount = 1;
  await click('Send Report');
  expect(mockPosts.filter((r) => r.url.includes('/reports/'))).toEqual([]);
  expect(container.textContent).toContain('Send Report');
  mockFailedCount = 0;
  await click('Send Report');
  expect(mockPosts.filter((r) => r.url.includes('/reports/')).map((r) => r.body)).toEqual([{ clips: 1 }]);
  expect(mockStop).toHaveBeenCalledTimes(1);
});

test('arrival waits for the final report and cannot hide a failed Send', async () => {
  await startMicrophoneReport();
  await click('Mark Arrived');
  const request = mockRequests.find((r) => r.url.includes('/reports/'));
  expect(request.body).toEqual({ clips: 1 });
  expect(mockPosts.some((r) => r.url.endsWith('/arrive'))).toBe(false);
  await act(async () => request.reject({ response: { status: 503 } }));
  expect(mockPosts.some((r) => r.url.endsWith('/arrive'))).toBe(false);
  expect(container.textContent).toContain('Retry Send Report');
});


test('audio recorded after live clips from another device uses the next free sequence', async () => {
  await startMicrophoneReport();
  await send('segment.created', { case_id: 1, segment: { ...segment(1, 18, 8, 'Other device'), client_id: 'other' } });
  await click('Send Report');
  expect(mockEnqueue).toHaveBeenCalledWith(expect.objectContaining({ caseId: 1, seq: 9 }));
});

test('a reconnect transcript refresh advances the next recording sequence', async () => {
  await act(async () => root.render(<LiveCaseRecorder />));
  await openPatient(1);
  await send('ready');
  await answer('/api/cases/1/segments', [{ ...segment(1, 18, 8, 'Missed while offline'), client_id: 'other' }]);
  getMicrophone.mockResolvedValue({ getTracks: () => [{ stop: jest.fn() }] });
  mockStart.mockImplementation(() => { mockRecording = true; return true; });
  mockStop.mockImplementation(async () => {
    mockRecording = false;
    mockOnSegment({ blob: new Blob(['next report']), recordedAt: at(10), durationMs: 1000 });
  });
  await click('Record Report');
  await click('Send Report');
  expect(mockEnqueue).toHaveBeenCalledWith(expect.objectContaining({ caseId: 1, seq: 9 }));
});

test('a rejected upload stays visible beside a different server clip with the same sequence', async () => {
  await act(async () => root.render(<LiveCaseRecorder />));
  await openPatient(1);
  mockPending = [{ caseId: 1, seq: 0, client_id: 'different-recording', status: 'upload_failed',
    recorded_at: at(1), duration_ms: 1000 }];
  await send('case.updated', { case: patient(1, 'Patient 1', 9) });
  expect(container.textContent).toContain('Report for patient 1');
  expect(container.textContent).toContain('Upload failed');
});
