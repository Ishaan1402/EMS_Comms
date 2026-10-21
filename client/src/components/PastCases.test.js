import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import PastCases from './PastCases';

const mockGet = jest.fn();
let mockHandler = null;
jest.mock('axios', () => ({ __esModule: true, default: { get: (url) => mockGet(url) } }));
jest.mock('../hooks/useCaseEvents', () => ({
  __esModule: true,
  default: (handler) => { mockHandler = handler; return 'live'; },
}));
jest.mock('./CaseOverview', () => ({ liveCase }) => <p>Handoff for case {liveCase.id}</p>);
jest.mock('./LiveTranscript', () => () => null);

const closed = (id, unit, closedAt) => ({
  id, ems_unit: unit, status: 'closed', patient_info: `${unit} patient`, destination_hospital_name: 'General',
  started_at: '2026-10-20T15:00:00.000Z', closed_at: closedAt, updated_at: closedAt,
});

let container;
let root;
beforeEach(() => {
  mockGet.mockImplementation((url) => Promise.resolve({ data: url === '/api/cases?status=closed' ? [
    closed(1, 'Medic 1', '2026-10-20T15:20:00.000Z'), closed(2, 'Medic 2', '2026-10-20T16:20:00.000Z'),
  ] : [] }));
  container = document.createElement('div');
  root = createRoot(container);
});
afterEach(() => act(() => root.unmount()));

const button = (text) => [...container.querySelectorAll('button')].find((b) => b.textContent.includes(text));

test('closed cases are folded away, newest first, and one opens to show its handoff', async () => {
  await act(async () => root.render(<PastCases />));
  const folder = button('Past cases');
  expect(folder.getAttribute('aria-expanded')).toBe('false');
  expect(folder.textContent).toContain('2 closed');

  await act(async () => folder.click());
  const rows = [...container.querySelectorAll('li > button')].map((b) => b.textContent);
  expect(rows[0]).toContain('Medic 2');
  expect(rows[1]).toContain('Medic 1');

  await act(async () => button('Medic 1').click());
  expect(container.textContent).toContain('Handoff for case 1');
});

test('a case the crew ends joins the folder; nothing shows while there are none', async () => {
  mockGet.mockImplementation(() => Promise.resolve({ data: [] }));
  await act(async () => root.render(<PastCases />));
  expect(container.textContent).toBe('');

  await act(async () => mockHandler('case.updated', { case: closed(3, 'Medic 3', '2026-10-20T17:00:00.000Z') }));
  expect(button('Past cases').textContent).toContain('1 closed');
});
