import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import QueueViewBar from './QueueViewBar';

let container;
let root;
beforeEach(() => {
  container = document.createElement('div');
  root = createRoot(container);
});
afterEach(() => act(() => root.unmount()));

const render = (props = {}) => act(() => root.render(
  <QueueViewBar sort="queue" onSort={() => {}} filterLabel={null} shown={5} total={5} onClear={() => {}}
                reorderBlocked={false} {...props} />,
));

test('offers the sorts, starting from the queue order, and reports the choice', () => {
  const onSort = jest.fn();
  render({ onSort });
  const select = container.querySelector('select');
  expect([...select.options].map((o) => o.textContent)).toEqual([
    'Queue order', 'Soonest arrival', 'Newest update', 'Waiting longest to be acknowledged']);
  expect(select.value).toBe('queue');
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set.call(select, 'eta');
    select.dispatchEvent(new Event('change', { bubbles: true }));
  });
  expect(onSort).toHaveBeenCalledWith('eta');
});

test('says nothing extra for the default view', () => {
  render();
  expect(container.textContent).toBe('SortQueue orderSoonest arrivalNewest updateWaiting longest to be acknowledged');
});

test('while a filter is on it says how much of the queue is showing and can clear it', () => {
  const onClear = jest.fn();
  render({ filterLabel: 'not acknowledged', shown: 2, total: 5, onClear });
  expect(container.textContent).toContain('Showing 2 of 5 (not acknowledged)');
  act(() => [...container.querySelectorAll('button')].find((b) => b.textContent === 'Clear filter').click());
  expect(onClear).toHaveBeenCalledTimes(1);
});

test('a sort other than the queue order says it is this screen\'s view only', () => {
  render({ sort: 'recent' });
  expect(container.textContent).toContain('Your view only: the queue order itself is unchanged.');
});

test('explains why the arrows are missing when the view is not the queue', () => {
  render({ reorderBlocked: true });
  expect(container.textContent).toContain('Clear the filter and sort to reorder with the arrows.');
});
