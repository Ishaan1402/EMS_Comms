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
  click(button('Settings'));
  expect(settings.hidden).toBe(false);
  expect(button('Settings').getAttribute('aria-expanded')).toBe('true');
  click(button('Settings'));
  expect(settings.hidden).toBe(true);
});

test('the custom risk fields are a visible section of Settings, listed with their add form, ahead of the arrival highlight', () => {
  const fields = [{ id: 1, label: 'On blood thinners', kind: 'yes_no' }, { id: 2, label: 'Frailty score', kind: 'number' }];
  render({ riskFields: fields });
  const settings = container.querySelector('#queue-settings');
  const section = [...settings.querySelectorAll('section')].find((x) => x.textContent.includes('Custom risk fields'));
  expect(section.closest('details')).toBeNull();            // not folded away inside a second collapsible
  expect(section.querySelector('h4').textContent).toBe('Custom risk fields (2)');
  expect(section.textContent).toContain('On blood thinners');
  expect(section.textContent).toContain('Frailty score');
  expect(section.querySelector('input[placeholder="e.g. On blood thinners"]')).not.toBeNull();
  expect(section.querySelector('button[type="submit"]').textContent).toBe('Add field');
  expect(section.textContent).toContain('They never change the queue order');
  expect(settings.textContent.indexOf('Custom risk fields')).toBeLessThan(settings.textContent.indexOf('Arrival highlight'));
});

test('with no custom fields the section says so and still offers to add one', () => {
  render();
  const section = container.querySelector('#queue-settings section');
  expect(section.querySelector('h4').textContent).toBe('Custom risk fields');
  expect(section.textContent).toContain('None yet.');
  expect(section.querySelector('form')).not.toBeNull();
});

test('an unsaved arrival-highlight draft survives opening and closing the settings', () => {
  render();
  click(button('Settings'));
  const input = () => container.querySelector('input[type="number"]');
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input(), '15');
    input().dispatchEvent(new Event('input', { bubbles: true }));
  });
  expect(input().value).toBe('15');
  click(button('Settings'));
  click(button('Settings'));
  expect(input().value).toBe('15');
});

test('without a linked hospital or counts there is nothing to show; without counts only the order and settings are', () => {
  render({ hospital: null, queue: queue({ counts: null }) });
  expect(container.textContent).toBe('');
  render({ queue: queue({ counts: null }) });
  expect(container.textContent).toContain('Settings');
  expect(container.textContent).not.toContain('inbound');
});
