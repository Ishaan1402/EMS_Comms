import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import useSegmentRecorder, { MAX_REPORT_MS } from './useSegmentRecorder';

let controls;
let instances;
let root;
let onSegment;
let onError;
let stopTrack;
let stream;
let originalRecorder;

class FakeRecorder {
  static isTypeSupported() { return true; }
  constructor() {
    this.mimeType = 'audio/webm';
    this.state = 'inactive';
    instances.push(this);
  }
  start() { this.state = 'recording'; }
  emit(words) { this.ondataavailable({ data: new Blob([words]) }); }
  stop() {
    this.state = 'inactive';
    setTimeout(() => { this.emit('final'); this.onstop(); }, 0);
  }
}

function Harness() {
  controls = useSegmentRecorder({ onSegment, onError });
  return <span>{controls.isRecording ? 'recording' : 'stopped'}</span>;
}

beforeEach(() => {
  jest.useFakeTimers();
  instances = [];
  onSegment = jest.fn();
  onError = jest.fn();
  stopTrack = jest.fn();
  stream = { getTracks: () => [{ stop: stopTrack }] };
  originalRecorder = window.MediaRecorder;
  window.MediaRecorder = FakeRecorder;
  root = createRoot(document.createElement('div'));
  act(() => root.render(<Harness />));
});
afterEach(() => {
  act(() => { root.unmount(); jest.runOnlyPendingTimers(); });
  window.MediaRecorder = originalRecorder;
  jest.useRealTimers();
});

test('the uninterrupted recording keeps all audio while separate live draft clips arrive', async () => {
  act(() => { expect(controls.start(stream)).toBe(true); });
  instances[0].emit('header');
  act(() => jest.advanceTimersByTime(24000));
  instances[0].emit('continuation');
  expect(instances.length).toBeGreaterThan(2);
  expect(instances[0].state).toBe('recording');
  expect(onSegment.mock.calls.length).toBeGreaterThan(0);
  expect(onSegment.mock.calls.every(([clip]) => clip.isPreview)).toBe(true);
  let stopped;
  act(() => { stopped = controls.stop(); });
  await act(async () => { jest.runOnlyPendingTimers(); await stopped; });
  const complete = onSegment.mock.calls.map(([clip]) => clip).filter((clip) => !clip.isPreview);
  expect(complete).toHaveLength(1);
  const report = complete[0];
  expect(report.blob.size).toBe('headercontinuationfinal'.length);
  expect(report.blob.type).toBe('audio/webm');
  expect(report.durationMs).toBe(24000);
  expect(stopTrack).toHaveBeenCalledTimes(1);
});

test('the duration limit stops once and retains one report for Send', async () => {
  act(() => controls.start(stream));
  act(() => jest.advanceTimersByTime(MAX_REPORT_MS));
  await act(async () => jest.runOnlyPendingTimers());
  expect(instances.every((recorder) => recorder.state === 'inactive')).toBe(true);
  expect(onSegment.mock.calls.filter(([clip]) => !clip.isPreview)).toHaveLength(1);
  expect(onError).toHaveBeenCalledWith(expect.stringContaining('Send this report'));
  expect(controls.isRecording).toBe(false);
});

test('concurrent stop calls wait for the same final audio', async () => {
  act(() => controls.start(stream));
  let first; let second;
  act(() => { first = controls.stop(); second = controls.stop(); });
  expect(first).toBe(second);
  await act(async () => { jest.runOnlyPendingTimers(); await first; });
  expect(onSegment.mock.calls.filter(([clip]) => !clip.isPreview)).toHaveLength(1);
});

test('an unsupported recorder releases the microphone and reports failure', () => {
  window.MediaRecorder = class extends FakeRecorder {
    constructor() { super(); throw new Error('Unsupported'); }
  };
  act(() => { expect(controls.start(stream)).toBe(false); });
  expect(stopTrack).toHaveBeenCalledTimes(1);
  expect(controls.isRecording).toBe(false);
  expect(onError).toHaveBeenCalledTimes(1);
});

test('unmount releases the microphone and hands off the last audio once', async () => {
  act(() => controls.start(stream));
  act(() => root.unmount());
  await act(async () => jest.runOnlyPendingTimers());
  expect(stopTrack).toHaveBeenCalled();
  expect(onSegment.mock.calls.filter(([clip]) => !clip.isPreview)).toHaveLength(1);
  expect(instances).toHaveLength(2);
});
