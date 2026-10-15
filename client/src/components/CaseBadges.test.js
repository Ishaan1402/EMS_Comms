import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { CategoryBadge, ProcessingBadge } from './CaseBadges';
import { CrewAlertBadges } from './CrewAlerts';

jest.mock('axios', () => ({ __esModule: true, default: { post: jest.fn() } }));
jest.mock('react-hot-toast', () => ({ __esModule: true, default: Object.assign(jest.fn(), { success: jest.fn(), error: jest.fn() }) }));

let container;
let root;
beforeEach(() => {
  container = document.createElement('div');
  root = createRoot(container);
});
afterEach(() => act(() => root.unmount()));

const render = (element) => {
  act(() => root.render(element));
  return container.firstChild.className;
};
const category = (preparation_category) => ({ preparation_category, processing: { status: 'completed' } });

// One meaning per colour: red asks for action, amber asks someone to look, gray is normal.
test('only Prepare now is red, and Can wait is never a warning colour', () => {
  expect(render(<CategoryBadge liveCase={category('Prepare now')} />)).toContain('text-red-700');
  ['Can wait', 'Routine'].forEach((label) => {
    const classes = render(<CategoryBadge liveCase={category(label)} />);
    expect(classes).toContain('text-gray-700');
    expect(classes).not.toMatch(/red|amber|orange|purple/);
  });
});

test('Cannot assess, an unknown label and a review need are amber; a processing failure is red', () => {
  expect(render(<CategoryBadge liveCase={category('Cannot assess')} />)).toContain('text-amber-900');
  expect(render(<CategoryBadge liveCase={category(null)} />)).toContain('text-amber-900');
  expect(render(<ProcessingBadge processing={{ status: 'needs_review', needs_review: true }} />)).toContain('text-amber-900');
  expect(render(<ProcessingBadge processing={{ status: 'failed', needs_review: true }} />)).toContain('text-red-700');
});

test('a crew alert is the only filled pill, so it is never mistaken for an AI label', () => {
  const alerts = { active: [{ alert: 'stroke', label: 'Stroke', key_time: null }], unacknowledged: false };
  act(() => root.render(<CrewAlertBadges liveCase={{ crew_alerts: alerts }} serverNow={0} />));
  expect(container.querySelector('span').className).toContain('bg-slate-900');
  ['Prepare now', 'Can wait', 'Routine', 'Cannot assess'].forEach((label) => {
    expect(render(<CategoryBadge liveCase={category(label)} />)).not.toContain('bg-slate-900');
  });
});
