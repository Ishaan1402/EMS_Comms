import { mergeNewer, upsertNewer } from './liveRows';

const stamp = '2026-10-08T02:00:00.123Z';

test('a row with the same updated_at does not replace the one shown', () => {
  const shown = { id: 1, updated_at: stamp, info_version: 2 };
  const stale = { id: 1, updated_at: stamp, info_version: 1 };
  expect(upsertNewer([shown], stale)[0].info_version).toBe(2);
});

test('a newer row replaces an older one, an older one is ignored', () => {
  const older = { id: 1, updated_at: '2026-10-08T02:00:00.100Z', info_version: 1 };
  const newer = { id: 1, updated_at: '2026-10-08T02:00:00.101Z', info_version: 2 };
  expect(upsertNewer([older], newer)[0].info_version).toBe(2);
  expect(upsertNewer([newer], older)[0].info_version).toBe(2);
});

test('rows missing from a snapshot are kept', () => {
  const pushed = { id: 2, updated_at: stamp };
  expect(mergeNewer([pushed], [{ id: 1, updated_at: stamp }]).map((r) => r.id)).toEqual([2, 1]);
});
