import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { TextEncoder, TextDecoder } from 'util';
import useCaseEvents from './useCaseEvents';

let container;
let root;
let requests;
let reads;
const originalFetch = global.fetch;
const originalDecoder = global.TextDecoder;
const encode = new TextEncoder();

function Screen({ onEvent, enabled = true }) {
  const status = useCaseEvents(onEvent, enabled);
  return <p>{status}</p>;
}

beforeEach(() => {
  jest.useFakeTimers();
  global.TextDecoder = TextDecoder;
  requests = [];
  reads = [];
  global.fetch = jest.fn((url, options) => new Promise((resolve) => requests.push({ options, resolve })));
  container = document.createElement('div');
  root = createRoot(container);
});
afterEach(() => {
  act(() => root.unmount());
  jest.useRealTimers();
  global.fetch = originalFetch;
  global.TextDecoder = originalDecoder;
});
const connect = () => act(async () => requests[requests.length - 1].resolve({
  ok: true, status: 200, body: { getReader: () => ({ read: () => new Promise((resolve) => reads.push(resolve)) }) },
}));
const send = (text) => act(async () => reads.shift()({ done: false, value: encode.encode(text) }));

test('expiry on an open stream stops delivery and retries, including the rest of its frame batch', async () => {
  const onEvent = jest.fn();
  act(() => root.render(<Screen onEvent={onEvent} />));
  await connect();
  await send('event: ready\ndata: {}\n\n');
  expect(container.textContent).toBe('live');
  await send('event: auth.expired\ndata: {}\n\nevent: case.updated\ndata: {"patient_info":"hidden"}\n\n');
  expect(container.textContent).toBe('unauthorized');
  expect(requests[0].options.signal.aborted).toBe(true);
  expect(onEvent.mock.calls).toEqual([['ready', {}]]);
  act(() => jest.advanceTimersByTime(60000));
  expect(global.fetch).toHaveBeenCalledTimes(1);
});

test.each([400, 401, 403])('HTTP %s stops auth retries', async (status) => {
  act(() => root.render(<Screen onEvent={jest.fn()} />));
  await act(async () => requests[0].resolve({ status }));
  expect(container.textContent).toBe('unauthorized');
  act(() => jest.advanceTimersByTime(60000));
  expect(global.fetch).toHaveBeenCalledTimes(1);
});

test('disabling a stream prevents a late ready event from reaching a replacement handler', async () => {
  const oldHandler = jest.fn(), nextHandler = jest.fn();
  act(() => root.render(<Screen onEvent={oldHandler} />));
  await connect();
  act(() => root.render(<Screen onEvent={nextHandler} enabled={false} />));
  await send('event: ready\ndata: {}\n\n');
  expect(requests[0].options.signal.aborted).toBe(true);
  expect(oldHandler).not.toHaveBeenCalled();
  expect(nextHandler).not.toHaveBeenCalled();
});
