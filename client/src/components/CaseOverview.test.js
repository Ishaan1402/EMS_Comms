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
  last_segment_at: '2020-01-01T00:00:00Z', preparation_category: 'Can wait',
  processing: { status: 'completed' }, open_report: null,
  current_assessment: { summary: 'Patient awake.' }, ...overrides,
});

const reporting = { open_report: { id: 'r1', clips: 2, started_at: '2026-10-13T15:00:00Z' } };

test('while the crew is reporting, the handoff says it covers the reports already sent', () => {
  act(() => root.render(<CaseOverview liveCase={inbound(reporting)} />));
  expect(container.textContent).toContain('Reporting…');
  expect(container.textContent).toContain("covers the reports they've already sent");
  expect(container.textContent).toContain('Patient awake.');
  act(() => root.render(<CategoryBadge liveCase={inbound(reporting)} />));
  expect(container.textContent).toBe('Can wait');
});

test.each([
  {}, { ...reporting, arrived_at: '2026-10-11T19:00:00Z' }, { ...reporting, status: 'closed' },
])('the reporting caveat is limited to inbound cases with a report in progress: %j', (overrides) => {
  act(() => root.render(<CaseOverview liveCase={inbound(overrides)} />));
  expect(container.textContent).not.toContain('Reporting');
  expect(container.textContent).not.toContain('provisional');
});

test('an ETA refresh keeps the earlier result visibly outdated', () => {
  act(() => root.render(<CaseOverview liveCase={inbound({ assessment_is_outdated: true })} />));
  expect(container.textContent).toContain('Can wait · earlier info');
  expect(container.textContent).toContain('based on earlier information or ETA');
});

test('the handoff identifies its assessment time separately from the live arrival time', () => {
  act(() => root.render(<CaseOverview liveCase={inbound({
    current_assessment: { summary: 'A report snapshot.', completed_at: '2026-10-12T20:00:00Z' },
  })} />));
  expect(container.textContent).toContain('Assessed at');
  expect(container.textContent).toContain('current arrival time shown above');
});

test('a report with no assessment never invents a category', () => {
  act(() => root.render(<CaseOverview liveCase={inbound({
    current_assessment: null, preparation_category: null,
    processing: { status: 'pending', assessment: { status: 'pending' } },
  })} />));
  expect(container.textContent).toContain('Assessing…');
  expect(container.textContent).not.toContain('Can wait');
});
