import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import Tabs from './Tabs';

let container;
let root;
beforeEach(() => {
  container = document.createElement('div');
  root = createRoot(container);
});
afterEach(() => act(() => root.unmount()));

const items = [{ id: 'all', label: 'All', count: 5 }, { id: 'new', label: 'New', count: 2 }, { id: 'plain', label: 'Plain' }];
const render = (props = {}) => act(() => root.render(<Tabs label="Filter" items={items} value="all" onChange={() => {}} {...props} />));
const tab = (label) => [...container.querySelectorAll('button')].find((b) => b.textContent.startsWith(label));

test('each choice shows its label and count, and exactly one is pressed', () => {
  render({ value: 'new' });
  expect(container.querySelector('[role="group"]').getAttribute('aria-label')).toBe('Filter');
  expect(tab('All').textContent).toBe('All5');
  expect(tab('Plain').textContent).toBe('Plain');
  expect([...container.querySelectorAll('button')].map((b) => b.getAttribute('aria-pressed'))).toEqual(['false', 'true', 'false']);
});

test('pressing a choice selects it; pressing the pressed one goes back to all', () => {
  const onChange = jest.fn();
  render({ onChange, value: 'all' });
  act(() => tab('New').click());
  expect(onChange).toHaveBeenLastCalledWith('new');
  render({ onChange, value: 'new' });
  act(() => tab('New').click());
  expect(onChange).toHaveBeenLastCalledWith('all');
  act(() => tab('All').click());
  expect(onChange).toHaveBeenLastCalledWith('all');
});

test('the pressed choice is underlined and darker; it is not a filled pill', () => {
  render({ value: 'new' });
  expect(tab('New').className).toContain('border-gray-900');
  expect(tab('New').className).not.toMatch(/\bbg-|rounded-full/);
  expect(tab('All').className).toContain('border-transparent');
});
