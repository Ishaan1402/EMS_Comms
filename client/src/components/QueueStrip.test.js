import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import QueueStrip from './QueueStrip';

jest.mock('axios', () => ({ __esModule: true, default: { put: jest.fn(), patch: jest.fn(), post: jest.fn(), get: jest.fn() } }));
jest.mock('react-hot-toast', () => ({ __esModule: true, default: Object.assign(jest.fn(), { error: jest.fn() }) }));

let container;
let root;
beforeEach(() => {
  container = document.createElement('div');
  root = createRoot(container);
});
afterEach(() => act(() => root.unmount()));

const queue = (overrides = {}) => ({
  rule_version: 'draft-4', rule: 'Arrival order rule.',
  counts: { inbound: 3, needs_review: 0, unacknowledged: 1, crew_alerts: 1, prepare_now: 2 }, ...overrides,
});
const hospital = { id: 1, name: 'General', arrival_highlight_minutes: 5, settings_version: 1 };
const render = (props = {}) => act(() => root.render(
  <QueueStrip queue={queue()} autoOrder hospital={hospital} riskFields={[]} onHospitalChange={() => {}}
              onAutoOrderChange={() => {}} {...props} />,
));
const button = (text) => [...container.querySelectorAll('button')].find((b) => b.textContent.includes(text));
const click = (node) => act(() => node.click());
const colourOf = (label) => [...container.querySelectorAll('span')]
  .find((s) => s.textContent.endsWith(label) && s.className).className;

test('counts that are zero fade to gray; the others keep their colour', () => {
  render();
  expect(container.textContent).toContain('3 inbound');
  expect(container.textContent).toContain('1 crew alert');
  expect(container.textContent).not.toContain('1 crew alerts');
  expect(colourOf('need review')).toContain('text-gray-500');
  expect(colourOf('not acknowledged')).toContain('text-amber-800');
  expect(colourOf('AI suggests prepare now')).toContain('text-red-700');
});

test('the strip always says whose order this is', () => {
  render();
  expect(container.textContent).toContain('Order: Auto · draft-4');
  render({ autoOrder: false });
  expect(container.textContent).toContain('Order: set by your team');
});

test('the order note and the settings stay in the page while closed, and open one at a time', () => {
  render();
  const note = container.querySelector('#queue-order-note');
  const settings = container.querySelector('#queue-settings');
  expect(note.hidden).toBe(true);
  expect(settings.hidden).toBe(true);
  expect(container.textContent).toContain('not validated on real EMS audio');
  expect(container.textContent).toContain('Arrival highlight · 5 min');

  click(button('How this order is set'));
  expect(note.hidden).toBe(false);
  expect(button('How this order is set').getAttribute('aria-expanded')).toBe('true');
  click(button('Queue settings'));
  expect(note.hidden).toBe(true);
  expect(settings.hidden).toBe(false);
  click(button('Queue settings'));
  expect(settings.hidden).toBe(true);
});

test('an unsaved arrival-highlight draft survives opening and closing the settings', () => {
  render();
  click(button('Queue settings'));
  const input = () => container.querySelector('input[type="number"]');
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input(), '15');
    input().dispatchEvent(new Event('input', { bubbles: true }));
  });
  expect(input().value).toBe('15');
  click(button('Queue settings'));
  click(button('Queue settings'));
  expect(input().value).toBe('15');
});

test('without a linked hospital or counts there is nothing to show; without counts only the settings are', () => {
  render({ hospital: null, queue: queue({ counts: null }) });
  expect(container.textContent).toBe('');
  render({ queue: queue({ counts: null }) });
  expect(container.textContent).toContain('Queue settings');
  expect(container.textContent).not.toContain('How this order is set');
  expect(container.textContent).not.toContain('inbound');
});
