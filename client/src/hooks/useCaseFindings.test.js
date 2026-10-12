import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import useCaseFindings from './useCaseFindings';

// Every GET waits until the test answers it.
const mockRequests = [];
jest.mock('axios', () => ({
  __esModule: true,
  default: {
    get: (url) => new Promise((resolve) => mockRequests.push({ url, resolve })),
    post: () => new Promise(() => {}),
  },
}));
jest.mock('react-hot-toast', () => ({ __esModule: true, default: { error: jest.fn() } }));

const at = (second) => `2026-10-11T18:00:${String(second).padStart(2, '0')}.000Z`;
const snapshot = (caseId, second, complaint) => ({
  case_id: caseId, as_of: at(second), status: 'completed', chief_complaint: { text: complaint },
  vitals: [], findings: [], treatments: [],
});

let hook;
const Probe = ({ caseId }) => {
  hook = useCaseFindings(caseId);
  return null;
};

let root;
beforeEach(() => {
  mockRequests.length = 0;
  root = createRoot(document.createElement('div'));
});
afterEach(() => act(() => root.unmount()));

const take = (url) => {
  const index = mockRequests.findIndex((request) => request.url === url);
  if (index < 0) throw new Error(`No request waiting for ${url}`);
  return mockRequests.splice(index, 1)[0];
};

test('a fetch answered after a newer pushed snapshot does not roll it back', async () => {
  await act(async () => root.render(<Probe caseId={5} />));
  await act(async () => take('/api/cases/5/findings').resolve({ data: snapshot(5, 1, 'first') }));

  await act(async () => { hook.reload(); });  // e.g. after a reconnect; held back
  const held = take('/api/cases/5/findings');
  await act(async () => hook.apply({ case_id: 5, findings: snapshot(5, 3, 'newest') }));
  await act(async () => held.resolve({ data: snapshot(5, 2, 'older') }));

  expect(hook.findings.chief_complaint.text).toBe('newest');
});

test("an answer for the previous case never replaces the current case's information", async () => {
  await act(async () => root.render(<Probe caseId={5} />));
  const forFive = take('/api/cases/5/findings');
  await act(async () => root.render(<Probe caseId={6} />));
  await act(async () => take('/api/cases/6/findings').resolve({ data: snapshot(6, 2, 'case six') }));
  await act(async () => forFive.resolve({ data: snapshot(5, 4, 'case five') }));

  expect(hook.findings.case_id).toBe(6);
  expect(hook.findings.chief_complaint.text).toBe('case six');
});
