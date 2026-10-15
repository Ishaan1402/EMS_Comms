import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import QueueToolbar from './QueueToolbar';

jest.mock('axios', () => ({ __esModule: true, default: { put: jest.fn() } }));
jest.mock('react-hot-toast', () => ({ __esModule: true, default: Object.assign(jest.fn(), { error: jest.fn() }) }));

let container;
let root;
beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(() => { act(() => root.unmount()); container.remove(); });

const counts = { inbound: 5, unacknowledged: 2, crewAlerts: 0, needsReview: 0, prepareNow: 1, arrivingSoon: 3 };
const render = (props = {}) => act(() => root.render(
  <QueueToolbar counts={counts} filter="all" onFilter={() => {}} sort="queue" onSort={() => {}} queueMode="standard"
                onModeChange={() => {}} hasHospital {...props} />,
));
const buttons = () => [...container.querySelectorAll('[aria-pressed]')].map((b) => b.textContent);
const menuButton = (prefix) => [...container.querySelectorAll('[aria-haspopup]')].find((b) => b.textContent.startsWith(prefix));

test('a filter with nothing in it is not offered, and All always is', () => {
  render();
  expect(buttons()).toEqual(['All5', 'Not acknowledged2', 'AI: Prepare now1', 'Arriving soon3']);
});

test('a filter that is on stays visible even when it has emptied, so it can be turned off', () => {
  render({ filter: 'crewAlerts' });
  expect(buttons()).toContain('Crew alerts0');
  expect(container.querySelector('[aria-pressed="true"]').textContent).toBe('Crew alerts0');
});

test('pressing a filter reports it', () => {
  const onFilter = jest.fn();
  render({ onFilter });
  act(() => [...container.querySelectorAll('[aria-pressed]')].find((b) => b.textContent.startsWith('Arriving')).click());
  expect(onFilter).toHaveBeenCalledWith('arrivingSoon');
});

test('the order and the sort are two quiet menus on the right, named in short words', () => {
  render({ queueMode: 'ai_assisted', sort: 'eta' });
  expect(menuButton('Order').textContent).toBe('Order: AI-assisted');
  expect(menuButton('Sort').textContent).toBe('Sort: ETA');
  act(() => menuButton('Sort').click());
  expect([...container.querySelectorAll('[role="option"]')].map((o) => o.querySelector('span span').textContent))
    .toEqual(['Queue', 'ETA', 'Updated', 'Waiting']);
});

test('picking a sort reports it', () => {
  const onSort = jest.fn();
  render({ onSort });
  act(() => menuButton('Sort').click());
  act(() => [...container.querySelectorAll('[role="option"]')].find((o) => o.textContent.startsWith('Waiting')).click());
  expect(onSort).toHaveBeenCalledWith('waiting');
});

test('without a hospital there is no order to choose; without counts only the menus show', () => {
  render({ hasHospital: false });
  expect(menuButton('Order')).toBeUndefined();
  render({ counts: null });
  expect(container.querySelectorAll('[aria-pressed]')).toHaveLength(0);
  expect(menuButton('Sort')).toBeDefined();
});
