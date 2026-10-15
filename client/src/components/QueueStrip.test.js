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
  queue_mode: 'standard', rules: { standard: 'Arrival order rule.', smart: 'Smart rule.' },
  rule_versions: { standard: 'draft-4', smart: 'smart-1' },
  counts: { inbound: 3, needs_review: 0, unacknowledged: 1, crew_alerts: 1, prepare_now: 2 }, ...overrides,
});
const hospital = { id: 1, name: 'General', arrival_highlight_minutes: 5, settings_version: 1 };
const render = (props = {}) => act(() => root.render(
  <QueueStrip queue={queue()} hospital={hospital} riskFields={[]} onHospitalChange={() => {}}
              onModeChange={() => {}} {...props} />,
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

test('the strip always shows which order is in use, with no draft label and no standing paragraph', () => {
  render();
  expect(container.querySelector('[aria-checked="true"]').textContent).toBe('Standard');
  render({ queue: queue({ queue_mode: 'smart' }) });
  expect(container.querySelector('[aria-checked="true"]').textContent).toBe('Smart (AI)');
  render({ queue: queue({ queue_mode: 'manual' }) });
  expect(container.querySelector('[aria-checked="true"]').textContent).toBe('Manual');
  expect(container.querySelector('#queue-order-note')).toBeNull();
  expect(container.textContent).not.toContain('How this order is set');
  expect([...container.querySelectorAll('p')].filter((p) => !p.closest('#queue-order-help, #queue-settings'))).toHaveLength(0);
});

test('the settings stay in the page while closed, and open from the Settings button', () => {
  render();
  const settings = container.querySelector('#queue-settings');
  expect(settings.hidden).toBe(true);
  expect(container.textContent).toContain('Arrival highlight · 5 min');
  click(button('Queue settings'));
  expect(settings.hidden).toBe(false);
  expect(button('Queue settings').getAttribute('aria-expanded')).toBe('true');
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

test('without a linked hospital or counts there is nothing to show; without counts only the order and settings are', () => {
  render({ hospital: null, queue: queue({ counts: null }) });
  expect(container.textContent).toBe('');
  render({ queue: queue({ counts: null }) });
  expect(container.textContent).toContain('Queue settings');
  expect(container.textContent).not.toContain('inbound');
});
