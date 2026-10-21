import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { EsiBadge, NewTag, OperationalBadge, ProcessingBadge, ReportingBadge } from './CaseBadges';
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
const esi = (esi_level, extra = {}) => ({
  esi_level, ai_esi_level: esi_level, risk_score: esi_level ? 6 : null, esi_change: null,
  preparation_category: esi_level ? 'derived' : null, processing: { status: 'completed' }, ...extra,
});

// One meaning per colour: red asks for action, amber asks someone to look, gray is normal. Nothing is a pill.
test('only ESI 1 is red, and no other level is a warning colour', () => {
  expect(render(<EsiBadge liveCase={esi(1)} />).className).toContain('text-red-600');
  [2, 3, 4, 5].forEach((level) => {
    const classes = render(<EsiBadge liveCase={esi(level)} />).className;
    expect(classes).toContain('text-gray-600');
    expect(classes).not.toMatch(/red|amber|orange|purple/);
  });
});

test('the case pane says whether the level is suggested or a doctor\'s, with the risk score', () => {
  expect(render(<EsiBadge liveCase={esi(2)} />).textContent).toBe('Suggested ESI 2 · Risk 6/10');
  const changed = esi(1, { ai_esi_level: 3, esi_change: { esi_level: 1, user_name: 'John Smith' } });
  expect(render(<EsiBadge liveCase={changed} />).textContent).toBe('ESI 1 · set by John Smith (suggested ESI 3) · Risk 6/10');
  const agreed = esi(3, { esi_change: { esi_level: 3, user_name: 'John Smith' } });
  expect(render(<EsiBadge liveCase={agreed} />).textContent).toBe('ESI 3 · set by John Smith · Risk 6/10');
  expect(render(<EsiBadge liveCase={esi(3, { assessment_is_outdated: true })} />).textContent)
    .toBe('Suggested ESI 3 · Risk 6/10 · earlier info');
});

test('a queue row shows the level and score alone, with where the level came from on hover', () => {
  const row = render(<EsiBadge liveCase={esi(2)} detail={false} />);
  expect(row.textContent).toBe('ESI 2 · Risk 6/10');
  expect(row.title).toBe('Suggested ESI');
  const changed = esi(1, { ai_esi_level: 3, esi_change: { esi_level: 1, user_name: 'John Smith' } });
  expect(render(<EsiBadge liveCase={changed} detail={false} />).title).toBe('ESI set by John Smith (suggested ESI 3)');
});

test('Cannot assess and no assessment are amber and never a guessed level', () => {
  const cannot = render(<EsiBadge liveCase={esi(null, { preparation_category: 'Cannot assess', risk_score: 5 })} />);
  expect(cannot.className).toContain('text-amber-700');
  expect(cannot.textContent).toBe('ESI: cannot assess · Risk 5/10');
  expect(render(<EsiBadge liveCase={esi(null)} />).textContent).toBe('Not assessed');
  expect(render(<EsiBadge liveCase={esi(null, { preparation_category: 'Routine' })} />).textContent).toBe('Routine');  // closed before ESI
  expect(render(<EsiBadge liveCase={esi(null, { processing: { assessment: { status: 'processing' } } })} />).textContent)
    .toBe('Assessing…');
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
  [1, 3, null].forEach((level) => {
    expect(render(<EsiBadge liveCase={esi(level)} />).className).not.toContain('bg-');
  });
});
