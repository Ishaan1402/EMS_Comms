import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import axios from 'axios';
import useSegmentUploads from './useSegmentUploads';
jest.mock('axios', () => ({ __esModule: true, default: { post: jest.fn() } }));
let controls;
let root;
let uploaded;
let warning;
function Harness() { controls = useSegmentUploads(uploaded); return null; }
const clip = (seq, is_preview = false) => ({ caseId: 7, seq, report_id: 'r1', is_preview,
  blob: new Blob(['audio'], { type: 'audio/webm' }), recorded_at: '2026-10-14T12:00:00Z', duration_ms: 8000 });
beforeEach(() => {
  axios.post.mockReset(); uploaded = jest.fn();
  warning = jest.spyOn(console, 'warn').mockImplementation(() => {});
  root = createRoot(document.createElement('div'));
  act(() => root.render(<Harness />));
});
afterEach(() => { act(() => root.unmount()); warning.mockRestore(); });

test('a stuck draft upload does not hold up complete audio or Send', async () => {
  let draftDone;
  axios.post.mockImplementation((url, data) => data.get('preview')
    ? new Promise((resolve) => { draftDone = resolve; })
    : Promise.resolve({ data: { id: 2, case_id: 7, is_preview: 0 } }));
  await act(async () => { controls.enqueue(clip(0, true)); controls.enqueue(clip(1)); await controls.waitForIdle(); });
  expect(uploaded).toHaveBeenCalledTimes(1);
  expect(controls.pending).toEqual([]);
  expect(axios.post.mock.calls[0][1].get('preview')).toBe('true');
  expect(axios.post.mock.calls[1][1].get('preview')).toBeNull();
  await act(async () => draftDone({ data: { id: 1, case_id: 7, is_preview: 1 } }));
  expect(uploaded).toHaveBeenCalledTimes(2);
});

test('a failed live draft is not retried and never becomes a failed complete upload', async () => {
  axios.post.mockRejectedValue({ response: { status: 503 } });
  await act(async () => { controls.enqueue(clip(0, true)); await controls.waitForIdle(); });
  expect(axios.post).toHaveBeenCalledTimes(1);
  expect(controls.failedCount()).toBe(0);
  expect(controls.pending).toEqual([]);
});
