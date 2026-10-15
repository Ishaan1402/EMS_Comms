import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { CategoryBadge, NewTag, OperationalBadge, ProcessingBadge, ReportingBadge } from './CaseBadges';
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
  return container.firstChild;
};
const category = (preparation_category, extra = {}) => ({ preparation_category, processing: { status: 'completed' }, ...extra });

// One meaning per colour: red asks for action, amber asks someone to look, gray is normal. Nothing is a pill.
test('only Prepare now is red, and Can wait is never a warning colour', () => {
  expect(render(<CategoryBadge liveCase={category('Prepare now')} />).className).toContain('text-red-600');
  ['Can wait', 'Routine'].forEach((label) => {
    const classes = render(<CategoryBadge liveCase={category(label)} />).className;
    expect(classes).toContain('text-gray-600');
    expect(classes).not.toMatch(/red|amber|orange|purple/);
  });
});

test('the AI category says it is the AI\'s, and an unknown label or Cannot assess is amber', () => {
  expect(render(<CategoryBadge liveCase={category('Prepare now')} />).textContent).toBe('AI: Prepare now');
  expect(render(<CategoryBadge liveCase={category('Cannot assess')} />).className).toContain('text-amber-700');
  expect(render(<CategoryBadge liveCase={category(null)} />).textContent).toBe('AI: unavailable');
  expect(render(<CategoryBadge liveCase={category('Can wait', { assessment_is_outdated: true })} />).textContent)
    .toBe('AI: Can wait · earlier info');
  expect(render(<CategoryBadge liveCase={category(null, { processing: { assessment: { status: 'processing' } } })} />).textContent)
    .toBe('AI: assessing…');
});

test('a processing failure is red, a review need is amber, and a finished assessment gets no marker at all', () => {
  expect(render(<ProcessingBadge processing={{ status: 'failed', needs_review: true }} />).className).toContain('text-red-600');
  expect(render(<ProcessingBadge processing={{ status: 'needs_review', needs_review: true }} />).className).toContain('text-amber-700');
  act(() => root.render(<ProcessingBadge processing={{ status: 'completed' }} />));
  expect(container.textContent).toBe('');
});

test('normal states are quiet: only where the case ended up is marked', () => {
  ['inbound', 'acknowledged'].forEach((status) => {
    act(() => root.render(<OperationalBadge status={status} />));
    expect(container.textContent).toBe('');
  });
  expect(render(<OperationalBadge status="arrived" />).textContent).toBe('Arrived');
  expect(render(<OperationalBadge status="closed" />).textContent).toBe('Closed');
});

test('reporting is shown only while a report is open on an active case', () => {
  const open = { status: 'active', arrived_at: null, open_report: { id: 'r1' } };
  expect(render(<ReportingBadge liveCase={open} />).textContent).toBe('Reporting…');
  act(() => root.render(<ReportingBadge liveCase={{ ...open, arrived_at: 'x' }} />));
  expect(container.textContent).toBe('');
});

test('the new-since-acknowledged tag is plain text, not a block of colour', () => {
  const tag = render(<NewTag />);
  expect(tag.textContent).toBe('New');
  expect(tag.className).not.toMatch(/\bbg-/);
});

test('a crew alert is the only filled chip, so it is never mistaken for an AI label', () => {
  const alerts = { active: [{ alert: 'stroke', label: 'Stroke', key_time: null }], unacknowledged: false };
  act(() => root.render(<CrewAlertBadges liveCase={{ crew_alerts: alerts }} serverNow={0} />));
  expect(container.querySelector('span').className).toContain('bg-slate-900');
  ['Prepare now', 'Can wait', 'Routine', 'Cannot assess'].forEach((label) => {
    expect(render(<CategoryBadge liveCase={category(label)} />).className).not.toContain('bg-');
  });
});
