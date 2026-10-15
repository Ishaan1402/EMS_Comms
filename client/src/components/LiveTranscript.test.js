import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import LiveTranscript from './LiveTranscript';

let container;
let root;
beforeEach(() => { container = document.createElement('div'); root = createRoot(container); });
afterEach(() => act(() => root.unmount()));
const draft = { id: 1, seq: 0, report_id: 'r1', is_preview: 1, report_complete: 0,
  status: 'completed', text: 'BP 100 over 60', recorded_at: '2026-10-14T12:00:00Z' };
const full = { ...draft, id: 2, seq: 1, is_preview: 0, report_complete: 1, text: 'BP 100 over 64', info_version: 2 };

test('live words are labelled draft before Send, then replaced with the completed transcript', () => {
  act(() => root.render(<LiveTranscript segments={[draft]} />));
  expect(container.textContent).toContain('BP 100 over 60');
  expect(container.textContent).toContain('Live draft');
  expect(container.textContent).toContain('not used by AI');
  act(() => root.render(<LiveTranscript segments={[draft, full]} />));
  expect(container.textContent).toContain('BP 100 over 64');
  expect(container.textContent).not.toContain('BP 100 over 60');
  act(() => container.querySelector('button').click());
  expect(container.textContent).toContain('BP 100 over 60');
  expect(container.textContent).toContain('Live draft');
});

test('a failed full transcription leaves the drafts visible and labelled', () => {
  act(() => root.render(<LiveTranscript segments={[draft, { ...full, status: 'failed', text: null, error: 'timed out' }]} />));
  expect(container.textContent).toContain('Live draft');
  expect(container.textContent).toContain('Transcription failed');
});

test('missing full audio cannot be repaired by retrying a draft transcription', () => {
  act(() => root.render(<LiveTranscript segments={[{ ...draft, status: 'failed',
    error: 'Complete report audio was not received; resend it or send a typed update.' }]}
    onRetry={jest.fn()} onDismiss={jest.fn()} />));
  expect(container.textContent).toContain('Complete report audio was not received');
  expect([...container.querySelectorAll('button')].map((b) => b.textContent)).toEqual(['Mark handled']);
});

test('a full recording with no detected speech does not hide the earlier draft', () => {
  act(() => root.render(<LiveTranscript segments={[draft, { ...full, text: '' }]} />));
  expect(container.textContent).toContain('Live draft');
  expect(container.textContent).toContain('no speech detected');
});
