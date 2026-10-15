import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import Menu from './Menu';

let container;
let root;
beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(() => { act(() => root.unmount()); container.remove(); });

const options = [
  { value: 'a', label: 'Alpha', description: 'The first one.' },
  { value: 'b', label: 'Beta', description: 'The second one.' },
  { value: 'c', label: 'Gamma' },
];
const render = (props = {}) => act(() => root.render(
  <Menu label="Pick" value="a" options={options} onChange={() => {}} {...props} />,
));
const trigger = () => container.querySelector('[aria-haspopup="listbox"]');
const optionNodes = () => [...container.querySelectorAll('[role="option"]')];
const key = (target, name) => act(() => { target.dispatchEvent(new KeyboardEvent('keydown', { key: name, bubbles: true })); });

test('the button names the choice and nothing else is in the way while it is closed', () => {
  render();
  expect(trigger().textContent).toBe('Pick: Alpha');
  expect(trigger().getAttribute('aria-expanded')).toBe('false');
  expect(container.querySelector('[role="listbox"]')).toBeNull();
});

test('opening shows each option with what it means and marks the current one', () => {
  render();
  act(() => trigger().click());
  expect(optionNodes().map((o) => o.textContent)).toEqual(['AlphaThe first one.', 'BetaThe second one.', 'Gamma']);
  expect(optionNodes().map((o) => o.getAttribute('aria-selected'))).toEqual(['true', 'false', 'false']);
  expect(trigger().getAttribute('aria-expanded')).toBe('true');
});

test('choosing another option reports it and closes; choosing the current one only closes', () => {
  const onChange = jest.fn();
  render({ onChange });
  act(() => trigger().click());
  act(() => optionNodes()[1].click());
  expect(onChange).toHaveBeenCalledWith('b');
  expect(container.querySelector('[role="listbox"]')).toBeNull();
  act(() => trigger().click());
  act(() => optionNodes()[0].click());
  expect(onChange).toHaveBeenCalledTimes(1);
});

test('Escape and a click elsewhere close it; a click inside does not', () => {
  render();
  act(() => trigger().click());
  act(() => container.querySelector('[role="listbox"]').dispatchEvent(new MouseEvent('mousedown', { bubbles: true })));
  expect(container.querySelector('[role="listbox"]')).not.toBeNull();
  act(() => document.body.dispatchEvent(new MouseEvent('mousedown', { bubbles: true })));
  expect(container.querySelector('[role="listbox"]')).toBeNull();
  act(() => trigger().click());
  key(document, 'Escape');
  expect(container.querySelector('[role="listbox"]')).toBeNull();
});

test('arrow keys move between the options, wrapping around, and the current option has the focus on open', () => {
  render();
  act(() => trigger().click());
  expect(document.activeElement).toBe(optionNodes()[0]);
  key(optionNodes()[0], 'ArrowDown');
  expect(document.activeElement).toBe(optionNodes()[1]);
  key(optionNodes()[1], 'End');
  expect(document.activeElement).toBe(optionNodes()[2]);
  key(optionNodes()[2], 'ArrowDown');
  expect(document.activeElement).toBe(optionNodes()[0]);
  key(optionNodes()[0], 'ArrowUp');
  expect(document.activeElement).toBe(optionNodes()[2]);
});

test('a footer sits under the options, and a disabled menu cannot be opened', () => {
  render({ footer: 'About these.' });
  act(() => trigger().click());
  expect(container.textContent).toContain('About these.');
  render({ disabled: true });
  expect(trigger().disabled).toBe(true);
});
