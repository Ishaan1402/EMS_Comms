import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import CaseOverview, { CategoryBadge } from './CaseOverview';

let container;
let root;
beforeEach(() => {
  container = document.createElement('div');
  root = createRoot(container);
});
afterEach(() => act(() => root.unmount()));

const inbound = (overrides = {}) => ({
  status: 'active', operational_status: 'inbound', segment_count: 1,
  // An old clip does not establish that the report is complete.
  last_segment_at: '2020-01-01T00:00:00Z', preparation_category: 'Can wait',
  processing: { status: 'completed' },
  current_assessment: { summary: 'Patient awake.' }, ...overrides,
});

test('an inbound audio handoff and its queue category stay provisional between clips', () => {
  act(() => root.render(<CaseOverview liveCase={inbound()} />));
  expect(container.textContent).toContain('Can wait · provisional');
  expect(container.textContent).toContain('the radio report may still be incomplete');
  expect(container.textContent).toContain('Patient awake.');
  act(() => root.render(<CategoryBadge liveCase={inbound()} />));
  expect(container.textContent).toBe('Can wait · provisional');
});

test.each([
  { segment_count: 0 }, { arrived_at: '2026-10-11T19:00:00Z' }, { status: 'closed' },
])('the live-radio caveat is limited to inbound audio cases: %j', (overrides) => {
  act(() => root.render(<CaseOverview liveCase={inbound(overrides)} />));
  expect(container.textContent).not.toContain('provisional');
  expect(container.textContent).not.toContain('Provisional');
});

test('an ETA refresh keeps the earlier result visibly outdated and provisional', () => {
  act(() => root.render(<CaseOverview liveCase={inbound({ assessment_is_outdated: true })} />));
  expect(container.textContent).toContain('Can wait · earlier info · provisional');
  expect(container.textContent).toContain('based on earlier information or ETA');
});

test('the handoff identifies its assessment time separately from the live arrival time', () => {
  act(() => root.render(<CaseOverview liveCase={inbound({
    current_assessment: { summary: 'A report snapshot.', completed_at: '2026-10-12T20:00:00Z' },
  })} />));
  expect(container.textContent).toContain('Assessed at');
  expect(container.textContent).toContain('current arrival time shown above');
});

test('a provisional report with no assessment never invents a category', () => {
  act(() => root.render(<CaseOverview liveCase={inbound({
    current_assessment: null, preparation_category: null,
    processing: { status: 'pending', assessment: { status: 'pending' } },
  })} />));
  expect(container.textContent).toContain('Assessing…');
  expect(container.textContent).not.toContain('Can wait');
});
