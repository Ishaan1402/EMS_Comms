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

const vitalsButton = () => [...container.querySelectorAll('button[aria-expanded]')].find((b) => b.textContent.startsWith('Vitals'));
const vitalsBody = () => container.querySelector(`[id="${vitalsButton().getAttribute('aria-controls')}"]`);

test('vitals are folded to one line on both screens, with each unit and the conditions of the reading', () => {
  [false, true].forEach((markNew) => {
    act(() => root.render(<CaseOverview liveCase={inbound()} {...reported} markNew={markNew} />));
    expect(vitalsButton().getAttribute('aria-expanded')).toBe('false');
    expect(vitalsBody().hidden).toBe(true);
    const line = vitalsButton().textContent;
    expect(line).toContain('BP 100/64 mmHg');
    expect(line).toContain('SpO2 95 % (on 4L oxygen)');
    expect(line).toContain('Temp 39.1 °C');
  });
});

test('pressing Vitals opens the trend, which was in the page all along', () => {
  document.body.appendChild(container);
  act(() => root.render(<CaseOverview liveCase={inbound()} {...reported} markNew />));
  expect(vitalsBody().textContent).toContain('SpO2');
  act(() => vitalsButton().click());
  expect(vitalsButton().getAttribute('aria-expanded')).toBe('true');
  expect(vitalsBody().hidden).toBe(false);
  container.remove();
});

test('in the vitals trend the latest reading stands out, earlier ones are muted, and one source icon marks the latest', () => {
  document.body.appendChild(container);
  const readings = {
    vitals: [typed(1, 'sbp', 'SBP', 'mmHg', 118), typed(2, 'dbp', 'DBP', 'mmHg', 76),
      { ...typed(3, 'sbp', 'SBP', 'mmHg', 100), measured_at: '2026-10-13T15:05:00Z' },
      { ...typed(4, 'dbp', 'DBP', 'mmHg', 64), measured_at: '2026-10-13T15:05:00Z' }],
    findings: { status: 'completed', chief_complaint: null, findings: [], treatments: [], vitals: [] },
  };
  act(() => root.render(<CaseOverview liveCase={inbound()} {...readings} markNew />));
  const cell = [...vitalsBody().querySelectorAll('td')].find((td) => td.textContent.includes('118/76'));
  const [earlier, latest] = [...cell.querySelectorAll(':scope > span[title]')];
  expect(earlier.textContent).toBe('118/76');
  expect(earlier.className).toContain('text-gray-500');
  expect(latest.textContent).toBe('100/64');
  expect(latest.className).toContain('font-semibold');
  expect(cell.querySelectorAll('svg')).toHaveLength(1);
  expect(latest.querySelector('svg')).not.toBeNull();
  container.remove();
});

test('the folded line shows which way each vital moved, and nothing for one that has one reading', () => {
  const readings = {
    vitals: [typed(1, 'hr', 'HR', 'bpm', 104), { ...typed(2, 'hr', 'HR', 'bpm', 126), measured_at: '2026-10-13T15:05:00Z' },
      typed(3, 'rr', 'RR', '/min', 24)],
    findings: { status: 'completed', chief_complaint: null, findings: [], treatments: [], vitals: [] },
  };
  act(() => root.render(<CaseOverview liveCase={inbound()} {...readings} />));
  const arrows = [...vitalsButton().querySelectorAll('svg[aria-label]')].map((a) => a.getAttribute('aria-label'));
  expect(arrows).toEqual(['up from 104']);                  // HR rose; RR has a single reading
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
  expect(vitalsBody().querySelector('table').textContent).toContain('94 (on room air)');
  expect(vitalsBody().querySelector('table').textContent).toContain('98 (on 4L oxygen)');
});

test('the handoff says what changed and what is missing as two plain rows, not a coloured bar', () => {
  const handoff = (meaningful_change) => inbound({ current_assessment: {
    summary: 'Worse.', meaningful_change, change_explanation: 'HR rose.', missing_information: 'No SpO2.' } });
  act(() => root.render(<CaseOverview liveCase={handoff('Yes')} />));
  const row = (label) => [...container.querySelectorAll('dt')].find((d) => d.textContent === label);
  expect(row('Changed').nextSibling.textContent).toBe('HR rose.');
  expect(row('Changed').className).toContain('font-semibold');
  expect(row('Missing').nextSibling.textContent).toBe('No SpO2.');
  expect(container.innerHTML).not.toMatch(/amber-4|border-l-4/);
  act(() => root.render(<CaseOverview liveCase={handoff('No')} />));
  expect(row('No change').className).not.toContain('font-semibold');
  act(() => root.render(<CaseOverview liveCase={handoff('Unclear')} />));
  expect(row('Change unclear')).toBeDefined();
});

test('the handoff carries the AI disclosure beside the AI\'s words', () => {
  act(() => root.render(<CaseOverview liveCase={inbound({ current_assessment: { summary: 'Calm.', completed_at: '2026-10-13T15:00:00Z' } })} />));
  expect(container.textContent).toContain("AI-written from the crew's reports, not validated on real EMS audio");
  expect(container.textContent).toContain('Assessed at');
});

test('a case where nothing is wrong has no pills and no all-caps labels: only states a person must see are marked', () => {
  act(() => root.render(<CaseOverview liveCase={inbound({ latest_update_acknowledged: true, operational_status: 'acknowledged',
    acknowledgment: { user_name: 'John Smith', acknowledged_at: '2026-10-13T15:00:00Z' } })} {...reported} markNew />));
  expect(container.textContent).toContain('Seen by John Smith');
  expect(container.textContent).not.toContain('Acknowledged');
  expect(container.textContent).not.toContain('Inbound');
  expect(container.querySelector('details, summary')).toBeNull();
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
