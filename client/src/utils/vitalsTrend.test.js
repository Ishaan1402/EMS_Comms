import { buildVitalRows } from './vitalsTrend';

const typed = (id, name, value, at) => ({ id, name, label: name, unit: 'u', value, measured_at: at });
const spoken = (id, name, value, at, extra = {}) => ({ id, name, label: name, unit: 'u', value, recorded_at: at,
  source: { kind: 'radio' }, quote: `${name} ${value}`, qualifier: null, ...extra });

test('typed and radio readings form one series per sign, oldest first, each with its source', () => {
  const rows = buildVitalRows([typed(1, 'hr', 128, '2026-10-11T18:08:00.000Z')],
    [spoken(5, 'hr', 118, '2026-10-11T18:01:00.000Z')]);
  expect(rows).toHaveLength(1);
  expect(rows[0].readings.map((r) => [r.value, r.source])).toEqual([[118, 'radio'], [128, 'typed vitals']]);
});

test('systolic and diastolic given together read as one blood pressure', () => {
  const at1 = '2026-10-11T18:01:00.000Z';
  const at2 = '2026-10-11T18:09:00.000Z';
  const rows = buildVitalRows([], [spoken(1, 'sbp', 110, at1), spoken(2, 'dbp', 70, at1),
    spoken(3, 'sbp', 92, at2), spoken(4, 'dbp', 58, at2), spoken(5, 'hr', 118, at1)]);
  expect(rows.map((r) => r.name)).toEqual(['hr', 'bp']);
  expect(rows[1].readings.map((r) => r.value)).toEqual(['110/70', '92/58']);
  expect(rows[1].unit).toBe('mmHg');
});

test('a diastolic without its systolic is still shown', () => {
  const rows = buildVitalRows([], [spoken(1, 'dbp', 60, '2026-10-11T18:01:00.000Z')]);
  expect(rows[0].readings.map((r) => r.value)).toEqual(['?/60']);
});

test('qualifiers and decimals are kept', () => {
  const rows = buildVitalRows([], [spoken(1, 'spo2', 89, '2026-10-11T18:01:00.000Z', { qualifier: 'on room air' }),
    spoken(2, 'temp_c', 39.1, '2026-10-11T18:01:00.000Z')]);
  expect(rows.map((r) => [r.name, r.readings[0].value, r.readings[0].qualifier]))
    .toEqual([['spo2', 89, 'on room air'], ['temp_c', '39.1', null]]);
});
