// The vital sign trend: typed readings and those extracted from the crew's reports, one row per
// sign, oldest value first. Every reading keeps where it came from (source, quote).

// Rows in the order clinicians read them; systolic and diastolic are shown together as BP.
export const VITAL_ORDER = ['hr', 'bp', 'rr', 'spo2', 'temp_c', 'gcs', 'glucose'];

export const formatValue = (value) => (Number.isInteger(value) ? value : Number(value).toFixed(1));

const byTime = (a, b) => (a.at || '').localeCompare(b.at || '');

/**
 * typed: vital_readings ({id, name, label, unit, value, measured_at}).
 * spoken: extracted vitals from GET /findings ({id, name, label, unit, value, qualifier,
 * recorded_at, source: {kind}, quote}).
 * Returns rows [{name, label, unit, readings: [{id, value, at, source, qualifier, quote}]}].
 */
export const buildVitalRows = (typed, spoken) => {
  const readings = [
    ...typed.map((r) => ({ id: `t${r.id}`, name: r.name, label: r.label, unit: r.unit, value: r.value,
      at: r.measured_at, source: 'typed vitals', qualifier: null, quote: null })),
    ...spoken.map((f) => ({ id: `f${f.id}`, name: f.name, label: f.label, unit: f.unit, value: f.value,
      at: f.recorded_at, source: f.source.kind, qualifier: f.qualifier, quote: f.quote })),
  ].sort(byTime);
  const byName = new Map();
  readings.forEach((r) => {
    if (!byName.has(r.name)) byName.set(r.name, []);
    byName.get(r.name).push({ ...r, value: formatValue(r.value) });
  });

  // Blood pressure reads as one value, 110/70: pair each systolic with the diastolic given with it.
  const diastolic = [...(byName.get('dbp') || [])];
  const pressures = (byName.get('sbp') || []).map((sbp) => {
    const index = diastolic.findIndex((dbp) => dbp.at === sbp.at && dbp.source === sbp.source);
    const dbp = index >= 0 ? diastolic.splice(index, 1)[0] : null;
    return { ...sbp, value: `${sbp.value}/${dbp ? dbp.value : '?'}` };
  });
  diastolic.forEach((dbp) => pressures.push({ ...dbp, value: `?/${dbp.value}` }));
  if (pressures.length) {
    byName.set('bp', pressures.sort(byTime));
  }

  return VITAL_ORDER.filter((name) => byName.has(name)).map((name) => {
    const series = byName.get(name);
    const latest = series[series.length - 1];
    return name === 'bp'
      ? { name, label: 'BP', unit: 'mmHg', readings: series }
      : { name, label: latest.label, unit: latest.unit, readings: series };
  });
};
