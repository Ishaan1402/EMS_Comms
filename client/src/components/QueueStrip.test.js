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
  rule_versions: { standard: 'draft-4', smart: 'smart-1' }, ...overrides,
});
const counts = { inbound: 3, needsReview: 0, unacknowledged: 1, crewAlerts: 1, prepareNow: 2, arrivingSoon: 1 };
const hospital = { id: 1, name: 'General', arrival_highlight_minutes: 5, settings_version: 1 };
const render = (props = {}) => act(() => root.render(
  <QueueStrip queue={queue()} counts={counts} hospital={hospital} riskFields={[]} onHospitalChange={() => {}}
              onModeChange={() => {}} {...props} />,
));
const button = (text) => [...container.querySelectorAll('button')].find((b) => b.textContent.includes(text));
const click = (node) => act(() => node.click());
const chip = (label) => [...container.querySelectorAll('[aria-pressed]')].find((b) => b.textContent.endsWith(label));

test('counts that are zero fade to gray; the others keep their colour', () => {
  render();
  expect(container.textContent).toContain('3 inbound');
  expect(container.textContent).toContain('1 crew alert');
  expect(container.textContent).not.toContain('1 crew alerts');
  expect(chip('need review').className).toContain('text-gray-500');
  expect(chip('not acknowledged').className).toContain('text-amber-800');
  expect(chip('AI suggests prepare now').className).toContain('text-red-700');
});

test('each count is a filter: pressing it narrows the list, pressing it again shows everything', () => {
  const onFilter = jest.fn();
  render({ onFilter });
  expect(container.querySelectorAll('[aria-pressed]')).toHaveLength(6);
  click(chip('not acknowledged'));
  expect(onFilter).toHaveBeenLastCalledWith('unacknowledged');
  click(chip('arriving soon'));
  expect(onFilter).toHaveBeenLastCalledWith('arrivingSoon');
  click(chip('crew alert'));
  expect(onFilter).toHaveBeenLastCalledWith('crewAlerts');

  render({ onFilter, filter: 'unacknowledged' });
  expect(chip('not acknowledged').getAttribute('aria-pressed')).toBe('true');
  expect(chip('not acknowledged').className).toContain('bg-blue-50');
  click(chip('not acknowledged'));
  expect(onFilter).toHaveBeenLastCalledWith('all');       // pressing the active one clears it
  click(chip('inbound'));
  expect(onFilter).toHaveBeenLastCalledWith('all');
});

test('with nothing filtered "inbound" is pressed but not highlighted; a narrowing filter is highlighted', () => {
  render();
  expect(chip('inbound').getAttribute('aria-pressed')).toBe('true');
  expect(chip('inbound').className).not.toContain('ring-1');
  render({ filter: 'crewAlerts' });
  expect(chip('inbound').getAttribute('aria-pressed')).toBe('false');
  expect(chip('crew alert').className).toContain('ring-1');
});

test('a count of zero can still be pressed, so an active filter that emptied can be cleared', () => {
  const onFilter = jest.fn();
  render({ onFilter, filter: 'needsReview' });
  click(chip('need review'));
  expect(onFilter).toHaveBeenLastCalledWith('all');
});

test('arriving soon is only offered when the screen can count it', () => {
  render({ counts: { ...counts, arrivingSoon: undefined } });
  expect(chip('arriving soon')).toBeUndefined();
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
  render({ hospital: null, counts: null });
  expect(container.textContent).toBe('');
  render({ counts: null });
  expect(container.textContent).toContain('Settings');
  expect(container.textContent).not.toContain('inbound');
});
