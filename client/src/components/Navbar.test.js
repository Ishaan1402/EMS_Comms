import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import Navbar from './Navbar';

let mockUser;
jest.mock('../contexts/AuthContext', () => ({ useAuth: () => ({ user: mockUser, logout: jest.fn() }) }));

let container;
let root;
beforeEach(() => {
  container = document.createElement('div');
  root = createRoot(container);
});
afterEach(() => act(() => root.unmount()));

const render = (user) => {
  mockUser = user;
  act(() => root.render(<MemoryRouter><Navbar /></MemoryRouter>));
};
const links = () => [...container.querySelectorAll('a')].map((a) => a.textContent);

test('a doctor gets Cases and Settings; an EMT gets neither, only the name and role', () => {
  render({ role: 'doctor', first_name: 'John', last_name: 'Smith' });
  expect(links()).toContain('Cases');
  expect(links()).toContain('Settings');
  expect(container.textContent).toContain('John Smith');
  expect(container.textContent).toContain('Doctor');
  render({ role: 'emt', first_name: 'Mike', last_name: 'Wilson' });
  expect(links()).not.toContain('Settings');
  expect(container.textContent).toContain('EMT');
});

test('the bar is plain: an icon mark instead of an emoji, no duplicated dashboard label, no heavy shadow', () => {
  render({ role: 'doctor', first_name: 'John', last_name: 'Smith' });
  expect(container.querySelector('svg')).not.toBeNull();
  expect(container.textContent).not.toMatch(/Dashboard|🏥/);
  expect(container.querySelector('nav').className).not.toContain('shadow');
});
