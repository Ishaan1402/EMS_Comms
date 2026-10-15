import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import Disclosure from './Disclosure';

let container;
let root;
beforeEach(() => {
  container = document.createElement('div');
  root = createRoot(container);
});
afterEach(() => act(() => root.unmount()));

const button = () => container.querySelector('button');
const body = () => container.querySelector('[id]');

test('folded: the title and a one-line summary show, the content is in the page but hidden', () => {
  act(() => root.render(<Disclosure title="Vitals" summary="HR 132 · BP 90/60"><p>The trend.</p></Disclosure>));
  expect(button().textContent).toBe('VitalsHR 132 · BP 90/60');
  expect(button().getAttribute('aria-expanded')).toBe('false');
  expect(body().hidden).toBe(true);
  expect(body().textContent).toBe('The trend.');
});

test('opening shows the content and drops the summary, which the content then says itself', () => {
  act(() => root.render(<Disclosure title="Vitals" summary="HR 132"><p>The trend.</p></Disclosure>));
  act(() => button().click());
  expect(body().hidden).toBe(false);
  expect(button().getAttribute('aria-expanded')).toBe('true');
  expect(button().textContent).toBe('Vitals');
  act(() => button().click());
  expect(body().hidden).toBe(true);
});

test('it can start open, and it can be controlled by its owner', () => {
  act(() => root.render(<Disclosure title="Typed updates" defaultOpen><p>x</p></Disclosure>));
  expect(body().hidden).toBe(false);
  const onToggle = jest.fn();
  act(() => root.render(<Disclosure title="Typed updates" open={false} onToggle={onToggle}><p>x</p></Disclosure>));
  act(() => button().click());
  expect(onToggle).toHaveBeenCalledWith(true);
  expect(body().hidden).toBe(true);                 // controlled: only the owner opens it
});

test('the chevron is an icon, not the browser\'s native triangle', () => {
  act(() => root.render(<Disclosure title="Vitals"><p>x</p></Disclosure>));
  expect(container.querySelector('svg')).not.toBeNull();
  expect(container.querySelector('details, summary')).toBeNull();
});
