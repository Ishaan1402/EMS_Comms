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
  expect(container.textContent).toBe('AI: Can wait');
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
  expect(container.textContent).toContain('assessing…');
  expect(container.textContent).not.toContain('Can wait');
});

// Vitals as the API returns them: typed readings, and those extracted from the crew's reports.
const spoken = (id, name, label, unit, value, qualifier) => ({
  id, name, label, unit, value, qualifier, recorded_at: '2026-10-13T15:00:00Z', source: { kind: 'radio' },
  quote: null, source_version: 2,
});
const typed = (id, name, label, unit, value) => ({
  id, name, label, unit, value, measured_at: '2026-10-13T15:00:00Z', info_version: 2,
});
const reported = {
  vitals: [typed(1, 'sbp', 'SBP', 'mmHg', 100), typed(2, 'dbp', 'DBP', 'mmHg', 64)],
  findings: {
    status: 'completed', chief_complaint: null, findings: [], treatments: [],
    vitals: [spoken(1, 'spo2', 'SpO2', '%', 95, 'on 4L oxygen'), spoken(2, 'temp_c', 'Temp', '°C', 39.1, null)],
  },
};
const vitalsDetails = () => [...container.querySelectorAll('details')].find((d) => d.textContent.startsWith('Vitals'));

test('folded vitals keep each unit and the conditions of the reading', () => {
  act(() => root.render(<CaseOverview liveCase={inbound()} {...reported} />));
  const line = vitalsDetails().querySelector('summary').textContent;
  expect(line).toContain('BP 100/64 mmHg');
  expect(line).toContain('SpO2 95 % (on 4L oxygen)');
  expect(line).toContain('Temp 39.1 °C');
});

test('the hospital reads the vitals trend open; the crew\'s screen starts folded', () => {
  act(() => root.render(<CaseOverview liveCase={inbound()} {...reported} markNew />));
  expect(vitalsDetails().open).toBe(true);
  act(() => root.render(<CaseOverview liveCase={inbound()} {...reported} />));
  expect(vitalsDetails().open).toBe(false);
});

const trend = () => [...container.querySelectorAll('details')].find((d) => d.textContent.startsWith('Vitals'));

test('in the vitals trend the latest reading stands out, earlier ones are muted, and one source icon marks the latest', () => {
  const readings = {
    vitals: [typed(1, 'sbp', 'SBP', 'mmHg', 118), typed(2, 'dbp', 'DBP', 'mmHg', 76),
      { ...typed(3, 'sbp', 'SBP', 'mmHg', 100), measured_at: '2026-10-13T15:05:00Z' },
      { ...typed(4, 'dbp', 'DBP', 'mmHg', 64), measured_at: '2026-10-13T15:05:00Z' }],
    findings: { status: 'completed', chief_complaint: null, findings: [], treatments: [], vitals: [] },
  };
  act(() => root.render(<CaseOverview liveCase={inbound()} {...readings} markNew />));
  const cell = [...trend().querySelectorAll('td')].find((td) => td.textContent.includes('118/76'));
  const [earlier, latest] = [...cell.querySelectorAll(':scope > span[title]')];
  expect(earlier.textContent).toBe('118/76');
  expect(earlier.className).toContain('text-gray-500');
  expect(latest.textContent).toBe('100/64');
  expect(latest.className).toContain('font-semibold');
  expect(cell.querySelectorAll('svg')).toHaveLength(1);
  expect(latest.querySelector('svg')).not.toBeNull();
});

test('earlier readings keep the conditions they were taken in', () => {
  const readings = {
    vitals: [],
    findings: { status: 'completed', chief_complaint: null, findings: [], treatments: [], vitals: [
      spoken(1, 'spo2', 'SpO2', '%', 94, 'on room air'),
      { ...spoken(2, 'spo2', 'SpO2', '%', 98, 'on 4L oxygen'), recorded_at: '2026-10-13T15:05:00Z' },
    ] },
  };
  act(() => root.render(<CaseOverview liveCase={inbound()} {...readings} markNew />));
  expect(trend().querySelector('table').textContent).toContain('94 (on room air)');
  expect(trend().querySelector('table').textContent).toContain('98 (on 4L oxygen)');
});

test('the handoff marks a change since the acknowledged report with an amber edge, and a missing item plainly', () => {
  const handoff = (meaningful_change) => inbound({ current_assessment: {
    summary: 'Worse.', meaningful_change, change_explanation: 'HR rose.', missing_information: 'No SpO2.' } });
  act(() => root.render(<CaseOverview liveCase={handoff('Yes')} />));
  const line = (label) => [...container.querySelectorAll('p')].find((p) => p.textContent.startsWith(label));
  expect(line('Changed since').className).toContain('border-amber-400');
  expect(line('Missing').className).toContain('border-gray-200');
  act(() => root.render(<CaseOverview liveCase={handoff('No')} />));
  expect(line('No meaningful change').className).not.toContain('border-amber-400');
});

test('whatever the screen passes in sits after the vitals and patient information, before the typed updates', () => {
  const updates = [{ id: 1, kind: 'note', created_at: '2026-10-13T15:00:00Z', body: 'Pale', info_version: 2 }];
  act(() => root.render(
    <CaseOverview liveCase={inbound()} updates={updates} {...reported} markNew><p>RISK SLOT</p></CaseOverview>,
  ));
  const text = container.textContent;
  expect(text.indexOf('Patient information')).toBeLessThan(text.indexOf('RISK SLOT'));
  expect(text.indexOf('RISK SLOT')).toBeLessThan(text.indexOf('Typed updates'));
});
